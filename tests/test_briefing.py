"""아침 경제 브리핑 시험 — 협회 숫자 모양 · 클라우드 글 검사(오늘 것만 · 짧게) · 8시 올리기와 기다리기."""
import asyncio
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from candleman import briefing, fmt, store  # noqa: E402

TODAY = dt.date(2026, 10, 22)


def fake_kofia(url, body):
    """협회 응답 흉내: 10/16 · 10/19 이틀치 (백만 원)."""
    obj = body["dmSearch"]["OBJ_NM"]
    if obj == "STATSCU0100000060BO":
        rows = [{"TMPV1": "20261016", "TMPV2": "94402968", "TMPV5": "1232273", "TMPV6": "57248", "TMPV7": "4.1"},
                {"TMPV1": "20261019", "TMPV2": "92096622", "TMPV5": "1429273", "TMPV6": "54231", "TMPV7": "4.4"}]
    else:
        rows = [{"TMPV1": "20261016", "TMPV2": "32252822"}, {"TMPV1": "20261019", "TMPV2": "31108258"}]
    return json.dumps({"ds1": rows})


def brief_json(date="2026-10-22", **k):
    j = {"date": date, "overnight": ["미국 증시: 나스닥 +1.2% · S&P500 +0.8%", "비트코인 $67,200 (+2.1%)"],
         "schedule": [{"when": "10/23 (금) 10:00", "what": "한국은행 금통위 · 기준금리 결정", "impact": 3, "why": "국내 금리 · 환율"},
                      {"when": "10/29 (목) 03:00", "what": "미국 FOMC 기준금리 결정", "impact": "5"},     # 현지 10/28 14:00 → 한국 시각
                      {"when": "10/30 (금) 05시쯤", "what": "아마존 실적 발표", "impact": 9},           # 이상한 점수는 별 없이
                      "11/4 (수) 오전 미국 중간선거 개표"]}
    j.update(k)
    return json.dumps(j, ensure_ascii=False)


class FormatTest(unittest.TestCase):
    def test_won(self):
        self.assertEqual(briefing.won("31108258"), "31.11조 원")
        self.assertEqual(briefing.won("54231"), "542억 원")
        self.assertEqual(briefing.won_delta(D("-1144564")), "−1.14조")
        self.assertEqual(briefing.won_delta(D("197000")), "+1,970억")
        self.assertEqual(briefing.won_delta(D("0")), "변화 없음")

    def test_credit_card_from_kofia(self):
        rows = briefing.kofia_rows(TODAY - dt.timedelta(days=14), TODAY, fetch=fake_kofia)
        card = briefing.credit_card(rows)
        self.assertEqual(card["title"], "💳 신용 · 미수 잔고 (10/19 월 기준)")
        self.assertEqual(card["description"].split("\n"), [
            "신용융자 **31.11조 원** (−1.14조)", "미수금 **1.43조 원** (+1,970억)",
            "반대매매 **542억 원** (미수금의 4.4%)", "투자자예탁금 **92.10조 원** (−2.31조)"])


class BriefTest(unittest.TestCase):
    def test_only_todays_brief_and_clipped(self):
        self.assertIsNone(briefing.parse_brief(brief_json(date="2026-10-21"), TODAY))     # 어제 것은 안 씀
        self.assertIsNone(briefing.parse_brief("<html>404</html>", TODAY))
        night, plan = briefing.parse_brief(brief_json(overnight=["가" * 200] + ["줄"] * 10), TODAY)
        self.assertEqual(len(night), briefing.MAX_LINES)
        self.assertEqual(len(night[0]), briefing.MAX_CHARS)
        self.assertEqual(plan, ["★★★☆☆ 3점 · **10/23 (금) 10:00** 한국은행 금통위 · 기준금리 결정",
                                "★★★★★ 5점 · **10/29 (목) 03:00** 미국 FOMC 기준금리 결정",
                                "**10/30 (금) 05시쯤** 아마존 실적 발표", "11/4 (수) 오전 미국 중간선거 개표"])

    def test_compose(self):
        rows = briefing.kofia_rows(TODAY, TODAY, fetch=fake_kofia)
        m = briefing.compose(TODAY, rows, briefing.parse_brief(brief_json(), TODAY))
        self.assertEqual(m["content"], "🗞️ **10/22 (목) 아침 경제 브리핑**")
        self.assertEqual([c["title"] for c in m["embeds"]], ["💳 신용 · 미수 잔고 (10/19 월 기준)", "🌙 밤사이",
                                                            "📅 다가오는 일정 (한국 시각) · 중요도 5점 만점"])
        self.assertEqual(m["embeds"][1]["description"], "· 미국 증시: 나스닥 +1.2% · S&P500 +0.8%\n· 비트코인 $67,200 (+2.1%)")
        self.assertEqual(m["embeds"][-1]["footer"]["text"], briefing.STAR_NOTE + "\n" + briefing.FOOTER)  # 별 뜻 + AI 정리
        plain = briefing.compose(TODAY, rows, briefing.parse_brief(brief_json(schedule=["11/4 (수) 오전 미국 중간선거 개표"]), TODAY))
        self.assertEqual(plain["embeds"][-1]["title"], "📅 다가오는 일정 (한국 시각)")       # 별이 없으면 설명도 없음 · 일정은 모두 한국 시각
        self.assertEqual(plain["embeds"][-1]["footer"]["text"], briefing.FOOTER)
        bare = briefing.compose(TODAY, rows, None)
        self.assertTrue(bare["content"].endswith("밤사이 소식 · 일정은 오늘 준비되지 않음"))
        self.assertEqual(len(bare["embeds"]), 1)


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.old = (store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG, briefing.kofia_rows)
        store.DATA_DIR = tmp
        store.STATE, store.JOURNAL, store.LOG = (os.path.join(tmp, n) for n in ("state.json", "journal.jsonl", "app.log"))
        briefing.kofia_rows = lambda a, b: self.old[4](a, b, fetch=fake_kofia)

    def tearDown(self):
        store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG, briefing.kofia_rows = self.old

    def make(self, brief_text):
        from candleman.app import App
        from candleman.discord import Discord
        app = App.__new__(App)
        app.d, app.state, app.cfg = Discord({"trade": "u1", "status": "u3", "briefing": "u6"}), {}, {}
        app._fetch_brief = lambda: brief_text
        return app

    def sent(self, app):
        out = []
        while not app.d.q.empty():
            ch, text, embeds = app.d.q.get_nowait()
            out.append((ch, text, len(embeds or [])))
        return out

    def at(self, h, mi):
        return int(dt.datetime(2026, 10, 22, h, mi, tzinfo=fmt.KST).timestamp() * 1000)

    def test_posts_at_eight_once(self):
        app = self.make(brief_json())
        asyncio.run(app._maybe_briefing(self.at(7, 59)))
        self.assertEqual(self.sent(app), [])
        asyncio.run(app._maybe_briefing(self.at(8, 0)))
        self.assertEqual(self.sent(app), [("briefing", "🗞️ **10/22 (목) 아침 경제 브리핑**", 3)])
        app._brief_next = 0
        asyncio.run(app._maybe_briefing(self.at(8, 5)))
        self.assertEqual(self.sent(app), [])                                       # 하루 한 번

    def test_waits_for_cloud_brief_then_posts_numbers_only(self):
        app = self.make(None)                                                       # 클라우드 글이 아직 없음
        asyncio.run(app._maybe_briefing(self.at(8, 0)))
        self.assertEqual(self.sent(app), [])                                       # 조금 기다림
        app._brief_next = 0
        asyncio.run(app._maybe_briefing(self.at(8, 21)))                            # 20분이 지나면 숫자만이라도
        out = self.sent(app)
        self.assertEqual([(ch, n) for ch, _, n in out], [("briefing", 1), ("status", 0)])
        self.assertIn("받지 못해 숫자만 올림", out[1][1])

    def test_off_without_webhook(self):
        app = self.make(brief_json())
        app.d = __import__("candleman.discord", fromlist=["Discord"]).Discord({"trade": "u1"})
        asyncio.run(app._maybe_briefing(self.at(8, 0)))
        self.assertEqual(self.sent(app), [])


if __name__ == "__main__":
    unittest.main()
