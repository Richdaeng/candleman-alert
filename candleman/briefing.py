"""매일 아침 경제 브리핑 — #일일-경제-브리핑 (2026-10-21 댕이 요청).

숫자(신용융자 · 미수금 · 반대매매 · 투자자예탁금)는 이 폰이 금융투자협회 종합통계에서 직접 받고,
밤사이 소식 · 다가오는 일정은 Claude 클라우드 예약 작업이 매일 아침 웹에서 찾아
GitHub(claude/briefing 가지의 briefing.json)에 올려 둔 것을 가져와 붙입니다.
웹후크 주소는 이 폰에만 있습니다. 가져온 글은 글자로만 쓰고(실행하지 않음), 줄 수 · 길이를 잘라 짧게 유지합니다.
협회는 전날 수치를 다음 영업일에 올리므로 카드 제목에 '몇 월 며칠 기준'을 적습니다.
"""
import datetime as dt
import json
import urllib.request

from . import fmt
from .fmt import ZERO, dec

KOFIA_URL = "https://freesis.kofia.or.kr/meta/getMetaDataList.do"
BRIEF_URL = "https://raw.githubusercontent.com/Richdaeng/candleman-alert/refs/heads/claude/briefing/briefing.json"
KOFIA_TABLES = {                                   # 표 이름 → TMPV2 부터 차례로 (금액 단위: 백만 원)
    "STATSCU0100000060BO": ["예탁금", "파생예수금", "RP", "미수금", "반대매매", "반대매매비중"],
    "STATSCU0100000070BO": ["신용융자", "신용융자_유가", "신용융자_코스닥"],
}
WAIT_MIN = 20              # 정한 시각에 브리핑 글이 아직 없으면 이만큼(분)까지 1분마다 다시 봄
LAST_HOUR = 12             # 이 시각이 지나면 그날 브리핑은 건너뜀 (늦게 켜졌을 때)
MAX_LINES, MAX_CHARS = 7, 90
COLOR = 3447003            # 브리핑 카드 띠 색 (파랑)
FOOTER = "투자 권유 아님 · 숫자: 금융투자협회 · 소식 · 일정: AI가 뉴스 검색으로 정리 (틀릴 수 있음)"
WEEKDAY = "월화수목금토일"


def _http_json(url, body=None, timeout=20):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"User-Agent": "Mozilla/5.0 candleman-alert", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json; charset=UTF-8"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def kofia_rows(since, until, fetch=_http_json):
    """협회 두 표를 날짜별로 합침 → [{'날짜': date, '신용융자': Decimal(백만 원), …}] 오래된 것부터."""
    merged = {}
    for obj, names in KOFIA_TABLES.items():
        body = {"dmSearch": {"tmpV40": "1000000", "tmpV41": "1", "tmpV1": "D", "tmpV45": since.strftime("%Y%m%d"),
                             "tmpV46": until.strftime("%Y%m%d"), "OBJ_NM": obj}}
        for r in json.loads(fetch(KOFIA_URL, body)).get("ds1") or []:
            d = str(r.get("TMPV1") or "")
            if len(d) != 8:
                continue
            row = merged.setdefault(d, {"날짜": dt.date(int(d[:4]), int(d[4:6]), int(d[6:]))})
            for i, name in enumerate(names):
                row[name] = dec(r.get("TMPV%d" % (i + 2)))
    return [merged[k] for k in sorted(merged)]


def won(million):
    """백만 원 → '31.11조 원' · '542억 원'."""
    v = dec(million)
    if abs(v) >= 1_000_000:
        return "%s조 원" % format((v / 1_000_000).quantize(dec("0.01")), "f")
    return "{:,}억 원".format(int((v / 100).quantize(dec("1"))))


def won_delta(million):
    """백만 원 차이 → '+1.14조' · '−210억' · '변화 없음'."""
    v = dec(million)
    if v == 0:
        return "변화 없음"
    sign = "+" if v > 0 else fmt.MINUS
    a = abs(v)
    if a >= 1_000_000:
        return "%s%s조" % (sign, format((a / 1_000_000).quantize(dec("0.01")), "f"))
    return "%s{:,}억".format(int((a / 100).quantize(dec("1")))) % sign


def credit_card(rows):
    """신용 · 미수 카드. 마지막 날 값과 그 전날 대비 변화. 자료가 없으면 None."""
    rows = [r for r in rows if r.get("신용융자") or r.get("미수금")]
    if not rows:
        return None
    last, prev = rows[-1], rows[-2] if len(rows) > 1 else None

    def line(label, key):
        s = "%s **%s**" % (label, won(last.get(key, ZERO)))
        return s + (" (%s)" % won_delta(last.get(key, ZERO) - prev.get(key, ZERO)) if prev else "")
    d = last["날짜"]
    lines = [line("신용융자", "신용융자"), line("미수금", "미수금")]
    ratio = last.get("반대매매비중")
    lines.append("반대매매 **%s**%s" % (won(last.get("반대매매", ZERO)), " (미수금의 %s%%)" % fmt.plain(ratio) if ratio else ""))
    lines.append(line("투자자예탁금", "예탁금"))
    return {"title": "💳 신용 · 미수 잔고 (%d/%d %s 기준)" % (d.month, d.day, WEEKDAY[d.weekday()]),
            "description": "\n".join(lines), "color": COLOR}


def impact(v):
    """중요도 1~5 (과거에 같은 일정이 시장을 얼마나 움직였나 · 클라우드 작업이 매김). 없거나 이상하면 0."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return n if 1 <= n <= 5 else 0


def stars(n):
    """중요도 그림: 4 → '★★★★☆'."""
    return "★" * n + "☆" * (5 - n)


def _clip(s):
    s = " ".join(str(s).split())
    return s if len(s) <= MAX_CHARS else s[:MAX_CHARS - 1] + "…"


def parse_brief(text, today):
    """클라우드 작업이 올린 briefing.json → (밤사이 줄들, 일정 줄들). 오늘 것이 아니거나 모양이 틀리면 None."""
    try:
        j = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(j, dict) or j.get("date") != today.isoformat():
        return None
    night = [_clip(x) for x in (j.get("overnight") or []) if isinstance(x, str) and x.strip()][:MAX_LINES]
    plan = []
    for x in (j.get("schedule") or [])[:MAX_LINES]:
        if isinstance(x, dict) and str(x.get("what") or "").strip():
            line = "**%s** %s" % (_clip(x.get("when") or ""), _clip(x["what"])) if x.get("when") else _clip(x["what"])
            n = impact(x.get("impact"))
            plan.append("%s %d점 · %s" % (stars(n), n, line) if n else line)
        elif isinstance(x, str) and x.strip():
            plan.append(_clip(x))
    if not night and not plan:
        return None
    return night, plan


def compose(today, rows, brief):
    """디스코드 글: 머리 한 줄 + 카드(신용 · 미수 / 밤사이 / 다가오는 일정). brief 가 None 이면 숫자 카드만 + 안내 한 줄."""
    cards = []
    c = credit_card(rows)
    if c:
        cards.append(c)
    if brief:
        night, plan = brief
        if night:
            cards.append({"title": "🌙 밤사이", "description": "\n".join("· " + s for s in night), "color": COLOR})
        if plan:
            rated = any(p.startswith(("★", "☆")) for p in plan)
            cards.append({"title": "📅 다가오는 일정" + (" · 중요도 5점 만점" if rated else ""), "description": "\n".join(plan),
                          "color": COLOR})
    if cards:
        cards[-1]["footer"] = {"text": FOOTER if brief else "투자 권유 아님 · 숫자: 금융투자협회"}
    head = "🗞️ **%d/%d (%s) 아침 경제 브리핑**" % (today.month, today.day, WEEKDAY[today.weekday()])
    if not brief:
        head += "\n밤사이 소식 · 일정은 오늘 준비되지 않음"
    return {"content": head, "embeds": cards}
