"""디스코드 봇 — 정해 둔 채널(기본 #잡담방)의 /포지션 · /주인장수익률 두 명령에 답하고, 운영자 전용 /일지(거래한 이유 남기기)를
받습니다. DISCORD_BOT_TOKEN 이 있을 때만 켜짐.

웹후크는 보내기만 해서 명령을 받으려면 디스코드 게이트웨이에 붙어 있어야 합니다. 이것도 폰에서 '나가는' 연결이라
밖에서 폰으로 들어오는 문은 생기지 않습니다. 받은 것에서는 명령 이름 · 채널 이름 · 부른 사람만 보고, 무엇도 실행하지 않습니다.
봇에는 서버 권한을 하나도 주지 않습니다(명령에 답하는 데는 권한이 필요 없음).

같은 사람이 5분 안에 10번 넘게 부르면(두 명령을 합쳐 셈) 그 사람의 명령은 6시간 막습니다(서버 주인 한 사람만 예외 — 관리자
역할을 받은 사람도 막힘). 이 규칙은 공지에 적지 않고, 막힌 사람에게는 '지금은 쓸 수 없음'만 보이며, 누가 막혔는지는 #봇-상태에만 알립니다.
/주인장수익률은 시작 금액(START_USD)이 있을 때만 등록합니다.

/일지(10/22 댕이: "내 데이터를 모아서 개선할 수 있는 방법" → 거래한 이유를 남김)는 서버 주인 한 사람만 씁니다. 다른 사람에게는 명령 목록에도
안 보이고(default_member_permissions "0"), 어느 채널에서 써도 확인 답은 쓴 사람에게만 보입니다. 남긴 것은 폰의 일지(journal, kind "note")와
#매매일지(DISCORD_WEBHOOK_JOURNAL — 10/22 댕이: 비공개가 아니어도 되고 글은 운영자만 쓰는 채널 · 없으면 나만 보는 #봇-상태)에 카드 한 장씩 —
나중에 체결 기록과 맞춰 이유별 · 확신별 성적을 셉니다.
"""
import asyncio
import json
import random
import time
import urllib.error
import urllib.request
from collections import deque

from . import fmt, names, store
from .spot import split_symbol
from .tracker import base_of
from .ws import WebSocket, WSClosed

GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/Richdaeng/candleman-alert, 1.0)"
COMMAND = {"name": "포지션", "description": "운영자가 지금 들고 있는 포지션 보기", "type": 1}   # 서버 전용 명령이라 contexts 는 안 씀
RETURN_COMMAND = {"name": "주인장수익률", "description": "운영자가 시작한 금액 대비 지금 자산과 수익률 보기", "type": 1}
NOTE_REASONS = ("거시 경제", "뉴스 · 일정", "차트", "직감", "기타")
NOTE_COMMAND = {
    "name": "일지", "type": 1, "description": "운영자 전용 · 이번 거래를 왜 했는지 #매매일지에 남기기",
    "default_member_permissions": "0",                     # 관리자(서버 주인)에게만 보이는 명령
    "options": [
        {"type": 3, "name": "이유", "description": "왜 들어갔나 (정리할 때는 왜 정리했나)", "required": True,
         "choices": [{"name": r, "value": r} for r in NOTE_REASONS]},
        {"type": 4, "name": "확신", "description": "얼마나 확신했나 (1 낮음 ~ 5 높음)", "min_value": 1, "max_value": 5},
        {"type": 3, "name": "때", "description": "진입 · 추가 · 정리 (비우면 진입)",
         "choices": [{"name": w, "value": w} for w in ("진입", "추가", "정리")]},
        {"type": 3, "name": "종목", "description": "예: EWY · SOL (비우면 마지막으로 주문 · 체결된 종목)", "max_length": 20},
        {"type": 10, "name": "손절", "description": "생각해 둔 손절 가격", "min_value": 0},
        {"type": 10, "name": "목표", "description": "생각해 둔 목표 가격", "min_value": 0},
        {"type": 3, "name": "메모", "description": "한 줄 메모", "max_length": 300},
    ],
}
NOTE_LOOKBACK_MS = 7 * 86_400_000      # 종목을 비우면 이만큼 안의 마지막 주문 · 체결 종목으로
WINDOW_S, LIMIT, BLOCK_S = 5 * 60, 10, 6 * 3600
EPHEMERAL = 1 << 6                     # 부른 사람에게만 보이는 답
FATAL_CLOSE = {4004: "봇 토큰이 맞지 않음", 4013: "intents 값이 잘못됨", 4014: "허용되지 않은 intents"}


class RateGuard:
    """같은 사람이 WINDOW_S 안에 LIMIT 번 넘게 부르면 BLOCK_S 동안 막음."""

    def __init__(self, blocked=None):
        self.hist = {}
        self.blocked = {k: float(v) for k, v in (blocked or {}).items()}      # 사람 → 막힘이 풀리는 시각(초)

    def check(self, user_id, now_s):
        """돌려줌: 'ok' · 'blocked'(이미 막힘) · 'just_blocked'(이번에 막힘)."""
        if now_s < self.blocked.get(user_id, 0):
            return "blocked"
        self.blocked.pop(user_id, None)
        dq = self.hist.setdefault(user_id, deque())
        while dq and now_s - dq[0] >= WINDOW_S:
            dq.popleft()
        dq.append(now_s)
        if len(dq) > LIMIT:
            self.blocked[user_id] = now_s + BLOCK_S
            dq.clear()
            return "just_blocked"
        return "ok"


def last_symbol(now_ms):
    """일지 종목을 비웠을 때: 최근 NOTE_LOOKBACK_MS 안에 마지막으로 주문하거나 체결된 종목의 기호(EWY · SOL …). 없으면 None."""
    for r in reversed(store.read_journal(now_ms - NOTE_LOOKBACK_MS, now_ms + 1)):
        if r.get("kind") in ("fill", "order_new") and r.get("symbol"):
            return base_of(r["symbol"])
        if r.get("kind") == "spot_fill" and r.get("symbol"):
            return split_symbol(r["symbol"])[0]
    return None


def note_record(opts, now_ms):
    """/일지 선택값 → 일지 한 줄 (kind "note")."""
    sym = str(opts.get("종목") or "").strip().upper()
    if sym.endswith("USDT") and len(sym) > 4:
        sym = sym[:-4]
    memo = str(opts.get("메모") or "").strip()
    return {"kind": "note", "t": now_ms, "when": opts.get("때") or "진입", "reason": opts.get("이유"), "confidence": opts.get("확신"),
            "symbol": sym or last_symbol(now_ms), "stop": opts.get("손절"), "target": opts.get("목표"), "memo": memo[:300] or None}


def note_text(rec):
    """'📝 일지 · 한국 주식 ETF(EWY) 진입' / '거시 경제 · 확신 4/5 · 손절 $150.00 · 목표 $165.00' / '메모: …'."""
    facts = [rec["reason"]]
    if rec.get("confidence"):
        facts.append("확신 %d/5" % rec["confidence"])
    for key, word in (("stop", "손절"), ("target", "목표")):
        if rec.get(key):
            facts.append("%s %s" % (word, fmt.price2(rec[key])))
    lines = ["📝 일지 · %s %s" % (names.label(rec["symbol"]) if rec.get("symbol") else "종목 모름", rec["when"]), " · ".join(facts)]
    if rec.get("memo"):
        lines.append("메모: " + rec["memo"])
    return "\n".join(lines)


def whoami(token):
    """check 명령용: 토큰이 맞으면 봇 이름을 돌려줌."""
    b = Bot.__new__(Bot)
    b._token = token
    j = b._rest("GET", "/users/@me")
    return j.get("global_name") or j.get("username") or "?"


class Bot:
    def __init__(self, token, app, channel_name="잡담방"):
        self._token = token
        self.app = app                       # app.positions_message() · app.d(디스코드 웹후크) · app.state 를 씀
        self.channel_name = channel_name
        self.app_id = None
        self.seq = None
        self.acked = True
        self.ws = None
        self.guard = RateGuard((app.state or {}).get("cmd_blocked"))
        self.owners = {}                     # 서버 번호 → 서버 주인 번호 (제한에서 빠지는 단 한 사람 · None 이면 알아보는 중)
        self.public = [COMMAND] + ([RETURN_COMMAND] if getattr(app, "start_usd", None) else [])   # #잡담방에서 누구나 (막는 규칙 적용)
        self.commands = self.public + [NOTE_COMMAND]

    def __repr__(self):                      # 실수로 찍혀도 토큰이 나오지 않게
        return "Bot(channel=%r)" % self.channel_name

    # ------------------------------------------------------------------ 디스코드 REST
    def _rest(self, method, path, body=None, auth=True):
        headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Bot " + self._token
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
        where = path.split("/")[1] if "/" in path else path          # 오류 글에 토큰이 섞인 주소를 넣지 않음
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    text = r.read().decode("utf-8")
                    return json.loads(text) if text else {}
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    try:
                        wait = float(json.loads(e.read().decode()).get("retry_after", 1.0))
                    except Exception:
                        wait = 1.0
                    time.sleep(min(max(wait, 0.5), 10))
                    continue
                if e.code >= 500 and attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise RuntimeError("디스코드 %s %s 실패: HTTP %s" % (method, where, e.code)) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(1 + attempt)
        raise RuntimeError("디스코드 %s %s 실패: 연결 안 됨" % (method, where))

    async def rest(self, *a, **k):
        return await asyncio.to_thread(self._rest, *a, **k)

    # ------------------------------------------------------------------ 게이트웨이
    async def run_forever(self):
        backoff = 2
        while True:
            try:
                await self._session()
                backoff = 2
            except WSClosed as e:
                if e.code in FATAL_CLOSE:
                    store.log("봇 멈춤: %s" % FATAL_CLOSE[e.code])
                    await self.app.d.put("status", "⛔ 명령 봇을 멈춤: %s. python -m candleman bot 으로 토큰을 다시 넣기." % FATAL_CLOSE[e.code])
                    return
                store.log("봇 연결 끊김: %s" % e.code)
            except Exception as e:
                store.log("봇 오류: %s %s" % (type(e).__name__, str(e)[:160]))
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)

    async def _session(self):
        self.ws = await WebSocket.connect(GATEWAY)
        beat = None
        try:
            hello = json.loads(await asyncio.wait_for(self.ws.recv(), 30))
            if hello.get("op") != 10:
                raise ConnectionError("게이트웨이 인사(Hello)가 없음")
            interval = hello["d"]["heartbeat_interval"] / 1000.0
            self.acked = True
            beat = asyncio.create_task(self._heartbeat(interval))
            await self.ws.send_text(json.dumps({"op": 2, "d": {"token": self._token, "intents": 0,
                                                              "properties": {"os": "android", "browser": "candleman", "device": "candleman"}}}))
            while True:
                msg = json.loads(await asyncio.wait_for(self.ws.recv(), interval * 3))
                if msg.get("s") is not None:
                    self.seq = msg["s"]
                op = msg.get("op")
                if op == 0:
                    t, d = msg.get("t"), msg.get("d") or {}
                    if t == "READY":
                        await self._on_ready(d)
                    elif t == "INTERACTION_CREATE":
                        asyncio.create_task(self._on_interaction_safe(d))
                elif op == 1:
                    await self._send_beat()
                elif op == 11:
                    self.acked = True
                elif op in (7, 9):               # 다시 붙으라는 뜻
                    return
        finally:
            if beat:
                beat.cancel()
            if self.ws:
                await self.ws.close()
                self.ws = None

    async def _heartbeat(self, interval):
        await asyncio.sleep(interval * random.random())
        while True:
            if not self.acked:                   # 앞 하트비트에 답이 없으면 연결이 죽은 것 → 끊고 다시 붙음
                if self.ws:
                    await self.ws.close()
                return
            self.acked = False
            await self._send_beat()
            await asyncio.sleep(interval)

    async def _send_beat(self):
        if self.ws:
            await self.ws.send_text(json.dumps({"op": 1, "d": self.seq}))

    async def _on_ready(self, d):
        self.app_id = (d.get("application") or {}).get("id")
        done = self.app.state.setdefault("cmd_registered", {})
        sig = json.dumps(self.commands, ensure_ascii=False, sort_keys=True)
        for g in d.get("guilds", []):
            gid = g.get("id")
            if not gid:
                continue
            await self._load_owner(gid)
            if done.get(gid) == sig:                      # 같은 명령들이 이미 등록돼 있으면 다시 하지 않음 (바뀌면 통째로 다시)
                continue
            try:
                await self.rest("PUT", "/applications/%s/guilds/%s/commands" % (self.app_id, gid), self.commands)
                done[gid] = sig
                store.save_state(self.app.state)
                store.log("명령 등록: " + " · ".join("/" + c["name"] for c in self.commands))
            except Exception as e:
                store.log("명령 등록 실패: %s" % e)

    async def _load_owner(self, gid):
        """서버 주인을 알아 둠. 못 알아내면 다음 명령 때 다시 묻고, 그동안은 주인도 다른 사람과 같은 제한을 받음."""
        try:
            owner = (await self.rest("GET", "/guilds/%s" % gid)).get("owner_id")
        except Exception as e:
            owner = None
            store.log("서버 주인 확인 실패: %s" % e)
        if owner:
            self.owners[gid] = str(owner)
        elif self.owners.get(gid) is None:
            self.owners.pop(gid, None)                    # 다음 명령 때 다시 물음

    # ------------------------------------------------------------------ 명령
    async def _on_interaction_safe(self, d):
        try:
            await self.handle(d, time.time())
        except Exception as e:
            store.log("명령 처리 오류: %s %s" % (type(e).__name__, str(e)[:160]))

    async def handle(self, d, now_s):
        name = (d.get("data") or {}).get("name")
        if d.get("type") != 2 or name not in [c["name"] for c in self.commands]:
            return
        iid, tok = d.get("id"), d.get("token")
        member = d.get("member") or {}
        user = member.get("user") or d.get("user") or {}
        uid = str(user.get("id", ""))
        who = member.get("nick") or user.get("global_name") or user.get("username") or uid
        gid = d.get("guild_id")
        if name == NOTE_COMMAND["name"]:
            return await self._note(d, iid, tok, uid, gid, now_s)
        if (d.get("channel") or {}).get("name") != self.channel_name:
            return await self._reply(iid, tok, "💬 /%s은 #%s에서 쓸 수 있음." % (name, self.channel_name), private=True)
        if gid and gid not in self.owners:                # 시작할 때 못 알아냈으면 뒤에서 다시 (이번 답은 늦추지 않음)
            self.owners[gid] = None                       # 알아보는 중 (겹쳐 묻지 않게)
            asyncio.create_task(self._load_owner(gid))
        if uid != self.owners.get(gid):                   # 서버 주인만 제한에서 빠짐
            verdict = self.guard.check(uid, now_s)
            if verdict == "just_blocked":
                self.app.state["cmd_blocked"] = self.guard.blocked
                store.save_state(self.app.state)
                await self.app.d.put("status", "🚫 %s의 명령(%s)을 6시간 막음 (5분 안에 10번 넘게 부름)"
                                     % (who, " · ".join("/" + c["name"] for c in self.public)))
            if verdict != "ok":
                return await self._reply(iid, tok, "⏳ 지금은 /%s을 쓸 수 없음. 나중에 다시 시도 바람." % name, private=True)
        await self.rest("POST", "/interactions/%s/%s/callback" % (iid, tok), {"type": 5}, auth=False)   # '생각 중…' 표시
        try:
            msg = await (self.app.positions_message() if name == COMMAND["name"] else self.app.return_message())
        except Exception as e:                            # 바이낸스가 안 받아도 '생각 중…'에 멈춰 있지 않게
            store.log("/%s 답 실패: %s %s" % (name, type(e).__name__, str(e)[:160]))
            msg = {"content": "⚠️ 지금은 바이낸스에서 값을 받지 못함. 잠시 뒤 다시 시도 바람.", "embeds": []}
        await self.rest("PATCH", "/webhooks/%s/%s/messages/@original" % (self.app_id, tok),
                        {"content": msg["content"][:2000], "embeds": msg.get("embeds", []), "allowed_mentions": {"parse": []}},
                        auth=False)

    async def _note(self, d, iid, tok, uid, gid, now_s):
        """/일지: 서버 주인만. 일지에 남기고, 쓴 사람에게만 보이는 답 + #봇-상태에 같은 글."""
        if gid and not self.owners.get(gid):              # 주인을 아직 모르면 지금 물어봄 (답은 3초 안에 해야 해서 한 번만)
            await self._load_owner(gid)
        if not gid or uid != self.owners.get(gid):
            return await self._reply(iid, tok, "🔒 /일지는 운영자만 쓸 수 있음.", private=True)
        opts = {o.get("name"): o.get("value") for o in (d.get("data") or {}).get("options") or []}
        rec = note_record(opts, int(now_s * 1000))
        store.append([rec])
        text = note_text(rec)
        has = getattr(self.app.d, "has", None)
        ch = "journal" if has and has("journal") else "status"   # #매매일지 웹후크가 있으면 거기, 없으면 #봇-상태
        await self._reply(iid, tok, text + "\n(이 답은 나만 보임 · #%s에 남김)" % ("매매일지" if ch == "journal" else "봇-상태"), private=True)
        await self.app.d.put(ch, text)

    async def _reply(self, iid, tok, text, private=False):
        data = {"content": text, "allowed_mentions": {"parse": []}}
        if private:
            data["flags"] = EPHEMERAL
        await self.rest("POST", "/interactions/%s/%s/callback" % (iid, tok), {"type": 4, "data": data}, auth=False)
