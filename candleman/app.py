"""프로그램 본체: 바이낸스 실시간 연결 → 장부 → 디스코드. 끊기면 다시 붙고, 그사이 바뀐 포지션은 조회로 채웁니다."""
import asyncio
import json
import os
import signal
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from . import briefing, cards, fmt, names, notices, store, summary, updater
from .binance import WS_PRIVATE, Binance, BinanceError
from .discord import Discord
from .fmt import ZERO, dec
from .spotfeed import SpotFeed
from .tracker import USD_ASSETS, Tracker, base_of
from .ws import WebSocket, WSClosed

KEEPALIVE_S = 30 * 60          # listenKey 연장 (60분 만료)
READ_TIMEOUT_S = 10 * 60       # 이만큼 아무 소식이 없으면 다시 붙음 (서버 ping 이 3분마다 옴)
REFRESH_S = 23 * 3600          # 24시간 끊김 전에 스스로 새로 붙음
GAP_NOTE_S = 20                # 이보다 길게 끊겼으면 그사이 바뀐 것을 조회
LATE_SUMMARY_S = 3 * 3600      # 요약 시각을 놓쳤으면 이만큼 안에서는 늦게라도 보냄
START_RETRY_S = 10             # 켤 때 바이낸스에 못 붙으면 이만큼 뒤 다시 (두 배씩 늘려 최대 5분)
OUTAGE_NOTE_S = 2 * 60         # 이보다 오래 바이낸스에 못 붙으면 #봇-상태에 한 번 알림
NET_ERRORS = (OSError, asyncio.TimeoutError, BinanceError, ValueError)   # 인터넷 · 거래소 쪽 문제 (코드 오류는 아님)
POS_POST_DELAY_S = 3           # 체결 뒤 이만큼 조용하면 #현재포지션에 새로 올림 (나눠서 체결돼도 한 번)
POS_CHECK_S = 10 * 60          # 체결 소식을 놓쳤을 때를 대비해 이만큼마다 포지션을 한 번 견줌
SPOT_MOVE_RATE = Decimal("0.01")   # 체결 소식 없이 현물 코인 수량이 이 비율도 넘고
SPOT_MOVE_USD = Decimal("10")      # 이만큼($) 어치도 넘게 바뀌면 #현재포지션 새 글 (Earn 이자 · 찌꺼기는 아님)
UPDATE_TRIES = 3               # 자동 업데이트: 시험에 떨어진 같은 판은 이만큼(하루 한 번)까지 다시 시험


def now_ms():
    return int(time.time() * 1000)


class App:
    def __init__(self, cfg):
        self.b = Binance(cfg["key"], cfg["secret"])
        self.d = Discord(cfg["hooks"], log=store.log)
        self.t = Tracker()
        self.hour = cfg["summary_hour"]
        self.cfg = cfg
        self.state = store.load_state()
        self.down_since = None
        self.ws = None
        self._pos_cache = None
        self._outage_told = False
        self._pos_due = None                 # 이 시각(ms)이 지나면 #현재포지션을 견줘 봄
        self._pos_why = None                 # 그 까닭: "fill" 체결 뒤 · "check" 다시 켬 · 끊김 · 10분 점검 (_post_positions)
        self._pos_posting = False
        self.start_usd = cfg.get("start_usd")   # 주인장 수익률의 시작 금액 (None 이면 수익률 글 · /주인장수익률 없음)
        self.start_year = cfg.get("start_year") or 2026   # 시작 금액이 기준인 해. 다음 해부터는 1월 1일 0시의 전체 자산
        self._year_next = 0                  # 새해 기준을 못 잡았을 때 다시 해 볼 시각(ms)
        self._ret_due = None                 # 이 시각(ms)이 지나면 #실시간-매매에 주인장 수익률 (포지션을 끝낸 뒤)
        self._ret_posting = False
        self._ret_cache = None
        self.spot = SpotFeed(self) if cfg.get("spot", True) else None   # 현물 체결 알림 · 보유 (SPOT=0 이면 끔)

    # ------------------------------------------------------------------ 시작
    async def run(self):
        try:                                              # stop.sh · 업데이트가 끄라고 하면 포지션을 저장하고 끝남
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, lambda: asyncio.ensure_future(self._shutdown()))
        except (NotImplementedError, AttributeError, RuntimeError, ValueError):
            pass                                          # 윈도에서 시험할 때
        asyncio.create_task(self.d.worker())
        await self._start_until_ok()
        if self.spot:
            asyncio.create_task(self.spot.run_forever())
        asyncio.create_task(self.ticker())
        asyncio.create_task(self.scheduler())
        if self.cfg.get("bot_token"):                 # /포지션 명령 봇 (토큰을 넣었을 때만)
            from .bot import Bot
            asyncio.create_task(Bot(self.cfg["bot_token"], self, self.cfg.get("command_channel") or "잡담방").run_forever())
        await self.stream_forever()

    async def bootstrap(self):
        pos = await self.b.positions()
        orders = await self.b.open_orders()
        algos = await self.b.open_algo_orders()
        prev = self.state.get("positions") or {}
        last_alive = int(self.state.get("last_alive") or 0)
        self.t.load_positions(pos, now_ms())
        self.t.load_orders(orders, algos)
        n = now_ms()
        line = "🤖 시작함\n포지션 %d개 · 미체결 주문 %d개 · 조건부 주문 %d개" % (len(pos), len(orders), len(algos))
        if last_alive:                                    # 다시 켜는 몇 초 사이의 체결도 놓치지 않게 늘 견줌
            changes = self._diff_positions(prev, pos)
            if n - last_alive > GAP_NOTE_S * 1000:
                self._add_gap(last_alive, n)
                line += "\n꺼져 있던 시간 %s ~ %s" % (fmt.kst_text(last_alive), fmt.kst_text(n))
            if changes:
                await self._post_gap_changes(last_alive, n, changes)
        await self.d.put("status", line)
        await self._report_update()
        await self._announce(n)
        if "last_summary_end" not in self.state:          # 처음 켠 날은 지난 구간 요약을 건너뜀
            self.state["last_summary_end"] = summary.window_for(n, self.hour)[1]
        self._snapshot()
        self._pos_due, self._pos_why = n, "check"         # 꺼져 있던 사이 바뀌었으면 #현재포지션도 새로
        try:                                              # 시작을 마쳤다는 표시 (termux/restart.sh 가 봄)
            with open(os.path.join(store.DATA_DIR, "started"), "w", encoding="utf-8") as f:
                f.write(str(n))
        except OSError:
            pass

    async def _start_until_ok(self):
        """켤 때 바이낸스에 못 붙어도 꺼지지 않음: 10초 → 20초 → … 최대 5분 간격으로 다시 시도하고, OUTAGE_NOTE_S 넘게
        안 되면 #봇-상태에 한 번 알림. 인터넷 문제가 아닌 오류(코드 오류)는 그대로 멈춤 → 업데이트 직후라면 원래 판으로 되돌아감."""
        since, wait, told = now_ms(), START_RETRY_S, False
        while True:
            try:
                await self.b.sync_time()
            except Exception as e:
                store.log("시간 맞추기 실패(계속함): %s" % e)
            try:
                await self.bootstrap()
                return
            except NET_ERRORS as e:
                why = "%s %s" % (type(e).__name__, str(e)[:120])
                store.log("시작 실패 — %d초 뒤 다시: %s" % (wait, why))
            self.t = Tracker()                            # 반쯤 채운 장부는 버리고 다음에 처음부터
            if not told and now_ms() - since >= OUTAGE_NOTE_S * 1000:
                told = True
                await self.d.put("status", "⚠️ 켜졌지만 바이낸스에 연결이 안 됨 (%s부터)\n폰의 인터넷(와이파이)을 확인해 주세요. "
                                           "연결되면 바로 시작함\n이유: %s" % (fmt.kst_text(since), why))
            await asyncio.sleep(wait)
            wait = min(wait * 2, 300)

    async def _tell_outage(self, n):
        """돌다가 바이낸스 연결이 끊긴 채 OUTAGE_NOTE_S 넘게 못 붙으면 #봇-상태에 한 번 알림 (다시 붙으면 '🔌 … 다시 붙음')."""
        if self.down_since and not getattr(self, "_outage_told", False) and n - self.down_since >= OUTAGE_NOTE_S * 1000:
            self._outage_told = True
            await self.d.put("status", "⚠️ 바이낸스 연결이 끊긴 뒤 다시 붙지 못하고 있음 (%s부터)\n폰의 인터넷(와이파이)을 확인해 주세요. "
                                       "다시 붙으면 그사이 바뀐 것을 알림" % fmt.kst_text(self.down_since))

    # ------------------------------------------------------------------ 실시간
    async def stream_forever(self):
        backoff = 1
        while True:
            keep = None
            try:
                key = await self.b.new_listen_key()
                self.ws = await WebSocket.connect(WS_PRIVATE + key)
                opened = time.time()
                if self.down_since:
                    await self._after_gap(self.down_since)
                    self.down_since = None
                self._outage_told = False
                backoff = 1
                keep = asyncio.create_task(self.keepalive())
                while True:
                    raw = await self.ws.recv_alive(READ_TIMEOUT_S)    # 조용해도 서버 ping(3분마다)이 오면 기다림
                    if isinstance(raw, bytes):
                        continue
                    ev = json.loads(raw)
                    if ev.get("e") == "listenKeyExpired":
                        store.log("listenKey 만료 — 다시 붙음")
                        break
                    msgs, recs = self.t.on_event(ev, now_ms())
                    await self.emit(msgs, recs)
                    self.state["last_alive"] = now_ms()
                    if time.time() - opened > REFRESH_S:
                        break
            except (WSClosed, asyncio.TimeoutError, ConnectionError, OSError, BinanceError, ValueError) as e:
                store.log("연결 끊김: %s %s" % (type(e).__name__, str(e)[:160]))
            except Exception as e:                       # 모르는 오류도 죽지 않고 다시 붙음
                store.log("예상 못 한 오류: %s %s" % (type(e).__name__, str(e)[:160]))
            finally:
                if keep:
                    keep.cancel()
                if self.ws:
                    await self.ws.close()
                    self.ws = None
                if self.down_since is None:
                    self.down_since = now_ms()
            await self._tell_outage(now_ms())
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def keepalive(self):
        while True:
            await asyncio.sleep(KEEPALIVE_S)
            try:
                await self.b.keepalive_listen_key()
            except BinanceError as e:
                store.log("listenKey 연장 실패 — 다시 붙음: %s" % e)
                if self.ws:
                    await self.ws.close()
                return

    async def _after_gap(self, since):
        n = now_ms()
        if not getattr(self, "_pos_due", None):           # 끊긴 사이 바뀌었으면 #현재포지션도 새로 (같으면 안 올림)
            self._pos_due, self._pos_why = n, "check"
        if n - since < GAP_NOTE_S * 1000:
            return
        self._add_gap(since, n)
        pos = await self.b.positions()
        prev = {"%s|%s" % k: [str(p.amt), str(p.entry)] for k, p in self.t.positions.items() if p.amt != 0}
        changes = self._diff_positions(prev, pos)
        if changes:
            await self._post_gap_changes(since, n, changes)
        # 장부를 지금 값으로 다시 채움
        self.t.positions.clear()
        self.t.orders.clear()
        self.t.load_positions(pos, n)
        self.t.load_orders(await self.b.open_orders(), await self.b.open_algo_orders())
        await self.d.put("status", "🔌 연결이 끊겼다가 다시 붙음 (%s ~ %s)" % (fmt.kst_text(since), fmt.kst_text(n)))

    def _diff_positions(self, prev, rows):
        cur = {}
        for r in rows:
            cur["%s|%s" % (r["symbol"], r.get("positionSide") or "BOTH")] = [r.get("positionAmt"), r.get("entryPrice")]
        out = []
        for k in sorted(set(prev) | set(cur)):
            a = dec((prev.get(k) or ["0"])[0])
            b = dec((cur.get(k) or ["0"])[0])
            if a != b:
                out.append((k.split("|")[0], a, b))
        return out

    async def _post_gap_changes(self, since, until, changes):
        lines = ["⏸ 프로그램 연결이 잠깐 끊긴 사이(%s ~ %s) 바뀐 포지션" % (fmt.kst_text(since), fmt.kst_text(until))]
        for sym, a, b in changes:
            base = base_of(sym)

            def word(v):
                if v == 0:
                    return "없음"
                return "%s %s" % ("롱" if v > 0 else "숏", names.amount(v, base))
            lines.append("· %s %s → %s" % (names.label(base), word(a), word(b)))
        try:
            rows = await self.b.income("REALIZED_PNL", since, until)
            total = sum((dec(r.get("income")) for r in rows), dec(0))
            if rows:
                lines.append("그사이 확정된 %s" % fmt.pnl(total))
        except Exception as e:
            store.log("끊긴 사이 손익 조회 실패: %s" % e)
        await self.d.put("trade", "\n".join(lines))

    # ------------------------------------------------------------------ 시계
    async def ticker(self):
        i = 0
        while True:
            await asyncio.sleep(1)
            try:
                msgs, recs = self.t.tick(now_ms())
                await self.emit(msgs, recs)
                if self.spot:
                    await self.spot.handle(*self.spot.tick(now_ms()))
            except Exception as e:
                store.log("tick 오류: %s" % e)
            i += 1
            if i % 60 == 0:
                self._snapshot()
            if i % POS_CHECK_S == 0 and not self._pos_due:
                self._pos_due, self._pos_why = now_ms(), "check"
            if self._pos_due and now_ms() >= self._pos_due and not self._pos_posting:
                why, self._pos_due, self._pos_why = self._pos_why or "fill", None, None
                asyncio.create_task(self._post_positions(why))
            if self._ret_due and now_ms() >= self._ret_due and not self._ret_posting:
                self._ret_due = None
                asyncio.create_task(self._post_return())

    async def scheduler(self):
        while True:
            await asyncio.sleep(20)
            n = now_ms()
            try:
                if self._update_due(n):
                    self.state["update_checked"] = fmt.kst(n).strftime("%Y-%m-%d")
                    store.save_state(self.state)
                    await self._auto_update()
            except Exception as e:
                store.log("업데이트 오류: %s" % e)
            try:
                await self._maybe_briefing(n)
            except Exception as e:
                store.log("브리핑 오류: %s %s" % (type(e).__name__, str(e)[:160]))
            try:
                await self._maybe_new_year(n)
            except Exception as e:
                store.log("새해 기준 오류: %s %s" % (type(e).__name__, str(e)[:160]))
            try:
                start, end = summary.window_for(n, self.hour)
                if int(self.state.get("last_summary_end") or 0) < end and n - end < LATE_SUMMARY_S * 1000:
                    await self.post_summary(start, end)
                    self.state["last_summary_end"] = end
                    self._snapshot()
                elif int(self.state.get("last_summary_end") or 0) < end:
                    self.state["last_summary_end"] = end          # 너무 늦었으면 건너뜀
            except Exception as e:
                store.log("요약 오류: %s" % e)

    async def post_summary(self, start, end):
        journal = store.read_journal(start, end)
        fills = [r for r in journal if r.get("kind") == "fill"]
        spot_fills = [r for r in journal if r.get("kind") == "spot_fill"]
        spot_rows = []
        if getattr(self, "spot", None):
            try:
                _, spot_rows = await self.spot.holdings()
            except Exception as e:                            # 현물 보유를 못 받아도 요약은 보냄
                store.log("요약: 현물 보유 조회 실패: %s" % e)
        realized = await self.b.income("REALIZED_PNL", start, end - 1)
        fees = await self.b.income("COMMISSION", start, end - 1)
        funding = await self.b.income("FUNDING_FEE", start, end - 1)
        positions = await self.b.positions()
        bnb = await self.b.price("BNBUSDT") if any((r.get("asset") == "BNB") for r in fees + funding) else None
        gaps = [(a, b) for a, b in self.state.get("gaps", []) if b > start and a < end]
        msg = summary.build_message(start, end, fills, realized, fees, funding, positions, bnb, gaps, spot_fills=spot_fills, spot_rows=spot_rows)
        await self.d.put("summary", msg["content"], msg["embeds"])

    async def positions_message(self):
        """/포지션 답 글(머리 한 줄 + 종목 카드). 바이낸스에는 30초에 한 번까지만 물음(연달아 불러도 같은 답)."""
        now = time.time()
        cache = getattr(self, "_pos_cache", None)
        if not cache or now - cache[0] > 30:
            positions = await self.b.positions()
            try:
                equity = (await self.b.account()).get("totalMarginBalance")
            except Exception as e:                      # 증거금을 못 받아도 포지션은 보여 줌 (레버리지 줄만 빠짐)
                store.log("증거금 조회 실패: %s" % e)
                equity = None
            shown = []
            if getattr(self, "spot", None):
                try:
                    _, shown = await self.spot.holdings()
                except Exception as e:                  # 현물을 못 받아도 선물 포지션은 보여 줌
                    store.log("현물 보유 조회 실패: %s" % e)
            cache = self._pos_cache = (now, positions, equity, shown)
        return summary.positions_message(cache[1], cache[2], fmt.kst_text(int(cache[0] * 1000)), cache[3] if len(cache) > 3 else None)

    # ------------------------------------------------------------------ 안쪽
    async def emit(self, msgs, recs):
        for m in msgs:
            await self.d.put(m.channel, m.text)
        store.append(recs)
        for r in recs:
            if r.get("kind") == "fill":                       # 포지션이 바뀜 → /포지션은 새 값으로, #현재포지션은 잠시 뒤 새 글
                self._pos_cache = self._ret_cache = None
                self._pos_due, self._pos_why = now_ms() + POS_POST_DELAY_S * 1000, "fill"
            if r.get("kind") == "fill" and r.get("effect") in ("close", "flip") and getattr(self, "start_usd", None):
                self._ret_due = now_ms() + POS_POST_DELAY_S * 1000   # 포지션을 끝냄 → 잠시 뒤 주인장 수익률
            if r.get("kind") == "fill" and r.get("effect") in ("open", "flip"):
                await self._introduce(r.get("symbol", ""), r.get("t"))

    async def _post_positions(self, why="fill"):
        """#현재포지션: 지난 글과 달라졌으면 지금 포지션 카드(/포지션과 같은 모양)를 새 글로 (10/22 댕이: "변화가 생길 때만").
        why — "fill" 체결 뒤: 종목 · 방향 · 수량이 조금이라도 다르면 · "check" 다시 켬 · 연결이 다시 붙음 · 10분마다(놓친 체결 ·
        입금 · 출금): 선물 포지션이 다르거나 현물 코인이 눈에 띄게 바뀌었을 때만(_spot_moved). 지갑끼리 옮긴 것(지갑은 합쳐 셈) ·
        Earn 이자 · 찌꺼기로는 올리지 않음 — 10/22 06:30 에 솔라나가 현물 지갑에서 빠져 거래 없이 올라간 글 뒤에 바꿈."""
        if not self.d.has("positions") or getattr(self, "_pos_posting", False):
            return
        self._pos_posting = True
        try:
            positions = await self.b.positions()
            spot_all, shown = await self.spot.holdings(with_avg=False) if getattr(self, "spot", None) else ([], [])
            futures = sorted("%s|%s|%s" % (r.get("symbol"), r.get("positionSide") or "BOTH", fmt.plain(r.get("positionAmt")))
                             for r in positions)
            sig = sorted(futures + ["SPOT|%s|%s" % (r["asset"], fmt.plain(r["qty"])) for r in spot_all])
            last = self.state.get("positions_posted")
            if sig == last:
                return
            if (why == "check" and last is not None and futures == sorted(s for s in last if not s.startswith("SPOT|"))
                    and not await self._spot_moved(last, spot_all)):
                return
            if shown:
                await self.spot.add_avg(shown)
            try:
                equity = (await self.b.account()).get("totalMarginBalance")
            except Exception as e:
                store.log("증거금 조회 실패: %s" % e)
                equity = None
            now = time.time()
            self._pos_cache = (now, positions, equity, shown)  # /포지션도 같은 값을 씀
            msg = summary.positions_message(positions, equity, fmt.kst_text(int(now * 1000)), shown)
            await self.d.put("positions", msg["content"], msg["embeds"])
            self.state["positions_posted"] = sig
            store.save_state(self.state)
        except NET_ERRORS as e:
            store.log("현재 포지션 조회 실패 — 1분 뒤 다시: %s" % e)
            if not getattr(self, "_pos_due", None):
                self._pos_due, self._pos_why = now_ms() + 60_000, why
        finally:
            self._pos_posting = False

    async def _spot_moved(self, last, rows):
        """체결 소식 없이 현물 코인이 눈에 띄게 바뀌었나 — 코인마다 지난 글의 수량과 견줘 SPOT_MOVE_RATE 도 넘고 SPOT_MOVE_USD 어치도
        넘게 다르면 (놓친 체결 · 입금 · 출금 · 에어드랍). Earn 이자처럼 조금씩 쌓이는 것은 그만큼 쌓였을 때 한 번."""
        if not getattr(self, "spot", None):
            return False
        before = {}
        for s in last:
            if s.startswith("SPOT|"):
                _, asset, q = s.split("|", 2)
                before[asset] = dec(q)
        now = {r["asset"]: r for r in rows}
        for asset in sorted(set(before) | set(now)):
            q0, q1 = before.get(asset, ZERO), now[asset]["qty"] if asset in now else ZERO
            diff = abs(q1 - q0)
            if diff <= max(q0, q1) * SPOT_MOVE_RATE:
                continue
            price = now[asset]["price"] if asset in now else await self.spot.price(asset)
            if price and diff * price >= SPOT_MOVE_USD:
                return True
        return False

    # ------------------------------------------------------------------ 주인장 수익률
    async def equity_usd(self):
        """지금 자산(달러) = 선물 지갑 + 현물 지갑 + Earn(자유 · 기간 고정) + 펀딩 지갑. 코인은 USDT 짝 시세로 매기고, 값을 못 매기는 것은 뺌.
        선물 지갑은 자산마다 증거금 잔고(지갑 잔고 + 미실현 손익) — 수수료용 BNB 같은 코인도 그 값으로(코인별 잔고가 없으면 증거금 합계).
        현물 쪽은 #현재포지션과 같은 방법(SpotFeed.wallets)으로 읽어 Earn 으로 옮긴 코인도 셈 (10/22: 현물 지갑만 읽으면 빠짐)."""
        account = await self.b.account()
        if account.get("assets"):
            total = ZERO
            for a in account["assets"]:
                q = dec(a.get("marginBalance"))
                if q:
                    total += q * (Decimal(1) if a.get("asset") in USD_ASSETS else await self._usd_price(a.get("asset")) or ZERO)
        else:
            total = dec(account.get("totalMarginBalance"))
        if getattr(self, "spot", None):
            w = await self.spot.wallets()
            for name in ("spot", "earn", "funding"):
                total += await self._wallet_usd(w[name])
            return total
        total += await self._wallet_usd(await self.b.spot_balances())
        try:
            total += await self._wallet_usd(await self.b.funding_balances())
        except NET_ERRORS as e:
            store.log("펀딩 지갑 조회 실패(자산에서 뺌): %s" % e)
        return total

    async def _wallet_usd(self, balances):
        total = ZERO
        for asset, qty in balances.items():
            p = await self._usd_price(asset)
            if p is None and asset.startswith("LD") and len(asset) > 2:
                p = await self._usd_price(asset[2:])
            if p:
                total += qty * p
        return total

    async def _usd_price(self, asset):
        """USDT 로 매긴 값. USDT 짝이 없으면 None (한 번 없던 것은 다시 묻지 않음)."""
        if asset == "USDT":
            return Decimal(1)
        no_market = getattr(self, "_no_market", None)
        if no_market is None:
            no_market = self._no_market = set()
        if asset in no_market:
            return None
        try:
            return dec(await self.b.spot_price(asset + "USDT")) or None
        except BinanceError as e:
            if e.status == 400:
                no_market.add(asset)
                return None
            raise

    def _year_base(self, year):
        """그해 수익률 기준 (금액, 잡은 때 ms 또는 None). START_YEAR 까지는 START_USD, 그 뒤 해는 1월 1일에 잡아 둔 전체 자산
        (10/21 댕이: "2027년 1월 1일 바이낸스에 있는 모든 자산을 읽어서 새 기준으로"). 아직 안 잡았으면 None."""
        if year <= getattr(self, "start_year", 2026):
            return self.start_usd, None
        rec = (self.state.get("year_start") or {}).get(str(year))
        return (dec(rec["usd"]), int(rec["t"])) if rec else None

    async def _take_year_start(self, year, n):
        """지금 바이낸스 전체 자산을 그해 기준으로 저장하고 #봇-상태에 남김 (1월 1일 0시에 꺼져 있었으면 다시 켜진 때 값)."""
        usd = await self.equity_usd()
        self.state.setdefault("year_start", {})[str(year)] = {"usd": fmt.plain(usd), "t": n}
        store.save_state(self.state)
        t = fmt.kst(n)
        late = "" if (t.month, t.day) == (1, 1) else " · 1월 1일에 꺼져 있어서 지금 값으로 잡음"
        await self.d.put("status", "📅 %d 주인장 수익률 새 기준: %s (%s 바이낸스 전체 자산)%s" % (year, fmt.usd(usd), fmt.kst_text(n), late))
        return usd, n

    async def _maybe_new_year(self, n):
        """해가 바뀌면(한국 시간 1월 1일 0시) 그해 기준을 한 번 잡음. 못 잡으면 1분 뒤 다시."""
        if not getattr(self, "start_usd", None) or n < getattr(self, "_year_next", 0):
            return
        year = fmt.kst(n).year
        if self._year_base(year) is not None:
            return
        try:
            await self._take_year_start(year, n)
        except NET_ERRORS as e:
            store.log("새해 기준 잡기 실패 — 1분 뒤 다시: %s" % e)
            self._year_next = n + 60_000

    async def return_message(self):
        """/주인장수익률 답 = 포지션을 끝낼 때 #실시간-매매에 붙는 글. 바이낸스에는 30초에 한 번까지만 물음."""
        now = time.time()
        year = fmt.kst(int(now * 1000)).year
        base = self._year_base(year)
        if base is None:                                  # 새해 기준을 아직 못 잡았으면 지금 잡음
            base = await self._take_year_start(year, int(now * 1000))
        cache = getattr(self, "_ret_cache", None)
        if not cache or now - cache[0] > 30:
            cache = self._ret_cache = (now, await self.equity_usd())
        start, t = base
        note = "%s 전체 자산" % fmt.kst_text(t) if t else None
        head, embeds = cards.card("trade", summary.return_text(year, start, cache[1], fmt.kst_text(int(cache[0] * 1000)), note))
        return {"content": head, "embeds": embeds or []}                 # #실시간-매매 글처럼 머리 한 줄 + 카드

    async def _post_return(self):
        """선물 포지션을 끝내거나(정리 · 방향 바꿈) 현물을 모두 판 뒤 #실시간-매매에 주인장 수익률을 한 번."""
        if getattr(self, "_ret_posting", False) or not getattr(self, "start_usd", None):
            return
        self._ret_posting = True
        try:
            self._ret_cache = None                        # 정리한 뒤의 값으로
            msg = await self.return_message()
            await self.d.put("trade", msg["content"], msg["embeds"])
        except NET_ERRORS as e:
            store.log("수익률 조회 실패 — 1분 뒤 다시: %s" % e)
            self._ret_due = now_ms() + 60_000
        finally:
            self._ret_posting = False

    # ------------------------------------------------------------------ 아침 경제 브리핑
    def _fetch_brief(self):
        """클라우드 예약 작업이 GitHub 에 올린 briefing.json (글자 그대로). 못 받으면 None."""
        url = (getattr(self, "cfg", None) or {}).get("briefing_url") or briefing.BRIEF_URL
        try:
            req = urllib.request.Request("%s?t=%d" % (url, int(time.time())), headers={"User-Agent": "candleman-alert"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8")
        except Exception as e:
            store.log("브리핑 글 받기 실패: %s" % e)
            return None

    async def post_briefing(self, today, wait_ok=True):
        """오늘 브리핑을 올림. wait_ok 이고 클라우드 글이 아직 없으면 False (조금 뒤 다시). 올렸으면 True."""
        text = await asyncio.to_thread(self._fetch_brief)
        brief = briefing.parse_brief(text, today) if text else None
        if brief is None and wait_ok:
            return False
        try:
            rows = await asyncio.to_thread(briefing.kofia_rows, today - timedelta(days=14), today)
        except Exception as e:                            # 숫자를 못 받아도 소식 · 일정은 올림
            store.log("협회 자료 받기 실패: %s" % e)
            rows = []
        msg = briefing.compose(today, rows, brief)
        await self.d.put("briefing", msg["content"], msg["embeds"])
        if brief is None:
            await self.d.put("status", "⚠️ 오늘 브리핑의 밤사이 소식 · 일정을 받지 못해 숫자만 올림 (Claude 클라우드 예약 작업 확인 필요)")
        return True

    async def _maybe_briefing(self, n):
        """매일 정한 시각(기본 08시)에 #일일-경제-브리핑. 클라우드 글이 아직 없으면 1분마다 다시 보다가 WAIT_MIN 분이 지나면 숫자만이라도."""
        if not self.d.has("briefing"):
            return
        t = fmt.kst(n)
        hour = (getattr(self, "cfg", None) or {}).get("briefing_hour", 8)
        if self.state.get("briefing_posted") == t.date().isoformat() or not hour <= t.hour < briefing.LAST_HOUR:
            return
        if n < getattr(self, "_brief_next", 0):
            return
        self._brief_next = n + 60_000
        late = (t.hour - hour) * 60 + t.minute >= briefing.WAIT_MIN
        if await self.post_briefing(t.date(), wait_ok=not late):
            self.state["briefing_posted"] = t.date().isoformat()
            store.save_state(self.state)

    # ------------------------------------------------------------------ 업데이트 · 끄기
    def _update_due(self, n):
        """자동 업데이트를 볼 때인지: 켜 둔 경우 · 정해 둔 시각(기본 05시)에 · 그날 아직 안 봤으면."""
        cfg = getattr(self, "cfg", None) or {}
        if not cfg.get("auto_update", True):
            return False
        t = fmt.kst(n)
        return t.hour == cfg.get("update_hour", 5) and self.state.get("update_checked") != t.strftime("%Y-%m-%d")

    async def _auto_update(self):
        """stable 에 새 판이 있으면 따로 시험해 보고, 통과하면 바꿔서 다시 켬. 결과는 #봇-상태에만."""
        status, old, new, detail = await asyncio.to_thread(updater.check)
        if status == "same":
            store.log("업데이트 확인: 새 판 없음")
            return
        if status == "error":
            store.log("업데이트 확인 실패: %s" % detail)
            return
        if self.state.get("update_skip") == new or (self.state.get("update_fails") or {}).get(new, 0) >= UPDATE_TRIES:
            return                                        # 이미 안 받기로 한 판 · 시험에 여러 번 떨어진 판
        if status == "diverged":
            self.state["update_skip"] = new
            store.save_state(self.state)
            await self.d.put("status", "⚠️ 자동 업데이트 안 함: GitHub 의 새 판(%s)이 지금 판(%s)에서 이어지지 않음. 직접 확인 필요."
                             % (new[:7], old[:7]))
            return
        ok, tail = await asyncio.to_thread(updater.test, new)
        if not ok:                                        # 폰에서 잠깐 떨어진 것일 수도 있어 사흘까지는 다음 날 다시 시험
            fails = {new: (self.state.get("update_fails") or {}).get(new, 0) + 1}
            self.state["update_fails"] = fails
            store.save_state(self.state)
            later = " · 내일 다시 시험함" if fails[new] < UPDATE_TRIES else " · 이 판은 더 시도하지 않음"
            await self.d.put("status", "⚠️ 자동 업데이트 안 함: 새 판(%s) 시험 실패 · 지금 판 그대로 (%s)%s" % (new[:7], tail, later))
            return
        ok, err = await asyncio.to_thread(updater.switch, new)
        if not ok:
            await self.d.put("status", "⚠️ 자동 업데이트 안 함: 새 판으로 바꾸지 못함 (%s)" % err)
            return
        updater.mark_done(old, new)
        store.log("새 판으로 바꿈: %s → %s · 다시 켬" % (old[:7], new[:7]))
        self._snapshot()
        try:
            await asyncio.wait_for(self.d.q.join(), 10)   # 보내던 글을 마저 보낸 뒤
        except asyncio.TimeoutError:
            pass
        updater.restart_detached(old)                     # 이 프로그램은 스크립트가 끄고 새 판을 켬

    async def _report_update(self):
        """업데이트 뒤 처음 켜졌을 때 결과를 #봇-상태에 알림 (바꿈 · 되돌림)."""
        done, failed = updater.take_notes()
        if failed:
            self.state["update_skip"] = failed            # 같은 판을 다음 날 또 시도하지 않게
            store.save_state(self.state)
            await self.d.put("status", "⚠️ 자동 업데이트: 새 판(%s)이 켜지지 않아 원래 판으로 되돌림 · 지금 판 그대로 돌고 있음" % failed[:7])
        elif done:
            old, new = str(done.get("from") or "?"), str(done.get("to") or "?")
            subjects = await asyncio.to_thread(updater.log_between, old, new)
            await self.d.put("status", "\n".join(["🔄 새 판으로 바꿈 · %s → %s" % (old[:7], new[:7])] + ["· " + s for s in subjects]))

    async def _shutdown(self):
        """끄라는 신호(stop.sh · 업데이트)를 받으면: 지금 포지션을 저장하고, 보내던 글을 마저 보낸 뒤(최대 5초) 끝냄."""
        try:
            self._snapshot()
            await asyncio.wait_for(self.d.q.join(), 5)
        except Exception:
            pass
        store.log("멈춤 (끄라는 신호)")
        os._exit(0)

    async def _announce(self, n):
        """새 판을 처음 켰을 때 안내 글(notices.py)을 정해 둔 채널에 한 번만 올림."""
        sent = self.state.setdefault("notices_sent", [])
        for a in notices.due(sent, fmt.kst(n).strftime("%Y-%m-%d")):
            for ch in self.d.distinct(a["channels"]):
                await self.d.put(ch, "", [dict(a["embed"])])
            sent.append(a["id"])
            store.save_state(self.state)
            store.log("안내 글 올림: %s" % a["id"])

    async def _introduce(self, symbol, t_ms=None):
        """처음 진입한 종목이면 #종목설명에 소개 카드를 올림 (DISCORD_WEBHOOK_SYMBOLS 가 있을 때만, 종목마다 한 번)."""
        base = base_of(symbol)
        done = self.state.setdefault("introduced", list(names.ALREADY_INTRODUCED))
        if not base or base in done or not self.d.has("symbols"):
            return
        done.append(base)
        store.save_state(self.state)
        known = base in names.DESCRIPTIONS
        card = names.intro_card(base, None if known else await self.b.symbol_kind(symbol))
        card["timestamp"] = datetime.fromtimestamp((t_ms or now_ms()) / 1000, timezone.utc).isoformat()
        await self.d.put("symbols", "", [card])
        if not known:
            await self.d.put("status", "📝 설명이 없는 새 종목 %s — #종목설명에 기본 문구로 올림. 설명 추가 필요." % base)

    def _add_gap(self, a, b):
        gaps = [g for g in self.state.get("gaps", []) if g[1] > now_ms() - 8 * 86400 * 1000]
        gaps.append([a, b])
        self.state["gaps"] = gaps

    def _snapshot(self):
        self.state["last_alive"] = now_ms()
        self.state["positions"] = {"%s|%s" % k: [str(p.amt), str(p.entry)] for k, p in self.t.positions.items() if p.amt != 0}
        store.save_state(self.state)
