"""매일 정해진 시각(기본 22시, 한국 시간)의 요약 글.

보여 주는 것(2026-10-21 댕이 결정): 거래 횟수 · 실현 손익 · 미실현 손익 · 수수료 · 펀딩비 · 들고 있는 포지션.
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


def build_text(start_ms, end_ms, fills, realized_rows, fee_rows, funding_rows, positions, bnb_price=None, gaps=()):
    orders = {r.get("key") for r in fills if r.get("kind") == "fill"}
    realized, _ = _sum_usd(realized_rows, bnb_price)
    fees, fee_conv = _sum_usd(fee_rows, bnb_price)
    funding, _ = _sum_usd(funding_rows, bnb_price)
    unreal = sum((dec(p.get("unRealizedProfit")) for p in positions), ZERO)
    s, e = fmt.kst(start_ms), fmt.kst(end_ms)
    lines = ["📊 %s 요약 (%s ~ %s)" % (e.strftime("%m/%d"), s.strftime("%m/%d %H:%M"), e.strftime("%m/%d %H:%M")),
             "거래 %d번" % len(orders) if orders else "거래 없음",
             "실현 손익 %s" % fmt.usd(realized, sign=True),
             "미실현 손익 %s" % fmt.usd(unreal, sign=True),
             "수수료 %s%s" % (fmt.usd(fees, sign=True), " (BNB로 낸 것은 지금 값으로 환산)" if fee_conv else ""),
             "펀딩비 %s" % fmt.usd(funding, sign=True)]
    if positions:
        lines.append("들고 있는 포지션")
        for p in sorted(positions, key=lambda r: r.get("symbol", "")):
            amt = dec(p.get("positionAmt"))
            side = "롱" if amt > 0 else "숏"
            if p.get("positionSide") in ("LONG", "SHORT"):
                side = "롱" if p["positionSide"] == "LONG" else "숏"
            notional = abs(dec(p.get("notional"))) or abs(amt) * dec(p.get("markPrice"))
            base = base_of(p.get("symbol", ""))
            lines.append("· %s %s %s · 평균 가격 %s · %s · 미실현 %s" % (
                names.label(base), side, names.amount(amt, base), fmt.dollar(dec(p.get("entryPrice"))),
                fmt.worth(notional), fmt.usd(dec(p.get("unRealizedProfit")), sign=True)))
    else:
        lines.append("들고 있는 포지션: 없음")
    if gaps:
        lines.append("※ 프로그램이 꺼져 있던 시간(%s)의 거래는 횟수에서 빠졌을 수 있음. 금액은 바이낸스 기록 기준이라 정확함." %
                     ", ".join("%s~%s" % (fmt.kst_text(a, False), fmt.kst_text(b, False)) for a, b in gaps))
    return "\n".join(lines)
