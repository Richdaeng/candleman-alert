"""디스코드 봇 — 정해 둔 채널(기본 #잡담방)의 /포지션 명령 하나에만 답합니다. DISCORD_BOT_TOKEN 이 있을 때만 켜짐.

웹후크는 보내기만 해서 명령을 받으려면 디스코드 게이트웨이에 붙어 있어야 합니다. 이것도 폰에서 '나가는' 연결이라
밖에서 폰으로 들어오는 문은 생기지 않습니다. 받은 것에서는 명령 이름 · 채널 이름 · 부른 사람만 보고, 무엇도 실행하지 않습니다.
봇에는 서버 권한을 하나도 주지 않습니다(명령에 답하는 데는 권한이 필요 없음).

같은 사람이 5분 안에 10번 넘게 부르면 그 사람의 /포지션은 6시간 막습니다(관리자는 예외). 이 규칙은 공지에 적지 않고,
막힌 사람에게는 '지금은 쓸 수 없음'만 보이며, 누가 막혔는지는 #봇-상태에만 알립니다.
"""
import asyncio
import json
import random
import time
import urllib.error
import urllib.request
from collections import deque

from . import store
from .ws import WebSocket, WSClosed

GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/Richdaeng/candleman-alert, 1.0)"
COMMAND = {"name": "포지션", "description": "지금 들고 있는 포지션 보기", "type": 1, "contexts": [0]}
WINDOW_S, LIMIT, BLOCK_S = 5 * 60, 10, 6 * 3600
ADMINISTRATOR = 1 << 3
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


def whoami(token):
    """check 명령용: 토큰이 맞으면 봇 이름을 돌려줌."""
    b = Bot.__new__(Bot)
    b._token = token
    j = b._rest("GET", "/users/@me")
    return j.get("global_name") or j.get("username") or "?"


class Bot:
    def __init__(self, token, app, channel_name="잡담방"):
        self._token = token
        self.app = app                       # app.positions_text() · app.d(디스코드 웹후크) · app.state 를 씀
        self.channel_name = channel_name
        self.app_id = None
        self.seq = None
        self.acked = True
        self.ws = None
        self.guard = RateGuard((app.state or {}).get("cmd_blocked"))

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
                    await self.app.d.put("status", "⛔ /포지션 봇을 멈춤: %s. python -m candleman bot 으로 토큰을 다시 넣기." % FATAL_CLOSE[e.code])
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
        sig = json.dumps(COMMAND, ensure_ascii=False, sort_keys=True)
        for g in d.get("guilds", []):
            gid = g.get("id")
            if not gid or done.get(gid) == sig:           # 같은 명령이 이미 등록돼 있으면 다시 하지 않음
                continue
            try:
                await self.rest("PUT", "/applications/%s/guilds/%s/commands" % (self.app_id, gid), [COMMAND])
                done[gid] = sig
                store.save_state(self.app.state)
                store.log("/포지션 명령 등록")
            except Exception as e:
                store.log("명령 등록 실패: %s" % e)

    # ------------------------------------------------------------------ 명령
    async def _on_interaction_safe(self, d):
        try:
            await self.handle(d, time.time())
        except Exception as e:
            store.log("명령 처리 오류: %s %s" % (type(e).__name__, str(e)[:160]))

    async def handle(self, d, now_s):
        if d.get("type") != 2 or (d.get("data") or {}).get("name") != COMMAND["name"]:
            return
        iid, tok = d.get("id"), d.get("token")
        member = d.get("member") or {}
        user = member.get("user") or d.get("user") or {}
        uid = str(user.get("id", ""))
        who = member.get("nick") or user.get("global_name") or user.get("username") or uid
        if (d.get("channel") or {}).get("name") != self.channel_name:
            return await self._reply(iid, tok, "💬 /포지션은 #%s에서 쓸 수 있음." % self.channel_name, private=True)
        if not int(member.get("permissions") or 0) & ADMINISTRATOR:
            verdict = self.guard.check(uid, now_s)
            if verdict == "just_blocked":
                self.app.state["cmd_blocked"] = self.guard.blocked
                store.save_state(self.app.state)
                await self.app.d.put("status", "🚫 %s의 /포지션을 6시간 막음 (5분 안에 10번 넘게 부름)" % who)
            if verdict != "ok":
                return await self._reply(iid, tok, "⏳ 지금은 /포지션을 쓸 수 없음. 나중에 다시 시도 바람.", private=True)
        await self.rest("POST", "/interactions/%s/%s/callback" % (iid, tok), {"type": 5}, auth=False)   # '생각 중…' 표시
        text = await self.app.positions_text()
        await self.rest("PATCH", "/webhooks/%s/%s/messages/@original" % (self.app_id, tok),
                        {"content": text[:2000], "allowed_mentions": {"parse": []}}, auth=False)

    async def _reply(self, iid, tok, text, private=False):
        data = {"content": text, "allowed_mentions": {"parse": []}}
        if private:
            data["flags"] = EPHEMERAL
        await self.rest("POST", "/interactions/%s/%s/callback" % (iid, tok), {"type": 4, "data": data}, auth=False)
