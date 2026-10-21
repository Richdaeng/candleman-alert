"""바이낸스 REST — 조회만 함(USDⓈ-M 선물 + 현물). 주문을 넣거나 돈을 옮기는 함수는 일부러 만들지 않았습니다.

키는 서명할 때만 쓰고 기록(log)에는 남기지 않습니다. 오류 글에도 키 · 서명을 넣지 않습니다.
"""
import asyncio
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .fmt import dec

BASE = "https://fapi.binance.com"
SPOT_BASE = "https://api.binance.com"
WS_PRIVATE = "wss://fstream.binance.com/private/ws/"     # 2026-04-23 이후 사용자 자료는 /private 로만 옴
WS_API = "wss://ws-api.binance.com:443/ws-api/v3"        # 현물 사용자 자료 — 서명으로 구독 (현물 listenKey 는 없어짐)


def sign_ws_params(secret, params):
    """WebSocket API 서명: signature 를 뺀 값들을 이름 순으로 'a=1&b=2' 로 이어 HMAC-SHA256 (16진수)."""
    payload = "&".join("%s=%s" % (k, params[k]) for k in sorted(params))
    return hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()


class BinanceError(Exception):
    def __init__(self, status, code, msg):
        super().__init__("바이낸스 오류 %s (code %s): %s" % (status, code, msg))
        self.status, self.code, self.msg = status, code, msg


class Binance:
    def __init__(self, key, secret, base=BASE):
        self._key = key
        self._secret = secret.encode()
        self.base = base
        self.offset_ms = 0

    def __repr__(self):                    # 실수로 찍혀도 키가 나오지 않게
        return "Binance(base=%r)" % self.base

    # ------------------------------------------------------------------ 기본
    def _call(self, method, path, params=None, signed=False, key=False, timeout=15):
        params = dict(params or {})
        headers = {"User-Agent": "candleman-alert"}
        if key or signed:
            headers["X-MBX-APIKEY"] = self._key
        if signed:
            params["timestamp"] = int(time.time() * 1000) + self.offset_ms
            params.setdefault("recvWindow", 10000)
            q = urllib.parse.urlencode(params)
            sig = hmac.new(self._secret, q.encode(), hashlib.sha256).hexdigest()
            q = q + "&signature=" + sig
        else:
            q = urllib.parse.urlencode(params)
        url = self.base + path + (("?" + q) if q else "")
        req = urllib.request.Request(url, method=method, headers=headers, data=b"" if method in ("POST", "PUT", "DELETE") else None)
        last = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    body = r.read().decode("utf-8")
                    return json.loads(body) if body else {}
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "replace")
                try:
                    j = json.loads(text)
                    code, msg = j.get("code"), j.get("msg")
                except Exception:
                    code, msg = None, text[:200]
                if e.code >= 500 and attempt < 2:
                    last = BinanceError(e.code, code, msg)
                    time.sleep(1 + attempt)
                    continue
                raise BinanceError(e.code, code, msg) from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = e
                time.sleep(1 + attempt)
        raise ConnectionError("바이낸스에 연결하지 못함: %s" % type(last).__name__)

    async def call(self, *a, **k):
        return await asyncio.to_thread(self._call, *a, **k)

    # ------------------------------------------------------------------ 쓰는 것들
    async def sync_time(self):
        j = await self.call("GET", "/fapi/v1/time")
        self.offset_ms = int(j["serverTime"]) - int(time.time() * 1000)
        return self.offset_ms

    async def new_listen_key(self):
        j = await self.call("POST", "/fapi/v1/listenKey", key=True)
        return j["listenKey"]

    async def keepalive_listen_key(self):
        return await self.call("PUT", "/fapi/v1/listenKey", key=True)

    async def positions(self):
        try:
            rows = await self.call("GET", "/fapi/v3/positionRisk", signed=True)
        except BinanceError:
            rows = await self.call("GET", "/fapi/v2/positionRisk", signed=True)
        return [r for r in rows if float(r.get("positionAmt") or 0) != 0]

    async def account(self):
        """선물 계좌 요약 — totalMarginBalance(증거금 합계 = 지갑 잔고 + 미실현 손익) 등."""
        try:
            return await self.call("GET", "/fapi/v3/account", signed=True)
        except BinanceError:
            return await self.call("GET", "/fapi/v2/account", signed=True)

    async def open_orders(self):
        return await self.call("GET", "/fapi/v1/openOrders", signed=True)

    async def open_algo_orders(self):
        try:
            j = await self.call("GET", "/fapi/v1/openAlgoOrders", signed=True)
        except BinanceError:
            return []
        if isinstance(j, dict):
            for k in ("orders", "rows", "data"):
                if isinstance(j.get(k), list):
                    return j[k]
            return []
        return j if isinstance(j, list) else []

    async def order(self, symbol, order_id):
        return await self.call("GET", "/fapi/v1/order", {"symbol": symbol, "orderId": order_id}, signed=True)

    async def income(self, income_type, start_ms, end_ms):
        """incomeType 별 기록을 모두 (1,000개씩 이어 받음)."""
        out, start = [], start_ms
        for _ in range(50):
            rows = await self.call("GET", "/fapi/v1/income", {"incomeType": income_type, "startTime": start, "endTime": end_ms, "limit": 1000}, signed=True)
            out += rows
            if len(rows) < 1000:
                break
            start = int(rows[-1]["time"]) + 1
        return out

    def _spot(self):
        b = Binance.__new__(Binance)
        b._key, b._secret, b.base, b.offset_ms = self._key, self._secret, SPOT_BASE, self.offset_ms
        return b

    async def api_restrictions(self):
        """이 키의 권한 (읽기 · 선물 거래 · 출금 등). 현물 쪽 주소(api.binance.com)에 있음."""
        return await self._spot().call("GET", "/sapi/v1/account/apiRestrictions", signed=True)

    # ------------------------------------------------------------------ 현물 (조회만)
    async def spot_balances(self):
        """현물 잔고 {자산: 수량(쓸 수 있는 것 + 주문에 묶인 것)} — 0 인 것은 뺌."""
        j = await self._spot().call("GET", "/api/v3/account", {"omitZeroBalances": "true"}, signed=True)
        out = {}
        for b in j.get("balances", []):
            q = dec(b.get("free")) + dec(b.get("locked"))
            if q > 0:
                out[b.get("asset")] = q
        return out

    async def funding_balances(self):
        """펀딩 지갑 잔고 {자산: 수량(쓸 수 있는 것 + 묶인 것 + 얼린 것)} — 주인장 수익률의 지금 자산에 넣음 (POST 지만 조회만 함)."""
        rows = await self._spot().call("POST", "/sapi/v1/asset/get-funding-asset", signed=True)
        out = {}
        for r in rows or []:
            q = dec(r.get("free")) + dec(r.get("locked")) + dec(r.get("freeze"))
            if q > 0:
                out[r.get("asset")] = q
        return out

    async def spot_open_orders(self):
        return await self._spot().call("GET", "/api/v3/openOrders", signed=True)

    async def spot_trades(self, symbol, from_id=0, limit=1000):
        """내 현물 거래 기록 (오래된 것부터, from_id 부터 limit 개)."""
        return await self._spot().call("GET", "/api/v3/myTrades", {"symbol": symbol, "fromId": from_id, "limit": limit}, signed=True)

    async def spot_price(self, symbol):
        j = await self._spot().call("GET", "/api/v3/ticker/price", {"symbol": symbol})
        return j.get("price")

    def spot_subscribe(self, req_id):
        """현물 사용자 자료 구독 요청(userDataStream.subscribe.signature). 키 · 서명은 이 요청 안에만 있음."""
        params = {"apiKey": self._key, "recvWindow": 10000, "timestamp": int(time.time() * 1000) + self.offset_ms}
        params["signature"] = sign_ws_params(self._secret, params)
        return {"id": req_id, "method": "userDataStream.subscribe.signature", "params": params}

    async def price(self, symbol):
        j = await self.call("GET", "/fapi/v1/ticker/price", {"symbol": symbol})
        return j.get("price")

    async def symbol_kind(self, symbol):
        """종목 분류(COIN · EQUITY · KR_EQUITY · COMMODITY …). 공개 정보라 키를 쓰지 않음. 모르면 None."""
        try:
            j = await self.call("GET", "/fapi/v1/exchangeInfo", timeout=30)
        except Exception:
            return None
        for s in j.get("symbols", []):
            if s.get("symbol") == symbol:
                return s.get("underlyingType")
        return None
