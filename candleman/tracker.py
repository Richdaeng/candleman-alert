"""체결 · 주문 · 포지션 장부. 네트워크를 쓰지 않는 순수한 계산이라 가짜 이벤트로 시험할 수 있습니다.

바깥에서 하는 일:
  - 바이낸스 이벤트(dict)를 on_event(ev, now_ms) 로 넣는다
  - 1초마다 tick(now_ms) 를 부른다 (미체결 주문 알림을 2초 늦게 · 부분 체결을 5초 묶어서 보내려고)
  - 돌려받은 글(Msg)을 디스코드로, 기록(dict)을 일지 파일로 보낸다

알림에 수익률(%)은 넣지 않습니다(2026-10-21 댕이 결정). 금액은 수량과 달러로만.
글은 투자를 모르는 사람도 읽게 씁니다(10/21): 첫 줄에 무슨 일인지(종목 이름 · 롱/숏 풀이 · 이익/손실),
둘째 줄에 가격 · 수량. 휴대폰 알림에 별표가 그대로 보여서 굵은 글씨(**)는 쓰지 않습니다.
"""
from dataclasses import dataclass, field, replace
from decimal import Decimal

from . import fmt, names
from .fmt import ZERO, dec

PENDING_DELAY_MS = 2000      # 미체결 주문 알림을 이만큼 늦게 — 바로 체결되는 지정가는 '주문' 글을 건너뜀
PARTIAL_GATHER_MS = 5000     # 부분 체결을 이만큼 모아서 한 번에
AUTH_GRACE_MS = 3000         # ACCOUNT_UPDATE 의 포지션 값은 이만큼 기다렸다 맞춤 (체결 이벤트가 늦게 올 수 있어서)
TRIGGER_LINK_MS = 30_000     # 조건부 주문(스탑 로스 등)이 발동한 앞뒤 이만큼 안에 생긴 같은 종목 · 같은 방향 주문은 그 발동으로 생긴 것으로 봄
QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "BNFCR")
USD_ASSETS = ("USDT", "USDC", "FDUSD", "BUSD", "BNFCR")

CONDITIONAL = ("STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET")


def base_of(symbol):
    for q in QUOTES:
        if symbol.endswith(q) and len(symbol) > len(q):
            return symbol[:-len(q)]
    return symbol


def sgn(d):
    return 1 if d > 0 else (-1 if d < 0 else 0)


@dataclass
class Msg:
    channel: str          # trade | summary | status
    text: str


@dataclass
class Position:
    symbol: str
    pside: str            # BOTH | LONG | SHORT
    amt: Decimal = ZERO   # 롱은 +, 숏은 −
    entry: Decimal = ZERO
    opened_ms: int = 0    # 0 이면 모름(프로그램을 켜기 전부터 있던 포지션)
    trip_pnl: Decimal = ZERO   # 이번 포지션에서 지금까지 실현한 손익 (일부 청산 포함)
    reduced: bool = False      # 이번 포지션에서 일부 청산을 한 적이 있음


@dataclass
class Order:
    key: str              # "o:<orderId>" 또는 "a:<algoId>"
    symbol: str
    side: str             # BUY | SELL
    otype: str
    pside: str = "BOTH"
    qty: Decimal = ZERO
    price: Decimal = ZERO
    stop: Decimal = ZERO
    reduce: bool = False
    closepos: bool = False
    liquidation: bool = False
    pending_due: int = 0          # 0 이 아니면 이 시각에 '주문' 알림
    pending_posted: bool = False
    triggered: bool = False       # 조건부 주문이 발동됨 (또는 발동해서 생긴 주문)
    trigger_kind: str = ""        # 발동한 조건부 주문의 종류 ('스탑 로스' · '익절 주문' …) — 체결 글에 '스탑 로스 발동'으로
    linked: bool = False          # (조건부 주문 쪽) 발동해서 생긴 실제 주문과 짝을 지었음
    seen_ms: int = 0              # 이 프로그램이 처음 본 때
    # 아직 글로 내보내지 않은 체결 묶음
    chunk_qty: Decimal = ZERO
    chunk_notional: Decimal = ZERO
    chunk_rp: Decimal = ZERO
    chunk_fee_usd: Decimal = ZERO
    chunk_fee_other: dict = field(default_factory=dict)
    chunk_due: int = 0
    chunk_first_ms: int = 0
    filled_total: Decimal = ZERO
    places: int = 0               # 가격 소수 자리수 (체결가 문자열에서)
    done: bool = False


class Tracker:
    def __init__(self):
        self.positions = {}       # (symbol, pside) -> Position
        self.orders = {}          # key -> Order
        self.auth = {}            # (symbol, pside) -> (amt, entry, 받은 시각) — ACCOUNT_UPDATE 가 알려 준 값
        self.unknown_seen = set()
        self.triggers = []        # 최근 발동한 조건부 주문 [(시각, 기호, 방향, 포지션 방향, 종류, 실제 주문 번호, 조건부 주문 키)] — 아직 짝을 못 찾은 것

    # ------------------------------------------------------------------ 처음 상태
    def load_positions(self, rows, now_ms):
        """REST positionRisk 결과로 시작 상태를 채움. 알림은 보내지 않음."""
        for r in rows:
            amt = dec(r.get("positionAmt"))
            if amt == 0:
                continue
            ps = r.get("positionSide") or "BOTH"
            p = Position(r["symbol"], ps, amt, dec(r.get("entryPrice")), 0)
            self.positions[(p.symbol, ps)] = p

    def load_orders(self, rows, algo_rows=()):
        """REST openOrders · openAlgoOrders 결과. 이미 걸린 주문은 '알림을 보낸 것'으로 쳐서, 나중의 체결 · 취소만 알림."""
        for r in rows:
            o = Order("o:%s" % r.get("orderId"), r.get("symbol", ""), r.get("side", ""), r.get("type", r.get("origType", "")),
                      r.get("positionSide") or "BOTH", dec(r.get("origQty")), dec(r.get("price")), dec(r.get("stopPrice")),
                      bool(r.get("reduceOnly")), bool(r.get("closePosition")))
            o.pending_posted = True
            o.filled_total = dec(r.get("executedQty"))
            self.orders[o.key] = o
        for r in algo_rows:
            f = _algo_fields(r)
            if not f["id"]:
                continue
            o = Order("a:%s" % f["id"], f["symbol"], f["side"], f["otype"], f["pside"], f["qty"], f["price"], f["trigger"], f["reduce"], f["closepos"])
            o.pending_posted = True
            self.orders[o.key] = o

    # ------------------------------------------------------------------ 이벤트
    def on_event(self, ev, now_ms):
        """돌려줌: (글 목록, 일지 기록 목록)."""
        e = ev.get("e")
        if e == "ORDER_TRADE_UPDATE":
            return self._order_update(ev, now_ms)
        if e == "ALGO_UPDATE":
            return self._algo_update(ev, now_ms)
        if e == "ACCOUNT_UPDATE":
            return self._account_update(ev, now_ms)
        if e == "MARGIN_CALL":
            return [Msg("status", "⚠️ 마진 콜 알림을 받음. 바이낸스 앱에서 포지션 확인 필요.")], [{"kind": "margin_call", "t": now_ms}]
        if e in ("TRADE_LITE", "ACCOUNT_CONFIG_UPDATE", "STRATEGY_UPDATE", "GRID_UPDATE"):
            return [], []
        if e == "CONDITIONAL_ORDER_TRIGGER_REJECT":
            return [Msg("status", "⛔ 조건부 주문이 발동했지만 거부됨. 바이낸스 앱에서 확인 필요.")], [{"kind": "trigger_reject", "t": now_ms, "raw_keys": sorted(ev.keys())}]
        # 처음 보는 이벤트는 이름만 기록 (값은 남기지 않음)
        if e not in self.unknown_seen:
            self.unknown_seen.add(e)
            return [], [{"kind": "unknown_event", "t": now_ms, "e": e, "keys": sorted(ev.keys())}]
        return [], []

    def _order_update(self, ev, now_ms):
        o = ev.get("o", {})
        key = "o:%s" % o.get("i")
        x = o.get("x")
        st = o.get("X")
        msgs, recs = [], []
        order = self.orders.get(key)
        if order is None:
            otype = o.get("o", "")
            order = Order(key, o.get("s", ""), o.get("S", ""), otype, o.get("ps") or "BOTH", dec(o.get("q")), dec(o.get("p")),
                          dec(o.get("sp")), bool(o.get("R")), bool(o.get("cp")))
            cid = str(o.get("c", ""))
            order.liquidation = otype == "LIQUIDATION" or cid.startswith("autoclose-") or cid.startswith("adl_autoclose")
            order.seen_ms = now_ms
            if o.get("ot") in CONDITIONAL and otype in ("MARKET", "LIMIT"):
                order.triggered = True        # 조건부 주문이 발동해서 생긴 주문 (원래 종류가 같이 옴)
                order.trigger_kind = self._kind(replace(order, otype=o.get("ot")), self._closing(order))
            elif otype in ("MARKET", "LIMIT") and not order.liquidation:
                kind = self._take_trigger(order, now_ms)   # 바이낸스 조건부 주문(algo)이 발동하면 원래 종류 없이 새 주문으로 옴
                if kind:
                    order.triggered, order.trigger_kind = True, kind
            self.orders[key] = order
        if x == "NEW":
            if order.otype != "MARKET" and not order.liquidation and not order.triggered and not order.pending_posted:
                order.pending_due = now_ms + PENDING_DELAY_MS
            recs.append({"kind": "order_new", "t": now_ms, "key": key, "symbol": order.symbol, "side": order.side, "type": order.otype,
                         "qty": str(order.qty), "price": str(order.price), "stop": str(order.stop)})
        elif x in ("TRADE", "CALCULATED"):
            if x == "CALCULATED":
                order.liquidation = True
            l, L = dec(o.get("l")), dec(o.get("L"))
            order.places = max(order.places, decimals(o.get("L")), decimals(o.get("p")))
            if order.chunk_qty == 0:
                order.chunk_first_ms = now_ms
            order.chunk_qty += l
            order.chunk_notional += l * L
            order.chunk_rp += dec(o.get("rp"))
            fee, fee_asset = dec(o.get("n")), o.get("N") or "USDT"
            if fee_asset in USD_ASSETS:
                order.chunk_fee_usd += fee
            elif fee:
                order.chunk_fee_other[fee_asset] = order.chunk_fee_other.get(fee_asset, ZERO) + fee
            order.filled_total += l
            order.pending_due = 0                  # 체결이 먼저 왔으면 '주문' 알림은 건너뜀
            if st == "FILLED":
                m, r = self._flush(order, now_ms)
                msgs += m
                recs += r
                order.done = True
            else:
                order.chunk_due = now_ms + PARTIAL_GATHER_MS
        elif x in ("CANCELED", "EXPIRED") or st in ("CANCELED", "EXPIRED", "EXPIRED_IN_MATCH"):
            if order.chunk_qty > 0:
                m, r = self._flush(order, now_ms)
                msgs += m
                recs += r
            if order.pending_posted and not order.liquidation and not self._orphan(order):
                expired = x == "EXPIRED" or st in ("EXPIRED", "EXPIRED_IN_MATCH")
                text = self._order_note(order, "⌛ %s 만료" if expired else "✖️ %s 취소")
                if order.filled_total > 0:
                    base = base_of(order.symbol)
                    text += " 중 %s 체결 · 남은 %s %s" % (names.amount(order.filled_total, base),
                                                       names.amount(order.qty - order.filled_total, base), "만료" if expired else "취소")
                msgs.append(Msg("trade", text))
            recs.append({"kind": "order_end", "t": now_ms, "key": key, "status": st or x})
            order.pending_due = 0
            order.done = True
        elif x == "AMENDMENT":
            order.qty, order.price = dec(o.get("q")) or order.qty, dec(o.get("p")) or order.price
            if order.pending_posted:
                msgs.append(Msg("trade", self._order_note(order, "✏️ %s 수정")))
            recs.append({"kind": "order_amend", "t": now_ms, "key": key, "qty": str(order.qty), "price": str(order.price)})
        self._gc()
        return msgs, recs

    def _algo_update(self, ev, now_ms):
        f = _algo_fields(ev.get("o") if isinstance(ev.get("o"), dict) else ev)
        msgs, recs = [], [{"kind": "algo", "t": now_ms, "status": f["status"], "id": f["id"], "symbol": f["symbol"], "type": f["otype"],
                           "keys": sorted((ev.get("o") or {}).keys()) if isinstance(ev.get("o"), dict) else sorted(ev.keys())}]
        if not f["id"]:
            return msgs, recs
        key = "a:%s" % f["id"]
        order = self.orders.get(key)
        if order is None:
            order = Order(key, f["symbol"], f["side"], f["otype"], f["pside"], f["qty"], f["price"], f["trigger"], f["reduce"], f["closepos"])
            self.orders[key] = order
        st = (f["status"] or "").upper()
        if st == "NEW":
            if not order.pending_posted and not order.pending_due:
                order.pending_due = now_ms + PENDING_DELAY_MS
        elif st in ("TRIGGERING", "TRIGGERED", "FINISHED"):
            if not order.linked and (not order.triggered or f.get("ai")):   # 발동 — 생길(또는 먼저 와 있는) 실제 주문과 짝지음
                self._add_trigger(order, f.get("ai"), now_ms)
            order.triggered = True
            order.pending_due = 0
            if st == "FINISHED":
                order.done = True
        elif st in ("CANCELED", "CANCELLED", "EXPIRED", "REJECTED"):
            if order.pending_posted and (st == "REJECTED" or not self._orphan(order)):
                word = {"EXPIRED": "⌛ %s 만료", "REJECTED": "⛔ %s 거부"}.get(st, "✖️ %s 취소")
                msgs.append(Msg("trade", self._order_note(order, word)))
            order.pending_due = 0
            order.done = True
        self._gc()
        return msgs, recs

    def _add_trigger(self, algo, ai, now_ms):
        """조건부 주문(algo)이 발동함 → 그 발동으로 생긴 주문이 이미 와 있으면 지금 표시하고, 아니면 TRIGGER_LINK_MS 동안 기다림.
        실제 주문 번호(ai)를 알면 그 번호로만, 모르면 같은 종목 · 방향 · 포지션 방향으로 짝지음."""
        kind = self._kind(algo, self._closing(algo))
        ai = str(ai or "")
        self.triggers = [t for t in self.triggers if now_ms - t[0] < TRIGGER_LINK_MS and t[6] != algo.key]
        for o in self.orders.values():
            if self._pairs(o, algo.symbol, algo.side, algo.pside, ai, now_ms):
                o.triggered, o.trigger_kind, algo.linked = True, kind, True
                return
        self.triggers.append((now_ms, algo.symbol, algo.side, algo.pside, kind, ai, algo.key))

    @staticmethod
    def _pairs(o, symbol, side, pside, ai, now_ms):
        """o 가 그 발동으로 생긴 주문인가 (아직 짝이 없는 일반 주문 중에서)."""
        if not o.key.startswith("o:") or o.liquidation or (o.triggered and o.trigger_kind) or o.otype not in ("MARKET", "LIMIT"):
            return False
        if ai:
            return o.key == "o:" + ai
        return (o.symbol, o.side, o.pside) == (symbol, side, pside) and abs(now_ms - o.seen_ms) < TRIGGER_LINK_MS and not o.done

    def _take_trigger(self, order, now_ms):
        """새로 생긴 주문이 최근 발동한 조건부 주문으로 생긴 것이면 그 종류를 돌려주고 짝을 지움. 아니면 ''."""
        self.triggers = [t for t in self.triggers if now_ms - t[0] < TRIGGER_LINK_MS]
        for t in reversed(self.triggers):
            if self._pairs(order, t[1], t[2], t[3], t[5], now_ms):
                self.triggers.remove(t)
                if t[6] in self.orders:
                    self.orders[t[6]].linked = True
                return t[4]
        return ""

    def _account_update(self, ev, now_ms):
        a = ev.get("a", {})
        reason = a.get("m")
        recs = []
        if reason == "FUNDING_FEE":
            for b in a.get("B", []):
                bc = dec(b.get("bc"))
                if bc:
                    recs.append({"kind": "funding", "t": now_ms, "asset": b.get("a"), "amount": str(bc), "symbol": a.get("S", "")})
            return [], recs
        for p in a.get("P", []):
            k = (p.get("s", ""), p.get("ps") or "BOTH")
            self.auth[k] = (dec(p.get("pa")), dec(p.get("ep")), now_ms)
        return [], recs

    # ------------------------------------------------------------------ 시간
    def tick(self, now_ms):
        msgs, recs = [], []
        for order in list(self.orders.values()):
            if order.pending_due and now_ms >= order.pending_due and not order.pending_posted:
                order.pending_due = 0
                order.pending_posted = True
                msgs.append(Msg("trade", self._order_posted(order)))
                recs.append({"kind": "pending_posted", "t": now_ms, "key": order.key})
            if order.chunk_qty > 0 and order.chunk_due and now_ms >= order.chunk_due:
                m, r = self._flush(order, now_ms)
                msgs += m
                recs += r
        self._reconcile(now_ms)
        self._gc()
        return msgs, recs

    # ------------------------------------------------------------------ 안쪽
    def _pos(self, symbol, pside):
        k = (symbol, pside)
        if k not in self.positions:
            self.positions[k] = Position(symbol, pside)
        return self.positions[k]

    def _has_chunk(self, k):
        return any(o.chunk_qty > 0 and (o.symbol, o.pside) == k for o in self.orders.values())

    def _reconcile(self, now_ms):
        """ACCOUNT_UPDATE 값과 장부가 다르면 맞춤 (아직 글로 내보내지 않은 체결이 없을 때만)."""
        for k, (amt, entry, t) in list(self.auth.items()):
            if now_ms - t < AUTH_GRACE_MS or self._has_chunk(k):
                continue
            p = self._pos(*k)
            if p.amt != amt:
                if p.amt == 0 and amt != 0 and not p.opened_ms:
                    p.opened_ms = now_ms
                p.amt = amt
            if amt != 0 and entry:
                p.entry = entry
            if amt == 0:
                p.entry, p.trip_pnl, p.opened_ms, p.reduced = ZERO, ZERO, 0, False
            del self.auth[k]

    def _gc(self):
        for k in [k for k, o in self.orders.items() if o.done and o.chunk_qty == 0 and not o.pending_due]:
            del self.orders[k]

    def _closing(self, o):
        """이 주문이 포지션을 줄이는(청산) 쪽인가."""
        buy = o.side == "BUY"
        if o.pside == "LONG":
            return not buy
        if o.pside == "SHORT":
            return buy
        pos = self.positions.get((o.symbol, o.pside))
        cur = pos.amt if pos else ZERO
        return o.reduce or o.closepos or (cur > 0 and not buy) or (cur < 0 and buy)

    def _orphan(self, o):
        """포지션이 이미 없어진 뒤의 청산 주문 — 바이낸스가 저절로 지우는 것이라 취소 · 만료를 알리지 않음."""
        if not self._closing(o):
            return False
        pos = self.positions.get((o.symbol, o.pside))
        return not pos or pos.amt == 0

    @staticmethod
    def _what(o, closing):
        buy = o.side == "BUY"
        if closing:
            return "숏 청산" if buy else "롱 청산"
        return "롱 진입" if buy else "숏 진입"

    @staticmethod
    def _kind(o, closing):
        if o.otype in ("STOP", "STOP_MARKET"):
            return "스탑 로스" if closing else "조건부 진입 주문"
        if o.otype in ("TAKE_PROFIT", "TAKE_PROFIT_MARKET"):
            return "익절 주문" if closing else "조건부 진입 주문"
        if o.otype == "TRAILING_STOP_MARKET":
            return "트레일링 스탑"
        return "주문"

    def _detail(self, o, closing, approx=False):
        """주문 내용을 말로: '$82,000에 0.05 BTC · 약 $4,100어치' · '가격이 $80,000에 닿으면 시장가로 모두 정리'."""
        base = base_of(o.symbol)
        amt = "모두" if o.closepos else (names.amount(o.qty, base) if o.qty else "")
        verb = "정리" if closing else "진입"
        if o.otype == "TRAILING_STOP_MARKET":
            s = " ".join(w for w in ("가격을 따라 움직이다 반대로 꺾이면 시장가로", amt, verb) if w)
        elif o.otype in CONDITIONAL:
            when = "가격이 %s에 닿으면" % fmt.dollar(o.stop) if o.stop else "조건이 맞으면"
            at = "시장가로" if o.otype.endswith("_MARKET") else ("%s에" % fmt.dollar(o.price) if o.price else "")
            s = " ".join(w for w in (when, at, amt, verb) if w)
        else:
            s = " ".join(w for w in ("%s에" % fmt.dollar(o.price) if o.price else "", amt) if w)
        ref = o.price or o.stop
        if approx and o.qty and ref and not o.closepos:
            s += " · " + fmt.worth(o.qty * ref)
        return s

    def _order_posted(self, o):
        """걸어 둔 주문: '📝 주문 걸어 둠 · 비트코인(BTC) 롱 진입 (오르면 이익)' + 둘째 줄에 가격 · 수량."""
        closing = self._closing(o)
        hint = "" if closing else " (%s)" % fmt.side_hint(1 if o.side == "BUY" else -1)
        state = "아직 발동 전" if o.otype in CONDITIONAL else "아직 체결 전"
        return "📝 %s 걸어 둠 · %s %s%s\n%s · %s" % (self._kind(o, closing), names.label(base_of(o.symbol)), self._what(o, closing), hint,
                                                  self._detail(o, closing, approx=True), state)

    def _order_note(self, o, word):
        """취소 · 만료 · 수정 소식 한 줄. word 는 '✖️ %s 취소' 처럼 주문 종류 자리를 비워 둔 글."""
        closing = self._closing(o)
        return "%s · %s %s · %s" % (word % self._kind(o, closing), names.label(base_of(o.symbol)), self._what(o, closing),
                                    self._detail(o, closing))

    def _flush(self, o, now_ms):
        """모은 체결 묶음을 포지션에 반영하고 글을 만듦."""
        q, notional = o.chunk_qty, o.chunk_notional
        avg = (notional / q) if q else ZERO
        rp, fee_usd, fee_other = o.chunk_rp, o.chunk_fee_usd, dict(o.chunk_fee_other)
        first_ms = o.chunk_first_ms
        o.chunk_qty = o.chunk_notional = o.chunk_rp = o.chunk_fee_usd = ZERO
        o.chunk_fee_other = {}
        o.chunk_due = 0
        p = self._pos(o.symbol, o.pside)
        delta = q if o.side == "BUY" else -q
        prev = p.amt
        new = prev + delta
        base = base_of(o.symbol)
        name = names.label(base)
        how = ""
        if o.liquidation:
            how = ""
        elif o.triggered:
            how = " · %s 발동" % (o.trigger_kind or "조건 주문")
        elif o.otype == "LIMIT":
            how = " · 걸어 둔 주문 체결"
        elif o.otype == "MARKET":
            how = " · 바로 체결(시장가)"
        msgs = []
        rec = {"kind": "fill", "t": now_ms, "first_t": first_ms, "key": o.key, "symbol": o.symbol, "pside": o.pside, "side": o.side,
               "qty": str(q), "avg": str(avg), "rp": str(rp), "fee_usd": str(fee_usd), "fee_other": {k: str(v) for k, v in fee_other.items()},
               "prev": str(prev), "new": str(new), "liq": o.liquidation}
        if o.triggered:
            rec["trigger"] = o.trigger_kind or "조건 주문"      # 스탑 로스 · 익절로 정리된 거래를 나중에 따로 셀 수 있게
        liq = o.liquidation

        places = max(o.places, 1)
        unit = Decimal(1).scaleb(-places)

        def px(v):
            return fmt.dollar(dec(v).quantize(unit))

        def amt(v):
            return names.amount(v, base)

        def opened(amount, at_price):
            d = sgn(amount)
            icon = "🟢" if d > 0 else "🔴"
            return "%s %s %s 진입 · %s\n%s에 %s · %s%s" % (icon, name, fmt.side_word(d), fmt.side_hint(d), px(at_price), amt(amount),
                                                       fmt.worth(abs(amount) * at_price), how)

        if prev == 0:
            p.amt, p.entry, p.opened_ms, p.trip_pnl, p.reduced = new, avg, first_ms or now_ms, ZERO, False
            msgs.append(Msg("trade", opened(new, avg)))
            rec["effect"] = "open"
        elif sgn(prev) == sgn(new) and abs(new) > abs(prev):
            p.entry = (abs(prev) * p.entry + q * avg) / abs(new)
            p.amt = new
            d = sgn(new)
            msgs.append(Msg("trade", "➕ %s %s 추가 진입 · %s\n%s에 %s 더 · 평균 가격 %s · 모두 %s · %s%s" % (
                name, fmt.side_word(d), fmt.side_hint(d), px(avg), amt(q), px(p.entry), amt(new), fmt.worth(abs(new) * p.entry), how)))
            rec["effect"] = "add"
        elif sgn(prev) == sgn(new) and new != 0:
            p.amt = new
            p.trip_pnl += rp
            p.reduced = True
            side = fmt.side_word(sgn(prev))
            if liq:
                text = "⚠️ %s %s 일부 강제 청산 · %s\n증거금이 모자라 거래소가 자동으로 정리 · %s에 %s · 남은 %s" % (
                    name, side, fmt.pnl(rp), px(avg), amt(q), amt(new))
            else:
                text = "🟠 %s %s 일부 청산 · %s\n%s에 %s 정리 · 남은 %s%s" % (name, side, fmt.pnl(rp), px(avg), amt(q), amt(new), how)
            msgs.append(Msg("trade", text))
            rec["effect"] = "reduce"
        else:
            closed_qty = abs(prev)
            trip = p.trip_pnl + rp
            side = fmt.side_word(sgn(prev))
            held = (" · %s 보유" % fmt.duration((now_ms - p.opened_ms) / 1000)) if p.opened_ms else ""
            if liq:
                text = "⚠️ %s %s 강제 청산 · %s\n증거금이 모자라 거래소가 자동으로 정리 · %s에 %s%s" % (
                    name, side, fmt.pnl(rp), px(avg), amt(closed_qty), held)
            else:
                text = "⬜ %s %s 청산 · %s\n%s에 %s%s 모두 정리%s%s" % (
                    name, side, fmt.pnl(rp), px(avg), "남은 " if p.reduced else "", amt(closed_qty), held, how)
            if p.reduced:
                text += "\n이번 거래 전체 %s" % fmt.pnl(trip)
            msgs.append(Msg("trade", text))
            rec["effect"] = "close"
            p.amt, p.entry, p.trip_pnl, p.opened_ms, p.reduced = ZERO, ZERO, ZERO, 0, False
            if new != 0:                                   # 반대로 넘어감
                p.amt, p.entry, p.opened_ms = new, avg, now_ms
                msgs.append(Msg("trade", opened(new, avg)))
                rec["effect"] = "flip"
        return msgs, [rec]


def decimals(x):
    """문자열 숫자의 소수 자리수 ("82400.10" → 2). 끝의 0 도 셈 — 바이낸스는 호가 단위대로 적어 보냄."""
    t = str(x or "")
    return len(t.split(".")[1]) if "." in t else 0


def _algo_fields(o):
    """ALGO_UPDATE 의 o, 또는 REST openAlgoOrders 의 한 줄. 이름이 둘 중 어느 쪽이어도 읽히게."""
    def g(*keys):
        for k in keys:
            v = o.get(k) if isinstance(o, dict) else None
            if v not in (None, ""):
                return v
        return None
    return {
        "id": g("aid", "algoId"),
        "ai": g("ai", "actualOrderId"),               # 발동해서 생긴 실제 주문 번호 (발동 전엔 비어 있음)
        "status": g("X", "algoStatus", "status"),
        "otype": g("o", "orderType", "type") or "",
        "symbol": g("s", "symbol") or "",
        "side": g("S", "side") or "",
        "pside": g("ps", "positionSide") or "BOTH",
        "qty": dec(g("q", "quantity", "origQty")),
        "price": dec(g("p", "price")),
        "trigger": dec(g("tp", "triggerPrice", "sp", "stopPrice")),
        "reduce": bool(g("R", "reduceOnly")),
        "closepos": bool(g("cp", "closePosition")),
    }
