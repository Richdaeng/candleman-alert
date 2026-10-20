"""프로그램 본체: 바이낸스 실시간 연결 → 장부 → 디스코드. 끊기면 다시 붙고, 그사이 바뀐 포지션은 조회로 채웁니다."""
import asyncio
import json
import time

from . import fmt, names, store, summary
from .binance import WS_PRIVATE, Binance, BinanceError
from .discord import Discord
from .fmt import dec
from .tracker import Tracker, base_of
from .ws import WebSocket, WSClosed

KEEPALIVE_S = 30 * 60          # listenKey 연장 (60분 만료)
READ_TIMEOUT_S = 10 * 60       # 이만큼 아무 소식이 없으면 다시 붙음 (서버 ping 이 3분마다 옴)
REFRESH_S = 23 * 3600          # 24시간 끊김 전에 스스로 새로 붙음
GAP_NOTE_S = 20                # 이보다 길게 끊겼으면 그사이 바뀐 것을 조회
LATE_SUMMARY_S = 3 * 3600      # 요약 시각을 놓쳤으면 이만큼 안에서는 늦게라도 보냄


def now_ms():
    return int(time.time() * 1000)


class App:
    def __init__(self, cfg):
        self.b = Binance(cfg["key"], cfg["secret"])
        self.d = Discord(cfg["hooks"], log=store.log)
        self.t = Tracker()
        self.hour = cfg["summary_hour"]
        self.state = store.load_state()
        self.down_since = None
        self.ws = None

    # ------------------------------------------------------------------ 시작
    async def run(self):
        asyncio.create_task(self.d.worker())
        try:
            await self.b.sync_time()
        except Exception as e:
            store.log("시간 맞추기 실패(계속함): %s" % e)
        await self.bootstrap()
        asyncio.create_task(self.ticker())
        asyncio.create_task(self.scheduler())
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
        line = "🤖 시작함 · 포지션 %d개 · 미체결 주문 %d개 · 조건부 주문 %d개" % (len(pos), len(orders), len(algos))
        if last_alive and n - last_alive > GAP_NOTE_S * 1000:
            self._add_gap(last_alive, n)
            line += " · 꺼져 있던 시간 %s ~ %s" % (fmt.kst_text(last_alive), fmt.kst_text(n))
            changes = self._diff_positions(prev, pos)
            if changes:
                await self._post_gap_changes(last_alive, n, changes)
        await self.d.put("status", line)
        if "last_summary_end" not in self.state:          # 처음 켠 날은 지난 구간 요약을 건너뜀
            self.state["last_summary_end"] = summary.window_for(n, self.hour)[1]
        self._snapshot()

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
                backoff = 1
                keep = asyncio.create_task(self.keepalive())
                while True:
                    raw = await asyncio.wait_for(self.ws.recv(), READ_TIMEOUT_S)
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
            except Exception as e:
                store.log("tick 오류: %s" % e)
            i += 1
            if i % 60 == 0:
                self._snapshot()

    async def scheduler(self):
        while True:
            await asyncio.sleep(20)
            try:
                n = now_ms()
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
        fills = [r for r in store.read_journal(start, end) if r.get("kind") == "fill"]
        realized = await self.b.income("REALIZED_PNL", start, end - 1)
        fees = await self.b.income("COMMISSION", start, end - 1)
        funding = await self.b.income("FUNDING_FEE", start, end - 1)
        positions = await self.b.positions()
        bnb = await self.b.price("BNBUSDT") if any((r.get("asset") == "BNB") for r in fees + funding) else None
        gaps = [(a, b) for a, b in self.state.get("gaps", []) if b > start and a < end]
        await self.d.put("summary", summary.build_text(start, end, fills, realized, fees, funding, positions, bnb, gaps))

    # ------------------------------------------------------------------ 안쪽
    async def emit(self, msgs, recs):
        for m in msgs:
            await self.d.put(m.channel, m.text)
        store.append(recs)

    def _add_gap(self, a, b):
        gaps = [g for g in self.state.get("gaps", []) if g[1] > now_ms() - 8 * 86400 * 1000]
        gaps.append([a, b])
        self.state["gaps"] = gaps

    def _snapshot(self):
        self.state["last_alive"] = now_ms()
        self.state["positions"] = {"%s|%s" % k: [str(p.amt), str(p.entry)] for k, p in self.t.positions.items() if p.amt != 0}
        store.save_state(self.state)
