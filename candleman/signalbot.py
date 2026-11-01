"""BTC 진입 · 청산 신호 — #봇-시그널 (2026-11-01 댕이 요청).

바이낸스 공개 시세(키 없음)의 밤 11시(UTC 14시) 마감 일봉으로 두 규칙을 판정해 카드로 올립니다. 주문은 내지 않습니다.
- 과감한 수익: 규칙이 켜지면 현물 롱, 꺼지면 선물 숏 1배.
- 안전 진입: 규칙이 켜지고 종가가 100일선 위면 자금의 50%만 현물 롱, 아니면 현금.
규칙 = 종가가 50일선 위이거나 RSI(5) 과매도 상태(10 아래에서 켜지고 70 위에서 꺼짐). 판정은 일봉 마감 한 번 · 다음 봉 시가에 실행 · 장중 손절 없음.
시험 값은 카드에 고정해 두고(사후 값), 시작 뒤 기록은 시세로 그때그때 다시 계산합니다(폰이 꺼졌던 날이 있어도 틀어지지 않게).
자리가 바뀐 날만 @everyone 으로 울립니다. 바뀌지 않은 날의 글은 조용히 올립니다.
"""
import json
import urllib.request
from datetime import datetime, timezone

from . import cards, fmt

URL = "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1d&timeZone=10&limit=1000"
DAY = 86_400_000
CLOSE_AT = 14 * 3_600_000                  # 일봉 마감 = UTC 14시 = 밤 11시
WAIT = 30_000                              # 마감 뒤 이만큼 기다렸다가 받음
RETRY = 120_000                            # 못 받으면 이만큼 뒤 다시
RECORD_FROM = int(datetime(2026, 10, 31, 14, tzinfo=timezone.utc).timestamp() * 1000)    # 기록 시작 = 이 판정부터
WARMUP = 300 * DAY                         # 기록 시작 앞으로 이만큼은 받아 둠 (이평 · RSI 상태)
FEE = 0.001                                # 편도 0.1% (기록에만 씀 · 숏 펀딩은 뺌)
RSI_N, RSI_LOW, RSI_HIGH = 5, 10, 70
SAFE_SIZE = 0.5
PURPLE = 10181046
GREEN = cards.GREEN
PANEL = cards.BLUE
# 시험 값(2019-01 ~ 2026-10 · 판정 시각 24칸의 중앙 · 왕복 0.2% · 숏 펀딩 포함 · 낙폭은 일봉 저가 · 고가까지 · 사후 값)
SAFE = {"title": "안전 진입", "tag": "낙폭 우선", "color": GREEN, "total": "+989%", "span": "2019-01 ~ 2026-10 시험 · 24칸 중앙 · 사후 값",
        "n": 58, "win": 17, "per": "+6.22%", "mdd": "−28.5%", "idle": "1,512일"}
BOLD = {"title": "과감한 수익", "tag": "수익 우선", "color": PURPLE, "total": "+22,733%", "span": "2019-01 ~ 2026-10 시험 · 24칸 중앙 · 사후 값",
        "n": 169, "win": 50, "per": "+5.55%", "mdd": "−57.5%", "idle": "0일"}
FOOTER = ("투자 권유 아님 · 주문 안 냄 · 시험 값은 규칙을 고른 구간의 사후 값이라 기대치가 아님 · 적중은 30%쯤 · "
          "2017-11부터 재면 낙폭 안전 −36.5% · 과감 −80.3%, 과감은 밤 11시 칸 2018년에 21연패 −70.5% · 2026년은 안전 +6.2% · 과감 +29.7%(밤 11시 칸은 +0.7% · −21.3%)")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 candleman-alert", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8")


def daily(fetch=_get):
    """일봉들 (오래된 것부터). 기록 시작 앞의 WARMUP 까지 모자라면 앞쪽을 더 받음."""
    rows = json.loads(fetch(URL))
    while rows and int(rows[0][0]) > RECORD_FROM - WARMUP:
        page = json.loads(fetch(URL + "&endTime=%d" % (int(rows[0][0]) - 1)))
        if not page:
            break
        rows = page + rows
    return rows


def closed_end(n):
    """지금(ms) 기준 마지막으로 닫힌 일봉의 마감 시각(ms)."""
    return (n - CLOSE_AT) // DAY * DAY + CLOSE_AT


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
    """종가들 → 날마다의 판정. on · safe · bold[i] = i일 종가 판정(i + 1일 시가부터). 끝의 값들은 다음 판정 계산에 씀."""
    ma50, ma100 = sma(c, 50), sma(c, 100)
    au = ad = 0.0
    hold = False
    on, safe, bold = [], [], []
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
    return {"on": on, "safe": safe, "bold": bold, "up50": up50, "up100": up100, "hold": hold, "rsi": r, "au": au, "ad": ad, "ma50": ma50[-1], "ma100": ma100[-1],
            "sum49": sum(c[-49:]), "sum99": sum(c[-99:])}


def after(c, J, x):
    """다음 판정 종가가 x 일 때 (규칙 켜짐, 안전 켜짐)."""
    au = J["au"] + (max(x - c[-1], 0.0) - J["au"]) / RSI_N
    ad = J["ad"] + (max(c[-1] - x, 0.0) - J["ad"]) / RSI_N
    r = rsi_of(au, ad)
    hold = True if r < RSI_LOW else False if r > RSI_HIGH else J["hold"]
    on = x > (J["sum49"] + x) / 50 or hold
    return on, on and x > (J["sum99"] + x) / 100


def bands(c, J, k):
    """다음 판정 종가별 자리 → [[아래 끝, 위 끝, 켜짐]] (지금 종가 ±30% 를 0.05% 간격으로 봄). k = 0 과감 · 1 안전."""
    out = []
    for j in range(-600, 601):
        x = c[-1] * (1 + j / 2000)
        on = after(c, J, x)[k]
        if out and out[-1][2] == on:
            out[-1][1] = x
        else:
            out.append([x, x, on])
    return out


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


def read(rows, n):
    """받은 일봉 → 지금 상태. 닫힌 봉만 씀."""
    bars = [r for r in rows if int(r[6]) < n]
    o = [float(r[1]) for r in bars]
    c = [float(r[4]) for r in bars]
    ends = [int(r[6]) + 1 for r in bars]
    J = judge(c)
    st = {"end": ends[-1], "next_end": ends[-1] + DAY, "close": c[-1], "ma50": J["ma50"], "ma100": J["ma100"], "rsi": J["rsi"], "up50": J["up50"], "up100": J["up100"], "hold": J["hold"], "on": J["on"][-1]}
    i0 = next((i for i, e in enumerate(ends) if e >= RECORD_FROM), None)
    for key, k in (("bold", 0), ("safe", 1)):
        st[key] = turn(J[key], o, c, ends)
        st[key]["bands"] = bands(c, J, k)
        st[key]["since"] = record(J[key], c, i0) if i0 is not None else {"n": 0, "win": 0, "ret": 0.0}
    return st


def _usd(x):
    return "$" + format(round(x), ",")


def pct1(x):
    """0.031 → '+3.1%' · 0 → '0.0%'."""
    v = round(x * 100, 1)
    return ("+" if v > 0 else fmt.MINUS if v < 0 else "") + "%.1f%%" % abs(v)


def band_text(rs, on_word, off_word):
    """[[아래, 위, 켜짐]] → '$70,000 아래 = 현금 · $70,000 위 = 자금 50% 현물 롱'."""
    word = lambda on: on_word if on else off_word
    if len(rs) == 1:
        return "지금 종가 ±30%% 안에서는 %s" % word(rs[0][2])
    out = []
    for i, (_, _, on) in enumerate(rs):
        if i == 0:
            out.append("%s 아래 = %s" % (_usd(rs[1][0]), word(on)))
        elif i == len(rs) - 1:
            out.append("%s 위 = %s" % (_usd(rs[i][0]), word(on)))
        else:
            out.append("%s ~ %s = %s" % (_usd(rs[i][0]), _usd(rs[i + 1][0]), word(on)))
    return " · ".join(out)


def rule_card(x, since):
    """규칙 카드: 왼쪽 색 띠 · 큰 숫자 · 건수 · 적중 · 건당 · 낙폭 · 쉰 날 · 시작 뒤 기록."""
    field = lambda name, value: {"name": name, "value": value, "inline": True}
    return {"title": "%s · %s" % (x["title"], x["tag"]), "color": x["color"], "description": "## %s\n%s" % (x["total"], x["span"]),
            "fields": [field("건수", "{:,}건".format(x["n"])), field("적중", "%d건 (%d%%)" % (x["win"], round(x["win"] / x["n"] * 100))),
                       field("건당 평균", x["per"]), field("최대 낙폭", x["mdd"]), field("쉰 날", x["idle"]), field("시작 뒤 기록", since)]}


def since_text(s):
    return "%d건 · %d승 · %s" % (s["n"], s["win"], pct1(s["ret"]))


def _when(ms):
    t = fmt.kst(ms)
    return "%d/%d %02d:%02d" % (t.month, t.day, t.hour, t.minute)


def safe_line(st):
    x = st["safe"]
    if x["fresh"] and x["w"]:
        return "🟢 진입 신호: 자금 50% 현물 롱 (다음 봉 시가 · 지금)"
    if x["fresh"]:
        return "🔴 청산 신호: 현물 전부 정리 → 현금 · 이번 구간 %s" % pct1(x["closed"])
    if x["w"]:
        return "유지: 자금 50%% 현물 롱 · %s 진입 · 자금 대비 %s" % (_when(x["from"]), pct1(x["gain"]))
    why = "규칙은 켜졌지만 종가가 100일선 아래" if st["on"] else "규칙 꺼짐 (종가가 50일선 아래)"
    return "오늘은 쉼 · 현금 · %s" % why


def bold_line(st):
    x = st["bold"]
    if x["fresh"] and x["w"] > 0:
        return "🟢 진입 신호: 선물 숏 청산 → 현물 롱 (다음 봉 시가 · 지금) · 이번 숏 구간 %s" % pct1(x["closed"])
    if x["fresh"]:
        return "🔴 청산 신호: 현물 청산 → 선물 숏 1배 (다음 봉 시가 · 지금) · 이번 롱 구간 %s" % pct1(x["closed"])
    return "유지: %s · %s 진입 · %s" % ("현물 롱" if x["w"] > 0 else "선물 숏 1배", _when(x["from"]), pct1(x["gain"]))


def head(st):
    """머리 한 줄 (굵은 글씨 없음) · 울릴지."""
    words = []
    if st["safe"]["fresh"]:
        words.append("안전 진입 %s" % ("진입" if st["safe"]["w"] else "청산"))
    if st["bold"]["fresh"]:
        words.append("과감한 수익 %s" % ("현물 롱 전환" if st["bold"]["w"] > 0 else "선물 숏 전환"))
    if words:
        return "🔔 BTC 신호 · " + " · ".join(words), True
    return "📡 BTC 판정 · 오늘은 자리 그대로", False


def compose(st):
    """디스코드 글: 머리 한 줄 + 규칙 카드 둘 + 봇의 선택. {"content", "embeds", "ring"}."""
    text, ring = head(st)
    side = lambda x, up: "%s %s" % (_usd(x), "위" if up else "아래")
    lines = ["**안전 진입** — " + safe_line(st), "**과감한 수익** — " + bold_line(st),
             "마지막 판정: 종가 %s · 50일선 %s · 100일선 %s · RSI(5) %.1f%s" % (_usd(st["close"]), side(st["ma50"], st["up50"]), side(st["ma100"], st["up100"]), st["rsi"],
                                                                     " · RSI 되돌림 켜짐" if st["hold"] else ""),
             "%s 판정 · 안전: %s" % (_when(st["next_end"]), band_text(st["safe"]["bands"], "자금 50% 현물 롱", "현금")),
             "%s 판정 · 과감: %s" % (_when(st["next_end"]), band_text(st["bold"]["bands"], "현물 롱", "선물 숏 1배"))]
    panel = {"title": "봇의 선택 · %s 마감 → %s" % (_when(st["end"]), _when(st["next_end"])), "color": PANEL, "description": "\n".join(lines), "footer": {"text": FOOTER}}
    return {"content": text, "embeds": [rule_card(SAFE, since_text(st["safe"]["since"])), rule_card(BOLD, since_text(st["bold"]["since"])), panel], "ring": ring}


def build(n, fetch=_get):
    """지금 판정 글을 만듦. 이번 마감 봉이 아직 안 오면 ValueError (조금 뒤 다시)."""
    st = read(daily(fetch), n)
    if st["end"] != closed_end(n):
        raise ValueError("이번 마감 봉이 아직 없음")
    return compose(st)
