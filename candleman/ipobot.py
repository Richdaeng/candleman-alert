"""신규 추격 신호 — 운영자 한 사람에게만 봇 DM (2026-11-01 댕이 요청).

코스닥 신규상장 · 이전상장주(스팩 제외 · 상장 1년 안) 중 그날 정규장 거래대금 1위를 장 마감 뒤에 골라, 그날 애프터마켓 매수 · 다음 거래일 고정 익절 · 손절 신호를 카드로 올립니다.
주문은 내지 않습니다. 목록은 KRX KIND, 시세는 다음 금융(둘 다 공개 자료 · 키 없음)에서 받습니다.
- 크게: 거래대금 5,000억 이상 · 익절 +10% · 손절 −6%.
- 짧게: 거래대금 3,000억 이상 · 익절 +4.3% · 손절 −10%.
- 다음 거래일에 코스닥 상장 예정(스팩 제외)이 있으면 수급이 나뉘니 거릅니다 (11/2 댕이 결정).
둘 다 안 닿으면 다음 거래일 정규장 끝에 정리합니다. 다음 거래일 장 마감 뒤 5분봉 순서로 결과를 판정해 시작 뒤 기록(data/state.json)에 넣습니다.
기록은 그날 애프터 종가에 산 것으로 칩니다. 한 봉에 익절 · 손절이 둘 다 닿으면 순서를 모르니 손절로 칩니다.
진입 신호가 있는 날만 운영자를 태그해서 폰 알림이 갑니다(ring).
"""
import datetime as dt
import html
import json
import re
import time
import urllib.parse
import urllib.request

from . import fmt, signalbot

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
DAUM = "https://finance.daum.net/api/"
KIND_LIST = "https://kind.krx.co.kr/listinvstg/listingcompany.do"
KIND_PLAN = "https://kind.krx.co.kr/listinvstg/pubofrprogcom.do"
SAMPLE = "A196170"                 # 장이 열린 날인지 볼 때 쓰는 큰 코스닥 종목
FROM_MIN, UNTIL_MIN, LAST_MIN = 15 * 60 + 36, 20 * 60, 16 * 60 + 10    # 15:36 ~ 20:00 사이에 올림 · 16:10 까지 그날 시세가 없으면 쉬는 날
RETRY = 120_000
BARS = 800                         # 5분봉 몇 개 (하루 약 128개 · KEEP_DAYS 안의 자리가 잘리지 않게)
PAUSE = 0.12                       # 다음 금융 요청 사이 (초)
LOOK_DAYS = 365                    # 상장 몇 일 안을 볼까
PLAN_DAYS = 14                     # 앞으로 몇 일 안의 상장 일정을 적을까
NEAR_DAYS = 3                      # 상장 예정일이 이만큼 안이면 목록에 없어도 찾아봄
PLAN_BACK = 200                    # 공모기업현황의 날짜 칸은 신고서 제출일이라 넉넉히 거슬러 받음
KEEP_DAYS = 7                      # 결과를 못 읽은 신호를 이만큼 지나면 버림
COST = 0.003                       # 왕복 비용
BUY_TIME = "17시 ~ 19:55"           # 애프터마켓에서 살 때 (기록은 애프터 종가)
AFTER_FROM, AFTER_UNTIL = "16:00", "20:00"    # 애프터마켓 (2026-09-14부터)
RULES = (("big", "크게", 5e11, 0.10, -0.06), ("small", "짧게", 3e11, 0.043, -0.10))
# 시험 값(2026-03-04 ~ 10-30 · 163거래일 · 다음 거래일 상장 예정이 있으면 거름 · 9/11까지 정규장 종가 · 9/14부터 애프터 종가 진입 · 5분봉 순서 · 왕복 0.3% · 하루 1건 복리 · 표본이 작은 사후 값)
BIG = {"title": "🔥 신규 추격 크게", "tag": "+10% · −6%", "color": signalbot.PURPLE, "total": "+123%", "span": "2026-03 ~ 10 시험 · 다음 날 상장 거름 · 9/14부터 애프터 종가 · 사후 값",
       "n": 27, "win": 16, "per": "+3.33%", "mdd": "−17.7%", "idle": "136일"}
SMALL = {"title": "⚡ 신규 추격 짧게", "tag": "+4.3% · −10%", "color": signalbot.GREEN, "total": "+264%", "span": "2026-03 ~ 10 시험 · 다음 날 상장 거름 · 9/14부터 애프터 종가 · 사후 값",
         "n": 40, "win": 34, "per": "+3.49%", "mdd": "−21.9%", "idle": "123일"}
# 적중(같은 기간 · 다음 거래일 청산이 비용 뒤 플러스): 규칙 키 → (적중 확률, 이긴 평균, 진 평균, 지난 건수)
HIT = {"big": (.593, "+9.9%", "−6.3%", 27), "small": (.850, "+5.8%", "−9.5%", 40)}
FOOTER = ("투자 권유 아님 · 주문 안 냄 · 시험 값은 표본이 작고 규칙을 추론한 자료의 사후 값\n"
          "적중 = 다음 거래일 청산이 비용 뒤 플러스 · 95% 구간 크게 41 ~ 75% · 짧게 71 ~ 93% · 같은 익절 · 손절을 아무 신규주에 걸면 36% · 48%\n"
          "같은 종목을 다음 날 종가까지 그냥 들면 건당 크게 −2.71% · 짧게 −1.91%\n"
          "다음 날 상장 예정이 있는 날은 거름(시험에서 크게 7건 · 짧게 11건 · 거른 거래 건당 −4.23% · −2.25% · 뚜렷하지는 않음)\n"
          "시가가 손절선 아래면 더 잃음(거르기 전 시험 최악 −21.7%) · 익절 · 손절가는 산 값 기준으로 다시 계산")


def _get(url, code=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                               "Referer": "https://finance.daum.net/quotes/" + code if code else "https://finance.daum.net/"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def _post(url, body):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(body).encode(), headers={
        "User-Agent": UA, "Referer": url + "?method=" + body["method"].replace("Sub", "Main"), "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def _cell(x):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", x)).split())


def _spac(name, product=""):
    return "스팩" in name or "기업인수" in name or "기업인수" in product


def parse_listed(text):
    """KIND 상장 표(신규상장 · 이전상장) → [{'name', 'code', 'listed', 'spac'}]. 코드 = 'A' + 표의 번호 + '0'."""
    out = []
    for issuer, _, row in re.findall(r"<tr[^>]*fnDetailView\('([^']+)','(\d+)'\)[^>]*>(.*?)</tr>", text, re.S):
        tds = [_cell(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(tds) >= 12:
            out.append({"name": tds[0], "code": "A%s0" % issuer, "listed": tds[1], "spac": _spac(tds[0], tds[10])})
    return out


def listed(today, post=_post):
    """상장 1년 안 코스닥 신규상장 + 이전상장(다른 시장에서 옮겨 옴 · 10/15 진코스텍) 종목. 스팩은 뺌."""
    out = []
    for page in range(1, 11):
        rows = parse_listed(post(KIND_LIST, {
            "method": "searchListingTypeSub", "forward": "listingtype_sub", "currentPageSize": "100", "pageIndex": str(page), "orderMode": "1", "orderStat": "D",
            "marketType": "2", "country": "", "industry": "", "listTypeArrStr": "01|02|", "choicTypeArrStr": "01|02|03|04|05|06|", "secuGrpArrStr": "ST|FS",
            "fromDate": (today - dt.timedelta(days=LOOK_DAYS)).isoformat(), "toDate": today.isoformat(), "repIsuSrtCd": "", "isurCd": "", "searchCodeType": "",
            "searchCorpName": "", "repMajAgntDesignAdvserComp": "", "repMajAgntComp": "", "designAdvserComp": ""}))
        out += rows
        if len(rows) < 100:
            break
    return [r for r in out if not r["spac"]]


def parse_plan(text):
    """KIND 공모기업현황 표 → [(상장 예정일, 회사)]. 스팩은 뺌."""
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S):
        tds = [_cell(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(tds) == 9 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", tds[7]) and not _spac(tds[0]):
            out.append((tds[7], tds[0]))
    return sorted(out)


def plan(today, post=_post):
    """앞으로의 코스닥 상장 일정. 못 받아도 신호는 올림."""
    try:
        return parse_plan(post(KIND_PLAN, {
            "method": "searchPubofrProgComSub", "forward": "pubofrprogcom_sub", "currentPageSize": "100", "pageIndex": "1", "orderMode": "1", "orderStat": "D", "searchMode": "",
            "searchCodeType": "", "searchCorpName": "", "isurCd": "", "repIsuSrtCd": "", "bzProcsNo": "", "detailMarket": "", "marketType": "2", "searchCorpNameTmp": "",
            "fromDate": (today - dt.timedelta(days=PLAN_BACK)).isoformat(), "toDate": today.isoformat(),
            "repMajAgntDesignAdvserComp": "", "repMajAgntComp": "", "designAdvserComp": ""}))
    except Exception:
        return []


def quote(code, day, get=_get):
    """그날 값 {'close', 'value', 'live'}. 일별 표에 그날이 없으면 현재가 쪽을 봄(live · 거래대금에 NXT 까지 들어가 조금 큼). 그날 거래가 없으면 None."""
    rows = json.loads(get(DAUM + "quote/%s/days?symbolCode=%s&page=1&perPage=5&pagination=true" % (code, code), code)).get("data") or []
    for r in rows:
        if str(r.get("date"))[:10] == day:
            return {"close": float(r["tradePrice"]), "value": float(r["accTradePrice"]), "live": False}
    q = json.loads(get(DAUM + "quotes/%s?summary=false&changeStatistics=true" % code, code))
    if str(q.get("tradeDate")) == day.replace("-", ""):
        return {"close": float(q.get("regularTradePrice") or q["tradePrice"]), "value": float(q["accTradePrice"]), "live": True}
    return None


def find_code(name, get=_get):
    """상장 목록에 아직 없는 오늘 상장 종목의 코드. 없으면 None."""
    try:
        items = json.loads(get(DAUM + "search?q=" + urllib.parse.quote(name))).get("suggestItems") or []
    except Exception:
        return None
    return next((x.get("symbolCode") for x in items if x.get("koreanName") == name), None)


def scan(today, plans, get=_get, post=_post, sleep=time.sleep):
    """오늘 거래가 있던 신규주들을 거래대금 큰 순으로 → (종목들, 목록에 없어 못 본 오늘 상장 종목들)."""
    day = today.isoformat()
    names = listed(today, post)
    have = {r["name"] for r in names}
    missing = []
    for when, name in plans:                 # 예정일은 실제와 며칠 다를 수 있음 (10/31 예정 → 10/30 상장) · 그날 시세가 없으면 아래에서 빠짐
        if name not in have and abs((dt.date.fromisoformat(when) - today).days) <= NEAR_DAYS:
            code = find_code(name, get)
            if code:
                names.append({"name": name, "code": code, "listed": day})
            elif when == day:
                missing.append(name)
    out, failed = [], 0
    for r in names:
        try:
            q = quote(r["code"], day, get)
        except Exception:
            failed += 1
            q = None
        if q:
            out.append(dict(r, **q))
        sleep(PAUSE)
    if names and failed > len(names) * 0.3:
        raise ValueError("시세를 못 받은 종목이 많음 (%d/%d)" % (failed, len(names)))
    out.sort(key=lambda x: -x["value"])
    return out, missing


def minutes(code, get=_get):
    """5분봉 → {날짜: [(시각, 시, 고, 저, 종)]}."""
    rows = json.loads(get(DAUM + "charts/%s/5/minutes?limit=%d&adjusted=false" % (code, BARS), code)).get("data") or []
    out = {}
    for b in rows:
        d, t = b["candleTime"].split(" ")
        out.setdefault(d, []).append((t[:5], float(b["openingPrice"]), float(b["highPrice"]), float(b["lowPrice"]), float(b["tradePrice"])))
    return {d: sorted(v) for d, v in out.items()}


def judge(bars, entry, tp, sl):
    """다음 거래일 5분봉 순서로 판정 → ('tp' · 'sl' · 'both' · 'close', 비용 전 수익). 시가가 선 밖이면 시가에 체결."""
    up, dn = entry * (1 + tp), entry * (1 + sl)
    last = None
    for t, o, h, l, c in bars:
        if t >= "15:30":
            continue
        if o >= up:
            return "tp", o / entry - 1
        if o <= dn:
            return "sl", o / entry - 1
        if h >= up and l <= dn:
            return "both", sl
        if h >= up:
            return "tp", tp
        if l <= dn:
            return "sl", sl
        last = c
    return "close", (last / entry - 1) if last else 0.0


def after_close(bars):
    """그날 5분봉 중 애프터마켓(16:00 ~ 20:00) 마지막 체결가 = 애프터 종가. 애프터 봉이 없으면 None."""
    late = [b for b in bars if AFTER_FROM <= b[0] < AFTER_UNTIL]
    return late[-1][4] if late else None


def fresh():
    one = {"n": 0, "win": 0, "eq": 1.0, "peak": 1.0, "mdd": 0.0}
    return {"pending": [], "big": dict(one), "small": dict(one)}


def add(r, p):
    r["n"] += 1
    r["win"] += p > 0
    r["eq"] *= 1 + p
    r["peak"] = max(r["peak"], r["eq"])
    r["mdd"] = min(r["mdd"], r["eq"] / r["peak"] - 1)


def settle(rec, today, get=_get, plans=()):
    """지난 신호들의 결과를 판정해 기록에 넣음 → 결과 줄들. 분봉을 아직 못 읽은 신호는 남겨 둠.
    애프터마켓 매수 신호는 그날 애프터 종가에 산 것으로 침(애프터 봉이 없으면 정규장 종가).
    거르기 규칙 전에 올린 신호(11/2 영광)는 다음 거래일 상장 예정이 있었으면 기록하지 않고 거름 · 애프터 신호는 올릴 때 이미 걸렀으니 다시 안 봄."""
    lines, keep = [], []
    rule = {k: (ko, tp, sl) for k, ko, _, tp, sl in RULES}
    seen = {}
    for x in rec["pending"]:
        ko, tp, sl = rule[x["rule"]]
        busy = [] if x.get("after") else [n for n in next_listings(dt.date.fromisoformat(x["date"]), plans) if n != x["name"]]
        if busy:
            lines.append("%s %s 거름(%s 상장)" % (x["name"], ko, " · ".join(busy)))
            continue
        try:
            bars = seen.setdefault(x["code"], minutes(x["code"], get))
        except Exception:
            bars = {}
        days = sorted(d for d in bars if x["date"] < d <= today.isoformat() and bars[d][0][0] <= "09:05")
        if not days:
            old = (today - dt.date.fromisoformat(x["date"])).days > KEEP_DAYS
            if not old:
                keep.append(x)
            continue
        after = after_close(bars.get(x["date"], [])) if x.get("after") else None
        kind, gross = judge(bars[days[0]], after or x["entry"], tp, sl)
        p = gross - COST
        add(rec[x["rule"]], p)
        word = {"tp": "익절", "sl": "손절", "both": "한 봉에 둘 다 닿아 손절로 침", "close": "안 닿아 종가 정리"}[kind]
        note = ""
        if x.get("after"):
            note = " (애프터 종가 %s 기준)" % price(after) if after else " (애프터 봉이 없어 정규장 종가 기준)"
        lines.append("%s %s %s %s%s" % (x["name"], ko, word, signalbot.pct1(p), note))
    rec["pending"] = keep
    return lines


def next_workday(d):
    d += dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def won(v):
    """원 → '1조 1,895억' · '3,120억'."""
    eok = int(round(v / 1e8))
    if eok >= 10000:
        return "%d조 %s억" % (eok // 10000, format(eok % 10000, ","))
    return format(eok, ",") + "억"


def price(x):
    return format(int(round(x)), ",") + "원"


def picks(rows):
    """거래대금 1위 → (1위 또는 None, [(rule 키, 이름, 거래대금 기준, 익절, 손절) 중 거래대금 기준을 넘은 것])."""
    if not rows:
        return None, []
    top = rows[0]
    return top, [r for r in RULES if top["value"] >= r[2]]


def next_listings(day, plans, done=None):
    """day 다음 거래일에 상장할 예정인 코스닥 종목 이름들(공모기업현황 예정일 · 스팩은 plan 에서 뺌).
    done(그날 거래된 종목들)을 주면 그 종목은 빼고(예정일보다 먼저 상장한 적이 있음 · 10/31 예정 → 10/30),
    예정일이 그날인데 거래가 없던 종목은 넣음(하루 밀려 상장한 적이 있음 · 10/29 예정 → 10/30)."""
    d0, d1 = day.isoformat(), next_workday(day).isoformat()
    if done is None:
        return [n for d, n in plans if d0 < d <= d1]
    return [n for d, n in plans if n not in done and d0 <= d <= d1]


def decide(today, rows, plans):
    """→ (1위, 진입할 규칙들, 거른 규칙들, 다음 거래일 상장 예정 이름들).
    다음 거래일에 상장이 있으면 수급이 나뉘어 거름 (2026-11-02 시험: 거른 거래 크게 7건 건당 −4.23% · 짧게 11건 −2.25%, 뚜렷하지는 않음)."""
    top, go = picks(rows)
    busy = next_listings(today, plans, {r["name"] for r in rows})
    return (top, [], go, busy) if busy else (top, go, [], busy)


def short_day(s):
    return "%d/%d" % (int(s[5:7]), int(s[8:10]))


def compose(today, rows, missing, plans, results, rec):
    """디스코드 글: 머리 한 줄 + 규칙 카드 둘 + 봇의 선택. {"content", "embeds", "ring"}."""
    top, go, held, busy = decide(today, rows, plans)
    nxt = next_workday(today)
    when = "%d/%d 장 마감 → %d/%d" % (today.month, today.day, nxt.month, nxt.day)
    soon_listing = "%d/%d %s 상장 예정" % (nxt.month, nxt.day, " · ".join(busy))
    lines = []
    if top:
        tag = "오늘 상장" if top["listed"] == today.isoformat() else "%s 상장" % short_day(top["listed"])
        lines.append("🏆 거래대금 1위: %s %s%s · 종가 %s · %s" % (top["name"], won(top["value"]), " (실시간 값 · NXT 포함)" if top.get("live") else "", price(top["close"]), tag))
    for key, ko, need, tp, sl in RULES:
        mark = "🔥" if key == "big" else "⚡"
        if any(r[0] == key for r in go):
            p, up, down, n = HIT[key]
            lines.append("%s **%s** — 🟢 **진입 신호** · %s 애프터마켓에서 매수 → %d/%d 익절 %s · 손절 %s(산 값 기준) · 안 닿으면 정규장 끝에 정리\n"
                         "💰 종가 %s 기준이면 익절 %s · 손절 %s\n🎯 적중 확률 **%d%%** · 이기면 평균 %s · 지면 평균 %s · 지난 %d건" % (
                             mark, ko, BUY_TIME, nxt.month, nxt.day, signalbot.pct1(tp), signalbot.pct1(sl),
                             price(top["close"]), price(top["close"] * (1 + tp)), price(top["close"] * (1 + sl)), round(p * 100), up, down, n))
        elif any(r[0] == key for r in held):
            lines.append("%s **%s** — ⏸️ 오늘은 거름 · %s(수급이 나뉨)" % (mark, ko, soon_listing))
        elif top:
            lines.append("%s **%s** — 💤 오늘은 쉼 · 1위 거래대금이 %s 미달" % (mark, ko, won(need)))
        else:
            lines.append("%s **%s** — 💤 오늘은 쉼 · 오늘 거래된 신규주 없음" % (mark, ko))
    if results:
        lines.append("📒 지난 신호: " + " · ".join(results))
    if missing:
        lines.append("⚠️ 오늘 상장 예정 %s은 목록에서 못 찾아 판정에서 빠짐" % " · ".join(missing))
    done = {r["name"] for r in rows}
    soon = [(d, n) for d, n in plans if n not in done and today.isoformat() < d <= (today + dt.timedelta(days=PLAN_DAYS)).isoformat()][:4]
    if soon:
        lines.append("🗓️ 다음 상장(예정): " + " · ".join("%s %s" % (short_day(d), n) for d, n in soon))
    panel = {"title": "🤖 봇의 선택 · " + when, "color": signalbot.GREEN if go else signalbot.PANEL, "description": "\n".join(lines), "footer": {"text": FOOTER}}
    since = lambda r: "%d건 · %d승 · %s" % (r["n"], r["win"], signalbot.pct1(r["eq"] - 1))
    odds = " · ".join("%s 적중 %d%%" % (r[1], round(HIT[r[0]][0] * 100)) for r in go)
    if go:
        text = "🔔 신규 추격 진입 신호 · %s · %s · %s" % (top["name"], when, odds)
    elif held:
        text = "📡 신규 추격 · %s · 오늘은 거름 · %s" % (when, soon_listing)
    else:
        text = "📡 신규 추격 · %s · 오늘은 쉼" % when
    return {"content": text, "embeds": [signalbot.rule_card(BIG, since(rec["big"])), signalbot.rule_card(SMALL, since(rec["small"])), panel], "ring": bool(go)}


def due(t, last):
    """지금(KST) 신규 추격 글을 볼 때인지: 평일 · 15:36 ~ 20:00 · 그날 아직 안 끝냈으면."""
    m = t.hour * 60 + t.minute
    return t.weekday() < 5 and FROM_MIN <= m < UNTIL_MIN and last != t.date().isoformat()


def build(today, rec=None, get=_get, post=_post, sleep=time.sleep):
    """오늘 글을 만듦 → (글 또는 None, 기록). 시세가 아직 오늘 것이 아니면 None (쉬는 날이거나 조금 이르거나)."""
    rec = json.loads(json.dumps(rec)) if rec else fresh()
    if quote(SAMPLE, today.isoformat(), get) is None:
        return None, rec
    plans = plan(today, post)
    results = settle(rec, today, get, plans)
    rows, missing = scan(today, plans, get, post, sleep)
    msg = compose(today, rows, missing, plans, results, rec)
    top, go, _, _ = decide(today, rows, plans)
    day = today.isoformat()
    rec["pending"] += [{"rule": r[0], "code": top["code"], "name": top["name"], "date": day, "entry": top["close"], "after": True} for r in go]
    return msg, rec
