"""시그널 시험 — BTC 판정(규칙 손계산 · 다음 판정 구간 · 기록) · 신규 추격(표 읽기 · 1위 · 문턱 · 다음 날 판정) · 글 모양 · 울리는 때 · 운영자 DM 으로만 가는지 · 다른 채널로 새지 않는지."""
import asyncio
import datetime as dt
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from candleman import config, fmt, ipobot, signalbot, store  # noqa: E402
from candleman.app import App  # noqa: E402
from candleman.bot import Bot, dm_owner, with_tag  # noqa: E402
from candleman.discord import Discord  # noqa: E402

DAY = signalbot.DAY
T0 = signalbot.RECORD_FROM - 400 * DAY     # 맨 앞 일봉의 마감 시각


def rows_of(closes, start=T0):
    """종가들 → 바이낸스 일봉 모양 (시가 = 전날 종가)."""
    out = []
    for i, x in enumerate(closes):
        o = closes[i - 1] if i else x
        end = start + i * DAY
        out.append([end - DAY, str(o), str(max(o, x)), str(min(o, x)), str(x), "1", end - 1, "1", 1, "1", "1", "0"])
    return out


def fake_fetch(rows):
    def fetch(url):
        return json.dumps(rows if "endTime" not in url else [])
    return fetch


def state_of(closes, n=None):
    rows = rows_of(closes)
    return signalbot.read(rows, n or int(rows[-1][6]) + 2)


def panel_of(msg):
    """봇의 선택 카드의 글 전체."""
    return "\n".join(f["name"] + "\n" + f["value"] for f in msg["embeds"][2]["fields"])


class RuleTest(unittest.TestCase):
    def test_closed_end_is_11pm_kst(self):
        t = dt.datetime(2026, 11, 1, 18, 17, tzinfo=fmt.KST)
        end = signalbot.closed_end(int(t.timestamp() * 1000))
        self.assertEqual(fmt.kst(end).strftime("%m/%d %H:%M"), "10/31 23:00")
        t = dt.datetime(2026, 11, 1, 23, 0, tzinfo=fmt.KST)                      # 마감 시각 그 순간에는 새 봉이 닫힘
        self.assertEqual(fmt.kst(signalbot.closed_end(int(t.timestamp() * 1000))).strftime("%m/%d %H:%M"), "11/01 23:00")

    def test_sma_needs_full_window(self):
        self.assertEqual(signalbot.sma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])
        self.assertEqual(signalbot.sma([1, 2], 3), [None, None])

    def test_above_50_is_on_and_above_100_is_safe(self):
        J = signalbot.judge([100.0 + i for i in range(120)])                    # 곧게 오름
        self.assertTrue(J["on"][-1])
        self.assertEqual(J["bold"][-1], 1.0)
        self.assertEqual(J["safe"][-1], 0.5)
        J = signalbot.judge([300.0 - i for i in range(120)])                    # 곧게 내림: RSI 는 0 이지만 처음 판정부터 낮아서 되돌림 켜짐
        self.assertTrue(J["hold"])
        self.assertTrue(J["on"][-1])
        self.assertEqual(J["bold"][-1], 1.0)
        self.assertEqual(J["safe"][-1], 0.0)                                    # 안전은 100일선 아래라 현금

    def test_rsi_state_turns_on_below_10_and_off_above_70(self):
        c = [100.0] * 60 + [100.0 - 2 * i for i in range(1, 9)]                 # 평평하다 내림 → RSI 가 0 으로 → 되돌림 켜짐
        J = signalbot.judge(c)
        self.assertLess(J["rsi"], 10)
        self.assertTrue(J["hold"])
        self.assertTrue(J["on"][-1])                                            # 50일선 아래인데도 켜짐
        c = c + [c[-1] + 1, c[-1] + 2, c[-1] + 3]                               # 조금 되돌림: 10 ~ 70 이면 그대로 켜짐
        J = signalbot.judge(c)
        self.assertTrue(J["hold"])
        c = c + [c[-1] + 2 * i for i in range(1, 6)]                            # 많이 되돌림: 70 위에서 꺼짐
        J = signalbot.judge(c)
        self.assertGreater(J["rsi"], 70)
        self.assertFalse(J["hold"])

    def test_rsi_by_hand(self):
        J = signalbot.judge([10, 11, 10, 12, 11, 13])
        au = ad = 0.0
        for u, d in ((1, 0), (0, 1), (2, 0), (0, 1), (2, 0)):
            au += (u - au) / 5
            ad += (d - ad) / 5
        self.assertAlmostEqual(J["au"], au)
        self.assertAlmostEqual(J["ad"], ad)
        self.assertAlmostEqual(J["rsi"], 100 - 100 / (1 + au / ad))

    def test_after_matches_the_real_next_judgment(self):
        c = [100 + 30 * ((i * 7) % 13 - 6) / 6 + i * 0.1 for i in range(160)]
        J = signalbot.judge(c)
        for j in range(-20, 21):
            x = c[-1] * (1 + j / 100)
            N = signalbot.judge(c + [x])
            self.assertEqual(signalbot.after(c, J, x), (N["on"][-1], N["safe"][-1] > 0), j)

    def test_bands_word_the_boundaries(self):
        c = [100.0] * 49 + [100.0] * 60
        c[-1] = 101.0
        J = signalbot.judge(c)
        rs = signalbot.bands(c, J, 0)
        self.assertEqual([r[2] for r in rs], [True, False, True])               # RSI 과매도 · 그 위 · 50일선 위
        text = signalbot.band_text(rs, "현물 롱", "선물 숏 1배")
        self.assertRegex(text, r"^\$\d+ 아래 = 현물 롱 · \$\d+ ~ \$\d+ = 선물 숏 1배 \(적중 26%\) · \$\d+ 위 = 현물 롱$")    # 신호가 나는 구간에만
        self.assertEqual(signalbot.band_text([[1, 2, True]], "롱", "숏"), "지금 종가 ±30% 안에서는 롱")

    def test_why_names_the_path(self):
        c = [100.0] * 300 + [101.0]                                              # 평평하다 오름: 50일선 위로 켜짐 · 전날 100일선 아래
        J = signalbot.judge(c)
        self.assertEqual(signalbot.why(J, "bold", len(c) - 1), "50일선 돌파")
        self.assertEqual(signalbot.why(J, "safe", len(c) - 1), "100일선 돌파")
        c = [100.0 + (1 if i % 2 else -1) for i in range(302)] + [96.0]          # 위아래로 오가다 이평 아래로 (RSI 는 10 위)
        J = signalbot.judge(c)
        self.assertEqual(J["bold"][-1], -1.0)
        self.assertEqual(signalbot.why(J, "bold", len(c) - 1), "50일선 이탈")
        self.assertEqual(signalbot.why(J, "safe", len(c) - 1), "규칙 꺼짐")

    def test_rsi_paths(self):
        c = [100.0] * 60 + [100.0 - 2 * i for i in range(1, 9)]                  # 50일선 아래인데 RSI 과매도로 켜짐
        J = signalbot.judge(c)
        i = next(k for k in range(1, len(c)) if J["bold"][k] > 0 and J["bold"][k - 1] < 0)
        self.assertEqual(signalbot.why(J, "bold", i), "RSI 되돌림")
        c = c + [c[-1] + 2 * k for k in range(1, 12)]                            # 많이 되돌림: 70 위에서 꺼지는데 아직 50일선 아래
        J = signalbot.judge(c)
        i = next(k for k in range(61, len(c)) if J["bold"][k] < 0 and J["bold"][k - 1] > 0)
        self.assertEqual(signalbot.why(J, "bold", i), "되돌림 끝남")

    def test_coming_matches_the_real_next_signal(self):
        for shift in range(0, 40, 3):
            c = [100 + 30 * (((i + shift) * 7) % 13 - 6) / 6 + i * 0.1 for i in range(160)]
            J = signalbot.judge(c)
            for j in range(-30, 31):
                x = c[-1] * (1 + j / 100)
                on = signalbot.after(c, J, x)
                N = signalbot.judge(c + [x])
                for k, key in ((0, "bold"), (1, "safe")):
                    want = signalbot.why(N, key, len(c)) if N[key][-1] != N[key][-2] else None
                    self.assertEqual(signalbot.coming(J, k, x, on), want, (shift, j, key))

    def test_every_path_has_a_probability(self):
        self.assertEqual(sorted(signalbot.HIT), sorted(["50일선 돌파", "RSI 되돌림", "되돌림 끝남", "50일선 이탈", "규칙 켜짐", "100일선 돌파", "규칙 꺼짐", "100일선 이탈"]))
        for p, up, down, n in signalbot.HIT.values():
            self.assertTrue(0 < p < 1 and up.startswith("+") and down.startswith("−") and n >= 10)
        self.assertEqual(signalbot.chance("규칙 켜짐"), "🎯 적중 확률 **23%** · 규칙 켜짐 · 이기면 평균 +41.4% · 지면 평균 −1.4% · 지난 29건")

    def test_record_counts_closed_runs_after_fees(self):
        pos = [1.0, 1.0, -1.0, -1.0, 1.0]
        c = [100.0, 110.0, 110.0, 99.0, 99.0]
        r = signalbot.record(pos, c, 0)
        self.assertEqual((r["n"], r["win"]), (2, 2))                            # +10% 롱 · +10% 숏
        want = (1 - .001) * 1.1 * (1 - .002) * 1.1 * (1 - .002) - 1
        self.assertAlmostEqual(r["ret"], want)
        r = signalbot.record([0.0, 0.5, 0.5], [100.0, 100.0, 110.0], 0)
        self.assertEqual(r["n"], 0)                                             # 아직 안 닫힘
        self.assertAlmostEqual(r["ret"], (1 - .0005) * 1.05 - 1)


class ComposeTest(unittest.TestCase):
    def flat(self, last):
        return [100.0] * 200 + last

    def test_quiet_day_does_not_ring(self):
        msg = signalbot.compose(state_of([100.0 + i for i in range(420)]))
        self.assertFalse(msg["ring"])
        self.assertEqual(msg["content"], "📡 BTC 판정 · 오늘은 자리 그대로")
        self.assertEqual(len(msg["embeds"]), 3)
        self.assertEqual((msg["embeds"][0]["title"], msg["embeds"][0]["author"]["name"]), ("🛡️ 안전 진입", "낙폭 우선"))
        self.assertEqual((msg["embeds"][1]["title"], msg["embeds"][1]["author"]["name"]), ("🚀 과감한 수익", "수익 우선"))
        self.assertTrue(msg["embeds"][0]["description"].startswith("## +989%\n"))
        self.assertEqual([f["value"] for f in msg["embeds"][1]["fields"]][:5], ["169건", "50건 (30%)", "+5.55%", "−57.5%", "0일"])
        self.assertIn("봇의 선택", msg["embeds"][2]["title"])
        self.assertIn("📌 유지 · 자금 50% 현물 롱", panel_of(msg))
        self.assertIn("📌 유지 · 현물 롱", panel_of(msg))
        self.assertNotIn("적중 확률", panel_of(msg))
        self.assertEqual(msg["embeds"][2]["color"], signalbot.PANEL)
        self.assertIn("주문 안 냄", msg["embeds"][2]["footer"]["text"])
        self.assertIn("아무 날 진입이 더 높음", msg["embeds"][2]["footer"]["text"])

    def test_turn_day_rings_with_entry_and_exit_words(self):
        c = [100.0 + (1 if i % 2 else -1) for i in range(302)]                   # 위아래로 오가다 이평 위에서
        msg = signalbot.compose(state_of(c + [96.0]))                            # 이평 아래로 (RSI 는 10 위라 되돌림 안 켜짐)
        self.assertTrue(msg["ring"])
        panel = panel_of(msg)
        self.assertIn("🔴 **청산 신호** · 현물 전부 정리", panel)
        self.assertIn("🔴 **청산 신호** · 현물 청산 → 선물 숏 1배", panel)
        self.assertEqual(panel.count("🎯 적중 확률"), 2)
        self.assertEqual(msg["content"], "🔔 BTC 신호 · 🛡️ 안전 청산 적중 25% · 🚀 과감 선물 숏 전환 적중 26%")
        self.assertEqual(msg["embeds"][2]["color"], signalbot.cards.RED)

    def test_big_drop_keeps_bold_long_by_rsi(self):
        up = [100.0 + i for i in range(420)]
        msg = signalbot.compose(state_of(up + [up[-1] * 0.6]))                   # 하루 −40%: RSI 가 10 아래로 → 과감은 되돌림으로 유지 · 안전은 100일선 아래라 청산
        panel = panel_of(msg)
        self.assertIn("🔴 **청산 신호** · 현물 전부 정리", panel)
        self.assertIn("📌 유지 · 현물 롱", panel)
        self.assertIn("RSI 되돌림 켜짐", panel)

    def test_entry_day_rings(self):
        msg = signalbot.compose(state_of([100.0] * 300 + [101.0]))                # 평평하다(꺼짐) 오름: 50일선 · 100일선 위로
        self.assertTrue(msg["ring"])
        panel = panel_of(msg)
        self.assertIn("🟢 **진입 신호** · 자금 50% 현물 롱", panel)
        self.assertIn("🟢 **진입 신호** · 선물 숏 청산 → 현물 롱", panel)
        self.assertIn("🎯 적중 확률 **32%** · 100일선 돌파", panel)
        self.assertIn("🎯 적중 확률 **21%** · 50일선 돌파", panel)
        self.assertEqual(msg["content"], "🔔 BTC 신호 · 🛡️ 안전 진입 적중 32% · 🚀 과감 현물 롱 전환 적중 21%")
        self.assertEqual(msg["embeds"][2]["color"], signalbot.GREEN)

    def test_post_fits_discord_limits(self):
        for closes in ([100.0] * 300 + [101.0], [100.0 + (1 if i % 2 else -1) for i in range(302)] + [96.0], [100.0 + i for i in range(420)]):
            msg = signalbot.compose(state_of(closes))
            total = 0
            for e in msg["embeds"]:
                total += len(e.get("title", "")) + len(e.get("description", "")) + len(e.get("footer", {}).get("text", "")) + len(e.get("author", {}).get("name", ""))
                for f in e["fields"] if "fields" in e else []:
                    self.assertLessEqual(len(f["value"]), 1024)
                    total += len(f["name"]) + len(f["value"])
            self.assertLess(total, 6000)
            self.assertLessEqual(len(msg["embeds"][2]["footer"]["text"]), 2048)

    def test_turn_gain_uses_next_open(self):
        pos = [0.0, 0.5, 0.5, 0.5]
        o = [1.0, 1.0, 100.0, 120.0]
        c = [1.0, 100.0, 120.0, 150.0]
        t = signalbot.turn(pos, o, c, [1, 2, 3, 4])
        self.assertAlmostEqual(t["entry"], 100.0)                                # 판정 다음 봉 시가
        self.assertAlmostEqual(t["gain"], 0.25)                                  # 자금의 절반만 들어감
        self.assertFalse(t["fresh"])
        t = signalbot.turn([0.5, 0.5, 0.0], [1.0, 10.0, 20.0], [10.0, 20.0, 15.0], [1, 2, 3])
        self.assertTrue(t["fresh"])
        self.assertAlmostEqual(t["closed"], 0.5 * (15.0 / 10.0 - 1))

    def test_record_starts_at_the_frozen_day(self):
        days = (signalbot.RECORD_FROM - T0) // DAY
        st = state_of([100.0 + i for i in range(int(days))])                     # 시작 전 → 빈 기록
        self.assertEqual(st["safe"]["since"], {"n": 0, "win": 0, "ret": 0.0})
        st = state_of([100.0 + i for i in range(int(days) + 5)])
        self.assertGreater(st["bold"]["since"]["ret"], 0)

    def test_build_waits_for_the_closed_bar(self):
        closes = [100.0 + i for i in range(420)]
        rows = rows_of(closes)
        n = int(rows[-1][6]) + DAY + 60_000                                      # 다음 봉이 닫혔는데 받은 자료에 없음
        with self.assertRaises(ValueError):
            signalbot.build(n, fake_fetch(rows))
        self.assertIn("embeds", signalbot.build(int(rows[-1][6]) + 60_000, fake_fetch(rows)))

    def test_open_bar_is_not_used(self):
        closes = [100.0 + i for i in range(420)]
        rows = rows_of(closes + [1.0])                                           # 아직 안 닫힌 봉의 값이 판정에 들어가면 안 됨
        st = signalbot.read(rows, int(rows[-2][6]) + 1000)
        self.assertEqual(st["close"], closes[-1])


KIND_LIST = """
<tr onclick="fnDetailView('52340','1234')"><td>인텔리빅스</td><td>2026-10-30</td><td>신규상장</td><td>주권</td><td>소프트웨어</td><td>한국</td><td>주관</td><td>500</td><td>1,000</td><td>100</td><td>보통주</td><td>1,000</td></tr>
<tr onclick="fnDetailView('47900','1235')"><td>어떤제9호스팩</td><td>2026-10-20</td><td>신규상장</td><td>주권</td><td>금융</td><td>한국</td><td>주관</td><td>100</td><td>2,000</td><td>100</td><td>기업인수목적회사</td><td>1,000</td></tr>
"""
KIND_PLAN = """
<tr><td>회사명</td></tr>
<tr><td><a href="#">래블업</a></td><td>2026-05-01</td><td>2026-10-10~2026-10-16</td><td>2026-10-20~2026-10-21</td><td>2026-10-23</td><td>30,000</td><td>100</td><td>2026-11-03</td><td>주관</td></tr>
<tr><td>영광</td><td>2026-05-01</td><td>-</td><td>-</td><td>-</td><td>17,000</td><td>100</td><td>2026-11-02</td><td>주관</td></tr>
<tr><td>아이엠스팩1호</td><td>2026-05-01</td><td>-</td><td>-</td><td>-</td><td>2,000</td><td>100</td><td>2026-11-04</td><td>주관</td></tr>
<tr><td>일정미정</td><td>2026-05-01</td><td>-</td><td>-</td><td>-</td><td>-</td><td>-</td><td>-</td><td>주관</td></tr>
"""


class KindTest(unittest.TestCase):
    def test_listed_table_marks_spac_and_builds_code(self):
        rows = ipobot.parse_listed(KIND_LIST)
        self.assertEqual(rows[0], {"name": "인텔리빅스", "code": "A523400", "listed": "2026-10-30", "spac": False})
        self.assertTrue(rows[1]["spac"])
        with mock.patch.object(ipobot, "parse_listed", return_value=[{"name": "x", "code": "A1", "listed": "d", "spac": i % 2 == 1} for i in range(100)]):
            pages = []

            def post(url, body):
                pages.append(body["pageIndex"])
                if len(pages) == 2:
                    ipobot.parse_listed.return_value = []
                return ""
            self.assertEqual(len(ipobot.listed(dt.date(2026, 10, 30), post)), 50)   # 100행이면 다음 쪽 · 스팩은 뺌
        self.assertEqual(pages, ["1", "2"])

    def test_plan_table_sorted_without_spac(self):
        self.assertEqual(ipobot.parse_plan(KIND_PLAN), [("2026-11-02", "영광"), ("2026-11-03", "래블업")])

    def test_plan_failure_is_not_fatal(self):
        def post(url, body):
            raise OSError("안 됨")
        self.assertEqual(ipobot.plan(dt.date(2026, 11, 2), post), [])


def days_row(date, close, value):
    return {"date": date + " 00:00:00", "tradePrice": close, "accTradePrice": value}


class QuoteTest(unittest.TestCase):
    def test_days_row_first(self):
        def get(url, code=None):
            self.assertIn("/days?", url)
            return json.dumps({"data": [days_row("2026-10-30", 31000, 1.19e12)]})
        self.assertEqual(ipobot.quote("A1", "2026-10-30", get), {"close": 31000.0, "value": 1.19e12, "live": False})

    def test_falls_back_to_quote_on_the_same_day(self):
        def get(url, code=None):
            if "/days?" in url:
                return json.dumps({"data": [days_row("2026-10-29", 1, 1)]})
            return json.dumps({"tradeDate": "20261030", "regularTradePrice": 31000, "tradePrice": 31500, "accTradePrice": 1.35e12})
        self.assertEqual(ipobot.quote("A1", "2026-10-30", get), {"close": 31000.0, "value": 1.35e12, "live": True})

    def test_no_trade_that_day(self):
        def get(url, code=None):
            return json.dumps({"data": [], "tradeDate": "20261029", "tradePrice": 1, "accTradePrice": 1})
        self.assertIsNone(ipobot.quote("A1", "2026-10-30", get))


class JudgeTest(unittest.TestCase):
    TP, SL = 0.10, -0.06

    def j(self, *bars):
        return ipobot.judge(list(bars), 100.0, self.TP, self.SL)

    def test_gap_up_fills_at_open(self):
        self.assertEqual(self.j(("09:00", 115, 120, 110, 118)), ("tp", 115 / 100 - 1))

    def test_gap_down_fills_at_open(self):
        self.assertEqual(self.j(("09:00", 78, 80, 70, 75)), ("sl", 78 / 100 - 1))

    def test_both_in_one_bar_counts_as_loss(self):
        self.assertEqual(self.j(("09:00", 100, 111, 93, 100)), ("both", self.SL))

    def test_first_touch_wins(self):
        self.assertEqual(self.j(("09:00", 100, 105, 99, 104), ("09:05", 104, 111, 103, 105), ("09:10", 105, 106, 90, 91)), ("tp", self.TP))
        self.assertEqual(self.j(("09:00", 100, 105, 93, 94), ("09:05", 94, 111, 93, 105)), ("sl", self.SL))

    def test_no_touch_closes_at_last_regular_bar(self):
        k, r = self.j(("09:00", 100, 104, 96, 103), ("15:25", 103, 104, 102, 102), ("15:30", 102, 130, 80, 120), ("19:55", 120, 130, 80, 125))
        self.assertEqual(k, "close")
        self.assertAlmostEqual(r, 0.02)                                          # 15:30 뒤 봉은 안 봄


class FakeDaum:
    """다음 금융 흉내: 종목별 그날 거래대금 · 종가."""
    def __init__(self, quotes, bars=None):
        self.quotes, self.bars = quotes, bars or {}

    def __call__(self, url, code=None):
        if "/minutes" in url:
            return json.dumps({"data": self.bars.get(code, [])})
        if "/days?" in url:
            return json.dumps({"data": self.quotes.get(code, [])})
        return json.dumps({"tradeDate": "19990101", "tradePrice": 1, "accTradePrice": 1})


def bar(day, t, o, h, l, c):
    return {"candleTime": "%s %s:00" % (day, t), "openingPrice": o, "highPrice": h, "lowPrice": l, "tradePrice": c}


class ScanTest(unittest.TestCase):
    def post(self, url, body):
        return KIND_LIST if url == ipobot.KIND_LIST else KIND_PLAN

    def test_top_value_and_thresholds(self):
        self.assertEqual(ipobot.picks([]), (None, []))
        big = {"value": 5e11}
        self.assertEqual([r[0] for r in ipobot.picks([big, {"value": 1}])[1]], ["big", "small"])
        self.assertEqual([r[0] for r in ipobot.picks([{"value": 4.9e11}])[1]], ["small"])
        self.assertEqual(ipobot.picks([{"value": 2.9e11}])[1], [])

    def test_scan_sorts_and_finds_todays_listing_by_search(self):
        def get(url, code=None):
            if "search?q=" in url:
                return json.dumps({"suggestItems": [{"koreanName": "영광", "symbolCode": "A777770"}]})
            return FakeDaum({"A523400": [days_row("2026-11-02", 30000, 1e11)], "A777770": [days_row("2026-11-02", 40000, 6e11)]})(url, code)
        rows, missing = ipobot.scan(dt.date(2026, 11, 2), ipobot.parse_plan(KIND_PLAN), get, self.post, sleep=lambda s: None)
        self.assertEqual([r["name"] for r in rows], ["영광", "인텔리빅스"])
        self.assertEqual(missing, [])
        self.assertEqual(rows[0]["listed"], "2026-11-02")

    def test_scan_looks_up_near_planned_dates(self):
        seen = []

        def get(url, code=None):
            if "search?q=" in url:
                seen.append(url)
                return json.dumps({"suggestItems": [{"koreanName": "예정보다일찍", "symbolCode": "A888880"}]})
            return FakeDaum({"A888880": [days_row("2026-10-30", 30000, 9e11)]})(url, code)
        rows, _ = ipobot.scan(dt.date(2026, 10, 30), [("2026-10-31", "예정보다일찍"), ("2026-12-22", "먼훗날")], get, self.post, sleep=lambda s: None)
        self.assertEqual([r["name"] for r in rows], ["예정보다일찍"])
        self.assertEqual(len(seen), 1)                                           # 먼 일정은 찾지 않음

    def test_scan_warns_when_todays_listing_is_not_found(self):
        def get(url, code=None):
            if "search?q=" in url:
                return json.dumps({"suggestItems": []})
            return FakeDaum({"A523400": [days_row("2026-11-02", 30000, 1e11)]})(url, code)
        rows, missing = ipobot.scan(dt.date(2026, 11, 2), ipobot.parse_plan(KIND_PLAN), get, self.post, sleep=lambda s: None)
        self.assertEqual(missing, ["영광"])
        msg = ipobot.compose(dt.date(2026, 11, 2), rows, missing, [], [], ipobot.fresh())
        self.assertIn("⚠️ 오늘 상장 예정 영광", msg["embeds"][2]["description"])

    def test_too_many_failures_raises(self):
        def get(url, code=None):
            raise OSError("안 됨")
        with self.assertRaises(ValueError):
            ipobot.scan(dt.date(2026, 10, 30), [], get, self.post, sleep=lambda s: None)


class SettleTest(unittest.TestCase):
    def test_settles_on_next_day_and_keeps_record(self):
        rec = ipobot.fresh()
        rec["pending"] = [{"rule": "big", "code": "A1", "name": "가", "date": "2026-10-30", "entry": 100.0},
                          {"rule": "small", "code": "A1", "name": "가", "date": "2026-10-30", "entry": 100.0}]
        bars = [bar("2026-10-30", "09:00", 100, 200, 50, 100)] + [bar("2026-11-02", "09:00", 100, 105, 99, 104), bar("2026-11-02", "09:05", 104, 111, 103, 110)]
        lines = ipobot.settle(rec, dt.date(2026, 11, 2), FakeDaum({}, {"A1": bars}))
        self.assertEqual(rec["pending"], [])
        self.assertEqual(rec["big"]["n"], 1)
        self.assertAlmostEqual(rec["big"]["eq"], 1 + 0.10 - 0.003)
        self.assertAlmostEqual(rec["small"]["eq"], 1 + 0.043 - 0.003)            # 짧게는 09:00 봉 고가 105 에서 먼저 익절
        self.assertEqual(lines, ["가 크게 익절 +9.7%", "가 짧게 익절 +4.0%"])

    def test_waits_until_next_day_bars_exist_then_gives_up(self):
        rec = ipobot.fresh()
        x = {"rule": "big", "code": "A1", "name": "가", "date": "2026-10-30", "entry": 100.0}
        rec["pending"] = [x]
        self.assertEqual(ipobot.settle(rec, dt.date(2026, 11, 2), FakeDaum({}, {"A1": []})), [])
        self.assertEqual(rec["pending"], [x])
        self.assertEqual(ipobot.settle(rec, dt.date(2026, 11, 8), FakeDaum({}, {"A1": []})), [])
        self.assertEqual(rec["pending"], [])
        self.assertEqual(rec["big"]["n"], 0)

    def test_cut_off_day_is_not_used(self):
        rec = ipobot.fresh()
        rec["pending"] = [{"rule": "big", "code": "A1", "name": "가", "date": "2026-10-30", "entry": 100.0}]
        bars = [bar("2026-11-02", "14:00", 100, 200, 50, 100)]                   # 앞이 잘린 날
        self.assertEqual(ipobot.settle(rec, dt.date(2026, 11, 2), FakeDaum({}, {"A1": bars})), [])
        self.assertEqual(rec["big"]["n"], 0)

    def test_loss_updates_drawdown(self):
        r = ipobot.fresh()["big"]
        for p in (0.1, -0.2, 0.05):
            ipobot.add(r, p)
        self.assertEqual((r["n"], r["win"]), (3, 2))
        self.assertAlmostEqual(r["mdd"], -0.2)


class IpoComposeTest(unittest.TestCase):
    today = dt.date(2026, 10, 30)

    def test_big_signal_rings_with_prices(self):
        rows = [{"name": "인텔리빅스", "code": "A1", "listed": "2026-10-30", "close": 31000.0, "value": 1.19e12}]
        msg = ipobot.compose(self.today, rows, [], [("2026-11-02", "영광"), ("2026-11-03", "래블업")], [], ipobot.fresh())
        self.assertTrue(msg["ring"])
        self.assertEqual(msg["content"], "🔔 신규 추격 진입 신호 · 인텔리빅스 · 10/30 장 마감 → 11/2 · 크게 적중 56% · 짧게 적중 76%")
        panel = msg["embeds"][2]["description"]
        self.assertIn("거래대금 1위: 인텔리빅스 1조 1,900억 · 종가 31,000원 · 오늘 상장", panel)
        self.assertIn("11/2 익절 34,100원(+10.0%) · 손절 29,140원(−6.0%)", panel)
        self.assertIn("익절 32,333원(+4.3%) · 손절 27,900원(−10.0%)", panel)
        self.assertIn("다음 상장(예정): 11/2 영광 · 11/3 래블업", panel)
        self.assertIn("🎯 적중 확률 **56%** · 이기면 평균 +9.9% · 지면 평균 −7.5% · 지난 34건", panel)
        self.assertIn("🎯 적중 확률 **76%** · 이기면 평균 +6.2% · 지면 평균 −10.9% · 지난 51건", panel)
        self.assertEqual(msg["embeds"][2]["color"], signalbot.GREEN)
        msg = ipobot.compose(self.today, rows, [], [("2026-10-31", "인텔리빅스"), ("2026-11-02", "영광")], [], ipobot.fresh())
        self.assertIn("다음 상장(예정): 11/2 영광", msg["embeds"][2]["description"])   # 예정일보다 먼저 상장한 종목은 뺌
        self.assertNotIn("10/31", msg["embeds"][2]["description"])
        self.assertEqual((msg["embeds"][0]["title"], msg["embeds"][0]["author"]["name"]), ("🔥 신규 추격 크게", "+10% · −6%"))

    def test_quiet_when_top_is_small(self):
        rows = [{"name": "가", "code": "A1", "listed": "2026-10-01", "close": 1000.0, "value": 1.559e11}]
        msg = ipobot.compose(self.today, rows, [], [], ["나 크게 손절 −6.3%"], ipobot.fresh())
        self.assertFalse(msg["ring"])
        self.assertEqual(msg["content"], "📡 신규 추격 · 10/30 장 마감 → 11/2 · 오늘은 쉼")
        self.assertNotIn("가", msg["content"])                                   # 조용한 글의 머리에는 종목명을 안 넣음
        panel = msg["embeds"][2]["description"]
        self.assertIn("**크게** — 💤 오늘은 쉼 · 1위 거래대금이 5,000억 미달", panel)
        self.assertIn("**짧게** — 💤 오늘은 쉼 · 1위 거래대금이 3,000억 미달", panel)
        self.assertIn("지난 신호: 나 크게 손절 −6.3%", panel)

    def test_small_only(self):
        rows = [{"name": "가", "code": "A1", "listed": "2026-10-01", "close": 1000.0, "value": 3.5e11}]
        panel = ipobot.compose(self.today, rows, [], [], [], ipobot.fresh())["embeds"][2]["description"]
        self.assertIn("**크게** — 💤 오늘은 쉼", panel)
        self.assertIn("**짧게** — 🟢 **진입 신호**", panel)

    def test_no_rows(self):
        msg = ipobot.compose(self.today, [], [], [], [], ipobot.fresh())
        self.assertFalse(msg["ring"])
        self.assertIn("오늘 거래된 신규주 없음", msg["embeds"][2]["description"])

    def test_friday_goes_to_monday(self):
        self.assertEqual(ipobot.next_workday(dt.date(2026, 10, 30)), dt.date(2026, 11, 2))
        self.assertEqual(ipobot.next_workday(dt.date(2026, 11, 2)), dt.date(2026, 11, 3))

    def test_won_text(self):
        self.assertEqual(ipobot.won(1.19e12), "1조 1,900억")
        self.assertEqual(ipobot.won(1.559e11), "1,559억")
        self.assertEqual(ipobot.won(5e11), "5,000억")


class DueTest(unittest.TestCase):
    def t(self, day, h, m):
        return dt.datetime(2026, 11, day, h, m, tzinfo=fmt.KST)

    def test_window(self):
        self.assertFalse(ipobot.due(self.t(2, 15, 35), None))
        self.assertTrue(ipobot.due(self.t(2, 15, 36), None))
        self.assertTrue(ipobot.due(self.t(2, 19, 59), None))
        self.assertFalse(ipobot.due(self.t(2, 20, 0), None))

    def test_weekend_and_done(self):
        self.assertFalse(ipobot.due(self.t(1, 16, 0), None))                     # 일요일
        self.assertFalse(ipobot.due(self.t(2, 16, 0), "2026-11-02"))
        self.assertTrue(ipobot.due(self.t(3, 16, 0), "2026-11-02"))


class BuildTest(unittest.TestCase):
    today = dt.date(2026, 10, 30)

    def post(self, url, body):
        return KIND_LIST if url == ipobot.KIND_LIST else ""

    def test_market_closed_makes_no_post_and_keeps_record(self):
        rec = ipobot.fresh()
        rec["pending"] = [{"rule": "big", "code": "A1", "name": "가", "date": "2026-10-29", "entry": 100.0}]
        msg, out = ipobot.build(self.today, rec, FakeDaum({}), self.post, sleep=lambda s: None)
        self.assertIsNone(msg)
        self.assertEqual(out, rec)

    def test_signal_goes_to_pending_and_input_is_not_changed(self):
        quotes = {ipobot.SAMPLE: [days_row("2026-10-30", 1, 1)], "A523400": [days_row("2026-10-30", 31000, 1.19e12)]}
        rec = ipobot.fresh()
        msg, out = ipobot.build(self.today, rec, FakeDaum(quotes), self.post, sleep=lambda s: None)
        self.assertTrue(msg["ring"])
        self.assertEqual(rec["pending"], [])                                     # 받은 기록은 안 건드림
        self.assertEqual([(x["rule"], x["entry"]) for x in out["pending"]], [("big", 31000.0), ("small", 31000.0)])
        self.assertTrue(json.dumps(out))


class Q:
    def __init__(self, hooks=()):
        self.sent = []
        self.hooks = set(hooks)
        self.ping = set()

    def has(self, ch):
        return ch in self.hooks

    async def put(self, ch, text, embeds=None):
        self.sent.append((ch, text, embeds))


class AppTest(unittest.TestCase):
    def app(self, d=None, token="t"):
        app = App.__new__(App)
        app.d = d or Q()
        app.state = {}
        app.cfg = {"bot_token": token}
        self.dms = []
        return app

    def dm(self, token, msg):
        self.dms.append(msg)

    def at(self, day, h, m):
        return int(dt.datetime(2026, 11, day, h, m, tzinfo=fmt.KST).timestamp() * 1000)

    def run_btc(self, app, n, build=None):
        with mock.patch.object(store, "save_state"), mock.patch("candleman.bot.dm_owner", self.dm), \
                mock.patch.object(signalbot, "build", build or (lambda n: {"content": "c", "embeds": [], "ring": True})):
            asyncio.run(app._maybe_signal(n))

    def test_btc_once_per_close_and_after_a_short_wait(self):
        app = self.app()
        self.run_btc(app, self.at(2, 22, 0))                                     # 처음 켜면 지난 마감(11/1 23:00)이 바로 감
        self.assertEqual(len(self.dms), 1)
        self.assertEqual(self.dms[0]["ring"], True)
        app._signal_next = 0
        self.run_btc(app, self.at(2, 23, 0) + 10_000)                            # 11/2 23:00 마감은 30초를 기다림
        self.assertEqual(len(self.dms), 1)
        app._signal_next = 0
        self.run_btc(app, self.at(2, 23, 1))
        self.assertEqual(len(self.dms), 2)
        app._signal_next = 0
        self.run_btc(app, self.at(2, 23, 30))
        self.assertEqual(len(self.dms), 2)                                       # 같은 마감은 한 번만

    def test_btc_retries_after_failure(self):
        app = self.app()

        def boom(n):
            raise ValueError("이번 마감 봉이 아직 없음")
        with self.assertRaises(ValueError):
            self.run_btc(app, self.at(2, 23, 1), boom)
        self.assertNotIn("signal_btc_end", app.state)
        self.run_btc(app, self.at(2, 23, 2))                                     # 2분이 안 지났으면 안 봄
        self.assertEqual(self.dms, [])
        self.run_btc(app, self.at(2, 23, 3))
        self.assertEqual(len(self.dms), 1)

    def test_goes_only_to_the_owner_dm(self):
        app = self.app()
        self.run_btc(app, self.at(2, 23, 1))
        self.assertEqual(len(self.dms), 1)
        self.assertEqual(app.d.sent, [])                                         # 서버 채널로는 아무것도 안 감

    def test_webhook_keeps_a_quiet_copy(self):
        app = self.app(Q(["signal"]))
        self.run_btc(app, self.at(2, 23, 1))
        self.assertEqual(len(self.dms), 1)
        self.assertEqual(app.d.sent, [("signal", "c", [])])

    def refused(self, app, n):
        def refuse(token, msg):
            raise RuntimeError("디스코드 POST channels 실패: HTTP 403")
        with mock.patch.object(store, "log"), mock.patch.object(store, "save_state"), mock.patch("candleman.bot.dm_owner", refuse), \
                mock.patch.object(signalbot, "build", lambda n: {"content": "c", "embeds": [], "ring": True}):
            asyncio.run(app._maybe_signal(n))

    def test_dm_failure_retries_and_tells_status_once(self):
        app = self.app()
        n = self.at(2, 23, 1)
        for i in range(4):
            with self.assertRaises(RuntimeError):
                self.refused(app, n + i * signalbot.RETRY)
        self.assertNotIn("signal_btc_end", app.state)
        self.assertEqual([s[0] for s in app.d.sent], ["status"])                 # 세 번째 실패 때 한 번만
        self.run_btc(app, n + 4 * signalbot.RETRY)
        self.assertEqual(len(self.dms), 1)
        self.assertIn("signal_btc_end", app.state)

    def test_dm_failure_still_reaches_the_private_channel(self):
        app = self.app(Q(["signal"]))
        self.refused(app, self.at(2, 23, 1))
        self.assertEqual(app.d.sent, [("signal", "c", [])])
        self.assertIn("signal_btc_end", app.state)

    def run_ipo(self, app, n, out):
        with mock.patch.object(store, "save_state"), mock.patch("candleman.bot.dm_owner", self.dm), mock.patch.object(ipobot, "build", lambda d, r: out):
            asyncio.run(app._maybe_ipo(n))

    def test_ipo_posts_once_and_saves_record(self):
        app = self.app()
        self.run_ipo(app, self.at(2, 15, 37), ({"content": "c", "embeds": [], "ring": False}, {"pending": [1]}))
        self.assertEqual(app.state["signal_ipo"], {"pending": [1]})
        self.assertEqual(app.state["signal_ipo_day"], "2026-11-02")
        self.assertEqual(self.dms[0]["ring"], False)
        app._ipo_next = 0
        self.run_ipo(app, self.at(2, 15, 50), ({"content": "c", "embeds": [], "ring": False}, {}))
        self.assertEqual(len(self.dms), 1)

    def test_ipo_closed_day_retries_then_ends_without_post(self):
        app = self.app()
        self.run_ipo(app, self.at(2, 15, 37), (None, {}))
        self.assertNotIn("signal_ipo_day", app.state)
        app._ipo_next = 0
        self.run_ipo(app, self.at(2, 16, 10), (None, {}))
        self.assertEqual(app.state["signal_ipo_day"], "2026-11-02")
        self.assertEqual(self.dms, [])
        self.assertNotIn("signal_ipo", app.state)

    def test_nothing_is_sent_without_bot_or_webhook(self):
        """봇 토큰도 웹후크도 없으면 #실시간-매매로 새면 안 됨 (_post 의 기본 채널이 trade)."""
        d = Discord({"trade": "https://discord.com/api/webhooks/1/t"})
        app = self.app(d, token=None)
        with mock.patch.object(signalbot, "build", side_effect=AssertionError("부르면 안 됨")), mock.patch.object(ipobot, "build", side_effect=AssertionError("부르면 안 됨")):
            asyncio.run(app._maybe_signal(self.at(2, 23, 1)))
            asyncio.run(app._maybe_ipo(self.at(2, 15, 40)))
        self.assertEqual(d.q.qsize(), 0)


class DmTest(unittest.TestCase):
    """bot.dm_owner — 서버 주인 한 사람에게만 · 폰 알림은 ring 인 글만."""

    def send(self, msg, guilds=(("g1", "99"),)):
        calls = []

        def rest(bot, method, path, body=None, **k):
            calls.append((method, path, body))
            if path == "/users/@me/guilds":
                return [{"id": g} for g, _ in guilds]
            if path.startswith("/guilds/"):
                return {"owner_id": dict(guilds)[path.split("/")[2]]}
            return {"id": "dm1"}
        with mock.patch.object(Bot, "_rest", rest):
            dm_owner("t", msg)
        return calls

    def test_ring_post_tags_only_the_owner(self):
        calls = self.send({"content": "🔔 BTC 신호\n둘째 줄", "embeds": [{"title": "카드"}], "ring": True})
        self.assertEqual(calls[-2], ("POST", "/users/@me/channels", {"recipient_id": "99"}))
        self.assertEqual(calls[-1], ("POST", "/channels/dm1/messages", {"content": "🔔 BTC 신호 <@99>\n둘째 줄", "embeds": [{"title": "카드"}],
                                                                       "allowed_mentions": {"parse": [], "users": ["99"]}}))

    def test_quiet_post_has_no_tag_and_no_phone_alert(self):
        calls = self.send({"content": "📡 BTC 판정", "embeds": [], "ring": False})
        self.assertEqual(calls[-1][2], {"content": "📡 BTC 판정", "embeds": [], "allowed_mentions": {"parse": []}, "flags": 4096})

    def test_tag_stays_out_of_the_original_post(self):
        msg = {"content": "🔔 BTC 신호", "embeds": [], "ring": True}
        self.send(msg)
        self.assertEqual(msg["content"], "🔔 BTC 신호")                          # 웹후크 사본은 태그 없이 조용히

    def test_real_signal_headlines_get_the_tag_at_the_end_of_the_first_line(self):
        self.assertEqual(with_tag("🔔 신규 추격 진입 신호 · 영광 · 적중 42%", "99"), "🔔 신규 추격 진입 신호 · 영광 · 적중 42% <@99>")
        self.assertEqual(with_tag("", "99"), "<@99>")

    def test_same_owner_in_two_servers_is_fine(self):
        calls = self.send({"content": "c", "embeds": [], "ring": True}, guilds=(("g1", "99"), ("g2", "99")))
        self.assertEqual(calls[-2][2], {"recipient_id": "99"})

    def test_two_owners_send_nothing(self):
        with self.assertRaises(RuntimeError):
            self.send({"content": "c", "embeds": [], "ring": True}, guilds=(("g1", "99"), ("g2", "77")))


class RingTest(unittest.TestCase):
    def setUp(self):
        self.d = Discord({"signal": "https://discord.com/api/webhooks/1/s", "trade": "https://discord.com/api/webhooks/1/t"}, ping=("trade",))

    def put(self, *a, **k):
        asyncio.run(self.d.put(*a, **k))
        return self.d.q.get_nowait()

    def test_signal_channel_never_rings(self):
        self.assertEqual(self.put("signal", "🔔 BTC 신호", [{"title": "카드"}]), ("signal", "🔔 BTC 신호", [{"title": "카드"}]))
        self.assertEqual(self.put("trade", "머리", [{"title": "카드"}])[1], "머리 @everyone")             # 다른 채널은 그대로

    def test_webhook_copy_cannot_mention_anyone(self):
        sent = []

        class Resp:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            sent.append(json.loads(req.data.decode("utf-8")))
            return Resp()
        with mock.patch("candleman.discord.urllib.request.urlopen", fake_urlopen):
            self.d._post("signal", "🔔 BTC 신호", [{"title": "카드"}])
        self.assertEqual(sent[0]["allowed_mentions"], {"parse": []})
        self.assertTrue(sent[0]["avatar_url"].endswith("/stable/assets/signalbot.png"))
        with mock.patch("candleman.discord.urllib.request.urlopen", fake_urlopen):
            self.d._post("trade", "머리", [{"title": "카드"}])
        self.assertNotIn("avatar_url", sent[1])                                  # 다른 채널은 웹후크 설정 그대로
        self.assertEqual(sent[1]["allowed_mentions"], {"parse": ["everyone"]})


class CommandTest(unittest.TestCase):
    """python -m candleman signal — 봇 토큰이 있으면 나에게만 DM · 없으면 웹후크 · 둘 다 없으면 안 보냄 · 기록은 안 건드림."""
    hook = "https://discord.com/api/webhooks/1/s"

    def run_it(self, hooks, args, token=None):
        from candleman import __main__ as cli
        sent = []
        msg = {"content": "머리", "embeds": [{"title": "카드"}], "ring": True}
        with mock.patch.object(signalbot, "daily", return_value=[]), mock.patch.object(signalbot, "read", return_value={}), \
                mock.patch.object(signalbot, "compose", return_value=msg), mock.patch.object(ipobot, "build", return_value=(msg, {"pending": [1]})), \
                mock.patch.object(store, "load_state", return_value={}), mock.patch.object(store, "save_state", side_effect=AssertionError("저장하면 안 됨")), \
                mock.patch.object(Discord, "_post", lambda self, ch, text, embeds=None: sent.append((ch, text))), \
                mock.patch("candleman.bot.dm_owner", lambda tok, m: sent.append(("dm", m["content"]))), mock.patch("builtins.print"):
            code = cli._signal({"hooks": hooks, "bot_token": token}, args)
        return code, sent

    def test_no_place_to_send(self):
        self.assertEqual(self.run_it({"trade": "https://discord.com/api/webhooks/1/t"}, []), (1, []))

    def test_bot_token_goes_to_dm_only(self):
        self.assertEqual(self.run_it({"signal": self.hook}, ["btc"], token="t"), (0, [("dm", "머리")]))
        self.assertEqual(self.run_it({}, [], token="t"), (0, [("dm", "머리"), ("dm", "머리")]))

    def test_each_target(self):
        hooks = {"signal": self.hook}
        self.assertEqual(self.run_it(hooks, []), (0, [("signal", "머리"), ("signal", "머리")]))
        self.assertEqual(self.run_it(hooks, ["btc"]), (0, [("signal", "머리")]))
        self.assertEqual(self.run_it(hooks, ["ipo", "2026-10-30"]), (0, [("signal", "머리")]))
        self.assertEqual(self.run_it(hooks, ["모름"]), (1, []))

    def test_tag_test_rings_the_dm_and_needs_the_bot(self):
        from candleman import __main__ as cli
        sent = []
        with mock.patch("candleman.bot.dm_owner", lambda tok, m: sent.append(m)), mock.patch("builtins.print"):
            self.assertEqual(cli._signal({"hooks": {}, "bot_token": "t"}, ["test"]), 0)
            self.assertEqual(cli._signal({"hooks": {"signal": self.hook}}, ["test"]), 1)   # 태그는 봇 DM으로만
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0]["ring"])

    def test_closed_day_posts_nothing(self):
        from candleman import __main__ as cli
        with mock.patch.object(ipobot, "build", return_value=(None, {})), mock.patch.object(store, "load_state", return_value={}), mock.patch("builtins.print"):
            self.assertEqual(cli._signal({"hooks": {"signal": self.hook}}, ["ipo", "2026-11-01"]), 0)


class ConfigTest(unittest.TestCase):
    def test_signal_hook(self):
        base = {"BINANCE_API_KEY": "k", "BINANCE_API_SECRET": "s", "DISCORD_WEBHOOK_TRADE": "https://discord.com/api/webhooks/1/t"}
        with mock.patch.object(config, "read_env", return_value=dict(base)):
            self.assertNotIn("signal", config.load()["hooks"])
        with mock.patch.object(config, "read_env", return_value=dict(base, DISCORD_WEBHOOK_SIGNAL="https://discord.com/api/webhooks/1/s")):
            self.assertEqual(config.load()["hooks"]["signal"], "https://discord.com/api/webhooks/1/s")
        with mock.patch.object(config, "read_env", return_value=dict(base, DISCORD_WEBHOOK_SIGNAL="https://example.com/x")):
            with self.assertRaises(config.ConfigError):
                config.load()


if __name__ == "__main__":
    unittest.main()
