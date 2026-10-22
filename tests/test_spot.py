"""현물 시험 — 가짜 이벤트 · 가짜 거래 기록으로: 체결 묶기 · 알림 글 · 평균 매수가와 손익 · #현재포지션 현물 카드."""
import asyncio
import os
import sys
import tempfile
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from candleman import store, summary  # noqa: E402
from candleman.binance import BinanceError, sign_ws_params  # noqa: E402
from candleman.spot import CostBook, SpotFill, SpotTracker, fill_text, how_text, split_symbol  # noqa: E402
from candleman.spotfeed import SpotFeed  # noqa: E402

D = Decimal


def er(oid, x, X, side="BUY", otype="LIMIT", q="0.05", p="80000", l="0", L="0", z="0", n="0", N=None, s="BTCUSDT", tif="GTC", P="0"):
    """executionReport 하나."""
    Y = str(D(l) * D(L))
    return {"e": "executionReport", "s": s, "S": side, "o": otype, "f": tif, "q": q, "p": p, "P": P, "x": x, "X": X, "i": oid,
            "l": l, "L": L, "z": z, "n": n, "N": N, "Y": Y}


class SignTest(unittest.TestCase):
    def test_matches_binance_doc_example(self):                            # 바이낸스 문서의 HMAC 예시 값
        sec = b"NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
        params = {"apiKey": "vmPUZE6mv9SD5VNHk4HlWFsOr6aKE2zvsw0MuIgwCIPy6utIco14y7Ju91duEh8A", "price": "52000.00",
                  "quantity": "0.01000000", "recvWindow": 100, "side": "SELL", "symbol": "BTCUSDT", "timeInForce": "GTC",
                  "timestamp": 1645423376532, "type": "LIMIT"}
        self.assertEqual(sign_ws_params(sec, params), "aa1b5712c094bc4e57c05a1a5c1fd8d88dcd628338ea863fec7b88e59fe2db24")

    def test_split_symbol(self):
        self.assertEqual(split_symbol("BTCUSDT"), ("BTC", "USDT"))
        self.assertEqual(split_symbol("ETHBTC"), ("ETH", "BTC"))
        self.assertEqual(split_symbol("SOLFDUSD"), ("SOL", "FDUSD"))
        self.assertEqual(split_symbol("USDCUSDT"), ("USDC", "USDT"))


class TrackerTest(unittest.TestCase):
    def test_market_buy_fills_gathered_into_one_without_order_post(self):
        t = SpotTracker()
        out = [t.on_event(er(1, "NEW", "NEW", otype="MARKET", q="0.05", p="0"), 0),
               t.on_event(er(1, "TRADE", "PARTIALLY_FILLED", otype="MARKET", q="0.05", p="0", l="0.02", L="80000", z="0.02",
                             n="0.00002", N="BTC"), 100),
               t.on_event(er(1, "TRADE", "FILLED", otype="MARKET", q="0.05", p="0", l="0.03", L="80100", z="0.05",
                             n="0.00003", N="BTC"), 200)]
        fills = [f for _, fs, _ in out for f in fs]
        self.assertEqual(len(fills), 1)
        f = fills[0]
        self.assertEqual((f.side, f.qty, f.quote_qty, f.partial), ("BUY", D("0.05"), D("4003.00"), False))
        self.assertEqual(f.fees, {"BTC": D("0.00005")})
        self.assertEqual(t.tick(5000), ([], [], []))                       # 시장가는 '걸어 둠' 글 없음

    def test_limit_posted_partially_filled_then_canceled(self):
        t = SpotTracker()
        t.on_event(er(2, "NEW", "NEW", side="SELL", q="0.10", p="83000"), 0)
        msgs, _, _ = t.tick(2500)
        self.assertEqual([m.text for m in msgs], ["📝 비트코인(BTC) 현물 매도 주문 걸어 둠\n$83,000.00에 0.1 BTC · 약 $8,300어치 · 아직 체결 전"])
        t.on_event(er(2, "TRADE", "PARTIALLY_FILLED", side="SELL", q="0.10", p="83000", l="0.04", L="83000", z="0.04",
                      n="3.32", N="USDT"), 3000)
        _, fills, _ = t.tick(9000)                                          # 5초 조용하면 모은 체결을 알림
        self.assertEqual((fills[0].qty, fills[0].fee_quote, fills[0].partial, fills[0].open_left), (D("0.04"), D("3.32"), True, D("0.06")))
        msgs, fills, _ = t.on_event(er(2, "CANCELED", "CANCELED", side="SELL", q="0.10", p="83000", z="0.04"), 10000)
        self.assertEqual(fills, [])
        self.assertEqual([m.text for m in msgs], ["✖️ 비트코인(BTC) 현물 매도 주문 취소\n$83,000.00에 0.1 BTC 중 0.04 BTC 체결 · 남은 0.06 BTC 취소"])

    def test_quick_cancel_and_market_remainder_are_quiet(self):
        t = SpotTracker()
        t.on_event(er(3, "NEW", "NEW"), 0)
        msgs, _, _ = t.on_event(er(3, "CANCELED", "CANCELED"), 1000)       # 알리기 전에 바로 취소
        self.assertEqual(msgs, [])
        t.on_event(er(4, "TRADE", "PARTIALLY_FILLED", otype="MARKET", p="0", l="0.01", L="80000", z="0.01"), 0)
        msgs, fills, _ = t.on_event(er(4, "EXPIRED", "EXPIRED", otype="MARKET", p="0", z="0.01"), 10)
        self.assertEqual((msgs, len(fills)), ([], 1))                       # 시장가의 남은 몫이 사라진 것은 글 없음

    def test_balances_follow_account_events(self):
        t = SpotTracker()
        t.load_balances({"BTC": D("0.1")})
        t.on_event({"e": "outboundAccountPosition", "B": [{"a": "BTC", "f": "0.03", "l": "0.02"}]}, 0)
        self.assertEqual(t.balances["BTC"], D("0.05"))


def fill(side, qty, quote, otype="MARKET", symbol="BTCUSDT", partial=False, open_left="0", fee_quote="0"):
    base, q = split_symbol(symbol)
    return SpotFill(oid=1, symbol=symbol, base=base, quote=q, side=side, otype=otype, qty=D(qty), quote_qty=D(quote),
                    fee_quote=D(fee_quote), fees={}, t_ms=0, partial=partial, open_left=D(open_left))


class TextTest(unittest.TestCase):
    def test_buy_first_and_more(self):
        self.assertEqual(fill_text(fill("BUY", "0.05", "4100"), D("0.05"), None),
                         "🟢 비트코인(BTC) 현물 매수\n$82,000.00에 0.05 BTC · 약 $4,100어치 · 바로 체결(시장가)")
        self.assertEqual(fill_text(fill("BUY", "0.05", "4100", otype="LIMIT"), D("0.15"), None),
                         "➕ 비트코인(BTC) 현물 추가 매수\n$82,000.00에 0.05 BTC 더 · 모두 0.15 BTC · 약 $4,100어치 · 걸어 둔 주문 체결")

    def test_sell_all_part_and_unknown_cost(self):
        self.assertEqual(fill_text(fill("SELL", "0.05", "4150"), D("0.00000004"), D("47.5")),
                         "⬜ 비트코인(BTC) 현물 모두 매도 · 이익 +$47.50\n$83,000.00에 0.05 BTC 정리 · 약 $4,150어치 · 바로 체결(시장가)")
        self.assertEqual(fill_text(fill("SELL", "0.02", "1660", otype="LIMIT", partial=True, open_left="0.03"), D("0.08"), D("-12.3")),
                         "🟠 비트코인(BTC) 현물 일부 매도 · 손실 −$12.30\n$83,000.00에 0.02 BTC 정리 · 남은 0.08 BTC · 약 $1,660어치 · "
                         "걸어 둔 주문 체결 · 나머지 0.03 BTC는 아직 걸려 있음")
        self.assertTrue(fill_text(fill("SELL", "0.05", "4150"), D("0"), None).endswith("· 산 값을 몰라 손익은 뺌"))

    def test_non_dollar_pair(self):
        self.assertEqual(fill_text(fill("BUY", "1", "0.05", symbol="ETHBTC"), D("1"), None),
                         "🟢 이더리움(ETH) 현물 매수\n0.05 BTC에 1 ETH · 0.05 BTC어치 · 바로 체결(시장가)")


def trade(tid, oid, buyer, qty, quote, fee="0", fee_asset="USDT", symbol="BTCUSDT"):
    return {"symbol": symbol, "id": tid, "orderId": oid, "isBuyer": buyer, "qty": qty, "quoteQty": quote,
            "commission": fee, "commissionAsset": fee_asset}


class CostBookTest(unittest.TestCase):
    def test_moving_average_with_fees(self):
        b = CostBook()
        b.apply(trade(1, 10, True, "0.1", "8000", fee="0.0001", fee_asset="BTC"))   # 코인으로 낸 수수료 → 받은 수량이 줄어듦
        b.apply(trade(2, 11, True, "0.1", "9000", fee="9", fee_asset="USDT"))       # 달러로 낸 수수료 → 산 값에 더함
        b.apply(trade(3, 12, True, "0.0999", "9990", fee="0.01", fee_asset="BNB"), bnb_price=D("600"))
        self.assertEqual(b.qty, D("0.2998"))
        self.assertEqual(b.cost, D("27005"))
        self.assertEqual(b.last_id, 3)
        self.assertEqual(b.pnl(D("0.1"), D("10000")).quantize(D("0.01")), D("992.33"))   # 10000 − 0.1 × 평균 90,076.72
        b.apply(trade(4, 13, False, "0.1", "10000"))
        self.assertEqual(b.qty, D("0.1998"))
        self.assertEqual(b.avg().quantize(D("0.01")), (D("27005") / D("0.2998")).quantize(D("0.01")))   # 팔아도 평균은 그대로

    def test_selling_more_than_known_has_no_pnl(self):
        b = CostBook()
        b.apply(trade(1, 10, True, "0.1", "8000"))
        self.assertIsNone(b.pnl(D("0.2"), D("17000")))                     # 입금한 몫까지 판 것
        b.apply(trade(2, 11, False, "0.2", "17000"))
        self.assertEqual((b.qty, b.cost), (D("0"), D("0")))
        self.assertEqual(CostBook.load(b.dump()).dump(), b.dump())


class HowTextTest(unittest.TestCase):
    def test_conditional_words(self):
        self.assertEqual(how_text("STOP_LOSS_LIMIT", "SELL"), "스탑 로스 발동")
        self.assertEqual(how_text("TAKE_PROFIT_LIMIT", "SELL"), "익절 주문 발동")
        self.assertEqual(how_text("STOP_LOSS", "BUY"), "조건 주문 발동")              # 사는 조건 주문(돌파 매수 등)
        self.assertEqual((how_text("MARKET", "SELL"), how_text("LIMIT", "BUY")), ("바로 체결(시장가)", "걸어 둔 주문 체결"))


class FakeBinance:
    def __init__(self):
        self.balances = {"USDT": D("500"), "BTC": D("0.05"), "ETH": D("0.002"), "LDBTC": D("1"), "SOL": D("2")}
        self.prices = {"BTCUSDT": "83000", "ETHUSDT": "3000", "SOLUSDT": "150", "BNBUSDT": "600"}
        self.trades = {"BTCUSDT": [trade(1, 10, True, "0.05", "4000"), trade(2, 11, True, "0.05", "4200")],
                       "SOLUSDT": [trade(5, 20, True, "1", "140", symbol="SOLUSDT")]}
        self.trade_calls = []
        self.flexible, self.locked, self.funding, self.futures = {}, {}, {}, {}   # Earn · 펀딩 · 선물 지갑 (오류를 넣으면 그 오류)

    async def spot_balances(self):
        return dict(self.balances)

    @staticmethod
    def _give(v):
        if isinstance(v, Exception):
            raise v
        return dict(v)

    async def earn_flexible(self):
        return self._give(self.flexible)

    async def earn_locked(self):
        return self._give(self.locked)

    async def funding_balances(self):
        return self._give(self.funding)

    async def futures_wallet(self):
        return self._give(self.futures)

    async def spot_price(self, symbol):
        if symbol not in self.prices:
            raise BinanceError(400, -1121, "Invalid symbol.")
        return self.prices[symbol]

    async def spot_trades(self, symbol, from_id=0, limit=1000):
        self.trade_calls.append((symbol, from_id))
        return [t for t in self.trades.get(symbol, []) if t["id"] >= from_id]


class FeedTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.old = (store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG)
        store.DATA_DIR = tmp
        store.STATE, store.JOURNAL, store.LOG = (os.path.join(tmp, n) for n in ("state.json", "journal.jsonl", "app.log"))

        class App:
            pass
        self.app = App()
        self.app.b, self.app.state = FakeBinance(), {}
        self.feed = SpotFeed(self.app)

    def tearDown(self):
        store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG = self.old

    def test_holdings_hide_usdt_and_small_and_add_average(self):
        rows, shown = asyncio.run(self.feed.holdings())
        self.assertEqual([r["asset"] for r in rows], ["BTC", "SOL", "ETH"])        # USDT · 값을 못 매기는 LD 자산은 빠짐
        self.assertEqual([r["asset"] for r in shown], ["BTC", "SOL"])              # ETH $6 은 $10 미만이라 안 보임
        btc, sol = shown
        self.assertEqual(btc["avg"], D("82000"))                                   # 0.05 BTC 를 $80,000 · $84,000 에 산 평균
        self.assertIsNone(sol["avg"])                                              # 1 SOL 만 산 값을 앎 (지금 2 SOL · 입금 등) → 모름

    def test_holdings_merge_wallets_and_show_where(self):
        """10/22: 산 솔라나가 현물 지갑에서 빠져(판 기록 없음) 06:30 글에서 사라짐 · BNB 도 안 보임 → Earn · 펀딩 · 선물 지갑도 셈."""
        b = self.app.b
        b.balances = {"USDT": D("500"), "SOL": D("0.0001"), "LDSOL": D("27.7"), "LDO": D("20")}   # LDO 는 진짜 코인(리도)
        b.prices["LDOUSDT"] = "1"
        b.flexible = {"SOL": D("27.7"), "USDC": D("100")}
        b.locked, b.funding, b.futures = {"BNB": D("0.5")}, {"BNB": D("0.1")}, {"BNB": D("0.2"), "USDT": D("3000")}
        rows, shown = asyncio.run(self.feed.holdings(with_avg=False))
        self.assertEqual([r["asset"] for r in shown], ["SOL", "BNB", "LDO"])        # 달러 코인(USDT · USDC)은 빠짐
        sol, bnb, ldo = shown
        self.assertEqual(sol["qty"], D("27.7001"))                                  # LDSOL 은 Earn 을 읽었으니 두 번 세지 않음
        self.assertEqual(bnb["qty"], D("0.8"))
        self.assertEqual(summary.where_text(sol), "Earn 예치")                      # $1 어치도 안 되는 현물 지갑 찌꺼기는 뺌
        self.assertEqual(summary.where_text(bnb), "Earn 예치 0.5 BNB + 펀딩 지갑 0.1 BNB + 선물 지갑 0.2 BNB")
        self.assertIsNone(summary.where_text(ldo))                                  # 현물 지갑에만 있으면 '보관' 줄 없음

    def test_refused_wallet_is_skipped_and_ld_assets_count_instead(self):
        b = self.app.b
        b.balances = {"LDSOL": D("27.7"), "LDUSDT": D("50")}
        b.flexible = BinanceError(401, -2015, "Invalid API-key, IP, or permissions for action.")
        _, shown = asyncio.run(self.feed.holdings(with_avg=False))
        self.assertEqual([(r["asset"], r["qty"], r["where"]) for r in shown], [("SOL", D("27.7"), {"earn": D("27.7")})])
        self.assertIn("Earn 자유 예치", self.feed.off)                               # 이번 실행 동안은 다시 묻지 않음
        b.flexible = ConnectionError("바이낸스에 연결하지 못함")
        asyncio.run(self.feed.holdings(with_avg=False))
        other = SpotFeed(self.app)                                                  # 인터넷 문제는 빼지 않고 그대로 올려 보냄 (조금 뒤 다시)
        with self.assertRaises(ConnectionError):
            asyncio.run(other.holdings(with_avg=False))
        self.assertEqual(other.off, set())

    def test_sell_pnl_uses_trades_before_this_order(self):
        self.app.b.trades["BTCUSDT"].append(trade(3, 12, False, "0.04", "3320", fee="3.32"))
        f = fill("SELL", "0.04", "3320", fee_quote="3.32")
        f.oid = 12
        pnl = asyncio.run(self.feed.sell_pnl(f))
        self.assertEqual(pnl, D("3320") - D("3.32") - D("0.04") * D("82000"))      # 평균 매수가 $82,000
        book = CostBook.load(self.app.state["spot_books"]["BTCUSDT"])
        self.assertEqual((book.qty, book.last_id), (D("0.06"), 3))                 # 이 주문 거래까지 장부에 넣음
        asyncio.run(self.feed.sell_pnl(f))
        self.assertEqual(self.app.b.trade_calls[-1], ("BTCUSDT", 4))               # 다음엔 새 거래만 받음


    def test_fill_worker_posts_sell_with_pnl(self):
        sent = []

        class D2:
            async def put(self, ch, text, embeds=None):
                sent.append((ch, text))
        self.app.d, self.app._pos_cache, self.app._pos_due = D2(), ("옛 값",), None
        self.app.b.trades["BTCUSDT"].append(trade(3, 12, False, "0.04", "3320", fee="3.32"))
        self.feed.t.load_balances({"BTC": D("0.06")})
        f = fill("SELL", "0.04", "3320", fee_quote="3.32")
        f.oid = 12
        import candleman.spotfeed as sf
        old, sf.SETTLE_S = sf.SETTLE_S, 0

        async def go():
            task = asyncio.ensure_future(self.feed._fill_worker())
            await self.feed.fills.put(f)
            for _ in range(500):                                                    # 느린 폰에서도 넉넉히 (최대 5초)
                await asyncio.sleep(0.01)
                if sent:
                    break
            task.cancel()
        try:
            asyncio.run(go())
        finally:
            sf.SETTLE_S = old
        self.assertEqual(sent[0][0], "trade")
        self.assertTrue(sent[0][1].startswith("🟠 비트코인(BTC) 현물 일부 매도 · 이익 +$36.68\n$83,000.00에 0.04 BTC 정리 · 남은 0.06 BTC"))
        self.assertIsNone(self.app._pos_cache)                                     # /포지션도 새 값으로
        self.assertIsNotNone(self.app._pos_due)                                    # #현재포지션 새 글
        rec, = [r for r in store.read_journal(0, 10 ** 15) if r.get("kind") == "spot_fill"]   # 하루 요약이 쓰는 기록
        self.assertEqual((rec["pnl"], rec["fee_usd"], rec["usd"]), ("36.68", "3.32", True))
        self.assertIsNone(getattr(self.app, "_ret_due", None))                    # 일부 매도라 수익률 글은 아님

    def test_selling_everything_schedules_owner_return(self):
        sent = []

        class D2:
            async def put(self, ch, text, embeds=None):
                sent.append((ch, text))
        self.app.d, self.app._pos_cache, self.app._pos_due, self.app._ret_due = D2(), None, None, None
        self.app.start_usd = D("7800")
        self.feed.t.load_balances({"BTC": D("0.00000004")})                       # 수수료 찌꺼기만 남음 = 모두 매도
        f = fill("SELL", "0.1", "8300")
        import candleman.spotfeed as sf
        old, sf.SETTLE_S = sf.SETTLE_S, 0

        async def go():
            task = asyncio.ensure_future(self.feed._fill_worker())
            await self.feed.fills.put(f)
            for _ in range(500):
                await asyncio.sleep(0.01)
                if sent:
                    break
            task.cancel()
        try:
            asyncio.run(go())
        finally:
            sf.SETTLE_S = old
        self.assertTrue(sent[0][1].startswith("⬜ 비트코인(BTC) 현물 모두 매도"))
        self.assertIsNotNone(self.app._ret_due)                                    # 잠시 뒤 #실시간-매매에 주인장 수익률


class SpotCardsTest(unittest.TestCase):
    def test_spot_cards_after_futures_cards(self):
        fut = [{"symbol": "EWYUSDT", "positionAmt": "20", "entryPrice": "152.83", "markPrice": "155", "notional": "3100",
                "unRealizedProfit": "12.5", "isolatedMargin": "0"}]
        spot = [{"asset": "BTC", "qty": D("0.05"), "price": D("83000"), "value": D("4150"), "avg": D("82000")},
                {"asset": "SOL", "qty": D("2"), "price": D("150"), "value": D("300"), "avg": None}]
        m = summary.positions_message(fut, "3875", "10/21 15:00", spot)
        ewy, btc, sol = m["embeds"]
        self.assertEqual(ewy["footer"], {"text": "실제 레버리지 = 거래 크기 ÷ 증거금"})   # 설명은 선물 카드에
        self.assertEqual((btc["title"], btc["color"]), ("🪙 비트코인(BTC) · 현물", 15776011))
        self.assertEqual(btc["description"].split("\n"), ["수량 **0.05 BTC**", "평균 매수가 **$82,000.00**", "지금 가격 **$83,000.00**",
                                                         "평가 금액 **$4,150.00**", "미실현 손익 **+$50.00**"])
        self.assertEqual(sol["description"].split("\n"), ["수량 **2 SOL**", "지금 가격 **$150.00**", "평가 금액 **$300.00**"])
        only_spot = summary.positions_message([], None, "10/21 15:00", spot[:1])
        self.assertTrue(only_spot["content"].startswith("📌 **운영자 포지션**"))        # 선물이 없어도 현물만으로 글
        self.assertEqual(len(only_spot["embeds"]), 1)

    def test_card_says_where_coin_is_kept(self):
        sol = {"asset": "SOL", "qty": D("27.716256"), "price": D("107.04"), "value": D("2966.75"), "avg": D("107.18"),
               "where": {"spot": D("0.000256"), "earn": D("27.716")}}
        card, = summary.positions_message([], None, "10/22 08:50", [sol])["embeds"]
        self.assertEqual(card["description"].split("\n")[:2], ["수량 **27.716256 SOL**", "보관 **Earn 예치**"])
        line = summary.holding_lines([], [sol])[1]
        self.assertTrue(line.endswith(" · 보관 Earn 예치"))                           # 하루 요약의 '들고 있는 포지션' 줄도


class PositionsWithSpotTest(unittest.TestCase):
    """#현재포지션 — 현물 수량이 바뀌어도 새 글, 가격만 바뀌면 안 올림."""

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.old = (store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG)
        store.DATA_DIR = tmp
        store.STATE, store.JOURNAL, store.LOG = (os.path.join(tmp, n) for n in ("state.json", "journal.jsonl", "app.log"))

    def tearDown(self):
        store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG = self.old

    def test_spot_change_posts_new_card(self):
        from candleman.app import App
        from candleman.discord import Discord
        fb = FakeBinance()

        async def positions():
            return []

        async def account():
            return {"totalMarginBalance": "1000"}
        fb.positions, fb.account = positions, account
        app = App.__new__(App)
        app.b, app.state = fb, {}
        app.d = Discord({"trade": "u1", "summary": "u2", "status": "u3", "positions": "u5"})
        app.spot = SpotFeed(app)

        def posted():
            out = []
            while not app.d.q.empty():
                ch, text, embeds = app.d.q.get_nowait()
                if ch == "positions":
                    out.append([e["title"] for e in embeds])
            return out
        asyncio.run(app._post_positions())
        self.assertEqual(posted(), [["🪙 비트코인(BTC) · 현물", "🪙 솔라나(SOL) · 현물"]])
        fb.prices["BTCUSDT"] = "90000"                                             # 가격만 바뀜
        asyncio.run(app._post_positions())
        self.assertEqual(posted(), [])
        fb.balances["BTC"] = D("0.08")                                             # 현물을 더 삼
        asyncio.run(app._post_positions())
        self.assertEqual(len(posted()), 1)

    def test_checks_post_only_real_changes(self):
        """다시 켬 · 끊김 · 10분 점검: 지갑끼리 옮김 · Earn 이자 · 찌꺼기로는 안 올리고 입금 · 출금 · 놓친 체결은 올림 (10/22 06:30 일 뒤)."""
        from candleman.app import App
        from candleman.discord import Discord
        fb, fut = FakeBinance(), []

        async def positions():
            return [dict(p) for p in fut]

        async def account():
            return {"totalMarginBalance": "1000"}
        fb.positions, fb.account = positions, account
        app = App.__new__(App)
        app.b, app.state, app._pos_due = fb, {}, None
        app.d = Discord({"trade": "u1", "status": "u3", "positions": "u5"})
        app.spot = SpotFeed(app)

        def count():
            n = 0
            while not app.d.q.empty():
                n += app.d.q.get_nowait()[0] == "positions"
            return n
        def check(**change):
            for k, v in change.items():
                getattr(fb, k).update(v)
            asyncio.run(app._post_positions("check"))
            return count()
        self.assertEqual(check(), 1)                                               # 처음엔 올림 (지난 글이 없음)
        fb.balances.pop("SOL")
        self.assertEqual(check(flexible={"SOL": D("2")}), 0)                       # 솔라나를 현물 지갑에서 Earn 으로 옮김
        self.assertEqual(check(flexible={"SOL": D("2.0001")}), 0)                  # Earn 이자
        self.assertEqual(check(balances={"ETH": D("0.004")}), 0)                   # $6 어치 에어드랍 · 찌꺼기
        self.assertEqual(check(flexible={"SOL": D("2.07")}), 1)                    # 쌓인 이자가 1% · $10 를 넘음
        self.assertEqual(check(balances={"BTC": D("0.08")}), 1)                    # 입금 (또는 놓친 매수)
        fb.flexible.pop("SOL")
        self.assertEqual(check(), 1)                                               # 출금 (또는 놓친 매도) — 값은 지금 시세로 셈
        fut.append({"symbol": "EWYUSDT", "positionAmt": "20", "entryPrice": "152.83", "markPrice": "155", "notional": "3100",
                    "unRealizedProfit": "12.5", "isolatedMargin": "0", "positionSide": "BOTH"})   # 놓친 선물 체결
        self.assertEqual(check(), 1)
        asyncio.run(app._post_positions("fill"))                                   # 체결 뒤인데 바뀐 것이 없으면 안 올림
        self.assertEqual(count(), 0)


class RecvAliveTest(unittest.TestCase):
    """조용한 계정: 메시지가 없어도 서버 ping 이 오면 연결을 끊지 않고, ping 마저 끊기면 죽은 연결로 봄."""

    def test_pings_keep_connection_and_silence_times_out(self):
        from candleman.ws import WebSocket

        class W:
            def write(self, b):
                pass

            async def drain(self):
                pass

            def close(self):
                pass

            async def wait_closed(self):
                pass

        async def go():
            r = asyncio.StreamReader()
            ws = WebSocket(r, W())

            async def server():
                for _ in range(6):
                    await asyncio.sleep(0.1)
                    r.feed_data(b"\x89\x00")                                        # ping
                r.feed_data(b"\x81\x02hi")                                          # 글 'hi'
            asyncio.ensure_future(server())
            got = await ws.recv_alive(1.0)                                          # 메시지 없이 ping 만 와도 기다림 (느린 폰도 넉넉히)
            with self.assertRaises(asyncio.TimeoutError):
                await ws.recv_alive(0.3)                                            # 아무것도 안 오면 끊음
            return got
        self.assertEqual(asyncio.run(go()), "hi")


if __name__ == "__main__":
    unittest.main()
