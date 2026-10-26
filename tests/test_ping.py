"""알림 채널의 @everyone 시험 — 머리 줄 끝에 붙는지 · #봇-상태는 안 울리는지 · 웹후크로 보내는 내용(allowed_mentions) ·
PING_EVERYONE 설정 (10/26 댕이: 서버 기본 알림을 '@mentions만'으로 바꾸고 매매 · 포지션 등의 알림에 에브리원)."""
import asyncio
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from candleman import config  # noqa: E402
from candleman.discord import Discord, with_ping  # noqa: E402


class WithPingTest(unittest.TestCase):
    def test_after_head_line(self):
        self.assertEqual(with_ping("🟢 롱 진입 · 비트코인(BTC)"), "🟢 롱 진입 · 비트코인(BTC) @everyone")
        self.assertEqual(with_ping("📌 **운영자 포지션** · 10/21 11:00 기준\n전체 실제 레버리지 **1.73배**"),
                         "📌 **운영자 포지션** · 10/21 11:00 기준 @everyone\n전체 실제 레버리지 **1.73배**")
        self.assertEqual(with_ping(""), "@everyone")                              # 카드만 있는 글

    def test_other_everyone_or_here_in_text_never_ring(self):
        self.assertEqual(with_ping("뉴스: @here 주목 @everyone"), "뉴스: @​here 주목 @​everyone @everyone")


class PutTest(unittest.TestCase):
    def setUp(self):
        hook = "https://discord.com/api/webhooks/1/%s"
        self.d = Discord({ch: hook % ch for ch in ("trade", "status", "positions", "briefing")}, ping=("trade", "positions", "briefing"))

    def put(self, *a):
        asyncio.run(self.d.put(*a))
        return self.d.q.get_nowait()

    def test_alert_channels_ring_and_status_does_not(self):
        ch, text, embeds = self.put("trade", "🟢 비트코인(BTC) 현물 매수\n$83,000.00에 0.05 BTC · 약 $4,150어치 · 바로 체결(시장가)")
        self.assertEqual(text, "🟢 비트코인(BTC) 현물 매수 @everyone")            # 카드로 바꾼 뒤 머리 줄 끝에
        self.assertNotIn("@everyone", json.dumps(embeds, ensure_ascii=False))       # 카드 안에는 안 넣음
        self.assertEqual(self.put("positions", "📌 **운영자 포지션** · 10/21 11:00 기준", [{"title": "카드"}])[1],
                         "📌 **운영자 포지션** · 10/21 11:00 기준 @everyone")
        self.assertEqual(self.put("status", "🤖 시작함\n포지션 1개")[1], "🤖 시작함")
        plain = Discord({"trade": "u1"})                                            # 설정이 없으면 아무 데도 안 울림
        asyncio.run(plain.put("trade", "🟢 롱 진입 · 비트코인(BTC)"))
        self.assertNotIn("@everyone", plain.q.get_nowait()[1])

    def test_webhook_payload_allows_everyone_only_for_alert_channels(self):
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
            self.d._post("trade", "🟢 롱 진입 · 비트코인(BTC) @everyone", [{"title": "카드"}])
            self.d._post("status", "🤖 시작함 @everyone")
        self.assertEqual(sent[0]["allowed_mentions"], {"parse": ["everyone"]})
        self.assertEqual(sent[0]["content"], "🟢 롱 진입 · 비트코인(BTC) @everyone")
        self.assertEqual(sent[1]["allowed_mentions"], {"parse": []})                # #봇-상태는 써 있어도 안 울림


class ConfigTest(unittest.TestCase):
    def test_ping_channels(self):
        self.assertEqual(config._ping({}), ("trade", "positions", "summary", "briefing"))
        self.assertEqual(config._ping({"PING_EVERYONE": "trade, journal,trade"}), ("trade", "journal"))
        for off in ("0", "off", "", "none"):
            self.assertEqual(config._ping({"PING_EVERYONE": off}), ())
        with self.assertRaises(config.ConfigError):
            config._ping({"PING_EVERYONE": "trade,status"})                         # #봇-상태는 넣을 수 없음


if __name__ == "__main__":
    unittest.main()
