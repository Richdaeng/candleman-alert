"""프로그램 본체: 바이낸스 실시간 연결 → 장부 → 디스코드. 끊기면 다시 붙고, 그사이 바뀐 포지션은 조회로 채웁니다."""
import asyncio
import json
import os
import signal
import time
from datetime import datetime, timezone

from . import fmt, names, notices, store, summary, updater
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
START_RETRY_S = 10             # 켤 때 바이낸스에 못 붙으면 이만큼 뒤 다시 (두 배씩 늘려 최대 5분)
OUTAGE_NOTE_S = 2 * 60         # 이보다 오래 바이낸스에 못 붙으면 #봇-상태에 한 번 알림
NET_ERRORS = (OSError, asyncio.TimeoutError, BinanceError, ValueError)   # 인터넷 · 거래소 쪽 문제 (코드 오류는 아님)
POS_POST_DELAY_S = 3           # 체결 뒤 이만큼 조용하면 #현재포지션에 새로 올림 (나눠서 체결돼도 한 번)
POS_CHECK_S = 10 * 60          # 체결 소식을 놓쳤을 때를 대비해 이만큼마다 포지션을 한 번 견줌


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
        self._pos_posting = False

    # ------------------------------------------------------------------ 시작
    async def run(self):
        try:                                              # stop.sh · 업데이트가 끄라고 하면 포지션을 저장하고 끝남
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, lambda: asyncio.ensure_future(self._shutdown()))
        except (NotImplementedError, AttributeError, RuntimeError, ValueError):
            pass                                          # 윈도에서 시험할 때
        asyncio.create_task(self.d.worker())
        await self._start_until_ok()
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
        line = "🤖 시작함 · 포지션 %d개 · 미체결 주문 %d개 · 조건부 주문 %d개" % (len(pos), len(orders), len(algos))
        if last_alive:                                    # 다시 켜는 몇 초 사이의 체결도 놓치지 않게 늘 견줌
            changes = self._diff_positions(prev, pos)
            if n - last_alive > GAP_NOTE_S * 1000:
                self._add_gap(last_alive, n)
                line += " · 꺼져 있던 시간 %s ~ %s" % (fmt.kst_text(last_alive), fmt.kst_text(n))
            if changes:
                await self._post_gap_changes(last_alive, n, changes)
        await self.d.put("status", line)
        await self._report_update()
        await self._announce(n)
        if "last_summary_end" not in self.state:          # 처음 켠 날은 지난 구간 요약을 건너뜀
            self.state["last_summary_end"] = summary.window_for(n, self.hour)[1]
        self._snapshot()
        self._pos_due = n                                 # 꺼져 있던 사이 바뀌었으면 #현재포지션도 새로
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
                await self.d.put("status", "⚠️ 켜졌지만 바이낸스에 연결이 안 됨 (%s부터) · 폰의 인터넷(와이파이)을 확인해 주세요. "
                                           "연결되면 바로 시작함\n이유: %s" % (fmt.kst_text(since), why))
            await asyncio.sleep(wait)
            wait = min(wait * 2, 300)

    async def _tell_outage(self, n):
        """돌다가 바이낸스 연결이 끊긴 채 OUTAGE_NOTE_S 넘게 못 붙으면 #봇-상태에 한 번 알림 (다시 붙으면 '🔌 … 다시 붙음')."""
        if self.down_since and not getattr(self, "_outage_told", False) and n - self.down_since >= OUTAGE_NOTE_S * 1000:
            self._outage_told = True
            await self.d.put("status", "⚠️ 바이낸스 연결이 끊긴 뒤 다시 붙지 못하고 있음 (%s부터) · 폰의 인터넷(와이파이)을 확인해 주세요. "
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
        self._pos_due = n                                 # 끊긴 사이 바뀌었으면 #현재포지션도 새로 (같으면 안 올림)
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
            if i % POS_CHECK_S == 0 and not self._pos_due:
                self._pos_due = now_ms()
            if self._pos_due and now_ms() >= self._pos_due and not self._pos_posting:
                self._pos_due = None
                asyncio.create_task(self._post_positions_if_changed())

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
            cache = self._pos_cache = (now, positions, equity)
        return summary.positions_message(cache[1], cache[2], fmt.kst_text(int(cache[0] * 1000)))

    # ------------------------------------------------------------------ 안쪽
    async def emit(self, msgs, recs):
        for m in msgs:
            await self.d.put(m.channel, m.text)
        store.append(recs)
        for r in recs:
            if r.get("kind") == "fill":                       # 포지션이 바뀜 → /포지션은 새 값으로, #현재포지션은 잠시 뒤 새 글
                self._pos_cache = None
                self._pos_due = now_ms() + POS_POST_DELAY_S * 1000
            if r.get("kind") == "fill" and r.get("effect") in ("open", "flip"):
                await self._introduce(r.get("symbol", ""), r.get("t"))

    async def _post_positions_if_changed(self):
        """#현재포지션: 종목 · 방향 · 수량이 지난번 글과 다르면 지금 포지션 카드(/포지션과 같은 모양)를 새 글로 올림."""
        if not self.d.has("positions") or getattr(self, "_pos_posting", False):
            return
        self._pos_posting = True
        try:
            positions = await self.b.positions()
            sig = sorted("%s|%s|%s" % (r.get("symbol"), r.get("positionSide") or "BOTH", fmt.plain(r.get("positionAmt")))
                         for r in positions)
            if sig == self.state.get("positions_posted"):
                return
            try:
                equity = (await self.b.account()).get("totalMarginBalance")
            except Exception as e:
                store.log("증거금 조회 실패: %s" % e)
                equity = None
            now = time.time()
            self._pos_cache = (now, positions, equity)       # /포지션도 같은 값을 씀
            msg = summary.positions_message(positions, equity, fmt.kst_text(int(now * 1000)))
            await self.d.put("positions", msg["content"], msg["embeds"])
            self.state["positions_posted"] = sig
            store.save_state(self.state)
        except NET_ERRORS as e:
            store.log("현재 포지션 조회 실패 — 1분 뒤 다시: %s" % e)
            self._pos_due = now_ms() + 60_000
        finally:
            self._pos_posting = False

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
        if self.state.get("update_skip") == new:          # 이미 안 받기로 한 판
            return
        if status == "diverged":
            self.state["update_skip"] = new
            store.save_state(self.state)
            await self.d.put("status", "⚠️ 자동 업데이트 안 함: GitHub 의 새 판(%s)이 지금 판(%s)에서 이어지지 않음. 직접 확인 필요."
                             % (new[:7], old[:7]))
            return
        ok, tail = await asyncio.to_thread(updater.test, new)
        if not ok:
            self.state["update_skip"] = new
            store.save_state(self.state)
            await self.d.put("status", "⚠️ 자동 업데이트 안 함: 새 판(%s) 시험 실패 · 지금 판 그대로 (%s)" % (new[:7], tail))
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
