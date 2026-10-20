"""종목 기호를 처음 보는 사람도 읽을 수 있는 이름으로. 여기 없는 종목은 기호 그대로 씁니다.

바이낸스 선물의 심볼은 '기호 + 결제 화폐'(BTCUSDT = BTC 를 USDT 로)라서, 결제 화폐를 뗀 기호로 찾습니다.
주식 · ETF 선물은 수량을 '주'로 적습니다 (실제 주식이 아니라 1주 가격을 따라가는 계약).
"""
from . import fmt

COINS = {
    "BTC": "비트코인", "ETH": "이더리움", "SOL": "솔라나", "XRP": "리플", "BNB": "바이낸스 코인",
    "DOGE": "도지코인", "ADA": "에이다", "TRX": "트론", "AVAX": "아발란체", "LINK": "체인링크",
    "SUI": "수이", "TON": "톤코인", "DOT": "폴카닷", "LTC": "라이트코인", "BCH": "비트코인 캐시",
    "ETC": "이더리움 클래식", "XLM": "스텔라루멘", "HBAR": "헤데라", "APT": "앱토스", "NEAR": "니어",
    "ARB": "아비트럼", "OP": "옵티미즘", "1000PEPE": "페페", "1000SHIB": "시바이누", "WLD": "월드코인",
    "HYPE": "하이퍼리퀴드", "ENA": "에테나", "ONDO": "온도",
}

STOCKS = {
    # 한국 관련
    "EWY": "한국 주식 ETF", "SKHY": "SK하이닉스 미국 주식", "SKHYNIX": "SK하이닉스", "SAMSUNG": "삼성전자",
    "NAVER": "네이버", "LGELECTRONICS": "LG전자", "KODEX200": "KODEX 200 ETF",
    # 미국 주식
    "TSLA": "테슬라", "NVDA": "엔비디아", "AAPL": "애플", "MSFT": "마이크로소프트", "GOOGL": "구글",
    "AMZN": "아마존", "META": "메타", "TSM": "TSMC", "MU": "마이크론", "INTC": "인텔", "AVGO": "브로드컴",
    "PLTR": "팔란티어", "MSTR": "스트래티지", "COIN": "코인베이스", "HOOD": "로빈후드", "NFLX": "넷플릭스",
    "ORCL": "오라클",
    # ETF
    "QQQ": "나스닥100 ETF", "SPY": "S&P500 ETF", "IWM": "러셀2000 ETF", "EWJ": "일본 주식 ETF",
    "EWT": "대만 주식 ETF", "EWZ": "브라질 주식 ETF", "SOXL": "미국 반도체 3배 ETF", "TQQQ": "나스닥100 3배 ETF",
    "SQQQ": "나스닥100 3배 인버스 ETF",
}

OTHERS = {
    "XAU": "금", "XAG": "은", "XPT": "백금", "XPD": "팔라듐", "CL": "WTI 원유", "BZ": "브렌트유",
    "NATGAS": "천연가스", "COPPER": "구리",
}


def label(base):
    """'비트코인(BTC)' · '한국 주식 ETF(EWY)' · 모르는 종목은 기호만."""
    name = COINS.get(base) or STOCKS.get(base) or OTHERS.get(base)
    return "%s(%s)" % (name, base) if name and name != base else base


def amount(q, base):
    """수량 글자: 주식 · ETF 는 '66.74주', 나머지는 '0.05 BTC'."""
    if base in STOCKS:
        return "%s주" % fmt.qty(q)
    return "%s %s" % (fmt.qty(q), base)
