"""/포지션 봇 시험 — 디스코드에 붙지 않고, 가짜 명령을 넣어 답과 막는 규칙을 봅니다."""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from candleman import store  # noqa: E402
from candleman.app import App  # noqa: E402
from candleman.bot import BLOCK_S, COMMAND, INTRO_COMMAND, JOINS_COMMAND, NOTE_COMMAND, RETURN_COMMAND, Bot, RateGuard  # noqa: E402


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

    async def positions_message(self):
        self.asked += 1
        return {"content": "📌 **운영자 포지션** · 10/21 11:00 기준", "embeds": [{"title": "🟢 한국 주식 ETF(EWY) · 롱", "description": "수량 **20주**"}]}


OWNER = "99"


def interaction(i, channel="잡담방", uid="42", perms="0"):
    return {"type": 2, "id": "i%d" % i, "token": "tok%d" % i, "data": {"name": "포지션"}, "channel": {"name": channel},
            "guild_id": "g1", "member": {"permissions": perms, "nick": "태규", "user": {"id": uid, "username": "taegyu"}}}


class BotCase(unittest.TestCase):
    """가짜 앱을 단 봇 (시험 함수는 아래 두 반에 있음)."""
    App = FakeApp

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.old = (store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG)
        store.DATA_DIR = tmp
        store.STATE, store.JOURNAL, store.LOG = (os.path.join(tmp, n) for n in ("state.json", "journal.jsonl", "app.log"))
        self.app = self.App()
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


class BotTest(BotCase):
    def test_answers_in_chat_room_for_everyone(self):
        calls = self.run_one(interaction(1))
        self.assertEqual([(m, p) for m, p, _, _ in calls], [("POST", "/interactions/i1/tok1/callback"), ("PATCH", "/webhooks/app1/tok1/messages/@original")])
        self.assertEqual(calls[0][2], {"type": 5})                       # 공개 답 ('생각 중…' 뒤 내용)
        self.assertTrue(calls[1][2]["content"].startswith("📌 **운영자 포지션**"))
        self.assertEqual(calls[1][2]["embeds"][0]["title"], "🟢 한국 주식 ETF(EWY) · 롱")         # 종목 카드가 같이 감
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
        self.assertEqual(self.app.d.sent, [("status", "🚫 태규의 명령(/포지션)을 6시간 막음 (5분 안에 10번 넘게 부름)")])
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
        self.assertEqual(self.calls[1][2], [COMMAND, NOTE_COMMAND, JOINS_COMMAND, INTRO_COMMAND])
        self.assertNotIn("contexts", COMMAND)                            # 서버 전용 명령에는 안 붙임
        self.assertEqual(self.bot.owners, {"g1": OWNER})
        self.calls.clear()
        asyncio.run(self.bot._on_ready(ready))                           # 다시 붙어도 같은 명령은 다시 등록하지 않음
        self.assertEqual([m for m, _, _, _ in self.calls], ["GET"])

    def test_other_commands_ignored(self):
        d = interaction(1)
        d["data"]["name"] = "다른명령"
        self.assertEqual(self.run_one(d), [])
        d["data"]["name"] = "주인장수익률"                                  # 시작 금액이 없으면 이 명령도 없음
        self.assertEqual(self.run_one(d), [])


class ReturnApp(FakeApp):
    start_usd = 7800
    fail = False

    async def return_message(self):
        if self.fail:
            raise ConnectionError("바이낸스에 연결하지 못함")
        return {"content": "📊 2026 주인장 수익률 · 10/21 20:00 기준\n시작: $7,800.00\n현재: $8,123.45\n수익률: +4.15% (+$323.45)", "embeds": []}


class ReturnCommandTest(BotCase):
    """/주인장수익률 — 시작 금액이 있으면 /포지션과 함께 등록되고, 같은 규칙(#잡담방 · 막기)으로 수익률 글을 답함."""
    App = ReturnApp

    def test_registers_both_and_reregisters_when_list_changes(self):
        self.app.state["cmd_registered"] = {"g1": '{"description": "운영자가 지금 들고 있는 포지션 보기", "name": "포지션", "type": 1}'}
        self.calls.clear()
        asyncio.run(self.bot._on_ready({"application": {"id": "app1"}, "guilds": [{"id": "g1"}]}))
        puts = [b for m, _, b, _ in self.calls if m == "PUT"]
        self.assertEqual(puts, [[COMMAND, RETURN_COMMAND, NOTE_COMMAND, JOINS_COMMAND, INTRO_COMMAND]])    # 옛 등록(/포지션만)과 달라서 다시 등록
        self.assertTrue(all(len(c["description"]) <= 100 for c in puts[0]))

    def test_answers_with_return_text(self):
        d = interaction(1)
        d["data"]["name"] = "주인장수익률"
        calls = self.run_one(d)
        self.assertEqual(calls[0][2], {"type": 5})
        self.assertEqual(calls[1][2]["content"].split("\n"), ["📊 2026 주인장 수익률 · 10/21 20:00 기준", "시작: $7,800.00",
                                                             "현재: $8,123.45", "수익률: +4.15% (+$323.45)"])
        self.assertEqual(self.app.asked, 0)                                  # /포지션 쪽은 묻지 않음

    def test_other_channel_hint_and_failure_message(self):
        d = interaction(2, channel="공지사항")
        d["data"]["name"] = "주인장수익률"
        self.assertEqual(self.run_one(d)[0][2]["data"]["content"], "💬 /주인장수익률은 #잡담방에서 쓸 수 있음.")
        self.app.fail = True
        d = interaction(3)
        d["data"]["name"] = "주인장수익률"
        calls = self.run_one(d)
        self.assertEqual(calls[1][2]["content"], "⚠️ 지금은 바이낸스에서 값을 받지 못함. 잠시 뒤 다시 시도 바람.")   # '생각 중…'에 멈추지 않음

    def test_spam_blocked_quietly(self):
        for i in range(10):
            self.run_one(interaction(i), now=1000.0 + i)
        d = interaction(10)
        d["data"]["name"] = "주인장수익률"                                  # 두 명령을 합쳐 셈
        calls = self.run_one(d, now=1010.0)
        self.assertEqual(calls[0][2]["data"]["content"], "⏳ 지금은 /주인장수익률을 쓸 수 없음. 나중에 다시 시도 바람.")
        self.assertEqual(self.app.d.sent, [("status", "🚫 태규의 명령(/포지션 · /주인장수익률)을 6시간 막음 (5분 안에 10번 넘게 부름)")])


def note(i, uid=OWNER, channel="봇-상태", **opts):
    return {"type": 2, "id": "n%d" % i, "token": "nt%d" % i, "channel": {"name": channel}, "guild_id": "g1",
            "data": {"name": "일지", "options": [{"name": k, "value": v} for k, v in opts.items()]},
            "member": {"permissions": "0", "nick": "댕이", "user": {"id": uid, "username": "richdaeng"}}}


class NoteCommandTest(BotCase):
    """/일지 — 서버 주인만, 어느 채널에서든, 답은 쓴 사람에게만 · 일지(journal)와 #봇-상태에 남김."""

    def notes(self):
        return [r for r in store.read_journal(0, 10 ** 15) if r.get("kind") == "note"]

    def test_hidden_command_with_reason_choices(self):
        self.assertEqual(NOTE_COMMAND["default_member_permissions"], "0")    # 다른 사람에게는 명령 목록에도 안 보임
        opts = NOTE_COMMAND["options"]
        self.assertEqual([o["name"] for o in opts if o.get("required")], ["이유", "때"])   # 10/22: '때'를 비워 '진입'으로 잘못 남은 뒤로 꼭 고름
        self.assertTrue(opts[0].get("required") and opts[1].get("required"))  # 꼭 넣을 것이 맨 앞 (디스코드 규칙)
        self.assertEqual([c["value"] for c in opts[0]["choices"]], ["거시 경제", "뉴스 · 일정", "차트", "직감", "기타"])
        self.assertEqual([c["value"] for c in opts[1]["choices"]], ["진입", "추가", "정리", "스탑", "목표"])   # 한 일만 (손익은 체결 기록으로)
        self.assertEqual(len({o["name"] for o in opts}), len(opts))           # 이름이 겹치지 않음 (손절가 · 목표가)
        self.assertTrue(all(len(o["description"]) <= 100 for o in opts))

    def test_owner_note_saved_and_answered_privately(self):
        calls = self.run_one(note(1, 이유="거시 경제", 때="진입", 확신=4, 종목="ewy", 손절가=150, 목표가=165.5, 메모="FOMC 앞 매수"))
        text = "📝 일지 · 한국 주식 ETF(EWY) 진입\n거시 경제 · 확신 4/5 · 손절가 $150.00 · 목표가 $165.50\n메모: FOMC 앞 매수"
        (m, p, body, auth), = calls
        self.assertEqual((m, p, auth), ("POST", "/interactions/n1/nt1/callback", False))
        self.assertEqual(body["type"], 4)
        self.assertEqual(body["data"]["flags"], 64)                          # 쓴 사람에게만 보임
        self.assertEqual(body["data"]["content"], text + "\n(이 답은 나만 보임 · #봇-상태에 남김)")
        self.assertEqual(self.app.d.sent, [("status", text)])
        rec, = self.notes()
        self.assertEqual({k: rec[k] for k in ("when", "reason", "confidence", "symbol", "stop", "target", "memo")},
                         {"when": "진입", "reason": "거시 경제", "confidence": 4, "symbol": "EWY", "stop": 150, "target": 165.5,
                          "memo": "FOMC 앞 매수"})

    def test_symbol_defaults_to_last_order_or_fill(self):
        now = 1_800_000_000_000
        store.append([{"kind": "fill", "t": now - 7_200_000, "symbol": "EWYUSDT"},
                      {"kind": "spot_fill", "t": now - 3_600_000, "symbol": "SOLUSDT"}])
        self.run_one(note(2, 이유="직감", 때="정리"), now=now / 1000)
        self.assertEqual(self.app.d.sent[-1], ("status", "📝 일지 · 솔라나(SOL) 정리\n직감"))
        self.run_one(note(3, 이유="차트"), now=now / 1000 + 8 * 86400)        # 7일 넘게 주문 · 체결이 없으면
        self.assertEqual(self.app.d.sent[-1], ("status", "📝 일지 · 종목 모름 진입\n차트"))

    def test_goes_to_journal_channel_when_its_webhook_exists(self):
        self.app.d.has = lambda ch: ch == "journal"                         # DISCORD_WEBHOOK_JOURNAL 을 넣은 경우
        calls = self.run_one(note(6, 이유="뉴스 · 일정", 종목="SOL"))
        self.assertEqual(self.app.d.sent, [("journal", "📝 일지 · 솔라나(SOL) 진입\n뉴스 · 일정")])
        self.assertTrue(calls[0][2]["data"]["content"].endswith("\n(이 답은 나만 보임 · #매매일지에 남김)"))

    def test_others_refused_and_nothing_saved(self):
        calls = self.run_one(note(4, uid="42", 이유="직감"))
        self.assertEqual(calls[0][2]["data"], {"content": "🔒 /일지는 운영자만 쓸 수 있음.", "allowed_mentions": {"parse": []}, "flags": 64})
        self.assertEqual((self.notes(), self.app.d.sent), ([], []))

    def test_owner_looked_up_when_unknown_and_not_rate_limited(self):
        self.bot.owners = {}
        calls = self.run_one(note(5, 이유="차트"))
        self.assertEqual([p for m, p, _, _ in calls], ["/guilds/g1", "/interactions/n5/nt5/callback"])
        for i in range(15):                                                  # 주인은 막는 규칙과 상관없음
            self.run_one(note(10 + i, 이유="차트"), now=1000.0 + i)
        self.assertEqual(len(self.notes()), 16)


def joins(i, uid=OWNER, channel="잡담방", cid="c1"):
    return {"type": 2, "id": "w%d" % i, "token": "wt%d" % i, "channel_id": cid, "channel": {"id": cid, "name": channel}, "guild_id": "g1",
            "data": {"name": "환영정리"}, "member": {"permissions": "0", "nick": "댕이", "user": {"id": uid, "username": "richdaeng"}}}


def sid(ms, n=0):
    """그 시각(ms)에 만든 디스코드 번호."""
    return str(((ms - 1_420_070_400_000) << 22) + n)


NOW_MS = 1_793_150_000_000                                                 # 2026-10-28 낮
WHY = "입장 환영 글 정리 (/환영정리)"


class JoinsCommandTest(BotCase):
    """/환영정리 — 서버 주인만, 명령을 쓴 채널에서 디스코드가 올린 입장 글(type 7)만 지움 · 2주 넘은 글은 하나씩."""

    def setUp(self):
        super().setUp()
        self.pages, self.fail = [], set()                                   # GET 이 차례로 돌려줄 기록 · 실패시킬 주소 끝

        async def fake_rest(method, path, body=None, auth=True, reason=None):
            self.calls.append((method, path, body, auth, reason))
            await asyncio.sleep(0)
            if (method, path) == ("GET", "/guilds/g1"):
                return {"owner_id": OWNER}
            if any(path.endswith(s) for s in self.fail):
                raise RuntimeError("디스코드 %s channels 실패: HTTP 403" % method)
            if method == "GET":
                return self.pages.pop(0) if self.pages else []
            return {}
        self.bot.rest = fake_rest

    def page(self, start_ms, n, kinds):
        """디스코드처럼 최근 것부터 n개 — k번째 글의 type 은 kinds 를 돌려 가며."""
        return [{"id": sid(start_ms - k * 60_000, k), "type": kinds[k % len(kinds)]} for k in range(n)]

    def test_hidden_owner_command(self):
        self.assertEqual(JOINS_COMMAND["default_member_permissions"], "0")  # 다른 사람에게는 명령 목록에도 안 보임
        self.assertLessEqual(len(JOINS_COMMAND["description"]), 100)
        self.assertIn(JOINS_COMMAND, self.bot.commands)

    def test_deletes_only_join_messages_across_pages(self):
        p1 = self.page(NOW_MS, 100, [7, 0, 19, 7, 6])                       # 입장 · 사람 글 · 손 흔들기 답장 · 입장 · 고정 알림
        p2 = self.page(NOW_MS - 100 * 60_000, 31, [7, 0])
        p2[-1] = old = {"id": sid(NOW_MS - 15 * 86_400_000), "type": 7}      # 2주 넘은 입장 글은 한꺼번에 못 지움
        self.pages = [p1, p2]
        calls = self.run_one(joins(1), now=NOW_MS / 1000)
        want = [m["id"] for m in p1 + p2 if m["type"] == 7]
        self.assertEqual(calls[0][:4], ("POST", "/interactions/w1/wt1/callback", {"type": 5, "data": {"flags": 64}}, False))   # 나만 보는 '생각 중…'
        self.assertEqual([p for m, p, *_ in calls if m == "GET"],
                         ["/channels/c1/messages?limit=100", "/channels/c1/messages?limit=100&before=%s" % p1[-1]["id"]])
        bulk = [b["messages"] for m, p, b, *_ in calls if p.endswith("/bulk-delete")]
        dels = [p for m, p, *_ in calls if m == "DELETE"]
        self.assertEqual(sum(bulk, []) + [p.rsplit("/", 1)[1] for p in dels], want)   # 입장 글만 · 빠짐없이 · 한 번씩
        self.assertTrue(all(2 <= len(b) <= 100 for b in bulk))
        self.assertEqual(dels, ["/channels/c1/messages/%s" % old["id"]])     # 오래된 것은 하나씩
        self.assertEqual({r for m, p, _, _, r in calls if m == "DELETE" or p.endswith("/bulk-delete")}, {WHY})   # 감사 로그 까닭
        self.assertEqual(calls[-1][:2], ("PATCH", "/webhooks/app1/wt1/messages/@original"))
        self.assertEqual(calls[-1][2]["content"], "🧹 #잡담방의 입장 환영 글 %d개 지움\n(이 답은 나만 보임)" % len(want))
        self.assertEqual(self.app.d.sent, [("status", "🧹 #잡담방 입장 환영 글 %d개 지움 (/환영정리)" % len(want))])

    def test_single_join_deleted_alone_and_empty_channel(self):
        self.pages = [[{"id": sid(NOW_MS), "type": 7}, {"id": sid(NOW_MS - 60_000), "type": 0}]]
        calls = self.run_one(joins(2), now=NOW_MS / 1000)
        self.assertEqual([(m, p) for m, p, *_ in calls if m == "DELETE" or p.endswith("/bulk-delete")],
                         [("DELETE", "/channels/c1/messages/%s" % sid(NOW_MS))])   # 한꺼번에 지우기는 2개부터라 하나는 그냥
        self.pages = [[{"id": sid(NOW_MS - 60_000), "type": 0}]]
        calls = self.run_one(joins(3), now=NOW_MS / 1000)
        self.assertEqual(calls[-1][2]["content"], "🧹 #잡담방에 지울 입장 환영 글이 없음\n(이 답은 나만 보임)")
        self.assertEqual(len(self.app.d.sent), 1)                            # 지운 게 없으면 #봇-상태에 안 남김

    def test_others_refused_and_nothing_read(self):
        calls = self.run_one(joins(4, uid="42"))
        self.assertEqual(calls[0][2]["data"], {"content": "🔒 /환영정리는 운영자만 쓸 수 있음.", "allowed_mentions": {"parse": []}, "flags": 64})
        self.assertEqual([m for m, *_ in calls], ["POST"])
        self.assertEqual(self.app.d.sent, [])

    def test_missing_permission_reported(self):
        perm = "⚠️ 다 지우지 못함 — 🤖 봇 역할에 이 채널의 '메시지 관리 · 메시지 기록 보기' 권한이 있어야 함"
        self.fail = {"limit=100"}                                           # 기록 읽기부터 막힘
        self.assertEqual(self.run_one(joins(5), now=NOW_MS / 1000)[-1][2]["content"].split("\n")[0], perm)
        old = sid(NOW_MS - 20 * 86_400_000)
        self.pages = [[{"id": sid(NOW_MS), "type": 7}, {"id": sid(NOW_MS - 60_000, 1), "type": 7}, {"id": old, "type": 7}]]
        self.fail = {old}                                                   # 한꺼번에는 지웠고 오래된 하나에서 막힘
        calls = self.run_one(joins(6), now=NOW_MS / 1000)
        self.assertEqual(calls[-1][2]["content"].split("\n")[:2], ["🧹 #잡담방의 입장 환영 글 2개 지움", perm])
        self.assertEqual(self.app.d.sent, [("status", "🧹 #잡담방 입장 환영 글 2개 지움 (/환영정리)")])


def intro(i, sym, uid=OWNER):
    return {"type": 2, "id": "c%d" % i, "token": "ct%d" % i, "channel": {"name": "공지사항"}, "guild_id": "g1",
            "data": {"name": "종목소개", "options": [{"name": "종목", "type": 3, "value": sym}]},
            "member": {"permissions": "0", "nick": "댕이", "user": {"id": uid, "username": "richdaeng"}}}


class IntroDiscord:
    def __init__(self):
        self.sent, self.hooks = [], {"symbols"}

    def has(self, ch):
        return ch in self.hooks

    async def put(self, ch, text, embeds=None):
        self.sent.append((ch, embeds[0]["title"] if embeds else text))


class IntroApp(FakeApp):
    _introduce = App._introduce                                            # 소개는 진짜 코드로

    def __init__(self):
        super().__init__()
        self.d, self.b = IntroDiscord(), self
        self.kind_of = {"FOOUSDT": "COIN"}

    async def symbol_kind(self, symbol):
        return self.kind_of.get(symbol)


class IntroCommandTest(BotCase):
    """/종목소개 — 서버 주인만, 아직 소개하지 않은 종목의 카드를 #종목설명에 한 번 올림 · 답은 쓴 사람에게만."""
    App = IntroApp

    def answer(self, calls):
        return calls[-1][2].get("content") or calls[-1][2]["data"]["content"]

    def test_hidden_owner_command(self):
        self.assertEqual(INTRO_COMMAND["default_member_permissions"], "0")  # 다른 사람에게는 명령 목록에도 안 보임
        self.assertLessEqual(len(INTRO_COMMAND["description"]), 100)
        self.assertIn(INTRO_COMMAND, self.bot.commands)

    def test_owner_posts_card_once_in_any_channel(self):
        calls = self.run_one(intro(1, " koruusdt "))
        self.assertEqual(calls[0][:4], ("POST", "/interactions/c1/ct1/callback", {"type": 5, "data": {"flags": 64}}, False))   # 나만 보는 '생각 중…'
        self.assertEqual(calls[1][:2], ("PATCH", "/webhooks/app1/ct1/messages/@original"))
        self.assertEqual(self.app.d.sent, [("symbols", "🆕 KORU · 한국 주식 3배 ETF")])
        self.assertEqual(self.answer(calls), "🆕 한국 주식 3배 ETF(KORU) 소개 카드를 #종목설명에 올림\n(이 답은 나만 보임)")
        self.assertIn("KORU", self.app.state["introduced"])                 # 다음 거래 때 또 올라가지 않음
        calls = self.run_one(intro(2, "KORU"))
        self.assertEqual(self.answer(calls), "ℹ️ 이미 소개한 종목이라 다시 올리지 않음: 한국 주식 3배 ETF(KORU)\n(이 답은 나만 보임)")
        self.assertEqual(len(self.app.d.sent), 1)
        self.run_one(intro(3, "BTC"))                                       # 공지에 처음부터 있던 종목
        self.assertEqual(len(self.app.d.sent), 1)

    def test_unknown_symbol_gets_basic_card_and_note(self):
        self.run_one(intro(1, "FOO"))
        self.assertEqual([s[0] for s in self.app.d.sent], ["symbols", "status"])

    def test_others_are_refused(self):
        calls = self.run_one(intro(1, "KORU", uid="42"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2]["data"]["flags"], 64)
        self.assertEqual(calls[0][2]["data"]["content"], "🔒 /종목소개는 운영자만 쓸 수 있음.")
        self.assertEqual(self.app.d.sent, [])
        self.assertNotIn("introduced", self.app.state)

    def test_without_hook_says_how_and_keeps_it_for_later(self):
        self.app.d.hooks = set()
        calls = self.run_one(intro(1, "KORU"))
        self.assertEqual(len(calls), 1)
        self.assertIn("python -m candleman hook symbols", calls[0][2]["data"]["content"])
        self.assertEqual(self.app.d.sent, [])
        self.assertNotIn("introduced", self.app.state)

    def test_blank_or_dollar_is_not_a_symbol(self):
        for i, sym in enumerate(("   ", "usdt", "USDC")):
            calls = self.run_one(intro(i, sym))
            self.assertIn("종목 기호", calls[0][2]["data"]["content"])
        self.assertEqual(self.app.d.sent, [])


EWY = {"symbol": "EWYUSDT", "positionAmt": "20", "entryPrice": "152.83045678", "markPrice": "155.00000000",
       "notional": "3100.00000000", "unRealizedProfit": "12.49876543", "isolatedMargin": "0.00000000"}


class PositionsMessageTest(unittest.TestCase):
    """/포지션 답 — 바이낸스가 주는 긴 소수(8자리)를 줄이고, 실제 레버리지(거래 크기 ÷ 증거금)를 카드에 넣음."""

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.old = (store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG)
        store.DATA_DIR = tmp
        store.STATE, store.JOURNAL, store.LOG = (os.path.join(tmp, n) for n in ("state.json", "journal.jsonl", "app.log"))

    def tearDown(self):
        store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG = self.old

    def ask(self, positions, account=None, times=1):
        from candleman.app import App
        asked = {"positions": 0, "account": 0}

        class FB:
            async def positions(self):
                asked["positions"] += 1
                return positions

            async def account(self):
                asked["account"] += 1
                if account is None:
                    raise RuntimeError("연결 안 됨")
                return account
        app = App.__new__(App)
        app.b = FB()
        msgs = [asyncio.run(app.positions_message()) for _ in range(times)]
        return msgs, asked

    def test_card_has_one_fact_per_line_and_two_decimals(self):
        (m1, m2), asked = self.ask([EWY], {"totalMarginBalance": "3875.00000000"}, times=2)
        self.assertEqual(asked, {"positions": 1, "account": 1})          # 30초 안에는 바이낸스에 다시 묻지 않음
        self.assertEqual(m1, m2)
        self.assertTrue(m1["content"].startswith("📌 **운영자 포지션** · "))
        self.assertNotIn("전체", m1["content"])                           # 하나뿐이면 '전체' 줄은 없음
        card, = m1["embeds"]
        self.assertEqual(card["title"], "🟢 한국 주식 ETF(EWY) · 롱")
        self.assertEqual(card["color"], 2533018)
        self.assertEqual(card["description"].split("\n"), [
            "수량 **20주**", "평균 가격 **$152.83**", "지금 가격 **$155.00**", "거래 크기 **$3,100.00**",
            "실제 레버리지 **0.8배**", "미실현 손익 **+$12.50**"])
        self.assertEqual(card["footer"], {"text": "실제 레버리지 = 거래 크기 ÷ 증거금"})

    def test_several_positions_short_card_and_isolated_margin(self):
        btc = {"symbol": "BTCUSDT", "positionAmt": "-0.050", "entryPrice": "82000.1", "markPrice": "81000",
               "notional": "-4050", "unRealizedProfit": "-3", "isolatedMargin": "0"}
        sol = {"symbol": "SOLUSDT", "positionAmt": "10", "entryPrice": "150", "markPrice": "150",
               "notional": "1500", "unRealizedProfit": "0", "isolatedMargin": "300"}
        (m,), _ = self.ask([sol, EWY, btc], {"totalMarginBalance": "5000"})
        self.assertIn("\n전체 실제 레버리지 **1.73배**", m["content"])     # (4050 + 3100 + 1500) ÷ 5000
        b, e, s = m["embeds"]                                           # 기호 순서: BTC · EWY · SOL
        self.assertEqual((b["title"], b["color"]), ("🔴 비트코인(BTC) · 숏", 15684432))
        self.assertIn("수량 **0.05 BTC**", b["description"])
        self.assertIn("평균 가격 **$82,000.10**", b["description"])
        self.assertIn("실제 레버리지 **0.81배**", b["description"])          # 교차 마진: 4050 ÷ 계좌 증거금 5000
        self.assertIn("미실현 손익 **−$3.00**", b["description"])
        self.assertIn("실제 레버리지 **5배**", s["description"])             # 격리 마진: 1500 ÷ 그 포지션 증거금 300
        self.assertNotIn("footer", b)
        self.assertIn("footer", s)                                       # 설명은 마지막 카드에만

    def test_without_margin_shows_cards_without_leverage(self):
        (m,), _ = self.ask([EWY], None)
        self.assertNotIn("레버리지", m["embeds"][0]["description"])
        self.assertNotIn("footer", m["embeds"][0])

    def test_no_positions(self):
        (m,), _ = self.ask([], {"totalMarginBalance": "100"})
        self.assertTrue(m["content"].startswith("📌 운영자는 지금 들고 있는 포지션이 없음 · "))
        self.assertEqual(m["embeds"], [])

    def test_more_than_ten_positions_fold_into_last_card(self):
        many = [dict(EWY, symbol="C%02dUSDT" % i) for i in range(12)]
        (m,), _ = self.ask(many, {"totalMarginBalance": "100000"})
        self.assertEqual(len(m["embeds"]), 10)
        self.assertEqual(m["embeds"][-1]["title"], "외 3개")


if __name__ == "__main__":
    unittest.main()
