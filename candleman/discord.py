"""디스코드 웹후크로 글 보내기 — 보내기만 하고 디스코드에서 아무것도 읽지 않습니다.

웹후크 주소는 기록에 남기지 않습니다(채널 이름만 남김). @everyone 같은 호출이 글에 섞여도 울리지 않게 막아 둡니다.
글쓴이 이름 · 사진은 보내지 않고 디스코드의 웹후크 설정을 그대로 씁니다(지금은 '매매일지봇' · 캔들 사진, 디스코드에서 바꾸면 바로 반영).
"""
import asyncio
import json
import time
import urllib.error
import urllib.request

USER_AGENT = "DiscordBot (https://github.com/Richdaeng/candleman-alert, 1.0)"


class Discord:
    def __init__(self, urls, log=print):
        self.urls = urls                   # {"trade": url, "summary": url, "status": url}
        self.q = asyncio.Queue()
        self.log = log

    def __repr__(self):
        return "Discord(channels=%s)" % sorted(self.urls)

    def _post(self, channel, text):
        url = self.urls.get(channel) or self.urls.get("trade")
        body = json.dumps({"content": text[:2000], "allowed_mentions": {"parse": []}}).encode("utf-8")
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

    async def put(self, channel, text):
        await self.q.put((channel, text))

    async def worker(self):
        while True:
            channel, text = await self.q.get()
            for part in _split(text):
                try:
                    await asyncio.to_thread(self._post, channel, part)
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
