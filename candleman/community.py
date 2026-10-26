"""커뮤니티 기능 — #규칙 입장 버튼 · 레벨(Lv.0 ~ Lv.10) · 도배 처리 (2026-10-26 댕이: "#규칙에서 뭔가를 해야 실제 서버로
들어오게" · "등급은 Lv 0 부터 Lv 10 까지 · 경험치봇" · "도배는 밴(같은 말을 계속 쓰거나 5초 안에 5개 이상)").

- 입장: 운영자가 #규칙에서 /규칙올리기 → 규칙 글 + '✅ 규칙에 동의하고 입장' 버튼. 누르면 '✅ 인증' 역할과 레벨 역할(처음이면 🥚 Lv.0).
  채널을 '✅ 인증'에게만 보이게 하는 것(채널 권한)은 운영자가 디스코드에서 정합니다.
- 레벨: 글을 쓰면 1분에 한 번 20점. Lv.n 이 되려면 n × n × 100점(Lv.10 = 10,000점이 최고). 레벨 역할은 이름의 'Lv.숫자'로 찾음
  (이모지 · 색은 디스코드에서 바꿔도 됨). 오르면 그 채널에 한 줄(울리지 않음) + 역할을 바꿈. 봇이 꺼져 있던 사이의 글은
  다시 붙을 때 채널 기록을 읽어 채움(이때는 레벨 업 글을 올리지 않음).
- 도배: 5초 안에 5개 이상 · 같은 말을 4번 연속(1분 안) → 그 글들을 지우고 1시간 타임아웃, 24시간 안에 또 하면 밴.
  '같은 말'은 글자를 늘인 것('ㅋㅋ' · 'ㅋㅋㅋ') · 문장 부호 · 띄어쓰기 차이를 같게 봄(10/26 댕이: "ㅋㅋ 하고 엔터, ㅋㅋㅋ 하고 엔터").
  글 하나 안의 'ㅋㅋㅋㅋ'는 도배가 아님. 서버 주인은 빼고, #봇-상태에 알림.

글 내용은 '같은 말인지' 견줄 때만 메모리에서 잠깐 보고 저장하지 않습니다. 저장하는 것: 사람별 점수 · 마지막으로 센 시각 ·
표시 이름 · 채널별 어디까지 셌는지 · 도배 처리 시각 · 규칙 글 위치.
"""
import asyncio
import re
import time
import unicodedata
from collections import deque
from datetime import datetime, timezone

from . import store

XP_PER_MSG = 20                     # 글 하나에 붙는 점수
XP_COOLDOWN_MS = 60_000             # 같은 사람은 1분에 한 번만 셈
LEVEL_MAX = 10
LEVEL_EMOJI = ("🥚", "🐜", "🦐", "🦀", "🐙", "🐟", "🐬", "🦈", "🐋", "🐳", "🔱")   # 그 레벨 역할이 서버에 없을 때 보이는 이름
SKIP_CHANNELS = ("봇-상태", "규칙", "매매일지")   # 점수를 세지 않는 채널
GATE_CHANNEL = "규칙"
GATE_ID = "gate:agree"
VERIFIED_WORD = "인증"              # 입장한 사람에게 주는 '✅ 인증' 역할 (이름으로 찾음)
FAST_N, FAST_MS = 5, 5_000          # 5초 안에 5개 이상
SAME_N, SAME_MS = 4, 60_000         # 같은 말 4번 연속 (1분 안)
STRIKE_MS = 24 * 3_600_000          # 이 안에 두 번째 도배면 밴
TIMEOUT_MS = 3_600_000              # 첫 도배는 1시간 타임아웃
QUIET_MS = 30_000                   # 한 번 처리한 사람은 이만큼 다시 처리하지 않음 (지우는 사이 들어온 글로 겹치지 않게)
PAGES_MAX = 50                      # 다시 붙을 때 채널마다 읽는 기록 (100개씩) — 넘으면 다음에 이어서
RESYNC_S = 15 * 60                  # 레벨 역할을 못 맞춘 사람이 남아 있으면 이만큼마다 다시 (운영자가 권한을 준 뒤 저절로)
EPHEMERAL = 1 << 6
DISCORD_EPOCH_MS = 1_420_070_400_000
LV_RE = re.compile(r"Lv\.?\s*(\d{1,2})(?!\d)", re.I)
RUN_RE = re.compile(r"(.)\1+", re.S)
BLUE, GOLD = 3447003, 15844367
REASONS = {"fast": "5초 안에 5개 이상", "same": "같은 말 4번 연속"}

LEVEL_COMMAND = {"name": "레벨", "description": "내 레벨 · 경험치 보기 (다른 사람도 고를 수 있음)", "type": 1,
                 "options": [{"type": 6, "name": "멤버", "description": "볼 사람 (비우면 나)"}]}
RANK_COMMAND = {"name": "순위", "description": "레벨 순위 보기 (상위 10명)", "type": 1}
RULES_COMMAND = {"name": "규칙올리기", "type": 1, "default_member_permissions": "0",       # 서버 주인(관리자)에게만 보임
                 "description": "운영자 전용 · #규칙에 규칙 글과 입장 버튼 올리기 (전에 올린 글은 지움)"}

RULES = (
    "1. 상호 존중 및 존대를 원칙으로 함 — 욕설 · 비하 · 인신공격 금지",
    "2. 광고 · 홍보 금지 — 다른 디스코드 · 텔레그램 · 오픈채팅 링크, 리딩방 · 수익 보장 글은 자동으로 막힘",
    "3. 사기 조심 — 운영자는 DM으로 입금 · 지갑 연결 · 개인 정보를 절대 요구하지 않음",
    "4. 투자 권유 아님 — 올라오는 포지션 · 글은 개인 기록이고, 투자 판단과 책임은 각자에게 있음",
    "5. 도배 · 멘션 남발 금지 — 5초 안에 5개 이상 쓰거나 같은 말을 4번 연속으로 보내면(ㅋㅋ · ㅋㅋㅋ 처럼 늘인 말도) "
    "자동으로 1시간 타임아웃, 24시간 안에 또 하면 밴 · 한 글에 멘션 5개 이상은 자동으로 막힘 · @everyone · @here 는 운영자만",
    "6. 개인 정보 올리지 않기 — 연락처 · 계좌 · 지갑 주소 등(본인 것도)",
    "7. 어기면 경고 → 타임아웃 → 추방 순으로 처리",
)


def need(level):
    """Lv.level 이 되는 데 필요한 누적 점수 (Lv.1 100 · Lv.5 2,500 · Lv.10 10,000)."""
    return 100 * level * level


def level_of(xp):
    lv = 0
    while lv < LEVEL_MAX and xp >= need(lv + 1):
        lv += 1
    return lv


def snowflake_ms(sid):
    """디스코드 번호(글 · 채널) → 만든 시각(ms)."""
    return (int(sid) >> 22) + DISCORD_EPOCH_MS


def display_name(d):
    """글(MESSAGE_CREATE · 기록) 또는 명령의 보이는 이름: 서버 별명 → 표시 이름 → 사용자 이름."""
    member = d.get("member") or {}
    user = d.get("author") or member.get("user") or d.get("user") or {}
    return member.get("nick") or user.get("global_name") or user.get("username") or str(user.get("id", "?"))


def find_roles(roles):
    """서버 역할 목록 → ({레벨: 역할}, 인증 역할 또는 None). 이름에 'Lv.숫자'가 든 역할이 레벨 역할(같은 숫자가 둘이면 위쪽)."""
    levels, verified = {}, None
    for r in sorted(roles or [], key=lambda r: r.get("position", 0)):
        name = r.get("name") or ""
        if r.get("managed"):                      # 봇이 들어올 때 생긴 역할은 건드리지 않음
            continue
        m = LV_RE.search(name)
        if m and int(m.group(1)) <= LEVEL_MAX:
            levels[int(m.group(1))] = {"id": str(r["id"]), "name": name, "color": int(r.get("color") or 0)}
        elif VERIFIED_WORD in name:
            verified = {"id": str(r["id"]), "name": name}
    return levels, verified


def level_name(levels, lv):
    r = (levels or {}).get(lv)
    return r["name"] if r else "%s Lv.%d" % (LEVEL_EMOJI[lv], lv)


# ---------------------------------------------------------------------- 도배
def same_form(content):
    """같은 말인지 견줄 꼴: 작은 글자로 · 띄어쓰기 · 문장 부호를 빼고 · 이어진 같은 글자는 하나로.
    'ㅋㅋ' = 'ㅋㅋㅋ' = 'ㅋ' · '가즈아아아!' = '가즈 아' = '가즈아' · '🚀🚀' = '🚀'. 글자가 하나도 안 남으면(사진만 · '!!!') 빈 값."""
    kept = "".join(ch for ch in (content or "").lower()
                   if ch not in "~～^" and not unicodedata.category(ch).startswith(("P", "Z", "C", "M")))   # '가즈아~' · '^^' 의 ~ ^ 도 뺌
    return RUN_RE.sub(r"\1", kept)


def _group(entries):
    out = {}
    for _, mid, cid, _ in entries:
        out.setdefault(cid, []).append(mid)
    return out


class FloodGuard:
    """사람별 최근 글(시각 · 글 번호 · 채널 · 같은 말 꼴)로 도배를 알아봄. 내용은 메모리에만 1분 동안."""

    def __init__(self, strikes=None):
        self.recent = {}
        self.quiet = {}
        self.strikes = {str(k): [int(t) for t in v] for k, v in (strikes or {}).items()}

    def check(self, uid, t, mid, cid, content):
        """도배면 (까닭, {채널: [지울 글 번호]}), 아니면 None. 까닭: fast(5초 안에 5개 이상) · same(같은 말 4번 연속)."""
        dq = self.recent.setdefault(uid, deque(maxlen=30))
        form = same_form(content)
        dq.append((t, str(mid), str(cid), form))
        while dq and t - dq[0][0] > SAME_MS:
            dq.popleft()
        if t < self.quiet.get(uid, 0):
            return None
        fast = [e for e in dq if t - e[0] < FAST_MS]
        if len(fast) >= FAST_N:
            return "fast", _group(fast)
        last = list(dq)[-SAME_N:]
        if form and len(last) == SAME_N and all(e[3] == form for e in last):
            return "same", _group(last)
        return None

    def strike(self, uid, now):
        """처리 단계: 24시간 안에 앞선 도배가 있으면 'ban', 아니면 'timeout'. 이번 것도 남김."""
        hist = [s for s in self.strikes.get(uid, []) if now - s < STRIKE_MS]
        action = "ban" if hist else "timeout"
        self.strikes[uid] = hist + [now]
        self.quiet[uid] = now + QUIET_MS
        self.recent.pop(uid, None)
        return action


# ---------------------------------------------------------------------- 레벨
class Levels:
    """사람별 누적 점수 — data 는 app.state['community'] (users · cursor · synced)."""

    def __init__(self, data):
        self.users = data.setdefault("users", {})        # 사람 → {"xp", "last"(ms), "name"}
        self.cursor = data.setdefault("cursor", {})      # 채널 → 마지막으로 본 글 번호 (다시 붙을 때 여기서부터 읽음)
        self.synced = data.setdefault("synced", {})      # 사람 → 역할까지 맞춘 레벨

    def xp(self, uid):
        return int((self.users.get(uid) or {}).get("xp", 0))

    def level(self, uid):
        return level_of(self.xp(uid))

    def add(self, uid, name, t):
        """글 하나 → 1분 안에 이미 셌으면 None, 아니면 (전 레벨, 새 레벨)."""
        u = self.users.setdefault(uid, {"xp": 0, "last": 0})
        if name:
            u["name"] = str(name)[:40]
        if t < int(u.get("last", 0)) + XP_COOLDOWN_MS:
            return None
        old = level_of(int(u.get("xp", 0)))
        u["xp"] = int(u.get("xp", 0)) + XP_PER_MSG
        u["last"] = t
        return old, level_of(u["xp"])

    def ranked(self):
        return sorted(self.users, key=lambda k: -self.xp(k))

    def rank(self, uid):
        order = self.ranked()
        return (order.index(uid) + 1 if uid in order else None), len(order)


def progress_bar(xp, lv):
    if lv >= LEVEL_MAX:
        return "▰" * 10 + " 최고 레벨"
    lo, hi = need(lv), need(lv + 1)
    frac = max(0.0, min(1.0, (xp - lo) / float(hi - lo)))
    k = int(frac * 10)
    return "▰" * k + "▱" * (10 - k) + " %d%%" % int(frac * 100)


def level_card(name, xp, levels, rank=None, total=0):
    """/레벨 답 카드."""
    lv = level_of(xp)
    head = "경험치 **{:,}점**".format(xp)
    if lv < LEVEL_MAX:
        head += " · 다음 레벨(%s)까지 **{:,}점**".format(need(lv + 1) - xp) % level_name(levels, lv + 1)
    lines = [head, progress_bar(xp, lv)]
    if rank:
        lines.append("순위 **%d위** / %d명" % (rank, total))
    color = ((levels or {}).get(lv) or {}).get("color") or BLUE
    return {"title": "%s · %s" % (level_name(levels, lv), name), "description": "\n".join(lines), "color": color,
            "footer": {"text": "1분에 한 번 · 글 하나에 20점 · Lv.n 까지 n × n × 100점"}}


def rank_card(lv_users, levels, total):
    """/순위 답 카드 — lv_users: [(이름, 점수), …] 높은 순."""
    lines = []
    for i, (name, xp) in enumerate(lv_users, 1):
        lines.append("%d. %s · %s · {:,}점".format(xp) % (i, level_name(levels, level_of(xp)), name))
    return {"title": "🏆 레벨 순위", "description": "\n".join(lines) or "아직 점수가 없음", "color": GOLD,
            "footer": {"text": "전체 %d명 · 1분에 한 번 · 글 하나에 20점" % total}}


def rules_message(levels):
    """#규칙 글: 규칙 카드 + 레벨 카드 + 입장 버튼."""
    names = [level_name(levels, n) for n in range(LEVEL_MAX + 1)]
    lv_lines = ["글을 쓰면 경험치가 쌓여 레벨이 오름 (1분에 한 번 · 글 하나에 20점)",
                " → ".join(names[:6]), "→ " + " → ".join(names[6:]),
                "Lv.n 까지 n × n × 100점 (Lv.1 100점 · Lv.5 2,500점 · Lv.10 10,000점)",
                "#잡담방에서 /레벨 · /순위 로 확인"]
    return {"embeds": [{"title": "📜 캔들맨의 실시간 포지션 규칙", "description": "\n".join(RULES), "color": BLUE},
                       {"title": "📈 레벨", "description": "\n".join(lv_lines), "color": GOLD,
                        "footer": {"text": "아래 버튼을 누르면 규칙에 동의한 것으로 보고 모든 채널이 열림"}}],
            "components": [{"type": 1, "components": [{"type": 2, "style": 3, "label": "규칙에 동의하고 입장",
                                                       "emoji": {"name": "✅"}, "custom_id": GATE_ID}]}],
            "allowed_mentions": {"parse": []}}


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


# ---------------------------------------------------------------------- 봇에 붙는 부분
class Community:
    """bot(Bot)의 REST · 서버 주인 정보와 app.state · app.d(#봇-상태)를 씀."""

    def __init__(self, bot):
        self.bot = bot
        self.app = bot.app
        self.data = self.app.state.setdefault("community", {})
        self.levels = Levels(self.data)
        self.guard = FloodGuard(self.data.get("strikes"))
        self.roles = {}                      # 서버 → ({레벨: 역할}, 인증 역할)
        self.channels = {}                   # 서버 → {채널 번호: 이름} (글 채널만)
        self.lock = asyncio.Lock()           # 점수 세기는 한 번에 하나씩 (다시 붙을 때 기록 읽기와 새 글이 겹치지 않게)
        self.caught = {}                     # 서버 → 다시 붙은 뒤 기록 읽기를 마쳤는지 (마치기 전 새 글은 기다렸다 셈)
        self.told = set()                    # 이번에 켜진 뒤 이미 알린 문제 (같은 경고를 되풀이하지 않음)
        self._saver = None
        self._resync = {}                    # 서버 → 역할 다시 맞추기 작업

    # ------------------------------------------------------------------ 저장 · 알림
    def _touch(self):
        """점수가 바뀜 → 30초 뒤 한 번 저장 (글마다 파일을 쓰지 않게)."""
        if self._saver is None or self._saver.done():
            self._saver = asyncio.ensure_future(self._save_later())

    async def _save_later(self):
        await asyncio.sleep(30)
        store.save_state(self.app.state)

    async def warn(self, key, text):
        if key in self.told:
            return
        self.told.add(key)
        store.log(text)
        await self.app.d.put("status", text)

    async def _post(self, cid, text):
        try:
            await self.bot.rest("POST", "/channels/%s/messages" % cid, {"content": text, "allowed_mentions": {"parse": []}})
        except Exception as e:
            store.log("채널 글 실패: %s" % e)

    # ------------------------------------------------------------------ 서버 정보
    async def load(self, gid):
        roles = await self.bot.rest("GET", "/guilds/%s/roles" % gid)
        chans = await self.bot.rest("GET", "/guilds/%s/channels" % gid)
        self.roles[gid] = find_roles(roles if isinstance(roles, list) else [])
        self.channels[gid] = {str(c["id"]): c.get("name") or "" for c in (chans if isinstance(chans, list) else [])
                              if c.get("type") in (0, 5)}

    async def on_ready(self, gids):
        for gid in gids:
            self.caught.setdefault(gid, asyncio.Event()).clear()
        for gid in gids:
            try:
                await self.load(gid)
                await self.catch_up(gid)
            except Exception as e:
                store.log("레벨 준비 실패: %s %s" % (type(e).__name__, str(e)[:160]))
            finally:
                self.caught[gid].set()
            if self._resync.get(gid) is None or self._resync[gid].done():
                self._resync[gid] = asyncio.ensure_future(self._resync_later(gid))

    def _unsynced(self, gid):
        owner = self.bot.owners.get(gid)
        return [u for u in self.levels.users if u != owner and self.levels.synced.get(u) != self.levels.level(u)]

    async def _resync_later(self, gid):
        """레벨 역할을 못 맞춘 사람이 남아 있으면 15분마다 다시 (운영자가 '역할 관리' 권한을 준 뒤 저절로 맞춰지게)."""
        while self._unsynced(gid):
            await asyncio.sleep(RESYNC_S)
            await self.sync_all(gid)

    # ------------------------------------------------------------------ 점수
    def _count(self, m, cid):
        """글 하나를 점수에 넣음 → (전, 새) 레벨 또는 None. 채널 커서는 늘 앞으로만."""
        mid = str(m.get("id") or 0)
        if int(mid) <= int(self.levels.cursor.get(cid) or 0):
            return None
        self.levels.cursor[cid] = mid
        a = m.get("author") or {}
        if a.get("bot") or m.get("webhook_id") or m.get("type", 0) not in (0, 19) or not a.get("id"):
            return None
        return self.levels.add(str(a["id"]), display_name(m), snowflake_ms(mid))

    async def _read_after(self, cid, after):
        """채널 기록에서 after 다음 글들 (오래된 것부터, 최대 PAGES_MAX × 100개)."""
        out = []
        for _ in range(PAGES_MAX):
            page = await self.bot.rest("GET", "/channels/%s/messages?after=%s&limit=100" % (cid, after))
            if not isinstance(page, list) or not page:
                break
            page.sort(key=lambda m: int(m["id"]))
            out += page
            after = page[-1]["id"]
            if len(page) < 100:
                break
        return out

    async def catch_up(self, gid):
        """봇이 꺼져 있던 사이의 글을 채널 기록에서 읽어 점수에 넣고(레벨 업 글은 안 올림), 레벨 역할을 맞춤.
        처음 켜질 때는 채널 기록 전부를 읽어 이미 쓴 글도 점수에 넣음."""
        async with self.lock:
            msgs = []
            for cid, name in sorted((self.channels.get(gid) or {}).items()):
                if name in SKIP_CHANNELS:
                    continue
                try:
                    for m in await self._read_after(cid, self.levels.cursor.get(cid) or "0"):
                        msgs.append((cid, m))
                except Exception as e:                       # 봇이 못 보는 채널(비공개) 등은 건너뜀
                    store.log("기록 읽기 건너뜀 #%s: %s" % (name, e))
            msgs.sort(key=lambda x: int(x[1]["id"]))
            for cid, m in msgs:
                self._count(m, cid)
        if msgs:
            store.save_state(self.app.state)
            store.log("레벨: 기록 %d개를 셈" % len(msgs))
        await self.sync_all(gid)

    async def sync_all(self, gid):
        """레벨과 역할이 다른 사람들의 역할을 맞춤. 서버 주인은 빼고(👑 운영자로 보임), 권한이 없으면 멈췄다가 다음에 다시."""
        for uid in self._unsynced(gid):
            if await self.sync_roles(gid, uid, self.levels.level(uid)) == "fail":
                break

    async def sync_roles(self, gid, uid, lv, have=None):
        """그 사람의 레벨 역할을 lv 하나로 맞춤 → 'ok' · 'gone'(서버를 나감) · 'skip'(그 레벨 역할이 없음) · 'fail'."""
        levels, _ = self.roles.get(gid) or ({}, None)
        want = levels.get(lv)
        if not want:
            return "skip"
        try:
            if have is None:
                have = (await self.bot.rest("GET", "/guilds/%s/members/%s" % (gid, uid))).get("roles") or []
            have = set(map(str, have))
            for n, r in sorted(levels.items()):
                if n != lv and r["id"] in have:
                    await self.bot.rest("DELETE", "/guilds/%s/members/%s/roles/%s" % (gid, uid, r["id"]), reason="레벨 바뀜")
            if want["id"] not in have:
                await self.bot.rest("PUT", "/guilds/%s/members/%s/roles/%s" % (gid, uid, want["id"]), reason="레벨 %d" % lv)
        except Exception as e:
            if "HTTP 404" in str(e):
                self.levels.synced[uid] = lv
                return "gone"
            await self.warn("roles", "⚠️ 레벨 역할을 바꾸지 못함\n🤖 봇 역할에 '역할 관리' 권한이 있고 레벨 역할들보다 위에 있어야 함\n(%s)" % e)
            return "fail"
        self.levels.synced[uid] = lv
        self._touch()
        return "ok"

    # ------------------------------------------------------------------ 새 글
    async def on_message(self, d, now_ms=None):
        gid = d.get("guild_id")
        a = d.get("author") or {}
        if not gid or a.get("bot") or d.get("webhook_id") or d.get("type", 0) not in (0, 19) or not a.get("id"):
            return
        uid, cid, mid = str(a["id"]), str(d.get("channel_id")), str(d.get("id"))
        name = display_name(d)
        if uid != self.bot.owners.get(gid):                  # 서버 주인은 도배 처리에서 빠짐
            hit = self.guard.check(uid, snowflake_ms(mid), mid, cid, d.get("content"))
            if hit:
                return await self.punish(gid, uid, name, cid, hit[0], hit[1], now_ms or int(time.time() * 1000))
        if (self.channels.get(gid) or {}).get(cid) in SKIP_CHANNELS:
            return
        ev = self.caught.get(gid)
        if ev and not ev.is_set():                            # 꺼져 있던 사이의 기록을 다 센 뒤에 (커서가 건너뛰지 않게)
            await ev.wait()
        async with self.lock:
            got = self._count(d, cid)
        if not got:
            return
        self._touch()
        if got[1] > got[0]:
            levels, _ = self.roles.get(gid) or ({}, None)
            await self._post(cid, "🎉 <@%s> 님 레벨 업! **%s**" % (uid, level_name(levels, got[1])))
            if uid != self.bot.owners.get(gid):
                await self.sync_roles(gid, uid, got[1], (d.get("member") or {}).get("roles"))

    async def punish(self, gid, uid, name, cid, why, targets, now):
        """도배: 그 글들을 지우고 첫 번째면 1시간 타임아웃, 24시간 안에 두 번째면 밴. 채널에 한 줄 + #봇-상태."""
        action = self.guard.strike(uid, now)
        self.data["strikes"] = self.guard.strikes
        self._touch()
        gone, errors, done = 0, [], False
        for ch, ids in sorted(targets.items()):                # 글 지우기와 타임아웃 · 밴은 따로 (하나가 막혀도 다른 것은 함)
            try:
                if len(ids) == 1:
                    await self.bot.rest("DELETE", "/channels/%s/messages/%s" % (ch, ids[0]), reason="도배")
                else:
                    await self.bot.rest("POST", "/channels/%s/messages/bulk-delete" % ch, {"messages": ids[:100]}, reason="도배")
                gone += len(ids)
            except Exception as e:
                errors.append(e)
        try:
            if action == "ban":
                await self.bot.rest("PUT", "/guilds/%s/bans/%s" % (gid, uid), {"delete_message_seconds": 3600}, reason="도배 되풀이")
            else:
                await self.bot.rest("PATCH", "/guilds/%s/members/%s" % (gid, uid),
                                    {"communication_disabled_until": _iso(now + TIMEOUT_MS)}, reason="도배")
            done = True
        except Exception as e:
            errors.append(e)
        if errors:
            await self.warn("flood", "⚠️ 도배 처리 실패 (%s · %s)\n🤖 봇 역할에 '메시지 관리 · 멤버 타임아웃 · 멤버 차단' 권한이 있어야 함\n(%s)"
                            % (name, REASONS[why], errors[0]))
        if not done:
            return
        if action == "ban":
            await self._post(cid, "🚫 도배를 되풀이해 <@%s> 님을 밴함 · 규칙 5번" % uid)
        else:
            await self._post(cid, "🧹 도배(%s)로 <@%s> 님 글을 지우고 1시간 타임아웃함 · 규칙 5번" % (REASONS[why], uid))
        await self.app.d.put("status", "%s 도배 처리 · %s\n%s · 글 %d개 지움 · %s" % (
            "🚫" if action == "ban" else "🧹", name, REASONS[why], gone, "밴" if action == "ban" else "1시간 타임아웃"))

    # ------------------------------------------------------------------ 입장 버튼
    async def on_button(self, d):
        if (d.get("data") or {}).get("custom_id") != GATE_ID:
            return
        iid, tok, gid = d.get("id"), d.get("token"), d.get("guild_id")
        member = d.get("member") or {}
        uid = str((member.get("user") or {}).get("id") or "")
        await self.bot.rest("POST", "/interactions/%s/%s/callback" % (iid, tok), {"type": 5, "data": {"flags": EPHEMERAL}}, auth=False)
        try:
            text = await self._admit(gid, uid, set(map(str, member.get("roles") or [])), display_name(d))
        except Exception as e:
            store.log("입장 처리 오류: %s %s" % (type(e).__name__, str(e)[:160]))
            text = "⚠️ 지금은 입장 처리가 안 됨. 잠시 뒤 다시 눌러 주세요."
        await self.bot.rest("PATCH", "/webhooks/%s/%s/messages/@original" % (self.bot.app_id, tok),
                            {"content": text, "allowed_mentions": {"parse": []}}, auth=False)

    async def _admit(self, gid, uid, have, name):
        if gid not in self.roles or not self.roles[gid][1]:
            await self.load(gid)
        levels, verified = self.roles.get(gid) or ({}, None)
        if not verified:
            await self.warn("verified", "⚠️ 입장 버튼: 이름에 '%s'이 든 역할을 찾지 못함" % VERIFIED_WORD)
            return "⚠️ 지금은 입장 처리가 안 됨. 운영자에게 알렸음."
        if verified["id"] in have:
            return "✅ 이미 입장한 상태임 · 모든 채널이 보임"
        try:
            await self.bot.rest("PUT", "/guilds/%s/members/%s/roles/%s" % (gid, uid, verified["id"]), reason="규칙 동의 · 입장")
        except Exception as e:
            await self.warn("gate", "⚠️ 입장 버튼: 역할을 주지 못함\n🤖 봇 역할에 '역할 관리' 권한이 있고 '%s'보다 위에 있어야 함\n(%s)"
                            % (verified["name"], e))
            return "⚠️ 지금은 입장 처리가 안 됨. 운영자에게 알렸음. 잠시 뒤 다시 눌러 주세요."
        if not any(r["id"] in have for r in levels.values()):
            await self.sync_roles(gid, uid, self.levels.level(uid), have)
        await self.app.d.put("status", "🚪 입장 · %s" % name)
        return "✅ 입장 완료 · 이제 모든 채널이 보임\n#잡담방에서 인사 남겨 주세요 (존댓말이 원칙)"

    # ------------------------------------------------------------------ 명령
    def level_message(self, d):
        """/레벨 [멤버] 답."""
        gid = d.get("guild_id")
        data = d.get("data") or {}
        pick = next((str(o.get("value")) for o in data.get("options") or [] if o.get("name") == "멤버"), None)
        if pick:
            res = data.get("resolved") or {}
            m, u = (res.get("members") or {}).get(pick) or {}, (res.get("users") or {}).get(pick) or {}
            name = m.get("nick") or u.get("global_name") or u.get("username") or (self.levels.users.get(pick) or {}).get("name") or pick
            uid = pick
        else:
            uid, name = str(((d.get("member") or {}).get("user") or d.get("user") or {}).get("id") or ""), display_name(d)
        levels, _ = self.roles.get(gid) or ({}, None)
        rank, total = self.levels.rank(uid)
        return {"content": "", "embeds": [level_card(name, self.levels.xp(uid), levels, rank, total)]}

    def rank_message(self, d):
        """/순위 답 (상위 10명)."""
        levels, _ = self.roles.get(d.get("guild_id")) or ({}, None)
        order = self.levels.ranked()
        top = [((self.levels.users[k].get("name") or k), self.levels.xp(k)) for k in order[:10]]
        return {"content": "", "embeds": [rank_card(top, levels, len(order))]}

    async def post_rules(self, d, iid, tok):
        """/규칙올리기 (서버 주인만 · #규칙에서): 규칙 글 + 입장 버튼을 올리고 전에 올린 글은 지움.
        봇에 #규칙 글쓰기 권한이 있으면 보통 글로, 없으면 명령의 후속 글로 올림(권한이 없어도 올라가지만 '사용함' 줄이 붙음)."""
        gid = d.get("guild_id")
        ch = d.get("channel") or {}
        cid = str(ch.get("id") or d.get("channel_id") or "")
        if ch.get("name") != GATE_CHANNEL:
            return await self.bot._reply(iid, tok, "📜 /규칙올리기는 #%s에서 써 주세요." % GATE_CHANNEL, private=True)
        await self.bot._reply(iid, tok, "📜 규칙 글을 올리는 중…", private=True)
        try:
            await self.load(gid)
        except Exception as e:
            store.log("서버 역할 읽기 실패(기본 이름으로 올림): %s" % e)
        msg = rules_message((self.roles.get(gid) or ({}, None))[0])
        try:
            posted = await self.bot.rest("POST", "/channels/%s/messages" % cid, msg)
        except Exception as e:
            store.log("#규칙 글쓰기 권한 없음 → 후속 글로: %s" % e)
            posted = await self.bot.rest("POST", "/webhooks/%s/%s" % (self.bot.app_id, tok), msg, auth=False)
        old = self.data.get("gate") or {}
        if old.get("id") and str(old["id"]) != str(posted.get("id")):
            try:
                await self.bot.rest("DELETE", "/channels/%s/messages/%s" % (old["channel"], old["id"]))
            except Exception as e:
                store.log("전 규칙 글 지우기 실패: %s" % e)
        self.data["gate"] = {"channel": cid, "id": str(posted.get("id") or "")}
        store.save_state(self.app.state)
        await self.bot.rest("PATCH", "/webhooks/%s/%s/messages/@original" % (self.bot.app_id, tok),
                            {"content": "✅ #%s에 규칙 글과 입장 버튼을 올렸음" % GATE_CHANNEL + (" (전에 올린 글은 지움)" if old.get("id") else "")},
                            auth=False)
