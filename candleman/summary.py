"""매일 정해진 시각(기본 22시, 한국 시간)의 요약 글.

보여 주는 것(2026-10-21 댕이 결정): 거래 횟수 · 실현 손익 · 미실현 손익 · 수수료 · 펀딩비 · 들고 있는 포지션.
현물도 같이 셈(10/21 댕이: "현물도 중요한 거래") — 현물 손익 · 수수료는 체결 때 이 프로그램이 셈해 둔 일지(spot_fill)로.
금액은 바이낸스 '손익 기록'(income) 으로 세서 프로그램이 잠깐 꺼져 있었어도 맞게 나옵니다.
거래 횟수만 이 프로그램의 일지로 세므로, 꺼져 있던 동안의 거래는 빠질 수 있어 그때는 한 줄로 알립니다.
"""
from datetime import datetime, timedelta

from . import fmt, names
from .fmt import KST, ZERO, dec
from .tracker import USD_ASSETS, base_of


def window_for(now_ms, hour):
    """지금 기준으로 가장 최근에 끝난 요약 구간 (전날 hour시 ~ 오늘 hour시)."""
    now = fmt.kst(now_ms)
    end = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if end > now:
        end -= timedelta(days=1)
    start = end - timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _sum_usd(rows, bnb_price):
    total, converted = ZERO, False
    for r in rows:
        v = dec(r.get("income"))
        a = r.get("asset") or "USDT"
        if a in USD_ASSETS:
            total += v
        elif a == "BNB" and bnb_price:
            total += v * dec(bnb_price)
            converted = True
    return total, converted


CARD_LONG, CARD_SHORT, CARD_REST = 2533018, 15684432, 9807270    # 카드 왼쪽 띠 색 — 캔들 사진의 초록 · 빨강, 나머지는 회색
CARD_SPOT = 15776011                                               # 현물 카드는 바이낸스 노랑
MAX_CARDS = 10                                                     # 디스코드 글 하나에 카드(embed)는 10장까지


def _basics(p):
    """positionRisk 한 줄 → (기호, 방향 +1/−1, 수량, 거래 크기)."""
    amt = dec(p.get("positionAmt"))
    d = 1 if amt > 0 else -1
    if p.get("positionSide") in ("LONG", "SHORT"):
        d = 1 if p["positionSide"] == "LONG" else -1
    notional = abs(dec(p.get("notional"))) or abs(amt) * dec(p.get("markPrice"))
    return base_of(p.get("symbol", "")), d, amt, notional


def position_lines(positions):
    """바이낸스 positionRisk 줄들 → '· 솔라나(SOL) 롱 12 SOL · 평균 가격 $150.25 · 약 $1,803어치(레버리지 포함) · 미실현 −$12.00'."""
    out = []
    for p in sorted(positions, key=lambda r: r.get("symbol", "")):
        base, d, amt, notional = _basics(p)
        out.append("· %s %s %s · 평균 가격 %s · %s · 미실현 %s" % (
            names.label(base), fmt.side_word(d), names.amount(amt, base), fmt.price2(p.get("entryPrice")),
            fmt.worth(notional), fmt.usd(dec(p.get("unRealizedProfit")), sign=True)))
    return out


def positions_message(positions, equity, when, spot=None):
    """/포지션 · #현재포지션 글: 머리 한 줄 + 종목마다 카드(embed) 한 장. 카드 안은 한 줄에 한 가지, 금액은 소수 둘째 자리까지.

    실제 레버리지 = 거래 크기 ÷ 증거금. 교차 마진은 계좌 전체 증거금(equity = totalMarginBalance)으로,
    격리 마진 포지션은 그 포지션에 넣은 증거금으로 나눔 — 바이낸스에 정해 둔 배율(예: 20배)이 아니라 실제로 쓰고 있는 배율.
    equity 를 모르면 레버리지 줄은 뺌. spot = 현물 보유(USDT 를 뺀 $10 이상, 큰 것부터) — 선물 카드 뒤에 노란 카드로."""
    spot = spot or []
    if not positions and not spot:
        return {"content": "📌 운영자는 지금 들고 있는 포지션이 없음 · %s 기준" % when, "embeds": []}
    equity = dec(equity)
    cards, total = [], ZERO
    for p in sorted(positions, key=lambda r: r.get("symbol", "")):
        base, d, amt, notional = _basics(p)
        total += notional
        iso = dec(p.get("isolatedMargin"))
        margin = iso if iso > 0 else equity
        lines = ["수량 **%s**" % names.amount(amt, base), "평균 가격 **%s**" % fmt.price2(p.get("entryPrice"))]
        if dec(p.get("markPrice")) > 0:
            lines.append("지금 가격 **%s**" % fmt.price2(p.get("markPrice")))
        lines.append("거래 크기 **%s**" % fmt.usd(notional))
        if margin > 0:
            lines.append("실제 레버리지 **%s**" % fmt.times(notional / margin))
        lines.append("미실현 손익 **%s**" % fmt.usd(dec(p.get("unRealizedProfit")), sign=True))
        cards.append({"title": "%s %s · %s" % ("🟢" if d > 0 else "🔴", names.label(base), fmt.side_word(d)),
                      "description": "\n".join(lines), "color": CARD_LONG if d > 0 else CARD_SHORT})
    head = "📌 **운영자 포지션** · %s 기준" % when
    if len(cards) > 1 and equity > 0:
        head += "\n전체 실제 레버리지 **%s**" % fmt.times(total / equity)
    if cards and equity > 0:                                     # 설명은 마지막 선물 카드에
        cards[-1]["footer"] = {"text": "실제 레버리지 = 거래 크기 ÷ 증거금"}
    for r in spot:
        avg = r.get("avg")
        lines = ["수량 **%s**" % names.amount(r["qty"], r["asset"])]
        if avg is not None:
            lines.append("평균 매수가 **%s**" % fmt.price2(avg))
        lines += ["지금 가격 **%s**" % fmt.price2(r["price"]), "평가 금액 **%s**" % fmt.usd(r["value"])]
        if avg is not None:
            lines.append("미실현 손익 **%s**" % fmt.usd((dec(r["price"]) - dec(avg)) * dec(r["qty"]), sign=True))
        cards.append({"title": "🪙 %s · 현물" % names.label(r["asset"]), "description": "\n".join(lines), "color": CARD_SPOT})
    if len(cards) > MAX_CARDS:
        rest = cards[MAX_CARDS - 1:]
        cards = cards[:MAX_CARDS - 1] + [{"title": "외 %d개" % len(rest), "color": CARD_REST,
                                           "description": "\n".join("· " + c["title"] for c in rest)}]
    return {"content": head, "embeds": cards}


def spot_lines(rows):
    """현물 보유 줄 — '· 비트코인(BTC) 현물 0.06 BTC · 평균 매수가 $82,000.00 · 약 $4,980어치 · 미실현 +$60.00'."""
    out = []
    for r in rows:
        avg = r.get("avg")
        s = "· %s 현물 %s" % (names.label(r["asset"]), names.amount(r["qty"], r["asset"]))
        if avg is not None:
            s += " · 평균 매수가 %s" % fmt.price2(avg)
        s += " · 약 %s어치" % fmt.usd_about(r["value"])
        if avg is not None:
            s += " · 미실현 %s" % fmt.usd((dec(r["price"]) - dec(avg)) * dec(r["qty"]), sign=True)
        out.append(s)
    return out


def _with_parts(total, parts):
    """'+$248.40 (선물 +$212.40 · 현물 +$36.00)' — 덧붙일 것이 없으면 합계만."""
    s = fmt.usd(total, sign=True)
    return s + (" (%s)" % " · ".join(parts) if parts else "")


def build_text(start_ms, end_ms, fills, realized_rows, fee_rows, funding_rows, positions, bnb_price=None, gaps=(),
               spot_fills=(), spot_rows=()):
    """하루 요약 글. 선물 금액은 바이낸스 손익 기록(income), 현물은 이 프로그램 일지(spot_fill: 체결 때 셈한 손익 · 달러 수수료)와
    지금 현물 보유(USDT 를 뺀 $10 이상 · spot_rows)로 셈. 현물이 있으면 합계 뒤에 (선물 · 현물)을 나눠 적음."""
    orders = {r.get("key") for r in fills if r.get("kind") == "fill"}
    spot = [r for r in spot_fills if r.get("kind") == "spot_fill"]
    spot_orders = {r.get("oid") for r in spot}
    sells = [r for r in spot if r.get("side") == "SELL" and r.get("usd", True)]
    spot_real = sum((dec(r["pnl"]) for r in sells if r.get("pnl") is not None), ZERO)
    spot_fee = -sum((dec(r["fee_usd"]) for r in spot if r.get("fee_usd") is not None), ZERO)     # 낸 돈이라 −
    known = [r for r in spot_rows if r.get("avg") is not None]
    spot_unreal = sum(((dec(r["price"]) - dec(r["avg"])) * dec(r["qty"]) for r in known), ZERO)
    realized, _ = _sum_usd(realized_rows, bnb_price)
    fees, fee_conv = _sum_usd(fee_rows, bnb_price)
    funding, _ = _sum_usd(funding_rows, bnb_price)
    unreal = sum((dec(p.get("unRealizedProfit")) for p in positions), ZERO)

    def both(fut, sp, show):
        return ["선물 %s" % fmt.usd(fut, sign=True), "현물 %s" % fmt.usd(sp, sign=True)] if show else []
    real_parts = both(realized, spot_real, bool(sells)) + (["현물 일부는 산 값을 몰라 뺌"] if any(r.get("pnl") is None for r in sells) else [])
    fee_parts = both(fees, spot_fee, bool(spot_fee)) + (["BNB로 낸 것은 지금 값으로 환산"] if fee_conv else [])
    n = len(orders) + len(spot_orders)
    s, e = fmt.kst(start_ms), fmt.kst(end_ms)
    lines = ["📊 %s 요약 (%s ~ %s)" % (e.strftime("%m/%d"), s.strftime("%m/%d %H:%M"), e.strftime("%m/%d %H:%M")),
             ("거래 %d번" % n + (" (선물 %d번 · 현물 %d번)" % (len(orders), len(spot_orders)) if spot_orders else "")) if n else "거래 없음",
             "실현 손익 " + _with_parts(realized + spot_real, real_parts),
             "미실현 손익 " + _with_parts(unreal + spot_unreal, both(unreal, spot_unreal, bool(known))),
             "수수료 " + _with_parts(fees + spot_fee, fee_parts),
             "펀딩비 %s" % fmt.usd(funding, sign=True)]
    if positions or spot_rows:
        lines.append("들고 있는 포지션")
        lines += position_lines(positions) + spot_lines(spot_rows)
    else:
        lines.append("들고 있는 포지션: 없음")
    if gaps:
        lines.append("※ 프로그램이 꺼져 있던 시간(%s)의 거래는 횟수와 현물 금액에서 빠졌을 수 있음. 선물 금액은 바이낸스 기록 기준이라 정확함." %
                     ", ".join("%s~%s" % (fmt.kst_text(a, False), fmt.kst_text(b, False)) for a, b in gaps))
    return "\n".join(lines)
