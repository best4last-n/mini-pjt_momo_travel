"""선택형 입력 옵션(시작 카드·조건 패널 공용) — SERVICE.md 3-6 (14).

선택형이 더 편한 항목(기간·동반자·이동 조합·강도·예산·관심사·차량)은 여기서 한 번에 정의해 화면과 서버가 같이 쓴다.
연료 단가·연비는 2026년 기준 대략값(추정 표기).
"""
from __future__ import annotations

from . import data

MAX_NIGHTS = 8

DURATION_PRESETS = [
    {"key": "day", "label": "당일치기", "nights": 0},
    {"key": "1n", "label": "1박 2일", "nights": 1},
    {"key": "2n", "label": "2박 3일", "nights": 2},
    {"key": "3n", "label": "3박 4일", "nights": 3},
    {"key": "4n", "label": "4박 5일", "nights": 4},
    {"key": "6n", "label": "일주일(6박 7일)", "nights": 6},
    {"key": "this_weekend", "label": "이번 주말", "nights": 1, "weekend": "this"},
    {"key": "next_weekend", "label": "다음 주말", "nights": 1, "weekend": "next"},
]

STYLE_PRESETS = [
    {"key": "family_kids", "label": "가족 체험", "desc": "아이와 체험·넓은 야외 위주, 이동 짧게",
     "apply": {"intensity": "보통", "companion_type": "어린이·유아", "interests": ["체험", "자연"]}},
    {"key": "parents", "label": "부모님 효도", "desc": "걷기 편한 곳·맛집, 여유 있게",
     "apply": {"intensity": "여유", "companion_type": "노약자", "interests": ["맛집", "사찰", "자연"]}},
    {"key": "couple", "label": "커플 데이트", "desc": "사진 명소·카페·야경",
     "apply": {"intensity": "보통", "companion_type": "보통", "interests": ["사진", "카페", "야경"]}},
    {"key": "food", "label": "맛집 투어", "desc": "향토 음식·시장·카페를 중심으로",
     "apply": {"intensity": "보통", "companion_type": "보통", "interests": ["맛집", "시장", "카페"]}},
    {"key": "history", "label": "역사 탐방", "desc": "유적·박물관·사찰 깊이 보기",
     "apply": {"intensity": "보통", "companion_type": "보통", "interests": ["역사", "박물관", "사찰"]}},
    {"key": "healing", "label": "자연 힐링", "desc": "정원·습지·숲길을 천천히",
     "apply": {"intensity": "여유", "companion_type": "보통", "interests": ["자연", "산책"]}},
    {"key": "photo", "label": "사진 여행", "desc": "전망대·일몰·풍경 명소",
     "apply": {"intensity": "빡빡", "companion_type": "보통", "interests": ["사진", "전망", "자연"]}},
    {"key": "rainy", "label": "비 오는 날 실내", "desc": "박물관·전시·실내 체험 위주",
     "apply": {"intensity": "보통", "companion_type": "보통", "interests": ["실내", "박물관", "체험"]}},
]

COMPANION_CHOICES = [
    {"label": "혼자", "type": "보통", "count": 1},
    {"label": "배우자/연인·친구", "type": "보통", "count": 2},
    {"label": "가족(아이 동반)", "type": "어린이·유아", "count": 4},
    {"label": "부모님 동반", "type": "노약자", "count": 4},
    {"label": "단체", "type": "보통", "count": 6},
]

TRANSPORT_COMBOS = [
    {"label": "자차", "long": "자차", "local": "자차"},
    {"label": "KTX·SRT + 렌터카", "long": "KTX·SRT", "local": "렌터카"},
    {"label": "KTX·SRT + 대중교통", "long": "KTX·SRT", "local": "대중교통"},
    {"label": "고속버스 + 대중교통", "long": "고속·시외버스", "local": "대중교통"},
    {"label": "고속버스 + 렌터카", "long": "고속·시외버스", "local": "렌터카"},
    {"label": "비행기 + 렌터카", "long": "비행기", "local": "렌터카"},
]

VEHICLE_TYPES = ["경차", "소형", "중형", "대형", "SUV", "승합"]
FUELS = ["휘발유", "고급휘발유", "경유", "LPG", "하이브리드", "전기"]
FUEL_PRICES = {"휘발유": 1700, "고급휘발유": 1950, "경유": 1600, "LPG": 1050, "하이브리드": 1700, "전기": 350}  # 원/L, 전기는 원/kWh
FUEL_UNIT = {"전기": "kWh"}
DEFAULT_EFFICIENCY = {   # km/L (전기는 km/kWh) — 차종·연료별 대략 복합 연비
    "경차": {"휘발유": 15, "고급휘발유": 15, "경유": 17, "LPG": 11, "하이브리드": 20, "전기": 6.5},
    "소형": {"휘발유": 14, "고급휘발유": 14, "경유": 17, "LPG": 10, "하이브리드": 19, "전기": 6},
    "중형": {"휘발유": 12, "고급휘발유": 12, "경유": 15, "LPG": 9, "하이브리드": 17, "전기": 5.5},
    "대형": {"휘발유": 9.5, "고급휘발유": 9.5, "경유": 12, "LPG": 7.5, "하이브리드": 14, "전기": 5},
    "SUV": {"휘발유": 10.5, "고급휘발유": 10.5, "경유": 13, "LPG": 8, "하이브리드": 15, "전기": 5},
    "승합": {"휘발유": 8, "고급휘발유": 8, "경유": 10.5, "LPG": 7, "하이브리드": 12, "전기": 4.5},
}
TOLL_PER_KM = {"경차": 50, "소형": 50, "중형": 50, "대형": 50, "SUV": 50, "승합": 52}   # 1종 승용 기준, 승합(12인승 이하) 근사
TOLL_DISCOUNTS = {"없음": 1.0, "경차 50%": 0.5, "친환경차 50%": 0.5, "장애인 50%": 0.5, "면제": 0.0}
PARKING_DISCOUNTS = {"없음": 1.0, "경차 50%": 0.5, "친환경차 50%": 0.5, "장애인 50%": 0.5, "면제": 0.0}
PARKING_PER_PLACE = 2000     # 관광지 1곳 주차 추정(공영 기준), '무료' 표기 장소는 0

INTENSITIES = [{"value": "강행군", "desc": "하루 최대 25곳 · 06:30~23:00 (새벽형·야행성, 숙소는 02시까지)"}, {"value": "빡빡", "desc": "하루 4~5곳 · 08~20시"}, {"value": "보통", "desc": "하루 3~4곳 · 09~18:30"},
               {"value": "여유", "desc": "하루 2~3곳 · 10~17:30"}]
INTERESTS = ["자연", "사찰", "역사", "박물관", "체험", "맛집", "카페", "시장", "사진", "전망", "야경", "산책", "실내"]
BUDGET_PRESETS = ["30만 원", "50만 원", "100만 원", "1인 20만 원"]
EXPENSE_CATEGORIES = ["장거리 교통", "현지 교통", "유류비", "통행료", "주차비", "렌터카", "식비", "카페·디저트",
                      "간식·음료", "입장료", "체험", "숙박", "기념품", "기타"]
REGION_DESC = {"gyeongju": "신라 천년 고도 · 불국사·대릉원·동궁과 월지",
               "suncheon_gurye": "순천만 정원·습지와 지리산 자락 구례",
               "suwon": "수원 화성과 화성행궁, 광교"}


def efficiency(vtype: str | None, fuel: str | None) -> float:
    return DEFAULT_EFFICIENCY.get(vtype or "중형", DEFAULT_EFFICIENCY["중형"]).get(fuel or "휘발유", 12)


def all_options() -> dict:
    return {
        "regions": [{"id": r, "label": data.region_label(r), "desc": REGION_DESC.get(r, "")} for r in data.available_regions()],
        "duration_presets": DURATION_PRESETS, "style_presets": STYLE_PRESETS,
        "companion_choices": COMPANION_CHOICES, "transport_combos": TRANSPORT_COMBOS,
        "vehicle_types": VEHICLE_TYPES, "fuels": FUELS, "fuel_prices": FUEL_PRICES, "fuel_units": FUEL_UNIT,
        "default_efficiency": DEFAULT_EFFICIENCY, "toll_discounts": list(TOLL_DISCOUNTS),
        "parking_discounts": list(PARKING_DISCOUNTS), "intensities": INTENSITIES, "interests": INTERESTS,
        "budget_presets": BUDGET_PRESETS, "expense_categories": EXPENSE_CATEGORIES, "max_nights": MAX_NIGHTS,
        "note": "연료 단가·연비·통행료는 2026년 기준 대략값(추정)이에요.",
    }
