"""정보 답변의 장소 목록 → 번호 카드 (2026-11-27 사용자 요청: '번호가 모두 1번, 밋밋함')."""
import re

from src.counsel import richtext

LLM_STYLE = """안녕하세요! 모모예요 🕊️

**순천·구례 추천 맛집**

1. **순천만전라도밥상** (순천)
   - 꼬막정식 전문 / 순천만 특정식
   - 08:00~20:00 (마지막 주문 19:00)
   - 연중무휴 / 0507-1489-2112

2. **금빈회관** (순천)
   - 떡갈비 전문
   - 11:00~20:40
   - 매주 화요일 휴무 / 061-744-5553

출처: 한국관광공사"""


def test_numbers_continue_across_blank_lines_and_sub_bullets():
    h = richtext.md_to_html(LLM_STYLE)
    assert re.findall(r'mm-num">(\d+)', h) == ["1", "2"]
    assert h.count('class="mm-card"') == 2
    assert '<h4 class="mm-head"><strong>순천·구례 추천 맛집</strong></h4>' in h


def test_rows_get_emoji_and_badges():
    h = richtext.md_to_html(LLM_STYLE)
    assert '<span class="mm-badge ok">연중무휴</span>' in h
    assert '<span class="mm-badge off">매주 화요일 휴무</span>' in h
    assert "📞" in h and "🕘" in h and "꼬막정식 전문 · 순천만 특정식" in h   # ' / '로 묶인 줄은 종류별로 나눔
    assert '<span class="mm-tag">순천</span>' in h


def test_repeated_one_numbers_are_renumbered_and_labels_used():
    h = richtext.md_to_html("1. **가**\n   - 운영: 09:00~18:00\n   - 휴무: 매주 화요일\n1. **나**\n   - 전화: 061-000-0000")
    assert re.findall(r'mm-num">(\d+)', h) == ["1", "2"]
    assert 'r-hours' in h and 'r-tel' in h and "운영:" not in h
    assert '<span class="mm-badge off">매주 화요일</span>' in h


def test_plain_numbered_list_stays_ol_and_html_is_escaped():
    h = richtext.md_to_html("1. 첫째 <b>x</b>\n2. 둘째")
    assert '<ol start="1">' in h and "mm-card" not in h
    assert "&lt;b&gt;" in h


# ---- 2차 평가 #18 회귀(2026-11-27): 출발지는 시·구 단위로 ----
from src.counsel import agent  # noqa: E402
from src.guardrails import guardrails  # noqa: E402


def test_departure_filled_to_gu_from_masked_question():
    q = guardrails.mask_pii("서울시 강남구 테헤란로 123 101동 1004호에서 출발해요. 제 번호 010-1234-5678로 일정 보내 주세요")[0]
    assert "테헤란로" not in q
    assert agent._dep_with_gu("서울", q) == "서울시 강남구"
    assert agent._dep_label(agent._dep_with_gu("서울", q)) == "서울시 강남구"


def test_departure_gu_only_from_same_sido_and_not_from_words():
    assert agent._dep_with_gu("부산", "서울 강남구에서 출발, 부산 해운대구 집") == "부산 해운대구"
    assert agent._dep_with_gu("서울", "서울 친구랑 출발") == "서울"
    assert agent._dep_with_gu("서울", "서울 중구에서 출발") == "서울 중구"
    assert agent._dep_with_gu("서울시 강남구", "서울시 강남구") == "서울시 강남구"
