"""커뮤니티 기능 시험 — 레벨 계산 · 도배 알아보기 · 입장 버튼 · 꺼져 있던 사이 기록 채우기 · /레벨 · /순위 · /규칙올리기 ·
게이트웨이 설정(글 받기 · 글 내용 받기가 거절될 때). 디스코드에 붙지 않고 가짜 REST 로 봅니다."""
import asyncio
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from candleman import community as cm  # noqa: E402
from candleman import store  # noqa: E402
from candleman.bot import COMMAND, GUILD_MESSAGES, MESSAGE_CONTENT, NOTE_COMMAND, Bot  # noqa: E402
from candleman.ws import WSClosed  # noqa: E402

G, OWNER = "g1", "99"
T0 = 1_793_000_000_000
ROLES = ([{"id": "r_owner", "name": "👑 운영자", "position": 15}, {"id": "r_bot", "name": "🤖 봇", "position": 14},
          {"id": "r_app", "name": "매매일지봇", "position": 13, "managed": True},
          {"id": "r_old", "name": "Lv.11 없는 레벨", "position": 13}, {"id": "r_ok", "name": "✅ 인증", "position": 1},
          {"id": G, "name": "@everyone", "position": 0}]
         + [{"id": "lv%d" % n, "name": "%s Lv.%d" % (cm.LEVEL_EMOJI[n], n), "position": 2 + n, "color": 1000 + n} for n in range(11)])
CHANNELS = [{"id": "c_chat", "name": "잡담방", "type": 0}, {"id": "c_rules", "name": "규칙", "type": 0},
            {"id": "c_status", "name": "봇-상태", "type": 0}, {"id": "c_trade", "name": "실시간-매매", "type": 0},
            {"id": "c_voice", "name": "일반", "type": 2}, {"id": "c_cat", "name": "채팅 채널", "type": 4}]


def sid(t_ms, n=0):
    return str(((t_ms - cm.DISCORD_EPOCH_MS) << 22) + n)


def msg(uid, t, cid="c_chat", content="안녕하세요", n=0, roles=None, nick=None, bot=False, webhook=False, live=True):
    d = {"id": sid(t, n), "channel_id": cid, "type": 0, "content": content,
         "author": {"id": uid, "username": "user%s" % uid, "global_name": None, "bot": bot}}
    if live:
        d.update({"guild_id": G, "member": {"nick": nick, "roles": roles or []}})
    if webhook:
        d["webhook_id"] = "w1"
    return d


def button(uid="42", roles=None, nick="태규"):
    return {"type": 3, "id": "b1", "token": "bt1", "guild_id": G, "data": {"custom_id": cm.GATE_ID, "component_type": 2},
            "member": {"nick": nick, "roles": roles or [], "user": {"id": uid, "username": "taegyu"}}}


class FakeD:
    def __init__(self):
        self.sent = []

    async def put(self, ch, text, embeds=None):
        self.sent.append((ch, text))


class FakeApp:
    def __init__(self):
        self.state = {}
        self.d = FakeD()

    async def positions_message(self):
        return {"content": "📌 포지션", "embeds": []}


class FakeRest:
    """Bot.rest 자리에 끼우는 가짜. 부른 것을 calls 에 남기고, 정해 둔 길에는 실패를 냄."""

    def __init__(self):
        self.calls = []
        self.fail = {}                 # (방법, 길 앞부분) → 오류 글
        self.history = {}              # 채널 → 기록 글들
        self.members = {}              # 사람 → 지금 역할
        self.next_id = 0

    async def __call__(self, method, path, body=None, auth=True, reason=None):
        self.calls.append((method, path, body, reason))
        await asyncio.sleep(0)
        for (m, prefix), err in self.fail.items():
            if m == method and path.startswith(prefix):
                raise RuntimeError(err)
        if path == "/guilds/g1/roles":
            return ROLES
        if path == "/guilds/g1/channels":
            return CHANNELS
        if path == "/guilds/g1":
            return {"owner_id": OWNER}
        if method == "GET" and path.endswith("/messages/@original"):
            return {"id": "orig1"}
        if method == "GET" and "/messages?after=" in path:
            cid = path.split("/")[2]
            after = int(path.split("after=")[1].split("&")[0])
            page = [m for m in sorted(self.history.get(cid, []), key=lambda m: int(m["id"])) if int(m["id"]) > after][:100]
            return list(reversed(page))                  # 디스코드처럼 새것부터
        if method == "GET" and path.startswith("/guilds/g1/members/"):
            uid = path.rsplit("/", 1)[1]
            if uid not in self.members:
                raise RuntimeError("디스코드 GET guilds 실패: HTTP 404")
            return {"roles": self.members[uid]}
        if method == "POST" and (path.endswith("/messages") or path.startswith("/webhooks/")):
            self.next_id += 1
            return {"id": "m%d" % self.next_id}
        return {}

    def made(self, method=None, prefix=""):
        return [(m, p, b) for m, p, b, _ in self.calls if (method is None or m == method) and p.startswith(prefix)]


class FakeBot:
    def __init__(self, app, rest):
        self.app, self.rest, self.app_id, self.owners = app, rest, "app1", {G: OWNER}
        self.replies = []

    async def _reply(self, iid, tok, text, private=False):
        self.replies.append((text, private))


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.old = (store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG)
        store.DATA_DIR = tmp
        store.STATE, store.JOURNAL, store.LOG = (os.path.join(tmp, n) for n in ("state.json", "journal.jsonl", "app.log"))
        self.app = FakeApp()
        self.rest = FakeRest()
        self.bot = FakeBot(self.app, self.rest)
        self.c = cm.Community(self.bot)
        asyncio.run(self.c.load(G))
        self.rest.calls.clear()

    def tearDown(self):
        store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG = self.old

    def feed(self, *msgs, now=None):
        async def go():
            for m in msgs:
                await self.c.on_message(m, now_ms=now)
        asyncio.run(go())


class MathTest(unittest.TestCase):
    def test_levels_are_n_squared_hundreds(self):
        self.assertEqual([cm.need(n) for n in (1, 2, 5, 10)], [100, 400, 2500, 10000])
        self.assertEqual([cm.level_of(x) for x in (0, 99, 100, 399, 400, 9999, 10000, 99999)], [0, 0, 1, 1, 2, 9, 10, 10])

    def test_level_card_and_bar(self):
        levels, _ = cm.find_roles(ROLES)
        card = cm.level_card("태규", 160, levels, 3, 12)
        self.assertEqual(card["title"], "🐜 Lv.1 · 태규")
        self.assertEqual(card["description"].split("\n"), ["경험치 **160점** · 다음 레벨(🦐 Lv.2)까지 **240점**",
                                                           "▰▰▱▱▱▱▱▱▱▱ 20%", "순위 **3위** / 12명"])
        self.assertEqual(card["color"], 1001)                                 # 그 레벨 역할 색
        self.assertEqual(cm.level_card("댕이", 12345, levels)["description"].split("\n"), ["경험치 **12,345점**", "▰▰▰▰▰▰▰▰▰▰ 최고 레벨"])
        self.assertEqual(cm.level_card("x", 0, {})["title"], "🥚 Lv.0 · x")      # 역할을 못 찾아도 기본 이름

    def test_find_roles_by_name(self):
        levels, verified = cm.find_roles(ROLES)
        self.assertEqual(sorted(levels), list(range(11)))
        self.assertEqual((levels[10]["id"], levels[10]["name"]), ("lv10", "🔱 Lv.10"))
        self.assertEqual(verified, {"id": "r_ok", "name": "✅ 인증"})
        self.assertNotIn(11, levels)                                         # Lv.11 은 레벨이 아님

    def test_rules_message(self):
        levels, _ = cm.find_roles(ROLES)
        m = cm.rules_message(levels)
        rules, lv = m["embeds"]
        self.assertEqual(rules["description"].split("\n")[0], "1. 상호 존중 및 존대를 원칙으로 함 — 욕설 · 비하 · 인신공격 금지")
        self.assertEqual(len(rules["description"].split("\n")), 7)
        self.assertIn("같은 말을 4번 연속으로 보내면(ㅋㅋ · ㅋㅋㅋ 처럼 늘인 말도)", rules["description"])
        self.assertTrue(rules["description"].split("\n")[4].endswith("@everyone · @here 는 운영자만"))
        self.assertIn("🥚 Lv.0 → 🐜 Lv.1 → 🦐 Lv.2 → 🦀 Lv.3 → 🐙 Lv.4 → 🐟 Lv.5", lv["description"])
        self.assertIn("→ 🐬 Lv.6 → 🦈 Lv.7 → 🐋 Lv.8 → 🐳 Lv.9 → 🔱 Lv.10", lv["description"])
        btn = m["components"][0]["components"][0]
        self.assertEqual((btn["custom_id"], btn["label"], btn["style"]), (cm.GATE_ID, "규칙에 동의하고 입장", 3))
        self.assertTrue(all(len(e["description"]) <= 4096 for e in m["embeds"]))


class FloodTest(unittest.TestCase):
    def test_five_in_five_seconds(self):
        g = cm.FloodGuard()
        got = [g.check("u", T0 + i * 900, "m%d" % i, "c", "글%d" % i) for i in range(5)]
        self.assertEqual(got[:4], [None] * 4)
        self.assertEqual(got[4], ("fast", {"c": ["m0", "m1", "m2", "m3", "m4"]}))

    def test_five_spread_over_six_seconds_is_fine(self):
        g = cm.FloodGuard()
        self.assertEqual([g.check("u", T0 + i * 1500, "m%d" % i, "c", "글%d" % i) for i in range(5)], [None] * 5)

    def test_same_words_four_times(self):
        g = cm.FloodGuard()
        got = [g.check("u", T0 + i * 10_000, "m%d" % i, "c%d" % (i % 2), "가즈아 !" if i % 2 else "가즈아!") for i in range(4)]
        self.assertEqual(got[3], ("same", {"c0": ["m0", "m2"], "c1": ["m1", "m3"]}))
        g2 = cm.FloodGuard()
        texts = ["가즈아", "가즈아", "와 미쳤다", "가즈아", "가즈아"]                # 중간에 다른 말이 끼면 아님
        self.assertEqual([g2.check("u", T0 + i * 10_000, "m%d" % i, "c", t) for i, t in enumerate(texts)], [None] * 5)
        g3 = cm.FloodGuard()                                                 # 1분이 넘게 띄엄띄엄이면 아님
        self.assertEqual([g3.check("u", T0 + i * 25_000, "m%d" % i, "c", "가즈아") for i in range(4)], [None] * 4)

    def run_texts(self, texts, gap_ms=2_000):
        g = cm.FloodGuard()
        return [g.check("u", T0 + i * gap_ms, "m%d" % i, "c", t) for i, t in enumerate(texts)]

    def test_laughs_entered_four_times_in_a_row(self):          # 10/26 댕이: "ㅋㅋ 하고 엔터, ㅋㅋㅋ 하고 엔터"
        got = self.run_texts(["ㅋㅋ", "ㅋㅋㅋ", "ㅋㅋ", "ㅋㅋㅋ"])
        self.assertEqual(got[:3], [None] * 3)
        self.assertEqual(got[3], ("same", {"c": ["m0", "m1", "m2", "m3"]}))
        self.assertEqual(self.run_texts(["가즈아!", "가즈아아아", "가즈 아", "가즈아~~"])[3][0], "same")
        self.assertEqual(self.run_texts(["🚀", "🚀🚀", "🚀", "🚀🚀🚀"])[3][0], "same")
        self.assertEqual(self.run_texts(["ㅇㅇ", "ㅇㅇ", "ㅇㅇ"]), [None] * 3)                 # 세 번까지는 괜찮음

    def test_not_flood(self):
        self.assertEqual(self.run_texts(["ㅋ" * 60, "가즈아 " * 10, "🚀" * 30]), [None] * 3)   # 글 하나 안의 되풀이는 도배 아님
        self.assertEqual(self.run_texts(["ㅋㅋ", "ㅋㅋㅋ", "아 진짜 ㅋㅋ", "ㅋㅋ", "ㅋㅋㅋ"]), [None] * 5)   # 중간에 다른 말
        self.assertEqual(self.run_texts(["", "", "", ""]), [None] * 4)                     # 사진만 올린 글은 내용으로 안 봄
        self.assertEqual(self.run_texts(["!!!", "?!", "!!", "..."]), [None] * 4)

    def test_same_form(self):
        self.assertEqual([cm.same_form(t) for t in ("ㅋㅋㅋ", "가즈아아아!", "가즈 아", "가즈아~~", "❤️❤️", "!!!", "^^", "BTC 롱!", "btc롱")],
                         ["ㅋ", "가즈아", "가즈아", "가즈아", "❤", "", "", "btc롱", "btc롱"])

    def test_timeout_then_ban_within_a_day(self):
        g = cm.FloodGuard()
        self.assertEqual(g.strike("u", T0), "timeout")
        quick = [g.check("u", T0 + 1000 + i * 500, "m%d" % i, "c", "글%d" % i) for i in range(5)]
        self.assertEqual(quick, [None] * 5)                                  # 처리한 직후 30초는 다시 처리하지 않음
        self.assertEqual(g.strike("u", T0 + 3_600_000), "ban")
        self.assertEqual(g.strike("v", T0), "timeout")
        self.assertEqual(g.strike("v", T0 + 25 * 3_600_000), "timeout")      # 24시간이 지나면 다시 첫 번째
        self.assertEqual(cm.FloodGuard({"w": [T0]}).strike("w", T0 + 60_000), "ban")   # 다시 켜져도 기억


class LevelsTest(unittest.TestCase):
    def test_once_a_minute(self):
        lv = cm.Levels({})
        self.assertEqual(lv.add("u", "태규", T0), (0, 0))
        self.assertIsNone(lv.add("u", "태규", T0 + 30_000))
        for i in range(1, 5):
            r = lv.add("u", "태규", T0 + i * 60_000)
        self.assertEqual((r, lv.xp("u")), ((0, 1), 100))
        self.assertIsNone(lv.add("u", "태규", T0))                            # 옛 글은 다시 안 셈
        self.assertEqual(lv.rank("u"), (1, 1))
        self.assertEqual(lv.rank("nobody"), (None, 1))


class LiveTest(Case):
    def test_level_up_announces_and_swaps_roles(self):
        self.feed(*[msg("42", T0 + i * 61_000, n=i, roles=["r_ok", "lv0"], nick="태규") for i in range(5)])
        self.assertEqual(self.c.levels.xp("42"), 100)
        posts = self.rest.made("POST", "/channels/c_chat/messages")
        self.assertEqual(posts, [("POST", "/channels/c_chat/messages", {"content": "🎉 <@42> 님 레벨 업! **🐜 Lv.1**",
                                                                         "allowed_mentions": {"parse": []}})])
        self.assertEqual([(m, p) for m, p, _ in self.rest.made(prefix="/guilds/g1/members/42/roles")],
                         [("DELETE", "/guilds/g1/members/42/roles/lv0"), ("PUT", "/guilds/g1/members/42/roles/lv1")])
        self.assertEqual(self.c.levels.synced["42"], 1)
        self.assertEqual(self.c.levels.users["42"]["name"], "태규")

    def test_quick_messages_count_once_and_skip_channels_bots_webhooks(self):
        self.feed(msg("42", T0), msg("42", T0 + 10_000, n=1, content="두 번째"), msg("7", T0, n=2, cid="c_status"),
                  msg("8", T0, n=3, bot=True), msg("9", T0, n=4, webhook=True), msg("5", T0, n=5, cid="c_rules"))
        self.assertEqual({k: v["xp"] for k, v in self.c.levels.users.items()}, {"42": 20})
        self.assertEqual(self.rest.calls, [])

    def test_flood_timeout_then_ban(self):
        now = T0 + 5_000
        self.feed(*[msg("42", T0 + i * 800, n=i, content="글%d" % i, nick="태규") for i in range(5)], now=now)
        bulk = self.rest.made("POST", "/channels/c_chat/messages/bulk-delete")
        self.assertEqual(bulk, [("POST", "/channels/c_chat/messages/bulk-delete", {"messages": [sid(T0 + i * 800, i) for i in range(5)]})])
        patch, = self.rest.made("PATCH", "/guilds/g1/members/42")
        self.assertEqual(patch[2], {"communication_disabled_until": "2026-10-26T08:33:25+00:00"})   # now + 1시간 (UTC)
        self.assertEqual(self.rest.made("POST", "/channels/c_chat/messages")[-1][2]["content"],
                         "🧹 도배(5초 안에 5개 이상)로 <@42> 님 글을 지우고 1시간 타임아웃함 · 규칙 5번")
        self.assertEqual(self.app.d.sent[-1], ("status", "🧹 도배 처리 · 태규\n5초 안에 5개 이상 · 글 5개 지움 · 1시간 타임아웃"))
        self.assertEqual(self.c.data["strikes"], {"42": [now]})
        self.rest.calls.clear()
        t1 = T0 + 40_000                                                     # 30초가 지나 다시 도배 → 하루 안 두 번째 = 밴
        self.feed(*[msg("42", t1 + i * 500, n=i, content="또%d" % i, nick="태규") for i in range(5)], now=t1 + 3_000)
        self.assertEqual(self.rest.made("PUT", "/guilds/g1/bans/42"), [("PUT", "/guilds/g1/bans/42", {"delete_message_seconds": 3600})])
        self.assertEqual(self.rest.made("POST", "/channels/c_chat/messages")[-1][2]["content"], "🚫 도배를 되풀이해 <@42> 님을 밴함 · 규칙 5번")
        self.assertTrue(self.app.d.sent[-1][1].startswith("🚫 도배 처리 · 태규"))
        acts = [(p, r) for m, p, b, r in self.rest.calls if m in ("PUT", "PATCH", "DELETE") or p.endswith("bulk-delete")]
        self.assertEqual(acts, [("/channels/c_chat/messages/bulk-delete", "도배"), ("/guilds/g1/bans/42", "도배 되풀이")])   # 감사 로그에 까닭

    def test_owner_is_never_punished(self):
        self.feed(*[msg(OWNER, T0 + i * 500, n=i, content="공지%d" % i) for i in range(8)])
        self.assertEqual(self.rest.made(prefix="/guilds"), [])
        self.assertEqual(self.c.levels.xp(OWNER), 20)

    def test_flood_without_permission_warns_once(self):
        self.rest.fail[("POST", "/channels/c_chat/messages/bulk-delete")] = "디스코드 POST channels 실패: HTTP 403"
        self.rest.fail[("PATCH", "/guilds/g1/members/")] = "디스코드 PATCH guilds 실패: HTTP 403"
        self.feed(*[msg("42", T0 + i * 800, n=i, content="글%d" % i) for i in range(5)], now=T0 + 5_000)
        self.feed(*[msg("43", T0 + i * 800, n=10 + i, content="글%d" % i) for i in range(5)], now=T0 + 5_000)
        warns = [t for ch, t in self.app.d.sent if t.startswith("⚠️ 도배 처리 실패")]
        self.assertEqual(len(warns), 1)
        self.assertIn("메시지 관리 · 멤버 타임아웃 · 멤버 차단", warns[0])
        self.assertEqual([p for m, p, _ in self.rest.made("POST") if p == "/channels/c_chat/messages"], [])   # 처리 못 했으면 채널에 안 알림
        self.assertFalse(any(t.startswith("🧹") for _, t in self.app.d.sent))

    def test_timeout_still_given_when_delete_is_refused(self):
        self.rest.fail[("POST", "/channels/c_chat/messages/bulk-delete")] = "디스코드 POST channels 실패: HTTP 403"
        self.feed(*[msg("42", T0 + i * 800, n=i, content="글%d" % i, nick="태규") for i in range(5)], now=T0 + 5_000)
        self.assertEqual(len(self.rest.made("PATCH", "/guilds/g1/members/42")), 1)          # 지우기가 막혀도 타임아웃은 함
        self.assertTrue(any(t.startswith("⚠️ 도배 처리 실패") for _, t in self.app.d.sent))
        self.assertEqual(self.app.d.sent[-1], ("status", "🧹 도배 처리 · 태규\n5초 안에 5개 이상 · 글 0개 지움 · 1시간 타임아웃"))

    def test_owner_level_up_keeps_roles(self):
        self.feed(*[msg(OWNER, T0 + i * 61_000, n=i, roles=["r_owner"]) for i in range(5)])
        self.assertEqual(self.c.levels.level(OWNER), 1)
        self.assertEqual(len(self.rest.made("POST", "/channels/c_chat/messages")), 1)       # 레벨 업 글은 올림
        self.assertEqual(self.rest.made(prefix="/guilds"), [])                             # 역할은 그대로 (👑 운영자로 보임)
        self.assertEqual(self.c._unsynced(G), [])


class GateTest(Case):
    def click(self, d):
        asyncio.run(self.c.on_button(d))
        return self.rest.made("PATCH", "/webhooks/app1/bt1/messages/@original")[-1][2]["content"]

    def test_new_member_gets_verified_and_level_zero(self):
        text = self.click(button())
        self.assertEqual(self.rest.calls[0][:3], ("POST", "/interactions/b1/bt1/callback", {"type": 5, "data": {"flags": 64}}))
        self.assertEqual([(m, p) for m, p, _ in self.rest.made("PUT")],
                         [("PUT", "/guilds/g1/members/42/roles/r_ok"), ("PUT", "/guilds/g1/members/42/roles/lv0")])
        self.assertEqual(text, "✅ 입장 완료 · 이제 모든 채널이 보임\n#잡담방에서 인사 남겨 주세요 (존댓말이 원칙)")
        self.assertEqual(self.app.d.sent, [("status", "🚪 입장 · 태규")])

    def test_member_with_points_gets_their_level(self):
        self.c.levels.users["42"] = {"xp": 500, "last": T0}
        self.click(button())
        self.assertEqual([p for m, p, _ in self.rest.made("PUT")], ["/guilds/g1/members/42/roles/r_ok", "/guilds/g1/members/42/roles/lv2"])

    def test_already_verified(self):
        self.assertEqual(self.click(button(roles=["r_ok", "lv3"])), "✅ 이미 입장한 상태임 · 모든 채널이 보임")
        self.assertEqual(self.rest.made("PUT"), [])

    def test_without_permission_tells_member_and_owner_once(self):
        self.rest.fail[("PUT", "/guilds/g1/members/")] = "디스코드 PUT guilds 실패: HTTP 403"
        self.assertEqual(self.click(button()), "⚠️ 지금은 입장 처리가 안 됨. 운영자에게 알렸음. 잠시 뒤 다시 눌러 주세요.")
        self.click(button(uid="43"))
        self.assertEqual(len([t for _, t in self.app.d.sent if t.startswith("⚠️ 입장 버튼")]), 1)

    def test_other_buttons_ignored(self):
        d = button()
        d["data"]["custom_id"] = "other"
        asyncio.run(self.c.on_button(d))
        self.assertEqual(self.rest.calls, [])


class CatchUpTest(Case):
    def test_history_counted_quietly_then_roles_synced(self):
        self.rest.history = {
            "c_chat": [msg("42", T0 + i * 61_000, n=i, live=False) for i in range(6)] + [msg("43", T0, n=50, live=False)],
            "c_trade": [msg("1", T0, n=60, live=False, webhook=True)],
            "c_status": [msg("44", T0, n=70, live=False)]}
        self.rest.members = {"42": ["r_ok"], "43": ["r_ok", "lv0"]}
        asyncio.run(self.c.on_ready([G]))
        self.assertEqual({k: v["xp"] for k, v in self.c.levels.users.items()}, {"42": 120, "43": 20})
        self.assertEqual(self.rest.made("POST"), [])                           # 기록을 셀 때는 레벨 업 글을 안 올림
        self.assertEqual(self.c.levels.cursor, {"c_chat": sid(T0 + 5 * 61_000, 5), "c_trade": sid(T0, 60)})
        self.assertNotIn("c_status", self.c.levels.cursor)                     # #봇-상태는 읽지 않음
        self.assertEqual([p for m, p, _ in self.rest.made("PUT")], ["/guilds/g1/members/42/roles/lv1"])
        self.assertEqual(self.c.levels.synced, {"42": 1, "43": 0})
        self.rest.calls.clear()
        asyncio.run(self.c.on_ready([G]))                                     # 다시 붙어도 같은 글은 두 번 안 셈
        self.assertEqual(self.c.levels.xp("42"), 120)
        self.assertEqual(self.rest.made("PUT"), [])

    def test_left_members_are_skipped(self):
        self.rest.history = {"c_chat": [msg("77", T0 + i * 61_000, n=i, live=False) for i in range(5)]}
        asyncio.run(self.c.on_ready([G]))
        self.assertEqual(self.c.levels.synced, {"77": 1})                      # 나간 사람(404)은 다시 묻지 않음
        self.assertEqual(self.app.d.sent, [])

    def test_new_messages_wait_until_history_is_read(self):
        self.rest.history = {"c_chat": [msg("42", T0, n=1, live=False)]}
        self.rest.members = {"42": []}

        async def go():
            await asyncio.gather(self.c.on_ready([G]), self.c.on_message(msg("42", T0 + 120_000, n=2)))
        asyncio.run(go())
        self.assertEqual(self.c.levels.xp("42"), 40)                           # 기록 글과 새 글 둘 다
        self.assertEqual(self.c.levels.cursor["c_chat"], sid(T0 + 120_000, 2))


class CommandTest(Case):
    def test_level_and_rank(self):
        self.c.levels.users.update({"42": {"xp": 160, "name": "태규"}, "43": {"xp": 2600, "name": "성규"}, "44": {"xp": 40, "name": "오"}})
        me = {"guild_id": G, "data": {"name": "레벨"}, "member": {"nick": "태규", "user": {"id": "42"}}}
        card, = self.c.level_message(me)["embeds"]
        self.assertEqual(card["title"], "🐜 Lv.1 · 태규")
        self.assertIn("순위 **2위** / 3명", card["description"])
        other = dict(me, data={"name": "레벨", "options": [{"name": "멤버", "type": 6, "value": "43"}],
                                "resolved": {"users": {"43": {"id": "43", "username": "sk"}}, "members": {"43": {"nick": "성규님"}}}})
        self.assertEqual(self.c.level_message(other)["embeds"][0]["title"], "🐟 Lv.5 · 성규님")
        top, = self.c.rank_message({"guild_id": G})["embeds"]
        self.assertEqual(top["description"].split("\n"), ["1. 🐟 Lv.5 · 성규 · 2,600점", "2. 🐜 Lv.1 · 태규 · 160점", "3. 🥚 Lv.0 · 오 · 40점"])
        self.assertEqual(top["footer"]["text"], "전체 3명 · 1분에 한 번 · 글 하나에 20점")

    def rules(self, channel="규칙"):
        d = {"guild_id": G, "channel": {"id": "c_rules" if channel == "규칙" else "c_chat", "name": channel}}
        asyncio.run(self.c.post_rules(d, "i1", "t1"))

    def test_post_rules_replaces_old_message(self):
        self.rules()
        post, = self.rest.made("POST", "/channels/c_rules/messages")
        self.assertEqual(post[2]["components"][0]["components"][0]["custom_id"], cm.GATE_ID)
        self.assertEqual(self.bot.replies, [("✅ #규칙에 규칙 글과 입장 버튼을 올렸음", True)])   # 확인은 쓴 사람에게만
        self.assertEqual(self.rest.made("GET"), [])                          # 3초 안에 답하려고 역할을 다시 읽지 않음
        self.assertEqual(self.c.data["gate"], {"channel": "c_rules", "id": "m1"})
        self.rules()
        self.assertEqual(self.rest.made("DELETE"), [("DELETE", "/channels/c_rules/messages/m1", None)])
        self.assertEqual(self.c.data["gate"]["id"], "m2")
        self.assertEqual(self.bot.replies[-1], ("✅ #규칙에 규칙 글과 입장 버튼을 올렸음 (전에 올린 글은 지움)", True))

    def test_post_rules_answers_in_public_without_channel_permission(self):
        self.rest.fail[("POST", "/channels/c_rules/messages")] = "디스코드 POST channels 실패: HTTP 403"
        self.rules()
        cb, = self.rest.made("POST", "/interactions/")
        self.assertEqual(cb[2]["type"], 4)
        self.assertNotIn("flags", cb[2]["data"])                              # 명령의 답 자체가 공개 규칙 글
        self.assertEqual(cb[2]["data"]["components"][0]["components"][0]["custom_id"], cm.GATE_ID)
        self.assertEqual(self.rest.made("POST", "/webhooks/"), [])            # 후속 글은 '원본 메시지가 삭제되었어요' 줄이 붙어서 안 씀
        self.assertEqual(self.c.data["gate"], {"channel": "c_rules", "id": "orig1"})
        self.assertEqual(self.bot.replies, [])

    def test_post_rules_only_in_rules_channel(self):
        self.rules(channel="잡담방")
        self.assertEqual(self.bot.replies, [("📜 /규칙올리기는 #규칙에서 써 주세요.", True)])
        self.assertEqual(self.rest.calls, [])


class BotWiringTest(unittest.TestCase):
    """Bot 에 커뮤니티를 켰을 때: 명령 목록 · 받는 것(intents) · 버튼 · 명령이 제자리로 가는지."""

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.old = (store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG)
        store.DATA_DIR = tmp
        store.STATE, store.JOURNAL, store.LOG = (os.path.join(tmp, n) for n in ("state.json", "journal.jsonl", "app.log"))
        self.app = FakeApp()
        self.bot = Bot("x.y.z", self.app, community=True)
        self.bot.app_id, self.bot.owners = "app1", {G: OWNER}
        self.rest = FakeRest()
        self.bot.rest = self.rest

    def tearDown(self):
        store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG = self.old

    def test_commands_and_intents(self):
        self.assertEqual([c["name"] for c in self.bot.commands], ["포지션", "레벨", "순위", "일지", "환영정리", "규칙올리기"])
        self.assertTrue(all(len(c["description"]) <= 100 for c in self.bot.commands))
        self.assertEqual(self.bot.commands[-1]["default_member_permissions"], "0")   # /규칙올리기는 운영자에게만 보임
        self.assertEqual(self.bot.intents(), GUILD_MESSAGES | MESSAGE_CONTENT)
        self.bot.content_off_until = 2_000_000_000
        self.assertEqual(self.bot.intents(now_s=1_900_000_000), GUILD_MESSAGES)
        self.assertEqual(self.bot.intents(now_s=2_000_000_001), GUILD_MESSAGES | MESSAGE_CONTENT)   # 6시간 뒤 다시 시도
        plain = Bot("x.y.z", FakeApp())
        self.assertEqual((plain.intents(), [c["name"] for c in plain.commands]), (0, [COMMAND["name"], NOTE_COMMAND["name"], "환영정리"]))

    def test_content_refused_reconnects_without_it(self):
        seen = []

        async def session():
            seen.append(self.bot.intents())
            if len(seen) == 1:
                raise WSClosed("", 4014)                    # 포털에서 글 내용 받기를 안 켬
            if len(seen) == 2:
                self.bot.content_off_until = 0              # 6시간이 지난 셈
                raise WSClosed("", 1006)
            if len(seen) == 3:
                raise WSClosed("", 4014)                    # 아직 안 켬 → 알림은 또 안 보냄
            raise asyncio.CancelledError()

        async def no_wait(s):
            return None
        self.bot._session = session
        with mock.patch("candleman.bot.asyncio.sleep", no_wait), self.assertRaises(asyncio.CancelledError):
            asyncio.run(self.bot.run_forever())
        both = GUILD_MESSAGES | MESSAGE_CONTENT
        self.assertEqual(seen, [both, GUILD_MESSAGES, both, GUILD_MESSAGES])
        told = [t for _, t in self.app.d.sent if t.startswith("ℹ️ 같은 말 도배 감지는 꺼진 채로 붙음")]
        self.assertEqual(len(told), 1)

    def test_reconnects_for_content_six_hours_later(self):
        closed, beats = [], []

        class WS:
            async def send_text(self, t):
                beats.append(t)

            async def close(self):
                closed.append(1)

        async def no_wait(s):
            return None
        self.bot.ws, self.bot.session_intents = WS(), GUILD_MESSAGES                # 내용 없이 붙어 있음
        self.bot.content_off_until = 2_000_000_000                                  # 아직 6시간 전 → 끊지 않음
        with mock.patch("candleman.bot.asyncio.sleep", no_wait):
            asyncio.run(self.bot._heartbeat(41.25))                                 # (답이 없어 두 번째에 끝남)
        self.assertEqual((len(beats), self.bot._retry_content), (1, False))
        self.bot.acked, self.bot.content_off_until = True, 0                        # 6시간이 지남 → 끊고 다시 붙음
        beats.clear()
        with mock.patch("candleman.bot.asyncio.sleep", no_wait):
            asyncio.run(self.bot._heartbeat(41.25))
        self.assertEqual((beats, self.bot._retry_content), ([], True))
        self.bot.session_intents = GUILD_MESSAGES | MESSAGE_CONTENT                 # 이미 내용을 받고 있으면 그대로
        self.bot.acked, self.bot._retry_content = True, False
        with mock.patch("candleman.bot.asyncio.sleep", no_wait):
            asyncio.run(self.bot._heartbeat(41.25))
        self.assertFalse(self.bot._retry_content)

        seen = []

        async def session():
            seen.append(self.bot.intents())
            if len(seen) == 1:
                self.bot._retry_content = True                                      # 하트비트가 일부러 끊음
                raise ConnectionError("끊김")
            raise asyncio.CancelledError()
        self.bot._session = session
        with mock.patch("candleman.bot.asyncio.sleep", no_wait), self.assertRaises(asyncio.CancelledError):
            asyncio.run(self.bot.run_forever())
        self.assertEqual(len(seen), 2)
        with open(store.LOG, encoding="utf-8") as f:
            log = f.read()
        self.assertIn("글 내용 받기를 다시 시도하려고 다시 붙음", log)
        self.assertNotIn("봇 오류", log)                                             # 일부러 끊은 것은 오류로 남기지 않음

    def test_button_and_commands_routed(self):
        asyncio.run(self.bot.handle(button(), 1000.0))
        self.assertTrue(self.rest.made("PUT", "/guilds/g1/members/42/roles/r_ok"))
        self.rest.calls.clear()
        d = {"type": 2, "id": "i1", "token": "t1", "guild_id": G, "data": {"name": "레벨"}, "channel": {"name": "잡담방"},
             "member": {"nick": "태규", "user": {"id": "42"}}}
        asyncio.run(self.bot.handle(d, 1000.0))
        self.assertEqual([(m, p) for m, p, _ in self.rest.made()][-2:],
                         [("POST", "/interactions/i1/t1/callback"), ("PATCH", "/webhooks/app1/t1/messages/@original")])
        self.assertEqual(self.rest.made("PATCH")[-1][2]["embeds"][0]["title"], "🥚 Lv.0 · 태규")
        self.rest.calls.clear()
        d = {"type": 2, "id": "i2", "token": "t2", "guild_id": G, "data": {"name": "규칙올리기"},
             "channel": {"id": "c_rules", "name": "규칙"}, "member": {"user": {"id": "42"}}}
        asyncio.run(self.bot.handle(d, 1000.0))
        self.assertEqual(self.rest.calls[0][2]["data"]["content"], "🔒 /규칙올리기는 운영자만 쓸 수 있음.")
        d["member"]["user"]["id"] = OWNER
        self.rest.calls.clear()
        asyncio.run(self.bot.handle(d, 1000.0))
        self.assertTrue(self.rest.made("POST", "/channels/c_rules/messages"))


if __name__ == "__main__":
    unittest.main()
