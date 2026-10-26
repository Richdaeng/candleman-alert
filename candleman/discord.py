"""디스코드 웹후크로 글 보내기 — 보내기만 하고 디스코드에서 아무것도 읽지 않습니다.

웹후크 주소는 기록에 남기지 않습니다(채널 이름만 남김). @everyone 같은 호출이 글에 섞여도 울리지 않게 막아 둡니다.
글쓴이 이름 · 사진은 보내지 않고 디스코드의 웹후크 설정을 그대로 씁니다(지금은 '매매일지봇' · 캔들 사진, 디스코드에서 바꾸면 바로 반영).

알림 채널(ping — 기본 #실시간-매매 · #현재포지션 · #일일-요약 · #일일-경제-브리핑)의 글은 머리 줄 끝에 @everyone 을 붙여 모두에게 울립니다
(10/26 댕이: 서버 기본 알림을 '@mentions만'으로 바꾸고 매매 · 포지션 알림만 울리게). 폰 알림에 내용이 먼저 보이게 끝에 붙이고,
글 속에 원래 섞인 @everyone · @here 는 그대로 끊어 둡니다(우리가 붙인 하나만 울림).
"""
import asyncio
import json
import time
import urllib.error
import urllib.request

from . import cards

USER_AGENT = "DiscordBot (https://github.com/Richdaeng/candleman-alert, 1.0)"
PING = "@everyone"


def with_ping(text):
    """머리 줄 끝에 @everyone ('🟢 롱 진입 · 비트코인(BTC) @everyone'). 글 속에 원래 있던 @everyone · @here 는 울리지 않게 끊음."""
    safe = (text or "").replace("@everyone", "@​everyone").replace("@here", "@​here")
    if not safe:
        return PING
    head, sep, rest = safe.partition("\n")
    return head + " " + PING + sep + rest


class Discord:
    def __init__(self, urls, log=print, ping=()):
        self.urls = urls                   # {"trade": url, "summary": url, "status": url}
        self.q = asyncio.Queue()
        self.log = log
        self.ping = set(ping or ())        # 글마다 @everyone 으로 울리는 채널 (#봇-상태는 넣지 않음)

    def __repr__(self):
        return "Discord(channels=%s)" % sorted(self.urls)

    def has(self, channel):
        """그 채널 웹후크가 따로 있는지 (없으면 보내지 말아야 하는 글을 가릴 때)."""
        return bool(self.urls.get(channel))

    def distinct(self, channels):
        """같은 웹후크(= 같은 채널)로 가는 것은 하나만 남긴 채널 이름들 (#일일-요약 웹후크를 따로 안 넣었을 때 두 번 가지 않게)."""
        seen, out = set(), []
        for ch in channels:
            url = self.urls.get(ch)
            if url and url not in seen:
                seen.add(url)
                out.append(ch)
        return out

    def _post(self, channel, text, embeds=None):
        url = self.urls.get(channel) or self.urls.get("trade")
        payload = {"allowed_mentions": {"parse": ["everyone"] if channel in self.ping else []}}
        if text:
            payload["content"] = text[:2000]
        if embeds:
            payload["embeds"] = embeds
        body = json.dumps(payload).encode("utf-8")
        for attempt in range(5):
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    try:
                        wait = float(json.loads(e.read().decode()).get("retry_after", 1.0))
                    except Exception:
                        wait = 1.0
                    time.sleep(min(max(wait, 0.5), 30))
                    continue
                if e.code >= 500:
                    time.sleep(2 + attempt * 2)
                    continue
                raise RuntimeError("디스코드 %s 채널로 보내기 실패: HTTP %s" % (channel, e.code)) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(2 + attempt * 2)
        raise RuntimeError("디스코드 %s 채널로 보내기 실패: 연결 안 됨" % channel)

    def send_now(self, channel, text):
        return self._post(channel, text)

    async def put(self, channel, text, embeds=None):
        if embeds is None:                           # #실시간-매매 · #봇-상태 줄글은 머리 한 줄 + 카드로 (cards.py)
            text, embeds = cards.card(channel, text)
        if channel in self.ping:                     # 알림 채널은 머리 줄 끝에 @everyone
            text = with_ping(text)
        await self.q.put((channel, text, embeds))

    async def worker(self):
        while True:
            channel, text, embeds = await self.q.get()
            parts = _split(text) if text else [""]
            for i, part in enumerate(parts):
                try:
                    await asyncio.to_thread(self._post, channel, part, embeds if i == len(parts) - 1 else None)
                except Exception as e:      # 한 번 실패해도 프로그램은 계속
                    self.log("디스코드 오류: %s" % e)
                await asyncio.sleep(0.6)    # 웹후크 속도 제한 아래로
            self.q.task_done()


def _split(text, limit=1900):
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = ""
        cur += (line + "\n")
    if cur:
        out.append(cur)
    return out
