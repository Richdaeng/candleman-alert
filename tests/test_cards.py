"""#실시간-매매 · #봇-상태 카드 시험 — 줄글이 '머리 한 줄 + 카드'로 바뀌는 모양과 띠 색."""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from candleman import cards  # noqa: E402
from candleman.discord import Discord  # noqa: E402


class CardTest(unittest.TestCase):
    def check(self, channel, text, head, lines, color):
        h, embeds = cards.card(channel, text)
        self.assertEqual(h, head)
        self.assertEqual(embeds[0]["description"].split("\n"), lines)
        self.assertEqual(embeds[0]["color"], color)

    def test_futures_open_and_close(self):
        self.check("trade", "🟢 비트코인(BTC) 롱 진입 · 오르면 이익\n$82,000에 0.05 BTC · 약 $4,100어치(레버리지 포함) · 걸어 둔 주문 체결",
                   "🟢 비트코인(BTC) 롱 진입 · 오르면 이익",                     # 폰 알림에 보이는 머리 줄은 그대로(굵은 글씨 없음)
                   ["**$82,000**에 0.05 BTC", "약 **$4,100**어치(레버리지 포함)", "걸어 둔 주문 체결"], cards.GREEN)
        self.check("trade", "⬜ 비트코인(BTC) 롱 청산 · 이익 +$67.50\n$83,500에 남은 0.04 BTC 모두 정리 · 1시간 12분 보유\n이번 거래 전체 이익 +$80.00",
                   "⬜ 비트코인(BTC) 롱 청산 · 이익 +$67.50",
                   ["**$83,500**에 남은 0.04 BTC 모두 정리", "1시간 12분 보유", "이번 거래 전체 이익 **+$80.00**"], cards.GRAY)
        self.check("trade", "🔴 비트코인(BTC) 숏 진입 · 내리면 이익\n$82,000에 0.05 BTC · 약 $4,100어치(레버리지 포함) · 바로 체결(시장가)",
                   "🔴 비트코인(BTC) 숏 진입 · 내리면 이익", ["**$82,000**에 0.05 BTC", "약 **$4,100**어치(레버리지 포함)", "바로 체결(시장가)"], cards.RED)

    def test_colors(self):
        self.assertEqual(cards.color("➕ 비트코인(BTC) 롱 추가 진입 · 오르면 이익"), cards.GREEN)
        self.assertEqual(cards.color("➕ 비트코인(BTC) 숏 추가 진입 · 내리면 이익"), cards.RED)
        self.assertEqual(cards.color("🟠 비트코인(BTC) 롱 일부 청산 · 이익 +$10.00"), cards.ORANGE)
        self.assertEqual(cards.color("⚠️ 비트코인(BTC) 롱 강제 청산 · 손실 −$40.20"), cards.DARK_RED)
        self.assertEqual(cards.color("📝 주문 걸어 둠 · 비트코인(BTC) 롱 진입 (오르면 이익)"), cards.BLUE)
        self.assertEqual(cards.color("🟢 비트코인(BTC) 현물 매수"), cards.YELLOW)            # 현물 매수는 바이낸스 노랑
        self.assertEqual(cards.color("📊 2026 주인장 수익률 · 10/21 20:00 기준", "수익률: +4.15%"), cards.GREEN)
        self.assertEqual(cards.color("📊 2026 주인장 수익률 · 10/21 20:00 기준", "수익률: −2.30%"), cards.RED)

    def test_one_line_order_note(self):
        self.check("trade", "✖️ 주문 취소 · 비트코인(BTC) 롱 진입 · $80,000에 0.05 BTC 중 0.02 BTC 체결 · 남은 0.03 BTC 취소",
                   "✖️ 주문 취소 · 비트코인(BTC) 롱 진입", ["**$80,000**에 0.05 BTC 중 0.02 BTC 체결", "남은 0.03 BTC 취소"], cards.GRAY)

    def test_status_keeps_lines_and_single_lines_stay_plain(self):
        self.check("status", "🔄 새 판으로 바꿈 · aaaaaaa → bbbbbbb\n· 브리핑 카드 · 요약 카드", "🔄 새 판으로 바꿈 · aaaaaaa → bbbbbbb",
                   ["· 브리핑 카드 · 요약 카드"], cards.BLUE)                          # 업데이트 내역 한 줄은 쪼개지 않음
        self.check("status", "🤖 시작함\n포지션 1개 · 미체결 주문 0개 · 조건부 주문 0개", "🤖 시작함",
                   ["포지션 1개 · 미체결 주문 0개 · 조건부 주문 0개"], cards.GREEN)
        text = "🚫 태규의 명령(/포지션)을 6시간 막음 (5분 안에 10번 넘게 부름)"
        self.assertEqual(cards.card("status", text), (text, None))
        self.assertEqual(cards.card("summary", "아무 글\n둘째 줄"), ("아무 글\n둘째 줄", None))    # 다른 채널은 손대지 않음
        self.check("journal", "📝 일지 · 솔라나(SOL) 진입\n차트 · 확신 3/5 · 손절 $95.00\n메모: 지지선 확인", "📝 일지 · 솔라나(SOL) 진입",
                   ["차트 · 확신 3/5 · 손절 **$95.00**", "메모: 지지선 확인"], cards.BLUE)   # #매매일지도 줄을 그대로 카드에

    def test_discord_put_converts_only_plain_text(self):
        d = Discord({"trade": "u1", "status": "u3"})
        asyncio.run(d.put("trade", "🟢 비트코인(BTC) 현물 매수\n$83,000.00에 0.05 BTC · 약 $4,150어치 · 바로 체결(시장가)"))
        ch, text, embeds = d.q.get_nowait()
        self.assertEqual((ch, text), ("trade", "🟢 비트코인(BTC) 현물 매수"))
        self.assertEqual(embeds[0]["description"].split("\n"), ["**$83,000.00**에 0.05 BTC", "약 **$4,150**어치", "바로 체결(시장가)"])
        given = [{"title": "이미 카드", "description": "x"}]
        asyncio.run(d.put("trade", "머리", given))
        self.assertEqual(d.q.get_nowait(), ("trade", "머리", given))                   # 카드를 직접 준 글은 그대로


if __name__ == "__main__":
    unittest.main()
