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
        self.assertIn("🔴 **정리 신호** · 현물 전부 정리", panel)
        self.assertIn("🔴 **정리 신호** · 현물 정리 → 선물 숏 1배", panel)
        self.assertEqual(panel.count("🎯 적중 확률"), 2)
        self.assertEqual(msg["content"], "🔔 BTC 신호 · 🛡️ 안전 정리 적중 25% · 🚀 과감 선물 숏 전환 적중 26%")
        self.assertEqual(msg["embeds"][2]["color"], signalbot.cards.RED)

    def test_big_drop_keeps_bold_long_by_rsi(self):
        up = [100.0 + i for i in range(420)]
        msg = signalbot.compose(state_of(up + [up[-1] * 0.6]))                   # 하루 −40%: RSI 가 10 아래로 → 과감은 되돌림으로 유지 · 안전은 100일선 아래라 정리
        panel = panel_of(msg)
        self.assertIn("🔴 **정리 신호** · 현물 전부 정리", panel)
        self.assertIn("📌 유지 · 현물 롱", panel)
        self.assertIn("RSI 되돌림 켜짐", panel)

    def test_entry_day_rings(self):
        msg = signalbot.compose(state_of([100.0] * 300 + [101.0]))                # 평평하다(꺼짐) 오름: 50일선 · 100일선 위로
        self.assertTrue(msg["ring"])
        panel = panel_of(msg)
        self.assertIn("🟢 **진입 신호** · 자금 50% 현물 롱", panel)
        self.assertIn("🟢 **진입 신호** · 선물 숏 정리 → 현물 롱", panel)
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

    def test_open_bar_is_not_used(self):
        closes = [100.0 + i for i in range(420)]
        rows = rows_of(closes + [1.0])                                           # 아직 안 닫힌 봉의 값이 판정에 들어가면 안 됨
        st = signalbot.read(rows, int(rows[-2][6]) + 1000)
        self.assertEqual(st["close"], closes[-1])


HOUR = signalbot.HOUR
WAVE = [100.0 + (1 if i % 2 else -1) for i in range(302)]                       # 50일선 언저리를 오가다 위에서 끝남


def six_rows(n, closes_of, late=()):
    """분할마다 n 에 마지막으로 닫힌 일봉까지 → {timeZone: 일봉}. late 의 분할은 마지막 봉이 아직 안 온 자료."""
    out = {}
    for h in signalbot.HOURS:
        c = closes_of(h)
        end = signalbot.closed_end(n, h * HOUR) - (DAY if h in late else 0)
        out[signalbot.tz_of(h)] = rows_of(c, end - (len(c) - 1) * DAY)
    return out


def six_fetch(by_tz):
    def fetch(url):
        if "endTime" in url:
            return "[]"
        return json.dumps(by_tz[int(url.split("timeZone=")[1].split("&")[0])])
    return fetch


def size_ok(case, msg):
    """디스코드 한도: 글 전체 6000자 · 카드 10장 · 필드 25개 · 필드 이름 256 · 값 1024 · 꼬리말 2048."""
    total = 0
    case.assertLessEqual(len(msg["embeds"]), 10)
    for e in msg["embeds"]:
        total += len(e.get("title", "")) + len(e.get("description", "")) + len(e.get("footer", {}).get("text", "")) + len(e.get("author", {}).get("name", ""))
        case.assertLessEqual(len(e.get("fields", [])), 25)
        case.assertLessEqual(len(e.get("footer", {}).get("text", "")), 2048)
        for f in e.get("fields", []):
            case.assertLessEqual(len(f["name"]), 256)
            case.assertLessEqual(len(f["value"]), 1024)
            total += len(f["name"]) + len(f["value"])
    case.assertLess(total, 6000)


class SixTest(unittest.TestCase):
    """6분할(11/4): 4시간마다 한 분할씩 · 분할마다 그 시각에 닫히는 일봉 · 다음 판정의 매수 · 매도 기준가를 달러로."""

    def at(self, day, h, m=1):
        return int(dt.datetime(2026, 11, day, h, m, tzinfo=fmt.KST).timestamp() * 1000)

    def sts(self, n, closes_of, late=()):
        return signalbot.read6(n, six_fetch(six_rows(n, closes_of, late)))

    def panel(self, msg):
        return msg["embeds"][-1]

    def test_hours_and_names(self):
        self.assertEqual([signalbot.tz_of(h) for h in signalbot.HOURS], [-2, -6, -10, 10, 6, 2])      # 14시 → 10 = 밤 11시 판정 주소 그대로
        self.assertEqual(signalbot.URL, signalbot.KLINES % signalbot.tz_of(14))
        self.assertEqual([signalbot.label(h) for h in signalbot.HOURS], ["11시 분할", "15시 분할", "19시 분할", "23시 분할", "03시 분할", "07시 분할"])

    def test_last_end_moves_every_4_hours(self):
        self.assertEqual(signalbot.last_end(self.at(5, 6, 59)), self.at(5, 3, 0))
        self.assertEqual(signalbot.last_end(self.at(5, 7, 0)), self.at(5, 7, 0))
        self.assertEqual(signalbot.last_end(self.at(5, 0, 30)), self.at(4, 23, 0))

    def test_get_reads_gzip_and_plain(self):
        import gzip

        class Resp:
            def __init__(self, body, enc):
                self.body, self.headers = body, ({"Content-Encoding": enc} if enc else {})

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return self.body
        sent = []
        for body, enc in ((gzip.compress("[1, \"가\"]".encode("utf-8")), "gzip"), ("[1, \"가\"]".encode("utf-8"), None)):
            def fake(req, timeout=None):
                sent.append(req.get_header("Accept-encoding"))
                return Resp(body, enc)
            with mock.patch("candleman.signalbot.urllib.request.urlopen", fake):
                self.assertEqual(signalbot._get("https://data-api.binance.vision/x"), "[1, \"가\"]")
        self.assertEqual(sent, ["gzip", "gzip"])

    def test_misaligned_bars_are_refused(self):
        n = self.at(5, 7, 1)
        rows = rows_of([100.0 + i for i in range(420)])                           # 모든 주소에 밤 11시 마감 봉이 오면
        with self.assertRaises(ValueError):
            signalbot.read6(n, fake_fetch(rows))

    def test_dollar_edges_flip_the_real_judgment_within_a_cent(self):
        for c in (WAVE, [100.0 + i for i in range(420)], [500.0 - i for i in range(420)]):
            J = signalbot.judge(c)
            rs = signalbot.edges(c, J, 0)
            self.assertEqual(rs[0][0], c[-1] * 0.7)
            for a, b in zip(rs, rs[1:]):
                lo, hi = signalbot.judge(c + [b[0] - 0.02])["bold"][-1], signalbot.judge(c + [b[0] + 0.02])["bold"][-1]
                self.assertEqual((lo > 0, hi > 0), (a[2], b[2]))
            ups = [b[0] for b in rs if b[3] == "50일선 돌파"]
            if ups:
                self.assertAlmostEqual(ups[0], sum(c[-49:]) / 49, delta=0.01)      # 50일선 기준 = 지난 49개 종가 합 / 49

    def test_criteria_words(self):
        w = "11/5 11:00"
        two = [[50, 60, True, "RSI 되돌림"], [60, 80, False, None], [80, 120, True, "50일선 돌파"]]
        self.assertEqual(signalbot.criteria(two, 75, w), "🟢 매수 · 11/5 11:00 종가 **$80** 위 (50일선 돌파 · 적중 21%) · 또는 **$60** 아래 (RSI 되돌림 · 적중 42%)")
        mid = [[50, 60, True, None], [60, 80, False, "50일선 이탈"], [80, 120, True, None]]
        self.assertEqual(signalbot.criteria(mid, 90, w), "🔴 매도 · 11/5 11:00 종가 **$80** 아래 (50일선 이탈 · 적중 26%) · 단 $60 아래면 RSI 되돌림으로 그대로")
        ends = [[50, 95, True, None], [95, 110, False, "되돌림 끝남"], [110, 130, True, None]]
        self.assertEqual(signalbot.criteria(ends, 90, w), "🔴 매도 · 11/5 11:00 종가 **$95** 위 (되돌림 끝남 · 적중 36%) · 단 $110 위면 50일선 위라 그대로")
        self.assertEqual(signalbot.criteria([[50, 130, True, None]], 90, w), "📌 11/5 11:00 종가가 지금 ±30% 안이면 그대로")

    def test_quiet_split_post(self):
        n = self.at(5, 7, 1)
        msg = signalbot.compose6(self.sts(n, lambda h: [100.0 + i for i in range(420)]), n, self.at(5, 3, 0))
        self.assertFalse(msg["ring"])
        self.assertEqual(msg["content"], "📡 BTC 6분할 · 07시 분할 판정 · 자리 그대로 · 현물 롱")
        self.assertEqual(len(msg["embeds"]), 1)                                  # 규칙 카드 · 안전 진입은 밤 11시 글에만
        p = self.panel(msg)
        self.assertEqual(p["title"], "🤖 07시 분할 · 11/5 07:00 판정 → 다음 11/6 07:00")
        self.assertEqual(p["color"], signalbot.PANEL)
        self.assertEqual(p["description"], "자금을 6으로 나눠 4시간마다 한 분할씩 판정 · 이 글은 07시 분할만")
        self.assertEqual([f["name"] for f in p["fields"]], ["⏱️ 07시 분할 · 방금 판정", "🔮 다음 판정 기준가"])   # 11/5 댕이: 그 분할 시각엔 그 분할만
        self.assertRegex(p["fields"][0]["value"], r"^📌 그대로 · 현물 롱 · \d+/\d+ 07:00 진입 \$[\d,]+ · ")
        self.assertTrue(p["fields"][1]["value"].startswith("🔴 매도 · 11/6 07:00 종가 **$"))   # 방금 판정한 분할은 내일 같은 시각
        self.assertIn("· 단 $", p["fields"][1]["value"])                          # 크게 떨어지면 RSI 되돌림으로 그대로
        self.assertNotIn("⏰", p["description"])
        self.assertNotIn("2017-11부터", p["footer"]["text"])
        size_ok(self, msg)

    def test_each_post_tells_only_its_own_split(self):
        """11/5 댕이 "6분할이 한번에 6번씩 오니까 너무 정신없어 · 그 분할에는 그 분할 시각에 맞는 알림만": 판정 시각마다 그 분할만 적힘."""
        up = lambda h: [100.0 + i for i in range(420)]                          # noqa: E731
        for kst in (3, 7, 11, 15, 19, 23):
            n = self.at(5, kst, 1)
            msg = signalbot.compose6(self.sts(n, up), n, n - 60_000 - 4 * HOUR)   # 4시간 전 글은 보냄 → 놓친 판정 없음
            mine = "%02d시 분할" % kst
            self.assertIn(mine, msg["content"])
            for other in (signalbot.label(k) for k in signalbot.HOURS):
                if other != mine:
                    self.assertNotIn(other, repr(msg), (kst, other))
            names = [f["name"] for f in self.panel(msg)["fields"]]
            self.assertEqual(names[:2], ["⏱️ %s · 방금 판정" % mine, "🔮 다음 판정 기준가"])
            self.assertEqual(len(msg["embeds"]), 3 if kst == 23 else 1)
            self.assertEqual(names[2:], ["🛡️ 안전 진입 · 밤 11시 판정"] if kst == 23 else [])   # 안전 진입도 밤 11시 판정이라 그 글에만
            size_ok(self, msg)

    def test_turn_at_the_judged_split_rings(self):
        n = self.at(5, 7, 1)
        msg = signalbot.compose6(self.sts(n, lambda h: WAVE + ([96.0] if h == 22 else [101.0])), n, self.at(5, 3, 0))
        self.assertTrue(msg["ring"])
        self.assertEqual(msg["content"], "🔔 BTC 6분할 신호 · 07시 분할 매도(선물 숏 1배) 적중 26%")
        now = self.panel(msg)["fields"][0]["value"]
        self.assertIn("🔴 **매도 신호** · 현물 정리 → 선물 숏 1배", now)
        self.assertIn("🎯 적중 확률 **26%** · 50일선 이탈", now)
        self.assertNotIn("지난", now)                                             # 지난 건수는 밤 11시 판정의 수
        self.assertEqual(self.panel(msg)["color"], signalbot.cards.RED)
        self.assertEqual([f["name"] for f in self.panel(msg)["fields"]], ["⏱️ 07시 분할 · 방금 판정", "🔮 다음 판정 기준가"])
        self.assertTrue(self.panel(msg)["fields"][1]["value"].startswith("🟢 매수 · 11/6 07:00 종가"))   # 숏이 된 분할의 다음 기준은 매수
        self.assertNotIn("롱 5", repr(msg))                                       # 다른 분할 자리는 안 셈
        size_ok(self, msg)

    def test_missed_turns_are_told_and_ring(self):
        n = self.at(5, 7, 1)
        sts = self.sts(n, lambda h: WAVE + ([96.0] if h == 18 else [101.0]))       # 03시 분할이 바뀐 사이 폰이 꺼져 있었음
        msg = signalbot.compose6(sts, n, self.at(4, 23, 0))
        self.assertTrue(msg["ring"])
        self.assertEqual(msg["content"], "🔔 BTC 6분할 신호 · 놓친 판정 03시 분할 매도")
        missed = self.panel(msg)["fields"][2]                                    # 이 분할(방금 판정 · 다음 기준가) 뒤에
        self.assertEqual(missed["name"], "🔔 놓친 판정 · 11/4 23:00 글 뒤")
        self.assertTrue(missed["value"].startswith("🔴 03시 분할 · 11/5 03:00 판정에 매도 → 선물 숏 1배 (50일선 이탈 · 적중 26%)"))
        self.assertTrue(self.panel(msg)["fields"][0]["value"].startswith("📌 그대로 · 현물 롱"))
        self.assertEqual(len(missed["value"].splitlines()), 1)                   # 자리가 그대로인 놓친 분할은 안 적음
        quiet = signalbot.compose6(sts, n, self.at(5, 3, 0))                     # 03시 글을 이미 보냈으면 다시 안 울림
        self.assertFalse(quiet["ring"])
        self.assertNotIn("놓친", quiet["content"])
        self.assertNotIn("03시 분할", repr(quiet))                               # 03시 분할은 03시 글에서 이미 알림
        self.assertFalse(signalbot.compose6(sts, n)["ring"])                     # 처음 보내는 글(last 없음)도 지난 일은 안 울림

    def test_11pm_post_has_rule_cards_and_safe_entry(self):
        n = self.at(4, 23, 1)
        msg = signalbot.compose6(self.sts(n, lambda h: [100.0 + i for i in range(420)]), n, self.at(4, 19, 0))
        self.assertEqual([e.get("title") for e in msg["embeds"][:2]], ["🛡️ 안전 진입", "🚀 과감한 수익 · 6분할"])
        bold = msg["embeds"][1]
        self.assertEqual(bold["author"]["name"], "수익 우선 · 4시간마다")
        self.assertTrue(bold["description"].startswith("## +23,593%\n"))
        self.assertEqual([f["name"] for f in bold["fields"]], ["분할마다 건수", "적중", "건당 평균", "최대 낙폭", "전환", "시작 뒤 기록"])
        p = self.panel(msg)
        self.assertEqual(p["fields"][-1]["name"], "🛡️ 안전 진입 · 밤 11시 판정")
        self.assertIn("📌 유지 · 자금 50% 현물 롱", p["fields"][-1]["value"])
        self.assertIn("🔴 정리(→ 현금) · 11/5 23:00 종가", p["fields"][-1]["value"])
        self.assertIn("2017-11부터", p["footer"]["text"])
        size_ok(self, msg)

    def test_11pm_turn_keeps_the_count(self):
        n = self.at(4, 23, 1)
        msg = signalbot.compose6(self.sts(n, lambda h: WAVE + ([96.0] if h == 14 else [101.0])), n, self.at(4, 19, 0))
        self.assertIn("· 지난 94건", self.panel(msg)["fields"][0]["value"])
        self.assertEqual(msg["content"], "🔔 BTC 6분할 신호 · 23시 분할 매도(선물 숏 1배) 적중 26% · 🛡️ 안전 정리 적중 25%")
        size_ok(self, msg)

    def test_safe_turn_missed_at_11pm_is_told_later(self):
        n = self.at(5, 3, 1)
        msg = signalbot.compose6(self.sts(n, lambda h: WAVE + ([96.0] if h == 14 else [101.0])), n, self.at(4, 19, 0))
        self.assertIn("🛡️ 안전 정리 적중 25%", msg["content"])
        self.assertEqual(self.panel(msg)["fields"][-1]["name"], "🛡️ 안전 진입 · 밤 11시 판정")
        self.assertEqual(len(msg["embeds"]), 1)

    def test_late_post_says_so(self):
        sts = self.sts(self.at(5, 7, 1), lambda h: [100.0 + i for i in range(420)])
        self.assertIn("⏰ 마감 15분 뒤에 보냄", self.panel(signalbot.compose6(sts, self.at(5, 7, 15)))["description"])
        self.assertIn("⏰ 마감 2시간 5분 뒤에 보냄", self.panel(signalbot.compose6(sts, self.at(5, 9, 5)))["description"])
        self.assertNotIn("⏰", self.panel(signalbot.compose6(sts, self.at(5, 7, 5)))["description"])

    def test_build_waits_for_every_split(self):
        n = self.at(5, 7, 1)
        up = lambda h: [100.0 + i for i in range(420)]                          # noqa: E731
        with self.assertRaises(ValueError):
            signalbot.build(n, six_fetch(six_rows(n, up, late=(22,))))          # 07시 봉이 아직 안 옴
        with self.assertRaises(ValueError):
            signalbot.build(n, six_fetch(six_rows(n, up, late=(10,))))          # 다른 분할 자료가 하루 밀림
        self.assertIn("embeds", signalbot.build(n, six_fetch(six_rows(n, up))))
        self.assertIn("embeds", signalbot.build(n, six_fetch(six_rows(n, up, late=(22,))), strict=False))   # 명령으로 보내 볼 땐 있는 데까지


KIND_LIST = """
<tr onclick="fnDetailView('52340','1234')"><td>인텔리빅스</td><td>2026-10-30</td><td>신규상장</td><td>주권</td><td>소프트웨어</td><td>한국</td><td>주관</td><td>500</td><td>1,000</td><td>100</td><td>보통주</td><td>1,000</td></tr>
<tr onclick="fnDetailView('47900','1235')"><td>어떤제9호스팩</td><td>2026-10-20</td><td>신규상장</td><td>주권</td><td>금융</td><td>한국</td><td>주관</td><td>100</td><td>2,000</td><td>100</td><td>기업인수목적회사</td><td>1,000</td></tr>
<tr onclick="fnDetailView('25003','1236')"><td>진코스텍</td><td>2026-10-15</td><td>이전상장</td><td>주권</td><td>화장품</td><td>한국</td><td>주관</td><td>500</td><td>5,000</td><td>100</td><td>화장품 OEM</td><td>1,000</td></tr>
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
        self.assertEqual(rows[2], {"name": "진코스텍", "code": "A250030", "listed": "2026-10-15", "spac": False})   # 이전상장도 같은 표
        with mock.patch.object(ipobot, "parse_listed", return_value=[{"name": "x", "code": "A1", "listed": "d", "spac": i % 2 == 1} for i in range(100)]):
            pages, kinds = [], set()

            def post(url, body):
                pages.append(body["pageIndex"])
                kinds.add(body["listTypeArrStr"])
                if len(pages) == 2:
                    ipobot.parse_listed.return_value = []
                return ""
            self.assertEqual(len(ipobot.listed(dt.date(2026, 10, 30), post)), 50)   # 100행이면 다음 쪽 · 스팩은 뺌
        self.assertEqual(pages, ["1", "2"])
        self.assertEqual(kinds, {"01|02|"})                                      # 신규상장 + 이전상장

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

    def test_after_market_signal_uses_after_close(self):
        rec = ipobot.fresh()
        rec["pending"] = [{"rule": "small", "code": "A1", "name": "가", "date": "2026-11-04", "entry": 100.0, "after": True}]
        bars = [bar("2026-11-04", "15:25", 100, 100, 100, 100), bar("2026-11-04", "16:00", 100, 101, 95, 96), bar("2026-11-04", "19:55", 96, 97, 95, 96),
                bar("2026-11-05", "09:00", 96, 100.2, 95, 100)]
        lines = ipobot.settle(rec, dt.date(2026, 11, 5), FakeDaum({}, {"A1": bars}))
        self.assertEqual(lines, ["가 짧게 익절 +4.0% (애프터 종가 96원 기준)"])     # 96 기준 익절선 100.13 → 고가 100.2 (정규장 종가 100 기준이면 104.3이라 못 닿음)
        self.assertAlmostEqual(rec["small"]["eq"], 1 + 0.043 - 0.003)
        rec["pending"] = [{"rule": "small", "code": "A1", "name": "가", "date": "2026-11-04", "entry": 100.0, "after": True}]
        lines = ipobot.settle(rec, dt.date(2026, 11, 5), FakeDaum({}, {"A1": bars[:1] + bars[3:]}))
        self.assertEqual(lines, ["가 짧게 안 닿아 종가 정리 −0.3% (애프터 봉이 없어 정규장 종가 기준)"])

    def test_signal_before_a_listing_day_is_dropped(self):
        rec = ipobot.fresh()
        rec["pending"] = [{"rule": "big", "code": "A1", "name": "영광", "date": "2026-11-02", "entry": 100.0},
                          {"rule": "small", "code": "A1", "name": "영광", "date": "2026-11-02", "entry": 100.0}]
        bars = [bar("2026-11-03", "09:00", 100, 111, 99, 110)]
        lines = ipobot.settle(rec, dt.date(2026, 11, 3), FakeDaum({}, {"A1": bars}), [("2026-11-02", "영광"), ("2026-11-03", "래블업")])
        self.assertEqual(lines, ["영광 크게 거름(래블업 상장)", "영광 짧게 거름(래블업 상장)"])   # 거르기 전에 올린 11/2 신호
        self.assertEqual(rec["pending"], [])
        self.assertEqual((rec["big"]["n"], rec["small"]["n"]), (0, 0))
        rec["pending"] = [{"rule": "big", "code": "A2", "name": "인텔리빅스", "date": "2026-10-30", "entry": 100.0}]
        bars = [bar("2026-11-02", "09:00", 100, 111, 99, 110)]
        lines = ipobot.settle(rec, dt.date(2026, 11, 2), FakeDaum({}, {"A2": bars}), [("2026-10-31", "인텔리빅스")])
        self.assertEqual(lines, ["인텔리빅스 크게 익절 +9.7%"])                     # 예정일(토)보다 먼저 상장한 자기 자신은 안 셈
        rec["pending"] = [{"rule": "big", "code": "A1", "name": "가", "date": "2026-11-02", "entry": 100.0, "after": True}]
        bars = [bar("2026-11-03", "09:00", 100, 111, 99, 110)]
        lines = ipobot.settle(rec, dt.date(2026, 11, 3), FakeDaum({}, {"A1": bars}), [("2026-11-03", "래블업")])
        self.assertEqual(lines, ["가 크게 익절 +9.7% (애프터 봉이 없어 정규장 종가 기준)"])   # 애프터 신호는 올릴 때 걸렀으니 다시 안 봄

    def test_next_listings(self):
        plans = [("2026-10-29", "크리에이츠"), ("2026-10-31", "인텔리빅스"), ("2026-11-02", "영광"), ("2026-11-03", "래블업")]
        self.assertEqual(ipobot.next_listings(dt.date(2026, 10, 30), plans, {"인텔리빅스"}), ["영광"])    # 예정일(토)보다 먼저 상장한 자기 자신은 뺌
        self.assertEqual(ipobot.next_listings(dt.date(2026, 10, 29), plans, set()), ["크리에이츠"])       # 예정일에 거래가 없으면 다음 날로 밀린 것으로 봄
        self.assertEqual(ipobot.next_listings(dt.date(2026, 10, 30), plans), ["인텔리빅스", "영광"])     # 지난 신호를 볼 때는 다음 날 예정만
        self.assertEqual(ipobot.next_listings(dt.date(2026, 11, 3), plans, {"래블업"}), [])

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
        msg = ipobot.compose(self.today, rows, [], [("2026-11-03", "래블업"), ("2026-11-04", "이에스티")], [], ipobot.fresh())
        self.assertTrue(msg["ring"])
        self.assertEqual(msg["content"], "🔔 신규 추격 진입 신호 · 인텔리빅스 · 10/30 장 마감 → 11/2 · 크게 적중 59% · 짧게 적중 85%")
        panel = msg["embeds"][2]["description"]
        self.assertIn("거래대금 1위: 인텔리빅스 1조 1,900억 · 종가 31,000원 · 오늘 상장", panel)
        self.assertIn("🔥 **크게** — 🟢 **진입 신호** · 17시 ~ 19:55 애프터마켓에서 매수 → 11/2 익절 +10.0% · 손절 −6.0%(산 값 기준) · 안 닿으면 정규장 끝에 정리", panel)
        self.assertIn("💰 종가 31,000원 기준이면 익절 34,100원 · 손절 29,140원", panel)
        self.assertIn("💰 종가 31,000원 기준이면 익절 32,333원 · 손절 27,900원", panel)
        self.assertIn("다음 상장(예정): 11/3 래블업 · 11/4 이에스티", panel)
        self.assertIn("🎯 적중 확률 **59%** · 이기면 평균 +9.9% · 지면 평균 −6.3% · 지난 27건", panel)
        self.assertIn("🎯 적중 확률 **85%** · 이기면 평균 +5.8% · 지면 평균 −9.5% · 지난 40건", panel)
        self.assertEqual(msg["embeds"][2]["color"], signalbot.GREEN)
        msg = ipobot.compose(self.today, rows, [], [("2026-10-31", "인텔리빅스"), ("2026-11-03", "래블업")], [], ipobot.fresh())
        self.assertTrue(msg["ring"])                                             # 예정일(토)보다 먼저 상장한 자기 자신은 다음 날 상장으로 안 셈
        self.assertIn("다음 상장(예정): 11/3 래블업", msg["embeds"][2]["description"])   # 예정일보다 먼저 상장한 종목은 뺌
        self.assertNotIn("10/31", msg["embeds"][2]["description"])
        self.assertEqual((msg["embeds"][0]["title"], msg["embeds"][0]["author"]["name"]), ("🔥 신규 추격 크게", "+10% · −6%"))

    def test_listing_next_day_holds_without_ring(self):
        rows = [{"name": "영광", "code": "A1", "listed": "2026-11-02", "close": 40000.0, "value": 6e11}]
        msg = ipobot.compose(dt.date(2026, 11, 2), rows, [], [("2026-11-02", "영광"), ("2026-11-03", "래블업")], [], ipobot.fresh())
        self.assertFalse(msg["ring"])
        self.assertEqual(msg["content"], "📡 신규 추격 · 11/2 장 마감 → 11/3 · 오늘은 거름 · 11/3 래블업 상장 예정")
        panel = msg["embeds"][2]["description"]
        self.assertIn("🔥 **크게** — ⏸️ 오늘은 거름 · 11/3 래블업 상장 예정(수급이 나뉨)", panel)
        self.assertIn("⚡ **짧게** — ⏸️ 오늘은 거름 · 11/3 래블업 상장 예정(수급이 나뉨)", panel)
        self.assertNotIn("진입 신호", panel)
        self.assertEqual(msg["embeds"][2]["color"], signalbot.PANEL)
        rows[0]["value"] = 3.5e11
        panel = ipobot.compose(dt.date(2026, 11, 2), rows, [], [("2026-11-03", "래블업")], [], ipobot.fresh())["embeds"][2]["description"]
        self.assertIn("🔥 **크게** — 💤 오늘은 쉼 · 1위 거래대금이 5,000억 미달", panel)   # 거래대금 미달이 먼저
        self.assertIn("⚡ **짧게** — ⏸️ 오늘은 거름", panel)

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
        self.assertEqual([(x["rule"], x["entry"], x["after"]) for x in out["pending"]], [("big", 31000.0, True), ("small", 31000.0, True)])
        self.assertTrue(json.dumps(out))

    def test_listing_next_day_adds_nothing_to_pending(self):
        quotes = {ipobot.SAMPLE: [days_row("2026-10-30", 1, 1)], "A523400": [days_row("2026-10-30", 31000, 1.19e12)]}
        post = lambda url, body: KIND_LIST if url == ipobot.KIND_LIST else KIND_PLAN    # 11/2 영광 상장 예정
        msg, out = ipobot.build(self.today, ipobot.fresh(), FakeDaum(quotes), post, sleep=lambda s: None)
        self.assertFalse(msg["ring"])
        self.assertEqual(msg["content"], "📡 신규 추격 · 10/30 장 마감 → 11/2 · 오늘은 거름 · 11/2 영광 상장 예정")
        self.assertEqual(out["pending"], [])


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
        self.lasts = []
        return app

    def dm(self, token, msg):
        self.dms.append(msg)

    def at(self, day, h, m):
        return int(dt.datetime(2026, 11, day, h, m, tzinfo=fmt.KST).timestamp() * 1000)

    def built(self, n, fetch=None, last=None):
        self.lasts.append(last)
        return {"content": "c", "embeds": [], "ring": True}

    def run_btc(self, app, n, build=None):
        with mock.patch.object(store, "save_state"), mock.patch("candleman.bot.dm_owner", self.dm), \
                mock.patch.object(signalbot, "build", build or self.built):
            asyncio.run(app._maybe_signal(n))

    def test_btc_once_per_4h_close_and_after_a_short_wait(self):
        app = self.app()
        self.run_btc(app, self.at(2, 22, 0))                                     # 처음 켜면 지난 마감(11/2 19:00 분할)이 바로 감
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
        app._signal_next = 0
        self.run_btc(app, self.at(3, 3, 1))                                      # 4시간 뒤 03시 분할
        self.assertEqual(len(self.dms), 3)
        self.assertEqual(self.lasts, [None, self.at(2, 19, 0), self.at(2, 23, 0)])   # 지난 글의 마감을 넘겨 그 사이 바뀐 분할을 같이 적게
        self.assertEqual(app.state["signal_btc_end"], self.at(3, 3, 0))

    def test_phone_off_for_hours_sends_only_the_latest_close(self):
        app = self.app()
        app.state["signal_btc_end"] = self.at(2, 23, 0)
        self.run_btc(app, self.at(3, 9, 40))                                     # 03 · 07시를 놓침 → 07시 판정 글 하나에 그 사이를 같이
        self.assertEqual(len(self.dms), 1)
        self.assertEqual(self.lasts, [self.at(2, 23, 0)])
        self.assertEqual(app.state["signal_btc_end"], self.at(3, 7, 0))

    def test_btc_retries_after_failure(self):
        app = self.app()

        def boom(n, *a, **k):
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
                mock.patch.object(signalbot, "build", self.built):
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
        with mock.patch.object(signalbot, "build", return_value=msg), mock.patch.object(ipobot, "build", return_value=(msg, {"pending": [1]})), \
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
