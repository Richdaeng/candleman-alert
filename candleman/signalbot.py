"""BTC 진입 · 정리 신호 — 운영자 한 사람에게만 봇 DM (2026-11-01 댕이 요청 · 11/4 6분할).

바이낸스 공개 시세(키 없음)의 일봉으로 판정해 카드로 올립니다. 주문은 내지 않습니다.
- 과감한 수익 · 6분할(11/4 댕이 "6분할로 디스코드로 신호를"): 자금을 6으로 나눠 분할마다 하루 한 번, 그 시각에 닫히는 일봉으로 판정.
  4시간마다(한국 3 · 7 · 11 · 15 · 19 · 23시) 한 분할씩 · 규칙이 켜지면 현물 롱, 꺼지면 선물 숏 1배 · 6분할 주문 봇(split-bot)과 같은 시각 · 같은 식.
- 안전 진입: 밤 11시(UTC 14시) 판정 하나. 규칙이 켜지고 종가가 100일선 위면 자금의 50%만 현물 롱, 아니면 현금.
규칙 = 종가가 50일선 위이거나 RSI(5) 과매도 상태(10 아래에서 켜지고 70 위에서 꺼짐). 판정은 일봉 마감 한 번 · 다음 봉 시가에 실행 · 장중 손절 없음.
글에는 방금 판정한 분할 하나만 적습니다(11/5 댕이 "그 분할에는 그 분할 시각에 맞는 알림만"): 그 분할의 자리와
다음 판정(내일 같은 시각)의 매수 · 매도 기준가(그 시각 종가가 얼마 위 · 아래면 자리가 바뀌는지)를 달러로(11/4 댕이 "매수 기준과 매도 기준을 달러 기준으로").
글은 머리 한 줄 + 카드 하나입니다(11/12 댕이 "매매일지봇도 카드 형식으로 깔끔하게") — 내용은 11/8 그대로라 카드 제목 · 꼬리말 ·
규칙 카드는 없습니다(11/8 댕이 "디스코드 판정 봇, 이렇게만 하자. 다른 건 너무 길다"). 카드 = 지금 자리 · 종가 · RSI(5) · 50일선 칸 · 다음 판정 기준가.
하루의 마지막 분할(밤 11시) 글에만 '하루 정리' 칸을 붙입니다: 과감한 수익 6분할의 롱 · 숏 수(그 분할 일봉의 50일선 · RSI(5)는 바로 위 칸)
(11/6 댕이 "하루의 마지막 분할(23시)에 초간단 버전으로만" · 11/7 "안전 진입은 빼버리고 과감한 수익 6분할에 50일선, 현재 RSI까지만").
6분할 글에는 안전 진입을 적지 않고(놓친 판정 · 알림에도 안 씀), 규칙 카드(시험 값)도 안 붙입니다. 안전 진입 계산은 옛 한 판정 글(compose)에만 남아 있습니다.
기록 시작(10/31) 뒤 기록은 시세로 그때그때 다시 계산합니다(폰이 꺼졌던 날이 있어도 틀어지지 않게).
신호가 난 판정은 왜 났는가(경로)별 적중 확률을 같이 적습니다. 값은 백테스트 표를 고정해 둔 것입니다(폰에서 다시 재지 않음).
자리가 바뀐 판정만 운영자를 태그해서 폰 알림이 갑니다(ring). 바뀌지 않은 판정의 글은 태그도 알림도 없이 보냅니다.
"""
import gzip
import json
import urllib.request
from datetime import datetime, timezone

from . import cards, fmt

KLINES = "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1d&timeZone=%d&limit=1000"
URL = KLINES % 10                          # 밤 11시 마감 일봉
DAY = 86_400_000
HOUR = 3_600_000
CLOSE_AT = 14 * HOUR                       # 일봉 마감 = UTC 14시 = 밤 11시
HOURS = (2, 6, 10, 14, 18, 22)             # 6분할 판정 시각(UTC) = 한국 11 · 15 · 19 · 23 · 3 · 7시 (split-bot 과 같음)
SAFE_H = 14                                # 밤 11시 분할(하루의 마지막 분할) — 하루 정리 줄 · 적중 표의 지난 건수는 이 분할 글에만
WAIT = 30_000                              # 마감 뒤 이만큼 기다렸다가 받음
RETRY = 120_000                            # 못 받으면 이만큼 뒤 다시
LATE = 10 * 60_000                         # 마감 뒤 이만큼 넘게 지나서 보내면 글에 적음
RECORD_FROM = int(datetime(2026, 10, 31, 14, tzinfo=timezone.utc).timestamp() * 1000)    # 기록 시작 = 이 판정부터
WARMUP = 300 * DAY                         # 기록 시작 앞으로 이만큼은 받아 둠 (이평 · RSI 상태)
FEE = 0.001                                # 편도 0.1% (기록에만 씀 · 숏 펀딩은 뺌)
RSI_N, RSI_LOW, RSI_HIGH = 5, 10, 70
SAFE_SIZE = 0.5
PURPLE = 10181046
GREEN = cards.GREEN
PANEL = cards.BLUE
# 시험 값(2019-01 ~ 2026-10 · 판정 시각 24개의 가운데 값 · 왕복 0.2% · 숏 펀딩 포함 · 낙폭은 일봉 저가 · 고가까지 · 사후 값)
SAFE = {"title": "🛡️ 안전 진입", "tag": "낙폭 우선", "color": GREEN, "total": "+989%", "span": "2019-01 ~ 2026-10 시험 · 판정 시각 24개의 가운데 값 · 사후 값",
        "n": 58, "win": 17, "per": "+6.22%", "mdd": "−28.5%", "idle": "1,512일"}
BOLD = {"title": "🚀 과감한 수익", "tag": "수익 우선", "color": PURPLE, "total": "+22,733%", "span": "2019-01 ~ 2026-10 시험 · 판정 시각 24개의 가운데 값 · 사후 값",
        "n": 169, "win": 50, "per": "+5.55%", "mdd": "−57.5%", "idle": "0일"}
# 6분할 규칙 카드(시험 값 +23,593% 등)는 11/6 댕이 요청으로 글에서 뺌 — 값은 btc-backtest/six_plan.md 에 있음.
# 신호별 적중(2017-11 ~ 2026-10 닫힌 신호): 경로 이름 → (적중 확률, 이긴 평균, 진 평균, 지난 건수). 확률 = 지난 승률을 건수가 적을수록 전체 쪽으로 당긴 값 · 평균 = 판정 시각 24개의 가운데 값 · 건수 = 밤 11시 판정
HIT = {"50일선 돌파": (.209, "+54.4%", "−3.6%", 90), "RSI 되돌림": (.417, "+12.1%", "−8.5%", 16), "되돌림 끝남": (.359, "+6.6%", "−5.3%", 12), "50일선 이탈": (.256, "+14.5%", "−3.1%", 94),
       "규칙 켜짐": (.227, "+41.4%", "−1.4%", 29), "100일선 돌파": (.317, "+17.5%", "−1.9%", 39), "규칙 꺼짐": (.249, "+23.1%", "−3.1%", 51), "100일선 이탈": (.324, "+19.7%", "−2.7%", 16)}
FOOTER = ("투자 권유 아님 · 주문 안 냄 · 시험 값은 규칙을 고른 구간의 사후 값이라 기대치가 아님\n"
          "적중 = 이 신호로 잡은 자리가 다음 신호에서 비용 뒤 플러스로 닫힘(안전 정리는 판 값보다 싸게 다시 삼) · 승률은 아무 날 진입이 더 높음(과감 52% · 안전 57%), 돈은 이긴 건의 크기에서 나옴\n"
          "2017-11부터 재면 낙폭 안전 −36.5% · 과감 −80.3% · 과감은 밤 11시 판정으로 2018년에 21연패 −70.5%\n"
          "2026년은 판정 시각 24개의 가운데 값이 안전 +6.2% · 과감 +29.7% · 실제 봇(밤 11시 판정)은 +0.7% · −21.3%")


def _get(url):
    """압축해서 받음 — 6분할은 4시간마다 주소 6개라 폰 데이터를 아낌(일봉 1000개 187KB → 65KB)."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 candleman-alert", "Accept": "application/json", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return body.decode("utf-8")


def daily(fetch=_get, url=None):
    """일봉들 (오래된 것부터). 기록 시작 앞의 WARMUP 까지 모자라면 앞쪽을 더 받음. url 을 안 주면 밤 11시 마감 일봉."""
    url = url or URL
    rows = json.loads(fetch(url))
    while rows and int(rows[0][0]) > RECORD_FROM - WARMUP:
        page = json.loads(fetch(url + "&endTime=%d" % (int(rows[0][0]) - 1)))
        if not page:
            break
        rows = page + rows
    return rows


def tz_of(h):
    """UTC h시에 닫히는 일봉의 timeZone 값(−12 ~ +11). 14시 → 10 (split-bot rule.tz_of 와 같음)."""
    return (12 - h) % 24 - 12


def closed_end(n, at=None):
    """지금(ms) 기준 마지막으로 닫힌 일봉의 마감 시각(ms). at = 하루 중 마감 시각(ms) · 안 주면 밤 11시."""
    at = CLOSE_AT if at is None else at
    return (n - at) // DAY * DAY + at


def last_end(n):
    """6분할 중 가장 최근에 닫힌 판정의 마감 시각(ms) — 4시간마다 바뀜."""
    return max(closed_end(n, h * HOUR) for h in HOURS)


def sma(c, n):
    out, s = [None] * len(c), 0.0
    for i, x in enumerate(c):
        s += x
        if i >= n:
            s -= c[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def rsi_of(au, ad):
    return 100 - 100 / (1 + au / ad) if ad > 0 else 100.0


def judge(c):
    """종가들 → 날마다의 판정. on · safe · bold[i] = i일 종가 판정(i + 1일 시가부터) · ups50 · ups100[i] = i일 종가가 그 선 위. 끝의 값들은 다음 판정 계산에 씀."""
    ma50, ma100 = sma(c, 50), sma(c, 100)
    au = ad = 0.0
    hold = False
    on, safe, bold, ups50, ups100 = [], [], [], [], []
    r = None
    for i, x in enumerate(c):
        d = x - c[i - 1] if i else 0.0
        u, v = max(d, 0.0), max(-d, 0.0)
        if i:
            au += (u - au) / RSI_N
            ad += (v - ad) / RSI_N
        else:
            au, ad = u, v
        if i >= RSI_N - 1:
            r = rsi_of(au, ad)
            if r < RSI_LOW:
                hold = True
            elif r > RSI_HIGH:
                hold = False
        up50 = ma50[i] is not None and x > ma50[i]
        up100 = ma100[i] is not None and x > ma100[i]
        on.append(up50 or hold)
        safe.append(SAFE_SIZE if (up50 or hold) and up100 else 0.0)
        bold.append(1.0 if up50 or hold else -1.0)
        ups50.append(up50)
        ups100.append(up100)
    return {"on": on, "safe": safe, "bold": bold, "ups50": ups50, "ups100": ups100, "up50": up50, "up100": up100, "hold": hold, "rsi": r, "au": au, "ad": ad, "ma50": ma50[-1], "ma100": ma100[-1],
            "sum49": sum(c[-49:]), "sum99": sum(c[-99:])}


def after(c, J, x):
    """다음 판정 종가가 x 일 때 (규칙 켜짐, 안전 켜짐)."""
    au = J["au"] + (max(x - c[-1], 0.0) - J["au"]) / RSI_N
    ad = J["ad"] + (max(c[-1] - x, 0.0) - J["ad"]) / RSI_N
    r = rsi_of(au, ad)
    hold = True if r < RSI_LOW else False if r > RSI_HIGH else J["hold"]
    on = x > (J["sum49"] + x) / 50 or hold
    return on, on and x > (J["sum99"] + x) / 100


def why(J, key, i):
    """i일 종가에 난 신호가 왜 났는가 → HIT 의 경로 이름."""
    if key == "bold":
        if J["bold"][i] > 0:
            return "50일선 돌파" if J["ups50"][i] else "RSI 되돌림"
        return "50일선 이탈" if J["ups50"][i - 1] else "되돌림 끝남"
    if J["safe"][i]:
        return "규칙 켜짐" if J["ups100"][i - 1] else "100일선 돌파"
    return "100일선 이탈" if J["on"][i] else "규칙 꺼짐"


def coming(J, k, x, on):
    """다음 판정 종가가 x 일 때 날 신호의 경로 이름 (자리가 그대로면 None). on = after() 값. k = 0 과감 · 1 안전."""
    if k == 0:
        if on[0] == (J["bold"][-1] > 0):
            return None
        if on[0]:
            return "50일선 돌파" if x > (J["sum49"] + x) / 50 else "RSI 되돌림"
        return "50일선 이탈" if J["up50"] else "되돌림 끝남"
    if on[1] == bool(J["safe"][-1]):
        return None
    if on[1]:
        return "규칙 켜짐" if J["up100"] else "100일선 돌파"
    return "100일선 이탈" if on[0] else "규칙 꺼짐"


def bands(c, J, k):
    """다음 판정 종가별 자리 → [[아래 끝, 위 끝, 켜짐, 신호 경로]] (지금 종가 ±30% 를 0.05% 간격으로 봄). k = 0 과감 · 1 안전."""
    out = []
    for j in range(-600, 601):
        x = c[-1] * (1 + j / 2000)
        on = after(c, J, x)
        state = [on[k], coming(J, k, x, on)]
        if out and out[-1][2:] == state:
            out[-1][1] = x
        else:
            out.append([x, x] + state)
    return out


def edges(c, J, k):
    """bands() 의 경계를 1센트 안으로 좁힘 — 기준가를 달러로 적으려고(0.05% 간격이면 7만 달러에서 35달러씩 틀림). 경계 = 위 구간이 시작하는 값."""
    rs = bands(c, J, k)
    for a, b in zip(rs, rs[1:]):
        lo, hi = a[1], b[0]
        while hi - lo > 0.01:
            mid = (lo + hi) / 2
            on = after(c, J, mid)
            if [on[k], coming(J, k, mid, on)] == a[2:]:
                lo = mid
            else:
                hi = mid
        a[1] = b[0] = hi
    return rs


def run_start(pos, i):
    while i > 0 and pos[i - 1] == pos[i]:
        i -= 1
    return i


def turn(pos, o, c, ends):
    """이번 구간: 비중 · 방금 바뀌었나 · 진입 시각 · 진입가 · 지금 손익(자금 대비 · 수수료 · 펀딩 전) · 방금 끝난 구간의 손익."""
    i = len(c) - 1
    s = run_start(pos, i)
    entry = o[s + 1] if s < i else c[i]
    out = {"w": pos[i], "fresh": i > 0 and s == i, "from": ends[s], "entry": entry, "gain": pos[i] * (c[i] / entry - 1), "prev": None, "closed": None}
    if out["fresh"]:
        p = run_start(pos, i - 1)
        out["prev"], out["closed"] = pos[i - 1], pos[i - 1] * (c[i] / o[p + 1] - 1)
    return out


def record(pos, c, i0):
    """시작 뒤 기록: 닫힌 구간 수 · 이긴 수 · 누적 (왕복 0.2% · 숏 펀딩 뺌 · 다음 봉 시가를 그날 종가로 봄)."""
    w, px = pos[i0], c[i0]
    eq, n, win = 1 - FEE * abs(w), 0, 0
    for i in range(i0 + 1, len(c)):
        if pos[i] == w:
            continue
        r = w * (c[i] / px - 1)
        if w:
            n += 1
            win += r > 2 * FEE * abs(w)
        eq *= (1 + r) * (1 - FEE * abs(pos[i] - w))
        w, px = pos[i], c[i]
    return {"n": n, "win": win, "ret": eq * (1 + w * (c[-1] / px - 1)) - 1}


def read(rows, n, exact=False):
    """받은 일봉 → 지금 상태. 닫힌 봉만 씀. exact 면 다음 판정 구간의 경계를 1센트 안으로(edges). why = 지금 자리를 연 신호의 경로(방금 난 신호면 그 신호)."""
    bars = [r for r in rows if int(r[6]) < n]
    o = [float(r[1]) for r in bars]
    c = [float(r[4]) for r in bars]
    ends = [int(r[6]) + 1 for r in bars]
    J = judge(c)
    st = {"end": ends[-1], "next_end": ends[-1] + DAY, "close": c[-1], "ma50": J["ma50"], "ma100": J["ma100"], "rsi": J["rsi"], "up50": J["up50"], "up100": J["up100"], "hold": J["hold"], "on": J["on"][-1]}
    i0 = next((i for i, e in enumerate(ends) if e >= RECORD_FROM), None)
    for key, k in (("bold", 0), ("safe", 1)):
        st[key] = turn(J[key], o, c, ends)
        st[key]["bands"] = (edges if exact else bands)(c, J, k)
        s = run_start(J[key], len(c) - 1)
        st[key]["why"] = why(J, key, s) if s else None
        st[key]["since"] = record(J[key], c, i0) if i0 is not None else {"n": 0, "win": 0, "ret": 0.0}
    return st


def read6(n, fetch=_get):
    """6분할: 판정 시각마다 그 시각에 닫히는 일봉으로 read() → {UTC 시: 상태}. 일봉 시작 시각이 어긋난 자료면 ValueError (그 시각 판정이 틀어지니 안 씀)."""
    out = {}
    for h in HOURS:
        rows = daily(fetch, KLINES % tz_of(h))
        if any((int(r[0]) - h * HOUR) % DAY for r in rows) or any(int(b[0]) - int(a[0]) != DAY for a, b in zip(rows, rows[1:])):
            raise ValueError("%d시 일봉의 시작 시각이 어긋남" % h)
        out[h] = read(rows, n, exact=True)
    return out


def _usd(x):
    return "$" + format(round(x), ",")


def pct1(x):
    """0.031 → '+3.1%' · 0 → '0.0%'."""
    v = round(x * 100, 1)
    return ("+" if v > 0 else fmt.MINUS if v < 0 else "") + "%.1f%%" % abs(v)


def odds(name):
    return "적중 %d%%" % round(HIT[name][0] * 100)


def band_text(rs, on_word, off_word):
    """[[아래, 위, 켜짐, 경로]] → '$70,000 아래 = 현금 · $70,000 위 = 자금 50% 현물 롱 (적중 23%)'. 신호가 나는 구간에만 적중을 붙임."""
    word = lambda r: (on_word if r[2] else off_word) + (" (%s)" % odds(r[3]) if len(r) > 3 and r[3] else "")
    if len(rs) == 1:
        return "지금 종가 ±30%% 안에서는 %s" % word(rs[0])
    out = []
    for i, r in enumerate(rs):
        if i == 0:
            out.append("%s 아래 = %s" % (_usd(rs[1][0]), word(r)))
        elif i == len(rs) - 1:
            out.append("%s 위 = %s" % (_usd(r[0]), word(r)))
        else:
            out.append("%s ~ %s = %s" % (_usd(r[0]), _usd(rs[i + 1][0]), word(r)))
    return " · ".join(out)


def rule_card(x, since):
    """규칙 카드: 왼쪽 색 띠 · 위에 작은 태그 · 큰 숫자 · 건수 · 적중 · 건당 · 낙폭 · 쉰 날(6분할은 전환 수) · 시작 뒤 기록."""
    field = lambda name, value: {"name": name, "value": value, "inline": True}
    six = "turns" in x
    return {"author": {"name": x["tag"]}, "title": x["title"], "color": x["color"], "description": "## %s\n%s" % (x["total"], x["span"]),
            "fields": [field("분할마다 건수" if six else "건수", "{:,}건".format(x["n"])), field("적중", "%d건 (%d%%)" % (x["win"], round(x["win"] / x["n"] * 100))),
                       field("건당 평균", x["per"]), field("최대 낙폭", x["mdd"]), field("전환", x["turns"]) if six else field("쉰 날", x["idle"]), field("시작 뒤 기록", since)]}


def since_text(s):
    return "%d건 · %d승 · %s" % (s["n"], s["win"], pct1(s["ret"]))


def _when(ms):
    t = fmt.kst(ms)
    return "%d/%d %02d:%02d" % (t.month, t.day, t.hour, t.minute)


def chance(name, count=True):
    """신호 밑에 붙는 적중 확률 줄. count = 지난 건수(밤 11시 판정의 수)를 붙일지 — 6분할의 다른 시각엔 안 붙임."""
    p, up, down, n = HIT[name]
    return "🎯 적중 확률 **%d%%** · %s · 이기면 평균 %s · 지면 평균 %s" % (round(p * 100), name, up, down) + (" · 지난 %d건" % n if count else "")


def safe_line(st):
    x = st["safe"]
    if x["fresh"] and x["w"]:
        return "🟢 **진입 신호** · 자금 50%% 현물 롱 (다음 봉 시가 · 지금)\n%s" % chance(x["why"])
    if x["fresh"]:
        return "🔴 **정리 신호** · 현물 전부 정리 → 현금 · 이번 구간 %s\n%s" % (pct1(x["closed"]), chance(x["why"]))
    if x["w"]:
        return "📌 유지 · 자금 50%% 현물 롱 · %s 진입 · 자금 대비 %s" % (_when(x["from"]), pct1(x["gain"]))
    off = "규칙은 켜졌지만 종가가 100일선 아래" if st["on"] else "규칙 꺼짐 (종가가 50일선 아래)"
    return "💤 오늘은 쉼 · 현금 · %s" % off


def bold_line(st):
    x = st["bold"]
    if x["fresh"] and x["w"] > 0:
        return "🟢 **진입 신호** · 선물 숏 정리 → 현물 롱 (다음 봉 시가 · 지금) · 이번 숏 구간 %s\n%s" % (pct1(x["closed"]), chance(x["why"]))
    if x["fresh"]:
        return "🔴 **정리 신호** · 현물 정리 → 선물 숏 1배 (다음 봉 시가 · 지금) · 이번 롱 구간 %s\n%s" % (pct1(x["closed"]), chance(x["why"]))
    return "📌 유지 · %s · %s 진입 · %s" % ("현물 롱" if x["w"] > 0 else "선물 숏 1배", _when(x["from"]), pct1(x["gain"]))


def head(st):
    """머리 한 줄 (굵은 글씨 없음) · 울릴지."""
    words = []
    if st["safe"]["fresh"]:
        words.append("🛡️ 안전 %s %s" % ("진입" if st["safe"]["w"] else "정리", odds(st["safe"]["why"])))
    if st["bold"]["fresh"]:
        words.append("🚀 과감 %s %s" % ("현물 롱 전환" if st["bold"]["w"] > 0 else "선물 숏 전환", odds(st["bold"]["why"])))
    if words:
        return "🔔 BTC 신호 · " + " · ".join(words), True
    return "📡 BTC 판정 · 오늘은 자리 그대로", False


def shade(st):
    """봇의 선택 카드 색: 진입 신호 초록 · 정리 신호만 빨강 · 신호 없는 날 파랑."""
    now = [x for x in (st["safe"], st["bold"]) if x["fresh"]]
    if not now:
        return PANEL
    return GREEN if any(x["w"] > 0 for x in now) else cards.RED


def compose(st):
    """디스코드 글: 머리 한 줄 + 규칙 카드 둘 + 봇의 선택. {"content", "embeds", "ring"}."""
    text, ring = head(st)
    side = lambda x, up: "%s %s" % (_usd(x), "위" if up else "아래")
    field = lambda name, value: {"name": name, "value": value, "inline": False}
    fields = [field(SAFE["title"], safe_line(st)), field(BOLD["title"], bold_line(st)),
              field("📊 마지막 판정", "종가 **%s** · RSI(5) %.1f%s\n50일선 %s · 100일선 %s" % (_usd(st["close"]), st["rsi"], " · RSI 되돌림 켜짐" if st["hold"] else "", side(st["ma50"], st["up50"]), side(st["ma100"], st["up100"]))),
              field("🔮 %s 판정 · 종가에 따라" % _when(st["next_end"]), "🛡️ %s\n🚀 %s" % (band_text(st["safe"]["bands"], "자금 50% 현물 롱", "현금"), band_text(st["bold"]["bands"], "현물 롱", "선물 숏 1배")))]
    panel = {"title": "🤖 봇의 선택 · %s 마감 → %s" % (_when(st["end"]), _when(st["next_end"])), "color": shade(st), "fields": fields, "footer": {"text": FOOTER}}
    return {"content": text, "embeds": [rule_card(SAFE, since_text(st["safe"]["since"])), rule_card(BOLD, since_text(st["bold"]["since"])), panel], "ring": ring}


def label(h):
    """UTC h시 판정 분할의 이름 = 한국 시각 (22 → '07시 분할')."""
    return "%02d시 분할" % ((h + 9) % 24)


def side_word(w):
    return "현물 롱" if w > 0 else "선물 숏 1배"


def _ago(ms):
    m = ms // 60_000
    return "%d시간 %d분" % divmod(m, 60) if m >= 60 else "%d분" % m


def _zones(rs, c):
    """다음 판정 종가별 구간(edges) → [(지금 종가 c 와의 거리, 롱인지, 구간 글)] 가까운 기준부터.
    위아래가 다 막힌 매도 구간은 50일선 쪽 끝을 기준으로 적고, 다른 끝 너머는 '단 … 그대로'."""
    zones = []
    for i, r in enumerate(rs):
        if not r[3]:
            continue
        lo = r[0] if i else None
        hi = rs[i + 1][0] if i + 1 < len(rs) else None
        tag = "(%s · %s)" % (r[3], odds(r[3]))
        if lo is None and hi is None:
            where = "어디든 " + tag
        elif lo is None:
            where = "**%s** 아래 %s" % (_usd(hi), tag)
        elif hi is None:
            where = "**%s** 위 %s" % (_usd(lo), tag)
        elif r[3] == "50일선 이탈":                # 더 떨어지면 RSI(5)가 10 아래라 규칙이 켜진 채
            where = "**%s** 아래 %s · 단 %s 아래면 RSI 되돌림으로 그대로" % (_usd(hi), tag, _usd(lo))
        elif r[3] == "되돌림 끝남":                # 더 오르면 50일선 위라 켜진 채
            where = "**%s** 위 %s · 단 %s 위면 50일선 위라 그대로" % (_usd(lo), tag, _usd(hi))
        else:
            where = "**%s ~ %s** 사이 %s" % (_usd(lo), _usd(hi), tag)
        gap = max(lo - c if lo is not None else 0, c - hi if hi is not None else 0, 0)
        zones.append((gap, r[2], where))
    zones.sort(key=lambda z: z[0])
    return zones


def criteria(rs, c, when, buy="🟢 매수", sell="🔴 매도"):
    """매수 · 매도 기준 한 줄 ('🔴 매도 · 11/5 11:00 종가 **$68,512** 아래 (50일선 이탈 · 적중 26%)') · 지금 종가 c 에 가까운 기준부터."""
    zones = _zones(rs, c)
    if not zones:
        return "📌 %s 종가가 지금 ±30%% 안이면 그대로" % when
    return "%s · %s 종가 %s" % (buy if zones[0][1] else sell, when, " · 또는 ".join(z[2] for z in zones))


def criteria_lines(rs, c, when, buy="🟢 매수", sell="🔴 매도"):
    """카드 칸에 넣는 기준가(11/12 댕이 "카드 형식으로 깔끔하게") — 첫 줄 '🔴 매도 · 11/5 11:00 종가' · 기준마다 한 줄 ·
    '단 … 그대로'는 그 아래 줄에. 값은 criteria 한 줄과 같음."""
    zones = _zones(rs, c)
    if not zones:
        return "📌 %s 종가가 지금 ±30%% 안이면 그대로" % when
    out = ["%s · %s 종가" % (buy if zones[0][1] else sell, when)]
    for z in zones:
        main, _, but = z[2].partition(" · 단 ")
        out.append("· " + main)
        if but:
            out.append("(단 %s)" % but)
    return "\n".join(out)


def now_text(h, st):
    """방금 판정한 분할 → 카드 설명: 신호(적중 확률) 또는 그대로(자리 · 진입 · 진입가 · 손익은 자금 대비 · 수수료 · 펀딩 전).
    지난 건수는 밤 11시 판정의 수라 그 분할에만 붙임. 종가 · RSI · 50일선은 카드의 나란한 칸 셋(stat_fields)."""
    x = st["bold"]
    if x["fresh"] and x["w"] > 0:
        return "🟢 **매수 신호** · 선물 숏 1배 정리 → 현물 롱 (다음 봉 시가 · 지금)\n이번 숏 구간 **%s**\n%s" % (pct1(x["closed"]), chance(x["why"], h == SAFE_H))
    if x["fresh"]:
        return "🔴 **매도 신호** · 현물 정리 → 선물 숏 1배 (다음 봉 시가 · 지금)\n이번 롱 구간 **%s**\n%s" % (pct1(x["closed"]), chance(x["why"], h == SAFE_H))
    return "📌 그대로 · **%s**\n%s 진입 **%s** · **%s**" % (side_word(x["w"]), _when(x["from"]), _usd(x["entry"]), pct1(x["gain"]))


def stat_fields(st):
    """종가 · RSI(5) · 50일선 — 카드 안에 나란한 칸 셋."""
    def f(name, value):
        return {"name": name, "value": value, "inline": True}
    return [f("종가", "**%s**" % _usd(st["close"])), f("RSI(5)", "%.1f%s" % (st["rsi"], " · 되돌림 켜짐" if st["hold"] else "")),
            f("50일선", "%s %s" % (_usd(st["ma50"]), "위" if st["up50"] else "아래"))]


def moved_line(h, st):
    """지난 글 뒤(폰이 꺼졌던 사이)에 자리가 바뀐 분할 한 줄."""
    x = st["bold"]
    return "%s %s · %s 판정에 %s → %s%s · 지금 %s" % ("🟢" if x["w"] > 0 else "🔴", label(h), _when(x["from"]), "매수" if x["w"] > 0 else "매도", side_word(x["w"]),
                                                  " (%s · %s)" % (x["why"], odds(x["why"])) if x["why"] else "", pct1(x["gain"]))


def day_line(sts):
    """하루의 마지막 분할(밤 11시) 글에만 붙는 한 줄: 과감한 수익 6분할의 롱 · 숏 수
    (11/7 댕이 "안전 진입은 빼버리고 과감한 수익 6분할에 50일선, 현재 RSI까지만").
    50일선 · RSI(5)는 같은 분할(밤 11시)의 값이라 카드의 나란한 칸(stat_fields)에 이미 있어 여기선 뺌(11/12 카드)."""
    longs = sum(sts[k]["bold"]["w"] > 0 for k in HOURS)
    return "🚀 과감한 수익 · 6분할 롱 %d · 숏 %d" % (longs, len(HOURS) - longs)


def compose6(sts, n=None, last=None):
    """6분할 디스코드 글 {"content", "embeds", "ring"}: 방금 판정한 분할 하나만(11/5 댕이 "그 분할에는 그 분할 시각에 맞는 알림만").
    머리 한 줄 + 카드 하나(11/12 댕이 "매매일지봇도 카드 형식으로 깔끔하게") · 내용은 11/8 그대로 — 카드 제목 · 꼬리말 · 규칙 카드 없음
    (11/8 댕이 "디스코드 판정 봇, 이렇게만 하자. 다른 건 너무 길다"):
        ⏱️ 19시 분할 · 방금 판정                         ← 머리 줄(폰 알림에 보이는 줄 · 굵게 안 함)
        ┃ 📌 그대로 · **선물 숏 1배**                    ← 카드 설명 = 지금 자리(now_text)
        ┃ 10/23 19:00 진입 **$80,000** · **+6.3%**
        ┃ 종가 · RSI(5) · 50일선                         ← 나란한 칸 셋(stat_fields)
        ┃ 🔮 다음 판정 기준가                            ← 칸(criteria_lines)
        ┃ 🟢 매수 · 11/9 19:00 종가
        ┃ · **$82,000** 위 (50일선 돌파 · 적중 21%)
        ┃ · **$65,000** 아래 (RSI 되돌림 · 적중 42%)
    (숫자는 예시 · 카드 띠는 판정 뒤 자리가 롱이면 초록 · 숏이면 빨강)
    신호가 난 판정은 머리 줄 끝에 '· 🟢 매수 신호'(또는 '· 🔴 매도 신호') · 카드 설명이 신호와 적중 확률이 됨. 가끔만 붙는 것:
    - 마감 뒤 한참 지나 보내면 머리 줄 끝에 '⏰ 마감 … 뒤에 보냄'(n = 보내는 때).
    - last(마지막으로 보낸 글의 판정 마감 시각) 뒤에 자리가 바뀐 다른 분할 = 폰이 꺼졌던 사이 놓친 판정 → 머리 줄에 '🔔 놓친 판정 N건' ·
      카드에 '놓친 판정' 칸(분할마다 한 줄) · 울림(자리가 그대로인 분할은 안 적음).
    - 밤 11시(하루의 마지막 분할) 글에만 하루 정리 칸(6분할 롱 · 숏 수 · 11/7 댕이). 안전 진입 · 규칙 카드는 안 씀(11/6 · 11/7 댕이).
    울리는 글(ring)은 보낼 때 dm_owner 가 머리 줄 끝에 운영자를 태그함."""
    h = max(HOURS, key=lambda k: sts[k]["end"])
    st = sts[h]
    x = st["bold"]
    moved = [m for m in HOURS if m != h and last is not None and sts[m]["bold"]["from"] > last]
    top = "⏱️ %s · 방금 판정" % label(h)
    if x["fresh"]:
        top += " · 🟢 매수 신호" if x["w"] > 0 else " · 🔴 매도 신호"
    if moved:
        top += " · 🔔 놓친 판정 %d건" % len(moved)
    if n is not None and n - st["end"] > WAIT + LATE:
        top += " · ⏰ 마감 %s 뒤에 보냄" % _ago(n - st["end"])
    fields = stat_fields(st) + [{"name": "🔮 다음 판정 기준가", "value": criteria_lines(x["bands"], st["close"], _when(st["next_end"]))[:1024], "inline": False}]
    if moved:
        fields.append({"name": "🔔 놓친 판정 · %s 글 뒤" % _when(last), "value": "\n".join(moved_line(m, sts[m]) for m in moved)[:1024], "inline": False})
    if h == SAFE_H:
        fields.append({"name": "🌙 하루 정리", "value": day_line(sts), "inline": False})
    card = {"color": GREEN if x["w"] > 0 else cards.RED, "description": now_text(h, st), "fields": fields}
    return {"content": top, "embeds": [card], "ring": bool(x["fresh"] or moved)}


def build(n, fetch=_get, last=None, strict=True):
    """지금 판정 글(6분할)을 만듦. strict 면 분할마다 이번 마감 봉이 다 와야 함 — 아니면 ValueError (조금 뒤 다시).
    last = 마지막으로 보낸 글의 판정 마감 시각(compose6)."""
    sts = read6(n, fetch)
    if strict and any(sts[h]["end"] != closed_end(n, h * HOUR) for h in HOURS):
        raise ValueError("이번 마감 봉이 아직 없음")
    return compose6(sts, n, last)
