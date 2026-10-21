"""#실시간-매매 · #봇-상태 글을 '머리 한 줄 + 카드'로 (10/21 댕이: "실시간 매매나 다른 채널들도 전부 예쁘게").

글을 만드는 쪽(tracker · spot · app)은 지금처럼 줄글을 만들고, 디스코드로 보내기 직전(Discord.put)에 여기서 바꿉니다.
- 머리 줄 = 첫 줄 그대로. 폰 알림에 이 줄이 보여서 굵은 글씨(별표)를 쓰지 않음.
- 카드 = 나머지 줄. #실시간-매매는 ' · ' 로 나눠 한 줄에 한 가지, 금액 · 퍼센트는 굵게. #봇-상태는 줄을 그대로 둠
  (업데이트 내역처럼 한 줄 안에 ' · ' 가 들어 있는 글이 있어서).
- 한 줄짜리 주문 소식('✖️ 주문 취소 · 비트코인(BTC) 롱 진입 · $80,000에 0.05 BTC')은 앞 두 토막이 머리.
- 띠 색은 머리 줄의 표시로: 롱 진입 · 추가 초록, 숏 빨강, 일부 정리 주황, 모두 정리 · 취소 회색, 강제 청산 진한 빨강,
  주문 걸어 둠 파랑, 현물 매수 노랑, 주인장 수익률은 수익이면 초록 · 손실이면 빨강.
"""
import re

GREEN, RED, ORANGE, GRAY, BLUE, YELLOW, DARK_RED = 2533018, 15684432, 15105570, 9807270, 3447003, 15776011, 10038562
CHANNELS = ("trade", "status")
_NUMBER = re.compile(r"(?<![\w*])([+−-]?\$[\d,]+(?:\.\d+)?|[+−-]?\d[\d,]*(?:\.\d+)?%)")


def bold_numbers(s):
    """금액('$82,000' · '+$47.50' · '−$40.20')과 퍼센트('+4.15%')를 굵게."""
    return _NUMBER.sub(r"**\1**", s)


def color(head, text=""):
    if head.startswith("📊"):
        return RED if "수익률: −" in text else GREEN if "수익률: +" in text else GRAY
    if head.startswith(("⚠️", "⛔", "🚫")):
        return DARK_RED if "강제 청산" in head else ORANGE
    if "현물" in head and head.startswith(("🟢", "➕")):
        return YELLOW
    if head.startswith(("🟢", "🤖")):
        return GREEN
    if head.startswith("🔴"):
        return RED
    if head.startswith("➕"):
        return GREEN if " 롱 " in head else RED if " 숏 " in head else GRAY
    if head.startswith("🟠"):
        return ORANGE
    if head.startswith(("📝", "⏸", "🔄", "📅")):
        return BLUE
    return GRAY


def card(channel, text):
    """줄글 → (머리 줄, [카드]). 카드로 나눌 것이 없으면 (원래 글, None)."""
    lines = [ln for ln in (text or "").split("\n") if ln.strip()]
    if channel not in CHANNELS or not lines:
        return text, None
    head, rest = lines[0], lines[1:]
    if channel == "trade":
        if not rest and head.count(" · ") >= 2:
            parts = head.split(" · ")
            head, rest = " · ".join(parts[:2]), [" · ".join(parts[2:])]
        facts = [f.strip() for ln in rest for f in ln.split(" · ") if f.strip()]
    else:
        facts = [ln.strip() for ln in rest]
    if not facts:
        return text, None
    return head, [{"description": "\n".join(bold_numbers(f) for f in facts), "color": color(head, text)}]
