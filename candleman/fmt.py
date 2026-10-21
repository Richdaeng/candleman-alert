"""글자 모양을 만드는 작은 함수들. 계산은 Decimal 로 해서 0.1 + 0.2 같은 끝자리 오차가 글에 나오지 않게 합니다."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

KST = timezone(timedelta(hours=9))
ZERO = Decimal("0")
MINUS = "−"


def dec(x):
    """바이낸스가 주는 문자열 숫자를 Decimal 로. 비었거나 이상하면 0."""
    if x is None or x == "":
        return ZERO
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        return ZERO


def plain(d):
    """끝의 0 을 지운 숫자 글자 (지수 표기 없이)."""
    s = format(dec(d), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def price(d):
    """가격: 정수 부분에 쉼표, 소수 부분은 받은 그대로(끝의 0 만 지움)."""
    s = plain(d)
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    head, _, tail = s.partition(".")
    head = "{:,}".format(int(head or "0"))
    out = head + ("." + tail if tail else "")
    return (MINUS if neg else "") + out


def qty(d):
    return plain(abs(dec(d)))


def usd(d, sign=False):
    """달러 금액. sign=True 면 +/− 를 붙임. 0.01 보다 작은 금액은 넷째 자리까지."""
    v = dec(d)
    a = abs(v)
    places = Decimal("0.01")
    if ZERO < a < Decimal("0.01"):
        places = Decimal("0.0001")
    s = "{:,}".format(a.quantize(places, rounding=ROUND_HALF_UP))
    if sign:
        if v > 0:
            return "+$" + s
        if v < 0:
            return MINUS + "$" + s
        return "$" + s
    return ("" if v >= 0 else MINUS) + "$" + s


def dollar(d):
    """가격 앞에 $: '$82,000' · '$154.90' (소수가 있으면 둘째 자리까지는 채움)."""
    s = price(d)
    _, dot, tail = s.partition(".")
    if dot and len(tail) == 1:
        s += "0"
    return "$" + s


def price2(d):
    """포지션 카드 · 요약의 가격: $1 이상은 소수 둘째 자리까지('$152.83' · '$82,000.00'), $1 보다 작으면
    유효숫자 넷째 자리까지('$0.08765'). 바이낸스 평균 가격의 긴 소수(152.83045678)를 줄임."""
    v = dec(d)
    a = abs(v)
    if a >= 1 or a == 0:
        s = "{:,}".format(a.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    else:
        s = plain(a.quantize(Decimal(1).scaleb(-min(8, 3 - a.adjusted())), rounding=ROUND_HALF_UP))
    return (MINUS if v < 0 else "") + "$" + s


def times(x):
    """배율: '0.8배' · '1.26배' · '2배' (소수 둘째 자리까지, 끝의 0 은 지움)."""
    v = dec(x)
    if ZERO < v < Decimal("0.01"):
        return "0.01배 미만"
    return "%s배" % plain(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def usd_about(d):
    """'약 …어치'에 쓰는 금액. $100 이상은 센트를 뺌 ('$4,100'), 그보다 작으면 센트까지."""
    v = abs(dec(d))
    if v >= 100:
        return "${:,}".format(v.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return usd(v)


def worth(d):
    """포지션 크기: '약 $4,100어치(레버리지 포함)'. 레버리지까지 친 크기라 실제로 넣은 돈은 이보다 적음."""
    return "약 %s어치(레버리지 포함)" % usd_about(d)


def pnl(d):
    """손익을 말로: '이익 +$47.50' · '손실 −$40.20' · '손익 $0.00'."""
    v = dec(d)
    word = "이익" if v > 0 else ("손실" if v < 0 else "손익")
    return "%s %s" % (word, usd(v, sign=True))


def pct(d):
    """퍼센트: '+4.15%' · '−2.30%' · '0.00%' (소수 둘째 자리)."""
    v = dec(d).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return ("+" if v > 0 else MINUS if v < 0 else "") + "{:,}".format(abs(v)) + "%"


def side_hint(direction):
    """롱 · 숏을 처음 보는 사람을 위한 풀이."""
    return "오르면 이익" if direction > 0 else "내리면 이익"


def duration(seconds):
    s = int(max(0, seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return "%d일 %d시간" % (d, h) if h else "%d일" % d
    if h:
        return "%d시간 %d분" % (h, m) if m else "%d시간" % h
    if m:
        return "%d분" % m
    return "%d초" % s


def kst(ms):
    return datetime.fromtimestamp(ms / 1000.0, tz=KST)


def kst_text(ms, with_date=True):
    t = kst(ms)
    return t.strftime("%m/%d %H:%M") if with_date else t.strftime("%H:%M")


def side_word(direction):
    """direction: +1 롱, -1 숏."""
    return "롱" if direction > 0 else "숏"
