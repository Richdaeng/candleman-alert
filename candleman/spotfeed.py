"""현물 실시간 연결 · 체결 알림 · 현물 보유(#현재포지션 · /포지션). 계산은 spot.py 에 있고, 여기서는 바이낸스 조회와 디스코드 보내기만.

현물 사용자 자료는 WebSocket API 에 서명으로 구독합니다(userDataStream.subscribe.signature). 이것도 폰에서 '나가는' 연결이고,
받은 것에서 숫자만 읽습니다. 키는 서명할 때만 쓰고(binance.py), 주문 · 출금 기능은 없습니다.
"""
import asyncio
import json
import time
from decimal import Decimal

from . import fmt, store
from .binance import WS_API, BinanceError
from .fmt import ZERO, dec
from .spot import DUST_USD, SHOW_USD, CostBook, SpotTracker, fill_text
from .ws import WebSocket, WSClosed

READ_IDLE_S = 5 * 60          # 서버 ping(20초마다)도 이만큼 없으면 죽은 연결로 보고 다시 붙음
REFRESH_S = 23 * 3600         # 한 연결은 24시간까지라 그 전에 새로 붙음
GAP_NOTE_S = 20               # 이보다 길게 끊겼으면 #봇-상태에 알림
SETTLE_S = 1.5                # 체결 글을 만들기 전 잠깐 기다림 (잔고 이벤트가 체결 이벤트 바로 뒤에 옴)
MAX_PAGES = 20                # 장부를 처음 만들 때 거래 기록을 이만큼(1,000개씩)까지 받음
REQ_ID = "candleman-spot"


def now_ms():
    return int(time.time() * 1000)


class SubscribeError(Exception):
    pass


class SpotFeed:
    def __init__(self, app):
        self.app = app                     # app.b(바이낸스) · app.d(디스코드) · app.state 를 씀
        self.t = SpotTracker()
        self.ws = None
        self.down_since = None
        self.fills = asyncio.Queue()
        self.no_market = set()             # USDT 짝이 없는 자산 (값을 매길 수 없음)
        self.told_reject = False

    # ------------------------------------------------------------------ 실시간
    async def run_forever(self):
        asyncio.create_task(self._fill_worker())
        backoff = 2
        while True:
            try:
                self.t.load_balances(await self.app.b.spot_balances())
                try:
                    self.t.load_orders(await self.app.b.spot_open_orders(), now_ms())
                except BinanceError as e:
                    store.log("현물 미체결 주문 조회 실패(계속): %s" % e)
                self.ws = await WebSocket.connect(WS_API)
                await self.ws.send_text(json.dumps(self.app.b.spot_subscribe(REQ_ID)))
                await self._await_reply()
                opened = time.time()
                if self.down_since:
                    await self._after_gap(self.down_since)
                    self.down_since = None
                backoff = 2
                while time.time() - opened < REFRESH_S:
                    msg = json.loads(await self.ws.recv_alive(READ_IDLE_S))
                    ev = msg.get("event") if isinstance(msg, dict) else None
                    if not isinstance(ev, dict):
                        continue
                    if ev.get("e") in ("eventStreamTerminated", "serverShutdown"):
                        store.log("현물 연결: %s — 다시 붙음" % ev.get("e"))
                        break
                    await self.handle(*self.t.on_event(ev, now_ms()))
            except SubscribeError as e:
                store.log("현물 구독 거절: %s" % e)
                if not self.told_reject:
                    self.told_reject = True
                    await self.app.d.put("status", "⚠️ 현물 알림 연결이 거절됨 (%s). 선물 알림은 그대로 감." % e)
            except (WSClosed, asyncio.TimeoutError, OSError, BinanceError, ValueError) as e:
                store.log("현물 연결 끊김: %s %s" % (type(e).__name__, str(e)[:160]))
            except Exception as e:                           # 모르는 오류도 죽지 않고 다시 붙음
                store.log("현물 예상 못 한 오류: %s %s" % (type(e).__name__, str(e)[:160]))
            finally:
                if self.ws:
                    await self.ws.close()
                    self.ws = None
                if self.down_since is None:
                    self.down_since = now_ms()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)

    async def _await_reply(self):
        """구독 요청의 답을 기다림 (그사이 온 이벤트도 처리)."""
        for _ in range(10):
            msg = json.loads(await self.ws.recv_alive(30))
            if msg.get("id") == REQ_ID:
                if msg.get("status") != 200:
                    raise SubscribeError(str((msg.get("error") or {}).get("msg") or msg.get("status"))[:160])
                return
            if isinstance(msg.get("event"), dict):
                await self.handle(*self.t.on_event(msg["event"], now_ms()))
        raise ConnectionError("현물 구독 답이 오지 않음")

    async def _after_gap(self, since):
        n = now_ms()
        self.app._pos_due = n                              # #현재포지션은 지금 값으로 다시 맞춤
        if n - since >= GAP_NOTE_S * 1000:
            await self.app.d.put("status", "🔌 현물 연결이 끊겼다가 다시 붙음 (%s ~ %s) · 그사이 현물 체결 알림은 빠졌을 수 있음"
                                 % (fmt.kst_text(since), fmt.kst_text(n)))

    async def handle(self, msgs, fills, recs):
        for m in msgs:
            await self.app.d.put(m.channel, m.text)
        store.append(recs)
        for f in fills:
            await self.fills.put(f)

    def tick(self, n):
        return self.t.tick(n)

    async def _fill_worker(self):
        """체결 글을 차례대로: 팔 때는 거래 기록으로 손익을 계산해 붙임."""
        while True:
            f = await self.fills.get()
            try:
                await asyncio.sleep(SETTLE_S)
                pnl = None
                if f.side == "SELL" and f.usd:
                    try:
                        pnl = await self.sell_pnl(f)
                    except Exception as e:
                        store.log("현물 손익 계산 실패: %s %s" % (type(e).__name__, str(e)[:160]))
                await self.app.d.put("trade", fill_text(f, self.t.balances.get(f.base), pnl))
                fee = None
                if f.usd:
                    try:
                        fee = await self.fee_usd(f)
                    except Exception as e:
                        store.log("현물 수수료 환산 실패: %s" % e)
                store.append([{"kind": "spot_fill", "t": f.t_ms, "oid": f.oid, "symbol": f.symbol, "side": f.side, "qty": str(f.qty),
                               "quote": str(f.quote_qty), "usd": f.usd, "pnl": None if pnl is None else str(pnl),
                               "fee_usd": None if fee is None else str(fee)}])         # 하루 요약이 이 기록으로 현물을 셈
                self.app._pos_cache = None                 # /포지션도 새 값으로
                self.app._pos_due = now_ms() + 3000        # #현재포지션 새 글
            except Exception as e:
                store.log("현물 체결 글 오류: %s %s" % (type(e).__name__, str(e)[:160]))

    async def fee_usd(self, f):
        """이 체결의 수수료를 달러로: 짝 화폐로 낸 것 + 산 코인으로 낸 것(체결 가격으로) + BNB 로 낸 것(지금 값으로)."""
        total = f.fee_quote
        for asset, amt in f.fees.items():
            if asset == f.base:
                total += amt * f.avg
            elif asset == "BNB":
                total += amt * (await self.price("BNB") or ZERO)
        return total

    # ------------------------------------------------------------------ 평균 매수가 · 손익
    def _book(self, symbol):
        return CostBook.load((self.app.state.get("spot_books") or {}).get(symbol))

    def _save_book(self, symbol, book):
        self.app.state.setdefault("spot_books", {})[symbol] = book.dump()
        store.save_state(self.app.state)

    async def _new_trades(self, symbol, from_id):
        out = []
        for _ in range(MAX_PAGES):
            rows = sorted(await self.app.b.spot_trades(symbol, from_id), key=lambda t: int(t.get("id", 0)))
            out += rows
            if len(rows) < 1000:
                break
            from_id = int(rows[-1]["id"]) + 1
        return out

    async def sell_pnl(self, f):
        """이 매도 주문의 거래 바로 전까지 장부를 이어 붙여 평균 매수가로 손익을 셈. 그 뒤 이 주문 거래도 장부에 넣음."""
        book = self._book(f.symbol)
        trades = await self._new_trades(f.symbol, book.last_id + 1)
        bnb = await self.price("BNB") if "BNB" in f.fees or any(t.get("commissionAsset") == "BNB" for t in trades) else None
        i = 0
        while i < len(trades) and trades[i].get("orderId") != f.oid:
            book.apply(trades[i], bnb)
            i += 1
        net = f.quote_qty - f.fee_quote - (f.fees.get("BNB", ZERO) * bnb if bnb else ZERO)
        pnl = book.pnl(f.qty, net)
        for t in trades[i:]:
            book.apply(t, bnb)
        self._save_book(f.symbol, book)
        return pnl

    async def avg_cost(self, asset, qty):
        """USDT 짝으로 산 평균 매수가. 산 값을 아는 수량이 지금 보유의 99% 에 못 미치면(입금 등) None."""
        symbol = asset + "USDT"
        book = self._book(symbol)
        trades = await self._new_trades(symbol, book.last_id + 1)
        if trades:
            bnb = await self.price("BNB") if any(t.get("commissionAsset") == "BNB" for t in trades) else None
            for t in trades:
                book.apply(t, bnb)
            self._save_book(symbol, book)
        if book.qty <= 0 or book.qty < qty * Decimal("0.99"):
            return None
        return book.avg()

    # ------------------------------------------------------------------ 보유 (#현재포지션 · /포지션)
    async def price(self, asset):
        """USDT 로 매긴 값. USDT 짝이 없으면 None."""
        if asset == "USDT":
            return Decimal(1)
        if asset in self.no_market:
            return None
        try:
            return dec(await self.app.b.spot_price(asset + "USDT")) or None
        except BinanceError as e:
            if e.status == 400:                            # 없는 짝 (예: Earn 의 LD 자산)
                self.no_market.add(asset)
                return None
            raise

    async def holdings(self, with_avg=True):
        """돌려줌 (견줄 것: USDT 를 뺀 $1 이상 전부, 보일 것: 그중 $10 이상 · 큰 것부터). with_avg 면 보일 것에 평균 매수가를 붙임."""
        rows = []
        for asset, qty in sorted((await self.app.b.spot_balances()).items()):
            if asset == "USDT":
                continue
            p = await self.price(asset)
            if not p or qty * p < DUST_USD:
                continue
            rows.append({"asset": asset, "qty": qty, "price": p, "value": qty * p})
        rows.sort(key=lambda r: r["value"], reverse=True)
        shown = [dict(r) for r in rows if r["value"] >= SHOW_USD]
        if with_avg:
            await self.add_avg(shown)
        return rows, shown

    async def add_avg(self, shown):
        for r in shown:
            try:
                r["avg"] = await self.avg_cost(r["asset"], r["qty"])
            except Exception as e:
                store.log("현물 평균 매수가 실패 %s: %s" % (r["asset"], e))
                r["avg"] = None
