"""새 판을 처음 켰을 때 한 번만 올리는 안내 글.

올린 안내의 id 는 state 의 notices_sent 에 적어 두어, 다시 켜도 또 올리지 않습니다.
새 안내는 id 를 새로 정해 ANNOUNCEMENTS 에 붙이면 됩니다. until(한국 날짜)이 지나면 올리지 않아서
나중에 새로 설치해도 지난 안내가 다시 나오지 않습니다. 글은 서버의 다른 안내처럼 '~함 · ~임'으로 씁니다.
"""

ANNOUNCEMENTS = [
    {"id": "2026-10-21-position-cards", "until": "2026-10-28", "channels": ("trade", "summary"),
     "embed": {"title": "📢 새 기능 · /포지션", "color": 2533018, "description": (
         "#잡담방에서 `/포지션`을 입력하면 운영자가 지금 들고 있는 포지션을 카드로 보여 줌.\n\n"
         "· 종목마다 카드 한 장 (🟢 롱 · 🔴 숏)\n"
         "· 수량 · 평균 가격 · 지금 가격 · 거래 크기 · 실제 레버리지 · 미실현 손익\n"
         "· 실제 레버리지 = 거래 크기 ÷ 증거금. 바이낸스에 정해 둔 배율(예: 20배)이 아니라 실제로 쓰고 있는 배율임.\n\n"
         "매매 알림과 하루 요약은 지금처럼 그대로 올라옴.")}},
]


def due(sent, today):
    """아직 안 올렸고 기한이 남은 안내들. today 는 한국 날짜 'YYYY-MM-DD'."""
    return [a for a in ANNOUNCEMENTS if a["id"] not in sent and today <= a["until"]]
