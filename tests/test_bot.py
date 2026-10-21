"""/포지션 봇 시험 — 디스코드에 붙지 않고, 가짜 명령을 넣어 답과 막는 규칙을 봅니다."""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from candleman import store  # noqa: E402
from candleman.bot import BLOCK_S, COMMAND, Bot, RateGuard  # noqa: E402


class RateGuardTest(unittest.TestCase):
    def test_eleventh_call_in_five_minutes_blocks_for_six_hours(self):
        g = RateGuard()
        self.assertEqual([g.check("u", 1000 + i) for i in range(10)], ["ok"] * 10)
        self.assertEqual(g.check("u", 1010), "just_blocked")
        self.assertEqual(g.check("u", 1011), "blocked")
        self.assertEqual(g.check("other", 1011), "ok")                 # 다른 사람은 상관없음
        self.assertEqual(g.check("u", 1010 + BLOCK_S - 1), "blocked")
        self.assertEqual(g.check("u", 1010 + BLOCK_S), "ok")

    def test_spread_out_calls_are_fine(self):
        g = RateGuard()
        for i in range(30):                                           # 40초마다 → 5분에 8번
            self.assertEqual(g.check("u", i * 40), "ok")

    def test_block_survives_restart(self):
        g = RateGuard({"u": 5000.0})
        self.assertEqual(g.check("u", 4999), "blocked")
        self.assertEqual(g.check("u", 5001), "ok")


class FakeDiscord:
    def __init__(self):
        self.sent = []

    async def put(self, ch, text, embeds=None):
        self.sent.append((ch, text))


class FakeApp:
    def __init__(self):
        self.state = {}
        self.d = FakeDiscord()
        self.asked = 0

    async def positions_text(self):
        self.asked += 1
        return "📌 운영자가 지금 들고 있는 포지션 · 10/21 11:00 기준\n· 한국 주식 ETF(EWY) 롱 20주 · …"


OWNER = "99"


def interaction(i, channel="잡담방", uid="42", perms="0"):
    return {"type": 2, "id": "i%d" % i, "token": "tok%d" % i, "data": {"name": "포지션"}, "channel": {"name": channel},
            "guild_id": "g1", "member": {"permissions": perms, "nick": "태규", "user": {"id": uid, "username": "taegyu"}}}


class BotTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.old = (store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG)
        store.DATA_DIR = tmp
        store.STATE, store.JOURNAL, store.LOG = (os.path.join(tmp, n) for n in ("state.json", "journal.jsonl", "app.log"))
        self.app = FakeApp()
        self.bot = Bot("x.y.z", self.app)
        self.bot.app_id = "app1"
        self.bot.owners = {"g1": OWNER}
        self.calls = []

        async def fake_rest(method, path, body=None, auth=True):
            self.calls.append((method, path, body, auth))
            await asyncio.sleep(0)                                       # 진짜처럼 기다리는 동안 다른 일이 돌 수 있게
            return {"owner_id": OWNER} if (method, path) == ("GET", "/guilds/g1") else {}
        self.bot.rest = fake_rest

    def tearDown(self):
        store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG = self.old

    def run_one(self, d, now=1000.0):
        self.calls.clear()
        asyncio.run(self.bot.handle(d, now))
        return list(self.calls)

    def test_answers_in_chat_room_for_everyone(self):
        calls = self.run_one(interaction(1))
        self.assertEqual([(m, p) for m, p, _, _ in calls], [("POST", "/interactions/i1/tok1/callback"), ("PATCH", "/webhooks/app1/tok1/messages/@original")])
        self.assertEqual(calls[0][2], {"type": 5})                       # 공개 답 ('생각 중…' 뒤 내용)
        self.assertTrue(calls[1][2]["content"].startswith("📌 운영자가 지금 들고 있는 포지션"))
        self.assertFalse(any(auth for _, _, _, auth in calls))           # 답할 때는 봇 토큰을 쓰지 않음

    def test_other_channel_gets_private_hint(self):
        calls = self.run_one(interaction(2, channel="공지사항"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2]["type"], 4)
        self.assertEqual(calls[0][2]["data"]["flags"], 64)
        self.assertIn("#잡담방", calls[0][2]["data"]["content"])
        self.assertEqual(self.app.asked, 0)

    def test_spam_blocked_quietly(self):
        for i in range(10):
            self.run_one(interaction(i), now=1000.0 + i)
        calls = self.run_one(interaction(10), now=1010.0)
        self.assertEqual(calls[0][2]["data"]["flags"], 64)
        self.assertEqual(calls[0][2]["data"]["content"], "⏳ 지금은 /포지션을 쓸 수 없음. 나중에 다시 시도 바람.")
        self.assertNotIn("6시간", calls[0][2]["data"]["content"])        # 막힌 사람에게 규칙을 알리지 않음
        self.assertEqual(self.app.d.sent, [("status", "🚫 태규의 /포지션을 6시간 막음 (5분 안에 10번 넘게 부름)")])
        self.assertIn("42", self.app.state["cmd_blocked"])
        self.run_one(interaction(11), now=1020.0)
        self.assertEqual(len(self.app.d.sent), 1)                        # 막힌 뒤에는 알림을 또 보내지 않음
        self.assertEqual(self.app.asked, 10)

    def test_owner_never_blocked(self):
        for i in range(20):
            calls = self.run_one(interaction(i, uid=OWNER), now=1000.0 + i)
            self.assertEqual(calls[0][2], {"type": 5})
        self.assertEqual(self.app.d.sent, [])

    def test_admin_role_is_still_limited(self):                          # 빠지는 사람은 서버 주인 한 명뿐
        for i in range(10):
            self.assertEqual(self.run_one(interaction(i, perms=str(1 << 3)), now=1000.0 + i)[0][2], {"type": 5})
        calls = self.run_one(interaction(10, perms=str(1 << 3)), now=1010.0)
        self.assertEqual(calls[0][2]["data"]["flags"], 64)

    def test_owner_learned_when_unknown(self):
        self.bot.owners = {}
        calls = self.run_one(interaction(1, uid=OWNER))
        self.assertEqual(calls[0][2], {"type": 5})                       # 주인을 알아보는 동안에도 답은 바로 함
        self.assertEqual([p for m, p, _, _ in calls if m == "GET"], ["/guilds/g1"])
        self.assertEqual(self.bot.owners, {"g1": OWNER})
        self.assertEqual([p for m, p, _, _ in self.run_one(interaction(2, uid=OWNER)) if m == "GET"], [])   # 다시 묻지 않음

    def test_ready_registers_command_and_learns_owner(self):
        self.bot.owners = {}
        ready = {"application": {"id": "app2"}, "guilds": [{"id": "g1", "unavailable": True}]}
        self.calls.clear()
        asyncio.run(self.bot._on_ready(ready))
        self.assertEqual([(m, p, a) for m, p, _, a in self.calls], [("GET", "/guilds/g1", True), ("PUT", "/applications/app2/guilds/g1/commands", True)])
        self.assertEqual(self.calls[1][2], [COMMAND])
        self.assertNotIn("contexts", COMMAND)                            # 서버 전용 명령에는 안 붙임
        self.assertEqual(self.bot.owners, {"g1": OWNER})
        self.calls.clear()
        asyncio.run(self.bot._on_ready(ready))                           # 다시 붙어도 같은 명령은 다시 등록하지 않음
        self.assertEqual([m for m, _, _, _ in self.calls], ["GET"])

    def test_other_commands_ignored(self):
        d = interaction(1)
        d["data"]["name"] = "다른명령"
        self.assertEqual(self.run_one(d), [])


class PositionsTextTest(unittest.TestCase):
    def test_cached_for_thirty_seconds(self):
        from candleman.app import App

        class FB:
            n = 0

            async def positions(self):
                FB.n += 1
                return [{"symbol": "EWYUSDT", "positionAmt": "20", "entryPrice": "152.83", "notional": "3100", "unRealizedProfit": "12.5"}]
        app = App.__new__(App)
        app.b = FB()
        t1 = asyncio.run(app.positions_text())
        t2 = asyncio.run(app.positions_text())
        self.assertEqual(FB.n, 1)
        self.assertEqual(t1, t2)
        self.assertTrue(t1.startswith("📌 운영자가 지금 들고 있는 포지션 · "))
        self.assertIn("· 한국 주식 ETF(EWY) 롱 20주 · 평균 가격 $152.83 · 약 $3,100어치(레버리지 포함) · 미실현 +$12.50", t1)


if __name__ == "__main__":
    unittest.main()
