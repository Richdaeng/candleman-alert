"""바이낸스 현물 — 체결 알림 · 평균 매수가 · 손익. 네트워크를 쓰지 않는 계산만 여기 두고(가짜 이벤트로 시험), 조회는 spotfeed.py 가 함.

알림 글은 선물과 같은 약속을 따릅니다: 첫 줄에 무슨 일인지, 둘째 줄에 가격 · 수량. 휴대폰 알림에 별표가 보이므로 굵은 글씨는 안 씀.
  🟢 처음 매수 · ➕ 추가 매수 · 🟠 일부 매도 · ⬜ 모두 매도 · 📝 주문 걸어 둠 · ✖️ 주문 취소
현물 손익은 바이낸스가 알려 주지 않아서, 이 계정의 거래 기록(myTrades)을 처음부터 차례로 넣어 평균 매수가를 이어 계산합니다(이동 평균).
그 코인 · 달러 짝(예: BTCUSDT)으로 산 것만 알 수 있으므로, 입금하거나 다른 짝으로 산 몫을 팔면 손익을 빼고 알립니다.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from . import fmt, names
from .fmt import ZERO, dec
from .tracker import USD_ASSETS, Msg

PENDING_DELAY_MS = 2000       # 걸어 둔 주문 알림을 이만큼 늦게 — 바로 체결되거나 바로 취소된 주문은 건너뜀
PARTIAL_GATHER_MS = 5000      # 다 체결되지 않은 주문의 체결은 이만큼 조용해지면 모아서 한 번에
DUST_USD = Decimal("1")       # 이보다 적게 남으면 '모두 매도'로 봄 (수수료 찌꺼기)
SHOW_USD = Decimal("10")      # #현재포지션 · /포지션에 보이는 현물 (USDT 는 뺌 · 2026-10-21 댕이 결정)
CASH = USD_ASSETS + ("TUSD", "DAI", "USDE", "USD1", "BFUSD", "USDP", "PYUSD", "RLUSD")   # 보유 카드에서 빼는 달러 코인 (현금과 같음)
SPOT_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "TUSD", "DAI", "BTC", "ETH", "BNB", "EUR", "TRY", "BRL", "JPY")
CONDITIONAL = ("STOP_LOSS", "STOP_LOSS_LIMIT", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT")
ENDED = {"CANCELED": "취소", "EXPIRED": "만료", "EXPIRED_IN_MATCH": "만료", "REJECTED": "거부", "TRADE_PREVENTION": "만료"}


def split_symbol(symbol):
    """'BTCUSDT' → ('BTC', 'USDT'). 모르는 짝이면 (symbol, '')."""
    for q in SPOT_QUOTES:
        if symbol.endswith(q) and len(symbol) > len(q):
            return symbol[:-len(q)], q
    return symbol, ""


@dataclass
class SpotOrder:
    oid: int
    symbol: str
    side: str                     # BUY | SELL
    otype: str                    # LIMIT · MARKET · STOP_LOSS_LIMIT …
    tif: str
    qty: Decimal
    price: Decimal
    stop: Decimal
    seen_ms: int                  # 이 프로그램이 처음 본 때
    status: str = "NEW"
    posted: bool = False          # '걸어 둠' 글을 올렸음 (또는 켜기 전부터 있던 주문)
    cum: Decimal = ZERO           # 지금까지 체결된 수량
    g_qty: Decimal = ZERO         # 아직 알리지 않은 체결 (모으는 중)
    g_quote: Decimal = ZERO
    g_fee_quote: Decimal = ZERO   # 짝 화폐(예: USDT)로 낸 수수료
    g_fees: dict = field(default_factory=dict)   # 다른 자산으로 낸 수수료 {자산: 수량}
    g_first_ms: int = 0
    g_last_ms: int = 0


@dataclass
class SpotFill:
    """알릴 체결 묶음 (주문 하나에서 모은 것). 손익 · 남은 수량은 글을 만들 때 붙임."""
    oid: int
    symbol: str
    base: str
    quote: str
    side: str
    otype: str
    qty: Decimal
    quote_qty: Decimal
    fee_quote: Decimal
    fees: dict
    t_ms: int
    partial: bool                 # 주문이 아직 다 체결되지 않고 걸려 있음
    open_left: Decimal            # partial 일 때 아직 걸려 있는 수량

    @property
    def avg(self):
        return self.quote_qty / self.qty if self.qty else ZERO

    @property
    def usd(self):
        return self.quote in USD_ASSETS


class SpotTracker:
    """현물 주문 · 체결 장부. on_event / tick 이 (글, 알릴 체결, 일지 기록)을 돌려줌."""

    def __init__(self):
        self.orders = {}
        self.balances = {}

    def load_balances(self, balances):
        self.balances = dict(balances)

    def load_orders(self, rows, now_ms=0):
        """(다시) 붙을 때 걸려 있는 주문 — '걸어 둠' 글은 다시 올리지 않고, 나중에 취소되면 알림.
        그사이 끝난 주문은 장부에서 뺌(알리던 체결이 남아 있으면 둠)."""
        open_ids = set()
        for r in rows or []:
            oid = r.get("orderId")
            open_ids.add(oid)
            if oid in self.orders:
                continue
            o = self._new(oid, r.get("symbol", ""), r.get("side", ""), r.get("type", ""), r.get("timeInForce", ""),
                          r.get("origQty"), r.get("price"), r.get("stopPrice"), now_ms)
            o.posted, o.cum, o.status = True, dec(r.get("executedQty")), r.get("status") or "NEW"
            self.orders[oid] = o
        for oid in list(self.orders):
            if oid not in open_ids and not self.orders[oid].g_qty:
                self.orders.pop(oid)

    @staticmethod
    def _new(oid, symbol, side, otype, tif, qty, price, stop, now_ms):
        return SpotOrder(oid=oid, symbol=symbol, side=side, otype=otype, tif=tif or "", qty=dec(qty), price=dec(price), stop=dec(stop),
                         seen_ms=now_ms)

    def on_event(self, ev, now_ms):
        e = ev.get("e")
        if e == "outboundAccountPosition":
            for b in ev.get("B") or []:
                self.balances[b.get("a")] = dec(b.get("f")) + dec(b.get("l"))
            return [], [], []
        if e != "executionReport":
            return [], [], []
        oid = ev.get("i")
        o = self.orders.get(oid)
        if o is None:
            o = self._new(oid, ev.get("s", ""), ev.get("S", ""), ev.get("o", ""), ev.get("f", ""), ev.get("q"), ev.get("p"), ev.get("P"), now_ms)
            self.orders[oid] = o
        x, status = ev.get("x"), ev.get("X") or o.status
        o.status = status
        msgs, fills, recs = [], [], []
        if x == "REPLACED":                               # 주문을 고침 (수량 · 가격)
            o.qty, o.price = dec(ev.get("q")) or o.qty, dec(ev.get("p")) or o.price
        elif x == "TRADE":
            last = dec(ev.get("l"))
            if not o.g_qty:
                o.g_first_ms = now_ms
            o.g_qty += last
            o.g_quote += dec(ev.get("Y")) or last * dec(ev.get("L"))
            o.g_last_ms = now_ms
            o.cum = dec(ev.get("z")) or o.cum + last
            fee, fee_asset = dec(ev.get("n")), ev.get("N")
            if fee and fee_asset:
                if fee_asset == split_symbol(o.symbol)[1]:
                    o.g_fee_quote += fee
                else:
                    o.g_fees[fee_asset] = o.g_fees.get(fee_asset, ZERO) + fee
            if status == "FILLED":
                fills.append(self._flush(o, now_ms, partial=False))
                self.orders.pop(oid, None)
        if x in ENDED or (x != "TRADE" and status in ENDED):
            why = ENDED.get(x) or ENDED.get(status)
            if o.g_qty:
                fills.append(self._flush(o, now_ms, partial=False))
            text = self._ended_text(o, why)
            if text:
                msgs.append(Msg("trade", text))
            recs.append({"kind": "spot_order_end", "t": now_ms, "oid": oid, "symbol": o.symbol, "status": status, "filled": str(o.cum)})
            self.orders.pop(oid, None)
        return msgs, fills, recs

    def tick(self, now_ms):
        msgs, fills = [], []
        for o in list(self.orders.values()):
            if (not o.posted and not o.cum and not o.g_qty and o.status == "NEW" and o.otype != "MARKET"
                    and o.tif not in ("IOC", "FOK") and now_ms - o.seen_ms >= PENDING_DELAY_MS):
                o.posted = True
                msgs.append(Msg("trade", posted_text(o)))
            if o.g_qty and now_ms - o.g_last_ms >= PARTIAL_GATHER_MS:
                fills.append(self._flush(o, now_ms, partial=True))
        return msgs, fills, []

    @staticmethod
    def _flush(o, now_ms, partial):
        base, quote = split_symbol(o.symbol)
        f = SpotFill(oid=o.oid, symbol=o.symbol, base=base, quote=quote, side=o.side, otype=o.otype, qty=o.g_qty, quote_qty=o.g_quote,
                     fee_quote=o.g_fee_quote, fees=dict(o.g_fees), t_ms=o.g_first_ms or now_ms, partial=partial,
                     open_left=max(o.qty - o.cum, ZERO))
        o.g_qty = o.g_quote = o.g_fee_quote = ZERO
        o.g_fees = {}
        return f

    @staticmethod
    def _ended_text(o, why):
        """취소 · 만료 글. 시장가 · 즉시 체결형(IOC · FOK)의 남은 몫이 사라지는 것과, 알리기 전에 바로 취소된 주문은 조용히."""
        if o.otype == "MARKET" or o.tif in ("IOC", "FOK") or (not o.posted and not o.cum):
            return None
        base, quote = split_symbol(o.symbol)
        line = order_line(o, base, quote)
        if o.cum:
            line += " 중 %s 체결 · 남은 %s %s" % (names.amount(o.cum, base), names.amount(max(o.qty - o.cum, ZERO), base), why)
        return "✖️ %s 현물 %s 주문 %s\n%s" % (names.label(base), side_word(o.side), why, line)


# ---------------------------------------------------------------------- 글
def side_word(side):
    return "매수" if side == "BUY" else "매도"


def price_text(p, quote):
    return fmt.price2(p) if quote in USD_ASSETS or not quote else "%s %s" % (fmt.plain(p), quote)


def worth_text(v, quote):
    return "약 %s어치" % fmt.usd_about(v) if quote in USD_ASSETS or not quote else "%s %s어치" % (fmt.plain(v), quote)


def order_line(o, base, quote):
    """'$80,000.00에 0.05 BTC' (조건 주문이면 '가격이 $79,000.00에 닿으면 …')."""
    s = "%s에 %s" % (price_text(o.price, quote), names.amount(o.qty, base)) if o.price else names.amount(o.qty, base)
    if o.stop:
        s = "가격이 %s에 닿으면 %s" % (price_text(o.stop, quote), s)
    return s


def posted_text(o):
    base, quote = split_symbol(o.symbol)
    cond = o.otype in CONDITIONAL
    what = cond_word(o.otype, o.side) if cond else "주문"
    tail = " · 아직 발동 전" if cond else " · 아직 체결 전"
    worth = " · " + worth_text(o.qty * o.price, quote) if o.price else ""
    return "📝 %s 현물 %s %s 걸어 둠\n%s%s%s" % (names.label(base), side_word(o.side), what, order_line(o, base, quote), worth, tail)


def how_text(otype, side=""):
    if otype == "MARKET":
        return "바로 체결(시장가)"
    if otype in CONDITIONAL:
        return "%s 발동" % cond_word(otype, side)
    return "걸어 둔 주문 체결"


def cond_word(otype, side):
    """조건 주문의 이름: 파는 STOP_LOSS 는 '스탑 로스', 파는 TAKE_PROFIT 은 '익절 주문', 나머지(사는 조건 주문)는 '조건 주문'."""
    if side == "SELL" and otype.startswith("STOP_LOSS"):
        return "스탑 로스"
    if side == "SELL" and otype.startswith("TAKE_PROFIT"):
        return "익절 주문"
    return "조건 주문"


def _dust(f, q):
    """이 체결 기준으로 q 개가 없는 것과 같나 (0 이하 · 수수료 찌꺼기만 남음). 모르면(None) 아님."""
    return q is not None and (q <= 0 or bool(f.usd and q * f.avg < DUST_USD))


def sold_out(f, held_after):
    """판 체결 뒤 그 코인을 다 정리했나 (⬜ 모두 매도 — 주인장 수익률을 붙이는 때)."""
    return f.side == "SELL" and _dust(f, held_after)


def fill_text(f, held_after, pnl):
    """체결 글. held_after = 체결 뒤 그 코인 보유량(모르면 None), pnl = 팔 때 손익(모르면 None)."""
    label = names.label(f.base)
    price = f.avg
    px, amt, worth = price_text(price, f.quote), names.amount(f.qty, f.base), worth_text(f.quote_qty, f.quote)
    if f.side == "BUY":
        before = None if held_after is None else held_after - f.qty
        if before is None or _dust(f, before):
            title, line = "🟢 %s 현물 매수" % label, "%s에 %s · %s" % (px, amt, worth)
        else:
            title = "➕ %s 현물 추가 매수" % label
            line = "%s에 %s 더 · 모두 %s · %s" % (px, amt, names.amount(held_after, f.base), worth)
    else:
        all_gone = sold_out(f, held_after)
        title = ("⬜ %s 현물 모두 매도" if all_gone else "🟠 %s 현물 일부 매도" if held_after is not None else "🟠 %s 현물 매도") % label
        if pnl is not None:
            title += " · " + fmt.pnl(pnl)
        line = "%s에 %s 정리" % (px, amt)
        if held_after is not None and not all_gone:
            line += " · 남은 %s" % names.amount(held_after, f.base)
        line += " · " + worth
    line += " · " + how_text(f.otype, f.side)
    if f.partial and f.open_left:
        line += " · 나머지 %s는 아직 걸려 있음" % names.amount(f.open_left, f.base)
    if f.side == "SELL" and pnl is None and f.usd:
        line += " · 산 값을 몰라 손익은 뺌"
    return title + "\n" + line


# ---------------------------------------------------------------------- 평균 매수가
class CostBook:
    """코인 · 달러 짝 하나(예: BTCUSDT)의 평균 매수가 장부. 거래 기록을 오래된 것부터 넣어 이동 평균으로 이어 감.
    qty = 산 값을 아는 보유량, cost = 그만큼의 총 매수 금액(달러). 산 값을 모르는 몫까지 팔면 장부를 비움."""

    def __init__(self, last_id=-1, qty=ZERO, cost=ZERO):
        self.last_id, self.qty, self.cost = int(last_id), dec(qty), dec(cost)

    @classmethod
    def load(cls, d):
        return cls(d.get("last_id", -1), d.get("qty"), d.get("cost")) if d else cls()

    def dump(self):
        return {"last_id": self.last_id, "qty": str(self.qty), "cost": str(self.cost)}

    def apply(self, t, bnb_price=None):
        """myTrades 한 줄을 넣음."""
        base, quote = split_symbol(t.get("symbol", ""))
        q, c = dec(t.get("qty")), dec(t.get("quoteQty"))
        fee, fee_asset = dec(t.get("commission")), t.get("commissionAsset")
        fee_usd = fee if fee_asset == quote else (fee * dec(bnb_price) if fee_asset == "BNB" and bnb_price else ZERO)
        if t.get("isBuyer"):
            if fee_asset == base:
                q -= fee                                   # 수수료를 코인으로 냄 → 받은 코인이 그만큼 적음
            self.qty += q
            self.cost += c + fee_usd
        else:
            if fee_asset == base:
                q += fee
            if q >= self.qty:                              # 산 값을 아는 몫을 다 (또는 그보다 많이) 팖
                self.qty, self.cost = ZERO, ZERO
            else:
                self.cost -= self.cost * q / self.qty
                self.qty -= q
        self.last_id = max(self.last_id, int(t.get("id", self.last_id)))

    def avg(self):
        return self.cost / self.qty if self.qty > 0 else None

    def pnl(self, qty_sold, net_proceeds):
        """팔기 바로 전 장부로 본 손익. 산 값을 아는 수량보다 많이 팔면(입금 · 다른 짝으로 산 몫) None."""
        if self.qty <= 0 or qty_sold > self.qty * Decimal("1.001"):
            return None
        return net_proceeds - self.cost * min(qty_sold, self.qty) / self.qty
