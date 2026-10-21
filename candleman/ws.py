"""아주 작은 WebSocket 손님(client) — 기본 파이썬(asyncio · ssl)만 씀. 바깥 부품을 받지 않으려고 직접 만듦.

RFC 6455 가운데 필요한 것만: 연결 인사(handshake) · 글자/이진 프레임 읽기 · 조각난 메시지 잇기 ·
ping 에 pong 답하기 · close. 보내는 프레임은 규칙대로 가면(mask)을 씌움.
들어오는 연결을 받지 않으므로(서버가 아님) 폰에 열린 문이 생기지 않습니다.
"""
import asyncio
import base64
import hashlib
import os
import ssl
import struct
import time
from urllib.parse import urlparse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 4 * 1024 * 1024        # 이보다 큰 메시지는 끊음 (이상한 자료 방어)


class WSClosed(Exception):
    def __init__(self, reason="", code=None):
        super().__init__(reason)
        self.code = code                 # 서버가 알려 준 끊는 이유 번호 (디스코드 4004 = 토큰 틀림 등)


class WebSocket:
    def __init__(self, reader, writer):
        self.r = reader
        self.w = writer
        self.closed = False
        self.last_rx = time.monotonic()      # 마지막으로 무엇이든(ping 포함) 받은 때 — 살아 있는 연결인지 볼 때 씀

    @classmethod
    async def connect(cls, url, timeout=20):
        u = urlparse(url)
        if u.scheme != "wss":
            raise ValueError("wss 주소만 씁니다")
        host, port = u.hostname, u.port or 443
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
        ctx = ssl.create_default_context()           # 인증서 확인을 켠 기본값
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port, ssl=ctx, server_hostname=host), timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\nUser-Agent: candleman-alert\r\n\r\n" % (path, host, key))
        writer.write(req.encode())
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)
        lines = head.decode("latin-1").split("\r\n")
        if not lines[0].startswith("HTTP/1.1 101"):
            writer.close()
            raise ConnectionError("WebSocket 연결 거절: %s" % lines[0][:80])
        headers = {}
        for ln in lines[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        want = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != want:
            writer.close()
            raise ConnectionError("WebSocket 인사 값이 맞지 않음")
        return cls(reader, writer)

    async def _send(self, opcode, payload=b""):
        if self.closed:
            raise WSClosed()
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.w.write(head + mask + body)
        await self.w.drain()

    async def send_text(self, text):
        await self._send(0x1, text.encode("utf-8"))

    async def _frame(self):
        b1, b2 = await self.r.readexactly(2)
        fin, opcode = b1 & 0x80, b1 & 0x0F
        masked, n = b2 & 0x80, b2 & 0x7F
        if n == 126:
            (n,) = struct.unpack("!H", await self.r.readexactly(2))
        elif n == 127:
            (n,) = struct.unpack("!Q", await self.r.readexactly(8))
        if n > MAX_MESSAGE:
            raise ConnectionError("너무 큰 프레임 (%d 바이트)" % n)
        mask = await self.r.readexactly(4) if masked else None
        data = await self.r.readexactly(n) if n else b""
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.last_rx = time.monotonic()
        return bool(fin), opcode, data

    async def recv_alive(self, idle_s):
        """다음 글자 메시지를 기다림. 조용한 계정은 몇 시간씩 메시지가 없으므로 시간 제한은 '아무것도(ping 도) 안 온 시간'으로 봄
        → idle_s 넘게 아무것도 없으면 asyncio.TimeoutError (죽은 연결). 읽던 프레임을 중간에 끊지 않게 recv 를 따로 돌림."""
        task = asyncio.ensure_future(self.recv())
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=min(idle_s, 30))
                if done:
                    return task.result()
                if time.monotonic() - self.last_rx > idle_s:
                    raise asyncio.TimeoutError()
        finally:
            if not task.done():
                task.cancel()

    async def recv(self):
        """다음 글자 메시지를 돌려줌. ping 은 알아서 pong, close 면 WSClosed."""
        buf, kind = b"", None
        while True:
            fin, opcode, data = await self._frame()
            if opcode == 0x9:                      # ping
                await self._send(0xA, data)
                continue
            if opcode == 0xA:                      # pong
                continue
            if opcode == 0x8:                      # close
                try:
                    await self._send(0x8, data[:2])
                except Exception:
                    pass
                await self.close()
                code = struct.unpack("!H", data[:2])[0] if len(data) >= 2 else None
                raise WSClosed(data[2:].decode("utf-8", "replace") if len(data) > 2 else "", code)
            if opcode in (0x1, 0x2):
                buf, kind = data, opcode
            elif opcode == 0x0:
                buf += data
            else:
                raise ConnectionError("모르는 프레임 %d" % opcode)
            if len(buf) > MAX_MESSAGE:
                raise ConnectionError("너무 큰 메시지")
            if fin:
                return buf.decode("utf-8") if kind == 0x1 else buf

    async def ping(self, payload=b"k"):
        await self._send(0x9, payload)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.w.close()
            await asyncio.wait_for(self.w.wait_closed(), 5)
        except Exception:
            pass
