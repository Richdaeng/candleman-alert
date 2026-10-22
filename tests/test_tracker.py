"""장부 시험 — 가짜 바이낸스 이벤트를 넣고 나오는 글을 봅니다. python -m unittest discover -s tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from candleman.tracker import Tracker  # noqa: E402

T0 = 1_792_500_000_000


def otu(i, S, o, x, X, q="0", p="0", l="0", L="0", rp="0", n="0", N="USDT", s="BTCUSDT", ps="BOTH", R=False, cp=False, c="web_x", ot=None, sp="0"):
    return {"e": "ORDER_TRADE_UPDATE", "E": 0, "T": 0,
            "o": {"s": s, "c": c, "S": S, "o": o, "f": "GTC", "q": q, "p": p, "ap": "0", "sp": sp, "x": x, "X": X, "i": i, "l": l, "z": "0",
                  "L": L, "N": N, "n": n, "T": 0, "t": 1, "R": R, "ps": ps, "cp": cp, "rp": rp, "ot": ot or o}}


def texts(res):
    return [m.text for m in res[0]]


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.t = Tracker()
        self.now = T0

    def ev(self, e, dt=0):
        self.now += dt
        return texts(self.t.on_event(e, self.now))

    def tick(self, dt):
        self.now += dt
        return texts(self.t.tick(self.now))

    def test_long_round_trip(self):
        # 1 지정가 매수를 걸어 둠 → 2초 뒤 '주문' 알림
        self.assertEqual(self.ev(otu(1, "BUY", "LIMIT", "NEW", "NEW", q="0.050", p="82000.0")), [])
        self.assertEqual(self.tick(1000), [])
        out = self.tick(1100)
        self.assertEqual(out, ["📝 주문 걸어 둠 · 비트코인(BTC) 롱 진입 (오르면 이익)\n$82,000에 0.05 BTC · 약 $4,100어치(레버리지 포함) · 아직 체결 전"])
        # 체결
        out = self.ev(otu(1, "BUY", "LIMIT", "TRADE", "FILLED", q="0.050", p="82000.0", l="0.050", L="82000.0", n="0.82"), 60_000)
        self.assertEqual(out, ["🟢 비트코인(BTC) 롱 진입 · 오르면 이익\n$82,000에 0.05 BTC · 약 $4,100어치(레버리지 포함) · 걸어 둔 주문 체결"])
        # 2 시장가로 추가 — '주문' 알림 없이 체결만
        self.assertEqual(self.ev(otu(2, "BUY", "MARKET", "NEW", "NEW", q="0.030"), 1000), [])
        out = self.ev(otu(2, "BUY", "MARKET", "TRADE", "FILLED", q="0.030", l="0.030", L="81500.0"), 10)
        self.assertEqual(out, ["➕ 비트코인(BTC) 롱 추가 진입 · 오르면 이익\n"
                               "$81,500에 0.03 BTC 더 · 평균 가격 $81,812.50 · 모두 0.08 BTC · 약 $6,545어치(레버리지 포함) · 바로 체결(시장가)"])
        # 3 일부 청산
        out = self.ev(otu(3, "SELL", "MARKET", "TRADE", "FILLED", q="0.040", l="0.040", L="83000.0", rp="47.50"), 3_600_000)
        self.assertEqual(out, ["🟠 비트코인(BTC) 롱 일부 청산 · 이익 +$47.50\n$83,000에 0.04 BTC 정리 · 남은 0.04 BTC · 바로 체결(시장가)"])
        # 4 지정가로 나머지 청산 — 2초 안에 체결되면 '주문' 글은 건너뜀
        self.assertEqual(self.ev(otu(4, "SELL", "LIMIT", "NEW", "NEW", q="0.040", p="83000.0", R=True), 7_200_000), [])
        out = self.ev(otu(4, "SELL", "LIMIT", "TRADE", "FILLED", q="0.040", p="83000.0", l="0.040", L="83000.0", rp="47.50", R=True), 500)
        self.assertEqual(out, ["⬜ 비트코인(BTC) 롱 청산 · 이익 +$47.50\n$83,000에 남은 0.04 BTC 모두 정리 · 3시간 보유 · 걸어 둔 주문 체결\n"
                               "이번 거래 전체 이익 +$95.00"])
        self.assertEqual(self.tick(5000), [])

    def test_flip_and_short(self):
        self.ev(otu(10, "BUY", "MARKET", "TRADE", "FILLED", q="0.02", l="0.02", L="80000.0"))
        out = self.ev(otu(11, "SELL", "MARKET", "TRADE", "FILLED", q="0.05", l="0.05", L="79000.0", rp="-20.00"), 60_000)
        self.assertEqual(out, ["⬜ 비트코인(BTC) 롱 청산 · 손실 −$20.00\n$79,000에 0.02 BTC 모두 정리 · 1분 보유 · 바로 체결(시장가)",
                               "🔴 비트코인(BTC) 숏 진입 · 내리면 이익\n$79,000에 0.03 BTC · 약 $2,370어치(레버리지 포함) · 바로 체결(시장가)"])

    def test_cancel_and_partial(self):
        self.ev(otu(20, "SELL", "LIMIT", "NEW", "NEW", q="0.050", p="90000.0", s="BTCUSDT"))
        self.assertEqual(self.tick(2100), ["📝 주문 걸어 둠 · 비트코인(BTC) 숏 진입 (내리면 이익)\n$90,000에 0.05 BTC · 약 $4,500어치(레버리지 포함) · 아직 체결 전"])
        self.assertEqual(self.ev(otu(20, "SELL", "LIMIT", "TRADE", "PARTIALLY_FILLED", q="0.050", p="90000.0", l="0.010", L="90000.0"), 1000), [])
        out = self.tick(5100)
        self.assertEqual(out, ["🔴 비트코인(BTC) 숏 진입 · 내리면 이익\n$90,000에 0.01 BTC · 약 $900어치(레버리지 포함) · 걸어 둔 주문 체결"])
        out = self.ev(otu(20, "SELL", "LIMIT", "CANCELED", "CANCELED", q="0.050", p="90000.0"), 10_000)
        self.assertEqual(out, ["✖️ 주문 취소 · 비트코인(BTC) 숏 진입 · $90,000에 0.05 BTC 중 0.01 BTC 체결 · 남은 0.04 BTC 취소"])

    def test_liquidation(self):
        self.ev(otu(30, "BUY", "MARKET", "TRADE", "FILLED", q="1", l="1", L="2500.00", s="ETHUSDT"))
        out = self.ev(otu(31, "SELL", "LIMIT", "TRADE", "FILLED", q="1", l="1", L="2100.00", rp="-400.00", s="ETHUSDT", c="autoclose-1234"), 1000)
        self.assertEqual(out, ["⚠️ 이더리움(ETH) 롱 강제 청산 · 손실 −$400.00\n증거금이 모자라 거래소가 자동으로 정리 · $2,100에 1 ETH · 1초 보유"])

    def test_partial_liquidation(self):
        self.ev(otu(32, "BUY", "MARKET", "TRADE", "FILLED", q="3", l="3", L="2500.00", s="ETHUSDT"))
        out = self.ev(otu(33, "SELL", "LIMIT", "TRADE", "FILLED", q="1", l="1", L="2100.00", rp="-400.00", s="ETHUSDT", c="autoclose-9"), 1000)
        self.assertEqual(out, ["⚠️ 이더리움(ETH) 롱 일부 강제 청산 · 손실 −$400.00\n증거금이 모자라 거래소가 자동으로 정리 · $2,100에 1 ETH · 남은 2 ETH"])

    def test_account_update_waits_for_late_fill(self):
        # 포지션 이벤트가 체결 이벤트보다 먼저 와도 진입을 맞게 가름
        au = {"e": "ACCOUNT_UPDATE", "a": {"m": "ORDER", "B": [], "P": [{"s": "SOLUSDT", "pa": "10", "ep": "150.00", "ps": "BOTH"}]}}
        self.assertEqual(self.ev(au), [])
        out = self.ev(otu(40, "BUY", "MARKET", "TRADE", "FILLED", q="10", l="10", L="150.00", s="SOLUSDT"), 200)
        self.assertEqual(out, ["🟢 솔라나(SOL) 롱 진입 · 오르면 이익\n$150에 10 SOL · 약 $1,500어치(레버리지 포함) · 바로 체결(시장가)"])
        self.tick(4000)
        self.assertEqual(str(self.t.positions[("SOLUSDT", "BOTH")].amt), "10")

    def test_funding_and_algo(self):
        res = self.t.on_event({"e": "ACCOUNT_UPDATE", "a": {"m": "FUNDING_FEE", "S": "BTCUSDT", "B": [{"a": "USDT", "wb": "1", "cw": "1", "bc": "-0.1234"}]}}, self.now)
        self.assertEqual(res[1][0]["kind"], "funding")
        self.assertEqual(res[1][0]["amount"], "-0.1234")
        self.ev(otu(776, "BUY", "MARKET", "TRADE", "FILLED", q="0.02", l="0.02", L="80000.0"))      # 롱을 들고 있는 상태
        algo = {"e": "ALGO_UPDATE", "o": {"aid": 777, "X": "NEW", "o": "STOP_MARKET", "s": "BTCUSDT", "S": "SELL", "q": "0.01", "tp": "78000.0", "ps": "BOTH", "R": True}}
        self.assertEqual(self.ev(algo), [])
        self.assertEqual(self.tick(2100), ["📝 스탑 로스 걸어 둠 · 비트코인(BTC) 롱 청산\n"
                                           "가격이 $78,000에 닿으면 시장가로 0.01 BTC 정리 · 약 $780어치(레버리지 포함) · 아직 발동 전"])
        algo["o"]["X"] = "CANCELED"
        self.assertEqual(self.ev(algo, 1000), ["✖️ 스탑 로스 취소 · 비트코인(BTC) 롱 청산 · 가격이 $78,000에 닿으면 시장가로 0.01 BTC 정리"])

    def test_triggered_stop_named_by_result(self):
        """10/22: 스탑이 발동해 생긴 시장가 주문이 '바로 체결(시장가)'로만 나옴 → 발동과 짝지음. 진입가보다 유리한 스탑(본절)은
        '이익 보전 스탑'("스탑로스인데 익절일 수도 있잖아?") · 불리한 쪽은 '스탑 로스'."""
        self.ev(otu(80, "BUY", "MARKET", "TRADE", "FILLED", q="65.13", l="65.13", L="153.44", s="EWYUSDT"))
        stop = {"e": "ALGO_UPDATE", "o": {"aid": 81, "X": "NEW", "o": "STOP_MARKET", "s": "EWYUSDT", "S": "SELL", "q": "32.56", "tp": "154",
                                          "ps": "BOTH", "R": True}}
        self.ev(stop, 100)
        self.assertEqual(self.tick(2100), ["📝 이익 보전 스탑 걸어 둠 · 한국 주식 ETF(EWY) 롱 청산\n"
                                           "가격이 $154에 닿으면 시장가로 32.56주 정리 · 약 $5,014어치(레버리지 포함) · 아직 발동 전"])
        stop["o"]["X"] = "TRIGGERING"                                      # 발동 (실제 주문 번호는 아직 없음)
        self.assertEqual(self.ev(stop, 60_000), [])
        self.ev(otu(82, "SELL", "MARKET", "NEW", "NEW", q="32.56", s="EWYUSDT", R=True), 50)      # 원래 종류(ot) 없이 새 시장가 주문
        out = self.ev(otu(82, "SELL", "MARKET", "TRADE", "FILLED", q="32.56", l="32.56", L="154.01", rp="18.25", s="EWYUSDT", R=True), 50)
        self.assertEqual(out, ["🟠 한국 주식 ETF(EWY) 롱 일부 청산 · 이익 +$18.25\n$154.01에 32.56주 정리 · 남은 32.57주 · 이익 보전 스탑 발동"])
        stop["o"].update(X="FINISHED", ai="82")
        self.assertEqual(self.ev(stop, 50), [])
        # 손으로 넣은 시장가 주문은 그대로 '바로 체결(시장가)'
        out = self.ev(otu(83, "SELL", "MARKET", "TRADE", "FILLED", q="10", l="10", L="155.00", rp="5", s="EWYUSDT"), 120_000)
        self.assertTrue(out[0].endswith("· 바로 체결(시장가)"))
        # 진입가보다 불리한 쪽의 스탑이 손실로 끝나면 '스탑 로스'
        loss = {"e": "ALGO_UPDATE", "o": {"aid": 88, "X": "NEW", "o": "STOP_MARKET", "s": "EWYUSDT", "S": "SELL", "q": "0", "tp": "150",
                                          "ps": "BOTH", "cp": True}}
        self.ev(loss, 100)
        self.assertTrue(self.tick(2100)[0].startswith("📝 스탑 로스 걸어 둠 · 한국 주식 ETF(EWY) 롱 청산"))
        loss["o"].update(X="TRIGGERED", ai="89")
        self.ev(loss, 60_000)
        out = self.ev(otu(89, "SELL", "MARKET", "TRADE", "FILLED", q="22.57", l="22.57", L="149.90", rp="-79.90", s="EWYUSDT", cp=True), 50)
        self.assertTrue(out[0].startswith("⬜ 한국 주식 ETF(EWY) 롱 청산 · 손실 −$79.90"))
        self.assertIn("· 스탑 로스 발동", out[0])

    def test_trigger_event_after_order_and_take_profit(self):
        """발동 소식(실제 주문 번호 포함)이 새 주문보다 늦게 와도 체결 전이면 짝지음 · 익절은 '익절 주문 발동'."""
        self.ev(otu(84, "SELL", "MARKET", "TRADE", "FILLED", q="2", l="2", L="3000.00", s="ETHUSDT"))
        tp = {"e": "ALGO_UPDATE", "o": {"aid": 85, "X": "NEW", "o": "TAKE_PROFIT_MARKET", "s": "ETHUSDT", "S": "BUY", "q": "0", "tp": "2800",
                                        "ps": "BOTH", "cp": True}}
        self.ev(tp, 100)
        self.tick(2100)
        self.ev(otu(86, "BUY", "MARKET", "NEW", "NEW", q="2", s="ETHUSDT", cp=True), 60_000)
        tp["o"].update(X="TRIGGERED", ai="86")
        self.ev(tp, 30)
        out = self.ev(otu(86, "BUY", "MARKET", "TRADE", "FILLED", q="2", l="2", L="2799.50", rp="401.00", s="ETHUSDT", cp=True), 30)
        self.assertTrue(out[0].startswith("⬜ 이더리움(ETH) 숏 청산 · 이익 +$401.00"))
        self.assertIn("· 익절 주문 발동", out[0])
        self.assertEqual(self.t.triggers, [])                                # 짝을 지었으니 기다리는 발동이 남지 않음

    def test_orphan_stop_not_announced(self):
        # 포지션을 다 정리하면 바이낸스가 스탑 로스 · 익절을 저절로 지움 → 그 만료 · 취소는 알리지 않음
        self.ev(otu(90, "BUY", "MARKET", "TRADE", "FILLED", q="0.05", l="0.05", L="82000.0"))
        stop = {"e": "ALGO_UPDATE", "o": {"aid": 91, "X": "NEW", "o": "STOP_MARKET", "s": "BTCUSDT", "S": "SELL", "q": "0", "tp": "80000", "ps": "BOTH", "cp": True}}
        self.ev(stop, 100)
        self.assertEqual(len(self.tick(2100)), 1)
        tp = otu(92, "SELL", "LIMIT", "NEW", "NEW", q="0.05", p="86000.0", R=True)
        self.ev(tp, 100)
        self.assertEqual(len(self.tick(2100)), 1)
        out = self.ev(otu(93, "SELL", "MARKET", "TRADE", "FILLED", q="0.05", l="0.05", L="83000.0", rp="50.00"), 60_000)
        self.assertTrue(out[0].startswith("⬜ 비트코인(BTC) 롱 청산 · 이익 +$50.00"))
        stop["o"]["X"] = "EXPIRED"
        self.assertEqual(self.ev(stop, 100), [])
        self.assertEqual(self.ev(otu(92, "SELL", "LIMIT", "CANCELED", "CANCELED", q="0.05", p="86000.0", R=True), 100), [])
        # 포지션이 없어도 진입 주문 취소는 그대로 알림
        self.ev(otu(94, "BUY", "LIMIT", "NEW", "NEW", q="0.01", p="70000.0"), 100)
        self.tick(2100)
        self.assertEqual(self.ev(otu(94, "BUY", "LIMIT", "CANCELED", "CANCELED", q="0.01", p="70000.0"), 100),
                         ["✖️ 주문 취소 · 비트코인(BTC) 롱 진입 · $70,000에 0.01 BTC"])

    def test_take_profit_close_all(self):
        self.ev(otu(60, "SELL", "MARKET", "TRADE", "FILLED", q="2", l="2", L="3000.00", s="ETHUSDT"))
        algo = {"e": "ALGO_UPDATE", "o": {"aid": 778, "X": "NEW", "o": "TAKE_PROFIT_MARKET", "s": "ETHUSDT", "S": "BUY", "q": "0", "tp": "2800", "ps": "BOTH", "cp": True}}
        self.ev(algo, 100)
        self.assertEqual(self.tick(2100), ["📝 익절 주문 걸어 둠 · 이더리움(ETH) 숏 청산\n가격이 $2,800에 닿으면 시장가로 모두 정리 · 아직 발동 전"])

    def test_stock_units_and_unknown_symbol(self):
        out = self.ev(otu(70, "SELL", "MARKET", "TRADE", "FILLED", q="20", l="20", L="154.90", s="EWYUSDT"))
        self.assertEqual(out, ["🔴 한국 주식 ETF(EWY) 숏 진입 · 내리면 이익\n$154.90에 20주 · 약 $3,098어치(레버리지 포함) · 바로 체결(시장가)"])
        out = self.ev(otu(71, "BUY", "MARKET", "TRADE", "FILLED", q="20", l="20", L="156.10", s="EWYUSDT", rp="0"), 60_000)
        self.assertEqual(out, ["⬜ 한국 주식 ETF(EWY) 숏 청산 · 손익 $0.00\n$156.10에 20주 모두 정리 · 1분 보유 · 바로 체결(시장가)"])
        out = self.ev(otu(72, "BUY", "MARKET", "TRADE", "FILLED", q="5", l="5", L="1.2345", s="FOOUSDT"))
        self.assertEqual(out, ["🟢 FOO 롱 진입 · 오르면 이익\n$1.2345에 5 FOO · 약 $6.17어치(레버리지 포함) · 바로 체결(시장가)"])

    def test_hedge_mode_short(self):
        self.ev(otu(50, "SELL", "MARKET", "TRADE", "FILLED", q="2", l="2", L="3000.00", s="ETHUSDT", ps="SHORT"))
        out = self.ev(otu(51, "BUY", "MARKET", "TRADE", "FILLED", q="2", l="2", L="2900.00", s="ETHUSDT", ps="SHORT", rp="200.00"), 120_000)
        self.assertEqual(out, ["⬜ 이더리움(ETH) 숏 청산 · 이익 +$200.00\n$2,900에 2 ETH 모두 정리 · 2분 보유 · 바로 체결(시장가)"])


if __name__ == "__main__":
    unittest.main()
