"""요약 글 · 본체(가짜 바이낸스 · 가짜 디스코드) 시험."""
import asyncio
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from candleman import fmt, summary  # noqa: E402


def ms(y, mo, d, h, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=fmt.KST).timestamp() * 1000)


class SummaryTest(unittest.TestCase):
    def test_window(self):
        s, e = summary.window_for(ms(2026, 10, 21, 22, 0) + 5000, 22)
        self.assertEqual((s, e), (ms(2026, 10, 20, 22), ms(2026, 10, 21, 22)))
        s, e = summary.window_for(ms(2026, 10, 21, 21, 59), 22)
        self.assertEqual((s, e), (ms(2026, 10, 19, 22), ms(2026, 10, 20, 22)))

    def test_text(self):
        s, e = ms(2026, 10, 20, 22), ms(2026, 10, 21, 22)
        fills = [{"kind": "fill", "key": "o:1"}, {"kind": "fill", "key": "o:1"}, {"kind": "fill", "key": "o:2"}, {"kind": "funding"}]
        realized = [{"income": "47.5", "asset": "USDT"}, {"income": "-10.25", "asset": "USDT"}]
        fees = [{"income": "-1.2", "asset": "USDT"}, {"income": "-0.001", "asset": "BNB"}]
        funding = [{"income": "-0.3333", "asset": "USDT"}]
        pos = [{"symbol": "SOLUSDT", "positionAmt": "12", "entryPrice": "150.24987654", "notional": "1803.00", "unRealizedProfit": "-12.00", "positionSide": "BOTH"}]
        text = summary.build_text(s, e, fills, realized, fees, funding, pos, bnb_price="600")
        self.assertEqual(text.split("\n"), [
            "📊 10/21 요약 (10/20 22:00 ~ 10/21 22:00)",
            "거래 2번",
            "실현 손익 +$37.25",
            "미실현 손익 −$12.00",
            "수수료 −$1.80 (BNB로 낸 것은 지금 값으로 환산)",
            "펀딩비 −$0.33",
            "들고 있는 포지션",
            "· 솔라나(SOL) 롱 12 SOL · 평균 가격 $150.25 · 약 $1,803어치(레버리지 포함) · 미실현 −$12.00",
        ])
        empty = summary.build_text(s, e, [], [], [], [], [], gaps=[(ms(2026, 10, 21, 3), ms(2026, 10, 21, 4, 30))])
        self.assertIn("들고 있는 포지션: 없음", empty)
        self.assertIn("거래 없음", empty)
        self.assertIn("03:00~04:30", empty)


class FakeBinance:
    def __init__(self):
        self.pos = []
        self.calls = []

    async def positions(self):
        return list(self.pos)

    async def open_orders(self):
        return []

    async def open_algo_orders(self):
        return []

    async def income(self, kind, a, b):
        self.calls.append(kind)
        return [{"income": "95.0", "asset": "USDT"}] if kind == "REALIZED_PNL" else []


class FakeDiscord:
    def __init__(self, channels=("trade", "summary", "status")):
        self.sent = []
        self.channels = channels

    def has(self, ch):
        return ch in self.channels

    async def put(self, ch, text, embeds=None):
        self.sent.append((ch, text, embeds) if embeds else (ch, text))


class GapTest(unittest.TestCase):
    def test_gap_reports_position_change(self):
        tmp = tempfile.mkdtemp()
        from candleman import config, store
        old = (config.DATA_DIR, store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG)
        store.DATA_DIR = tmp
        store.STATE, store.JOURNAL, store.LOG = (os.path.join(tmp, n) for n in ("state.json", "journal.jsonl", "app.log"))
        try:
            from candleman.app import App
            app = App.__new__(App)
            from candleman.tracker import Tracker
            app.t, app.b, app.d, app.state, app.hour, app.down_since, app.ws = Tracker(), FakeBinance(), FakeDiscord(), {}, 22, None, None
            app.t.load_positions([{"symbol": "BTCUSDT", "positionAmt": "0.08", "entryPrice": "81812.5", "positionSide": "BOTH"}], 0)
            app.b.pos = []                                   # 끊긴 사이 다 청산됨
            since = int(__import__("time").time() * 1000) - 120_000
            asyncio.run(app._after_gap(since))
            texts = [t for ch, t in app.d.sent if ch == "trade"]
            self.assertEqual(len(texts), 1)
            self.assertIn("비트코인(BTC) 롱 0.08 BTC → 없음", texts[0])
            self.assertIn("그사이 확정된 이익 +$95.00", texts[0])
            self.assertTrue(any(ch == "status" for ch, _ in app.d.sent))
            self.assertEqual(app.t.positions, {})
        finally:
            store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG = old[1], old[2], old[3], old[4]


class FakeKind:
    def __init__(self, kind):
        self.kind, self.asked = kind, []

    async def symbol_kind(self, symbol):
        self.asked.append(symbol)
        return self.kind


class IntroTest(unittest.TestCase):
    """처음 진입한 종목을 #종목설명에 한 번만 소개하는지."""

    def setUp(self):
        from candleman import store
        tmp = tempfile.mkdtemp()
        self.old = (store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG)
        store.DATA_DIR = tmp
        store.STATE, store.JOURNAL, store.LOG = (os.path.join(tmp, n) for n in ("state.json", "journal.jsonl", "app.log"))

    def tearDown(self):
        from candleman import store
        store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG = self.old

    def make(self, channels=("trade", "status", "symbols"), kind="EQUITY"):
        from candleman.app import App
        app = App.__new__(App)
        app.state, app.d, app.b = {}, FakeDiscord(channels), FakeKind(kind)
        return app

    def cards(self, app):
        return [s[2][0] for s in app.d.sent if s[0] == "symbols"]

    def test_known_symbol_once(self):
        app = self.make()
        asyncio.run(app.emit([], [{"kind": "fill", "effect": "open", "symbol": "KORUUSDT", "t": ms(2026, 10, 21, 10)}]))
        asyncio.run(app.emit([], [{"kind": "fill", "effect": "flip", "symbol": "KORUUSDT", "t": ms(2026, 10, 21, 11)}]))
        asyncio.run(app.emit([], [{"kind": "fill", "effect": "add", "symbol": "TSLAUSDT", "t": ms(2026, 10, 21, 11)}]))
        asyncio.run(app._introduce("BTCUSDT"))                 # 공지에 처음부터 있던 종목
        cards = self.cards(app)
        self.assertEqual([c["title"] for c in cards], ["🆕 KORU · 한국 주식 3배 ETF"])
        self.assertIn("3배", cards[0]["description"])
        self.assertTrue(cards[0]["timestamp"].startswith("2026-10-21T01:00"))
        self.assertEqual(app.b.asked, [])                      # 설명이 있으면 바이낸스에 묻지 않음
        self.assertFalse(any(s[0] == "status" for s in app.d.sent))

    def test_unknown_symbol_gets_basic_card(self):
        app = self.make(kind="EQUITY")
        asyncio.run(app._introduce("FOOUSDT"))
        card = self.cards(app)[0]
        self.assertEqual(card["title"], "🆕 FOO")
        self.assertEqual(card["description"], "미국 주식 · ETF 선물. 자세한 설명은 곧 추가함.")
        self.assertEqual([s[0] for s in app.d.sent], ["symbols", "status"])
        self.assertIn("FOO", app.d.sent[1][1])

    def test_no_symbols_hook(self):
        app = self.make(channels=("trade", "status"))
        asyncio.run(app._introduce("KORUUSDT"))
        self.assertEqual(app.d.sent, [])
        self.assertNotIn("KORU", app.state.get("introduced", []))   # 나중에 웹후크를 넣으면 그때 소개


class AnnounceTest(unittest.TestCase):
    """새 판을 처음 켰을 때 안내 글을 #실시간-매매 · #일일-요약에 딱 한 번만."""

    def setUp(self):
        from candleman import store
        tmp = tempfile.mkdtemp()
        self.store = store
        self.old = (store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG)
        store.DATA_DIR = tmp
        store.STATE, store.JOURNAL, store.LOG = (os.path.join(tmp, n) for n in ("state.json", "journal.jsonl", "app.log"))

    def tearDown(self):
        self.store.DATA_DIR, self.store.STATE, self.store.JOURNAL, self.store.LOG = self.old

    def make(self, urls):
        from candleman.app import App
        from candleman.discord import Discord
        app = App.__new__(App)
        app.d, app.state = Discord(urls), {}
        return app

    def queued(self, app):
        out = []
        while not app.d.q.empty():
            out.append(app.d.q.get_nowait())
        return out

    def test_once_to_trade_and_summary(self):
        app = self.make({"trade": "u1", "summary": "u2", "status": "u3", "symbols": "u4"})
        asyncio.run(app._announce(ms(2026, 10, 21, 13)))
        sent = self.queued(app)
        self.assertEqual([(ch, text) for ch, text, _ in sent], [("trade", ""), ("summary", "")])
        card, = sent[0][2]
        self.assertEqual(card["title"], "📢 새 기능 · /포지션")
        self.assertIn("#잡담방에서 `/포지션`을 입력하면", card["description"])
        self.assertNotIn("6시간", card["description"])                    # 막는 규칙은 알리지 않음
        asyncio.run(app._announce(ms(2026, 10, 21, 14)))                  # 다시 켜도
        self.assertEqual(self.queued(app), [])                             # 또 올리지 않음
        self.assertEqual(self.store.load_state()["notices_sent"], ["2026-10-21-position-cards"])

    def test_same_webhook_once_and_expired_skipped(self):
        app = self.make({"trade": "u1", "summary": "u1", "status": "u3"})  # #일일-요약 웹후크를 따로 안 넣은 경우
        asyncio.run(app._announce(ms(2026, 10, 21, 13)))
        self.assertEqual([ch for ch, _, _ in self.queued(app)], ["trade"])
        late = self.make({"trade": "u1", "summary": "u2"})
        asyncio.run(late._announce(ms(2026, 11, 5, 13)))                   # 기한이 지나면 (새로 설치해도) 안 올림
        self.assertEqual(self.queued(late), [])


class PositionsChannelTest(unittest.TestCase):
    """#현재포지션 — 종목 · 방향 · 수량이 바뀔 때만 지금 포지션 카드를 새 글로."""

    def setUp(self):
        from candleman import store
        tmp = tempfile.mkdtemp()
        self.store = store
        self.old = (store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG)
        store.DATA_DIR = tmp
        store.STATE, store.JOURNAL, store.LOG = (os.path.join(tmp, n) for n in ("state.json", "journal.jsonl", "app.log"))

    def tearDown(self):
        self.store.DATA_DIR, self.store.STATE, self.store.JOURNAL, self.store.LOG = self.old

    def make(self, urls=None):
        from candleman.app import App
        from candleman.discord import Discord

        class FB:
            def __init__(self):
                self.asked = 0
                self.pos = [{"symbol": "EWYUSDT", "positionAmt": "20", "entryPrice": "152.83045678", "markPrice": "155",
                             "notional": "3100", "unRealizedProfit": "12.5", "isolatedMargin": "0", "positionSide": "BOTH"}]

            async def positions(self):
                self.asked += 1
                return [dict(p) for p in self.pos]

            async def account(self):
                return {"totalMarginBalance": "3875"}
        app = App.__new__(App)
        app.b, app.state = FB(), {}
        app.d = Discord(urls or {"trade": "u1", "summary": "u2", "status": "u3", "positions": "u5"})
        return app

    def posted(self, app):
        out = []
        while not app.d.q.empty():
            ch, text, embeds = app.d.q.get_nowait()
            if ch == "positions":
                out.append((text, embeds))
        return out

    def test_posts_only_when_positions_change(self):
        app = self.make()
        asyncio.run(app._post_positions_if_changed())
        (text, embeds), = self.posted(app)
        self.assertTrue(text.startswith("📌 **운영자 포지션** · "))
        self.assertEqual(embeds[0]["title"], "🟢 한국 주식 ETF(EWY) · 롱")
        self.assertIn("평균 가격 **$152.83**", embeds[0]["description"])
        self.assertIn("실제 레버리지 **0.8배**", embeds[0]["description"])
        app.b.pos[0].update(markPrice="160", unRealizedProfit="140")           # 가격 · 손익만 바뀐 것은
        asyncio.run(app._post_positions_if_changed())
        self.assertEqual(self.posted(app), [])                                   # 다시 올리지 않음
        app.b.pos[0]["positionAmt"] = "30"                                       # 수량이 바뀌면 새 글
        asyncio.run(app._post_positions_if_changed())
        (_, embeds), = self.posted(app)
        self.assertIn("수량 **30주**", embeds[0]["description"])
        app.b.pos = []                                                           # 다 정리하면 '없음' 글
        asyncio.run(app._post_positions_if_changed())
        (text, embeds), = self.posted(app)
        self.assertTrue(text.startswith("📌 운영자는 지금 들고 있는 포지션이 없음"))
        self.assertEqual(embeds, [])

    def test_no_repost_after_restart_when_unchanged(self):
        app = self.make()
        asyncio.run(app._post_positions_if_changed())
        self.assertEqual(len(self.posted(app)), 1)
        again = self.make()
        again.state = self.store.load_state()                                    # 다시 켠 프로그램
        asyncio.run(again._post_positions_if_changed())
        self.assertEqual(self.posted(again), [])

    def test_off_without_webhook_and_fill_schedules_check(self):
        app = self.make({"trade": "u1", "summary": "u2", "status": "u3"})
        asyncio.run(app._post_positions_if_changed())
        self.assertEqual(app.b.asked, 0)                                         # 웹후크가 없으면 바이낸스에 묻지도 않음
        app._pos_cache, app._pos_due = ("옛 값",), None
        asyncio.run(app.emit([], [{"kind": "fill", "t": 1, "effect": "add", "symbol": "EWYUSDT"}]))
        self.assertIsNone(app._pos_cache)                                        # /포지션도 새 값으로
        self.assertIsNotNone(app._pos_due)                                       # 잠시 뒤 #현재포지션 확인


class FmtTest(unittest.TestCase):
    def test_price2_cuts_long_decimals(self):
        self.assertEqual(fmt.price2("152.83045678"), "$152.83")
        self.assertEqual(fmt.price2("82000"), "$82,000.00")
        self.assertEqual(fmt.price2("0.0876543"), "$0.08765")          # $1 보다 싼 것은 유효숫자 네 자리
        self.assertEqual(fmt.price2("-1.005"), "−$1.01")

    def test_times(self):
        self.assertEqual(fmt.times("0.80"), "0.8배")
        self.assertEqual(fmt.times("1.255"), "1.26배")
        self.assertEqual(fmt.times("2"), "2배")
        self.assertEqual(fmt.times("0.004"), "0.01배 미만")


if __name__ == "__main__":
    unittest.main()
