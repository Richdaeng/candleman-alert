"""요약 글 · 본체(가짜 바이낸스 · 가짜 디스코드) 시험."""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from candleman import cards, fmt, summary  # noqa: E402


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
        msg = summary.build_message(s, e, fills, realized, fees, funding, pos, bnb_price="600")
        self.assertEqual(msg["content"], "📒 10/21 (수) 하루 요약 · 10/20 22:00 ~ 10/21 22:00")       # 머리 줄은 굵은 글씨 없이
        money, hold = msg["embeds"]
        self.assertEqual((money["title"], money["color"]), ("💵 하루 손익", summary.CARD_LONG))      # 실현 + 수수료 + 펀딩비 > 0
        self.assertEqual(money["description"].split("\n"), [
            "거래 **2번**",
            "실현 손익 **+$37.25**",
            "미실현 손익 **−$12.00**",
            "수수료 **−$1.80**",
            "└ BNB로 낸 것은 지금 값으로 환산",
            "펀딩비 **−$0.33**",
        ])
        self.assertEqual(hold["title"], "📌 들고 있는 포지션")
        self.assertEqual(hold["description"].split("\n"), [
            "🟢 **솔라나(SOL) 롱** · 12 SOL",
            "└ 평균 가격 $150.25 · 약 $1,803어치(레버리지 포함) · 미실현 −$12.00",
        ])
        empty = summary.build_message(s, e, [], [], [], [], [], gaps=[(ms(2026, 10, 21, 3), ms(2026, 10, 21, 4, 30))])
        money, hold = empty["embeds"]
        self.assertEqual(money["description"].split("\n")[0], "거래 없음")
        self.assertEqual(money["color"], summary.CARD_REST)                                     # 손익 0 이면 회색
        self.assertEqual(hold["description"], "없음")
        self.assertIn("03:00~04:30", hold["footer"]["text"])                                    # 꺼져 있던 시간은 작은 글씨로

    def test_text_with_spot(self):
        from decimal import Decimal as D
        s, e = ms(2026, 10, 21, 22), ms(2026, 10, 22, 22)
        fills = [{"kind": "fill", "key": "o:1"}]
        spot = [{"kind": "spot_fill", "oid": 7, "side": "BUY", "usd": True, "pnl": None, "fee_usd": "0.41"},
                {"kind": "spot_fill", "oid": 8, "side": "SELL", "usd": True, "pnl": "36.68", "fee_usd": "3.32"},
                {"kind": "spot_fill", "oid": 8, "side": "SELL", "usd": True, "pnl": "10", "fee_usd": "1"},        # 같은 주문의 나머지 체결
                {"kind": "spot_fill", "oid": 9, "side": "SELL", "usd": True, "pnl": None, "fee_usd": "0.5"}]      # 산 값을 모르는 매도
        rows = [{"asset": "BTC", "qty": D("0.06"), "price": D("83000"), "value": D("4980"), "avg": D("82000")},
                {"asset": "SOL", "qty": D("2"), "price": D("150"), "value": D("300"), "avg": None}]
        msg = summary.build_message(s, e, fills, [{"income": "212.4", "asset": "USDT"}], [{"income": "-8.1", "asset": "USDT"}], [], [],
                                    spot_fills=spot, spot_rows=rows)
        money, hold = msg["embeds"]
        self.assertEqual(money["description"].split("\n"), [
            "거래 **4번** (선물 1번 · 현물 3번)",
            "실현 손익 **+$259.08**",
            "└ 선물 +$212.40 · 현물 +$46.68 · 현물 일부는 산 값을 몰라 뺌",
            "미실현 손익 **+$60.00**",
            "└ 선물 $0.00 · 현물 +$60.00",
            "수수료 **−$13.33**",
            "└ 선물 −$8.10 · 현물 −$5.23",
            "펀딩비 **$0.00**",
        ])
        self.assertEqual(hold["description"].split("\n"), [
            "🪙 **비트코인(BTC) 현물** · 0.06 BTC",
            "└ 평균 매수가 $82,000.00 · 약 $4,980어치 · 미실현 +$60.00",
            "🪙 **솔라나(SOL) 현물** · 2 SOL",
            "└ 약 $300어치",
        ])
        losing = summary.build_message(s, e, [], [{"income": "-50", "asset": "USDT"}], [], [], [])
        self.assertEqual(losing["embeds"][0]["color"], summary.CARD_SHORT)                      # 손실이 난 날은 빨강


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


class ReturnTest(unittest.TestCase):
    """주인장 수익률 — 시작 금액 대비 지금 자산(선물 + 현물 + 펀딩 지갑). 포지션을 끝낼 때 #실시간-매매에 한 번."""

    def setUp(self):
        from candleman import store
        tmp = tempfile.mkdtemp()
        self.store = store
        self.old = (store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG)
        store.DATA_DIR = tmp
        store.STATE, store.JOURNAL, store.LOG = (os.path.join(tmp, n) for n in ("state.json", "journal.jsonl", "app.log"))

    def tearDown(self):
        self.store.DATA_DIR, self.store.STATE, self.store.JOURNAL, self.store.LOG = self.old

    def make(self, start=D("7800"), funding_fails=False):
        from candleman.app import App
        from candleman.binance import BinanceError

        class FB:
            async def account(self):
                return {"totalMarginBalance": "5000"}

            async def spot_balances(self):
                return {"USDT": D("1000"), "BTC": D("0.01"), "LDUSDT": D("200"), "LDBTC": D("0.001"), "XYZ": D("5")}

            async def funding_balances(self):
                if funding_fails:
                    raise ConnectionError("바이낸스에 연결하지 못함")
                return {"USDT": D("50")}

            async def spot_price(self, symbol):
                if symbol != "BTCUSDT":
                    raise BinanceError(400, -1121, "Invalid symbol.")
                return "80000"
        app = App.__new__(App)
        app.b, app.d, app.state, app.start_usd = FB(), FakeDiscord(), {}, start
        app.start_year = self.year = fmt.kst(int(time.time() * 1000)).year      # 시험이 실제 연도에 매이지 않게 (폰은 해마다 시험을 다시 돌림)
        app._pos_cache = app._pos_due = app._ret_due = app._ret_cache = None
        app._year_next = 0
        return app

    def test_text(self):
        self.assertEqual(summary.return_text(2026, D("7800"), D("8123.454"), "10/21 20:00").split("\n"), [
            "📊 2026 주인장 수익률 · 10/21 20:00 기준", "시작: $7,800.00", "현재: $8,123.45", "수익률: +4.15% (+$323.45)"])
        self.assertEqual(summary.return_text(2026, D("7800"), D("7620.6"), "10/21 20:00").split("\n")[-1], "수익률: −2.30% (−$179.40)")
        self.assertEqual(summary.return_text(2026, D("7800"), D("7800"), "10/21 20:00").split("\n")[-1], "수익률: 0.00% ($0.00)")
        self.assertEqual(summary.return_text(2027, D("9100"), D("9282"), "01/05 09:00", "01/01 00:00 전체 자산").split("\n")[:2], [
            "📊 2027 주인장 수익률 · 01/05 09:00 기준", "시작: $9,100.00 (01/01 00:00 전체 자산)"])

    def test_new_year_takes_whole_account_as_base(self):
        app = self.make()
        app.start_year = 2026
        self.assertEqual(app._year_base(2026), (D("7800"), None))               # 2026년은 처음 시작한 금액
        self.assertIsNone(app._year_base(2027))
        new_year = ms(2027, 1, 1, 0, 0) + 20_000
        asyncio.run(app._maybe_new_year(new_year))
        self.assertEqual(app._year_base(2027), (D("7130"), new_year))           # 1월 1일 0시의 전체 자산
        self.assertEqual(app.d.sent, [("status", "📅 2027 주인장 수익률 새 기준: $7,130.00 (01/01 00:00 바이낸스 전체 자산)")])
        asyncio.run(app._maybe_new_year(new_year + 60_000))
        self.assertEqual(len(app.d.sent), 1)                                    # 한 해에 한 번
        self.assertEqual(self.store.load_state()["year_start"]["2027"]["usd"], "7130")   # 다시 켜도 그대로

    def test_new_year_taken_late_says_so(self):
        app = self.make()
        app.start_year = 2026
        asyncio.run(app._maybe_new_year(ms(2027, 1, 3, 10, 0)))
        self.assertTrue(app.d.sent[0][1].endswith("· 1월 1일에 꺼져 있어서 지금 값으로 잡음"))

    def test_equity_counts_futures_spot_and_funding(self):
        # 선물 5,000 + 현물 USDT 1,000 + BTC 0.01 × 80,000 + Earn LDUSDT 200 + LDBTC 0.001 × 80,000 + 펀딩 50 (XYZ 는 값을 못 매겨 뺌)
        self.assertEqual(asyncio.run(self.make().equity_usd()), D("7130"))
        self.assertEqual(asyncio.run(self.make(funding_fails=True).equity_usd()), D("7080"))   # 펀딩 지갑을 못 읽어도 계속

    def test_close_posts_return_once_to_trade(self):
        app = self.make()
        asyncio.run(app.emit([], [{"kind": "fill", "t": 1, "effect": "add", "symbol": "EWYUSDT"}]))
        self.assertIsNone(app._ret_due)                                          # 늘리기 · 줄이기는 아님
        asyncio.run(app.emit([], [{"kind": "fill", "t": 2, "effect": "close", "symbol": "EWYUSDT"}]))
        self.assertIsNotNone(app._ret_due)                                       # 다 정리 → 잠시 뒤 수익률
        asyncio.run(app._post_return())
        (ch, head, embeds), = app.d.sent
        self.assertEqual(ch, "trade")
        self.assertTrue(head.startswith("📊 %d 주인장 수익률 · " % self.year))                 # 머리 줄(폰 알림)은 굵은 글씨 없이
        self.assertEqual(embeds[0]["description"].split("\n"),
                         ["시작: **$7,800.00**", "현재: **$7,130.00**", "수익률: **−8.59%** (**−$670.00**)"])
        self.assertEqual(embeds[0]["color"], cards.RED)                                       # 손실이면 빨간 띠

    def test_off_without_start(self):
        app = self.make(start=None)
        asyncio.run(app.emit([], [{"kind": "fill", "t": 2, "effect": "close", "symbol": "EWYUSDT"}]))
        self.assertIsNone(app._ret_due)
        asyncio.run(app._post_return())
        self.assertEqual(app.d.sent, [])


class FmtTest(unittest.TestCase):
    def test_pct(self):
        self.assertEqual(fmt.pct("4.1499"), "+4.15%")
        self.assertEqual(fmt.pct("-2.3"), "−2.30%")
        self.assertEqual(fmt.pct("0"), "0.00%")
        self.assertEqual(fmt.pct("1234.5"), "+1,234.50%")

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
