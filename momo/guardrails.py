"""가드레일 (SERVICE.md 4, 4-1). 코드(정규식)로 먼저 막고 LLM 의도 분류는 보조.

- mask_pii: 입력 직후·출력 직전·trace 기록 전 모두 적용
- detect_injection: 직접 인젝션·키/프롬프트 요구
- check_output: 응답에 비밀·시스템 프롬프트 흔적이 있으면 차단
"""
from __future__ import annotations

import re

B, E = r"(?<![\d])", r"(?![\d])"   # 한글이 바로 붙어도 동작하도록 단어 경계 대신 숫자 경계 사용
PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("주민번호", re.compile(B + r"\d{6}\s*-\s*[1-8]\d{6}" + E)),
    ("카드번호", re.compile(B + r"(?:\d{4}[\s-]?){3}\d{4}" + E)),
    ("여권번호", re.compile(r"(?<![A-Za-z])[MSRODmsrod]\d{8}" + E)),
    ("운전면허번호", re.compile(B + r"\d{2}-\d{2}-\d{6}-\d{2}" + E)),
    ("전화번호", re.compile(r"(?:\+?82[\s-]?)?" + B + r"0\d{1,3}[\s.-]?\d{3,4}[\s.-]?\d{4}" + E)),
    ("계좌번호", re.compile(B + r"\d{2,6}-\d{2,6}-\d{2,7}(?:-\d{1,3})?" + E)),
    ("이메일", re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z.]{2,}")),
    # 상세 주소: 동·호, 도로명+번호, 번지 (차량번호보다 먼저)
    ("상세주소", re.compile(r"\d+\s*동\s*\d+\s*호")),
    ("상세주소", re.compile(r"[가-힣0-9]*[가-힣0-9](?<!으)(?<!에)(?:로|길)\s*\d+(?![,.\d])(?!\s*(?:박|일|시|분|명|곳|월|개|번째|살|세|인|km|킬로|시간|가지|군데|층|대|인분))(?:-\d+)?(?:\s*번길\s*\d+)?(?:\s*\d+동)?(?:\s*\d+호)?")),
    ("상세주소", re.compile(r"[가-힣]+동\s*\d+(?:-\d+)?\s*번지")),
    ("차량번호", re.compile(B + r"\d{2,3}\s?[가-힣]\s?\d{4}" + E)),
]


def _is_account(m: re.Match) -> bool:
    digits = re.sub(r"\D", "", m.group(0))
    return len(digits) >= 10 and not re.fullmatch(r"(19|20)\d{2}-\d{1,2}-\d{1,2}", m.group(0))


INJECTION = re.compile(
    r"(이전\s*(지시|명령|규칙).{0,6}(무시|잊)|지시(는|를)?\s*(다\s*)?무시|ignore\s+(all\s+|the\s+)?(previous|prior|above)|"
    r"system\s*prompt|시스템\s*프롬프트|개발자\s*모드|developer\s*mode|jailbreak|제한\s*없는\s*AI|"
    r"API\s*키|api[_\s-]?key|secret|환경\s*변수|\.env|AWS_[A-Z_]+|TOURAPI_SERVICE_KEY|토큰\s*(을|를)?\s*(보여|알려))",
    re.I)

SECRET_OUT = re.compile(r"(AKIA[0-9A-Z]{16}|AWS_SECRET|TOURAPI_SERVICE_KEY|LANGSMITH_API_KEY|VWORLD_API_KEY|"
                        r"lsv2_[A-Za-z0-9_]{10,}|너는 여행 상담 에이전트 '모모'다)")


def mask_pii(text: str, allow: set[str] | None = None) -> tuple[str, list[str]]:
    """(마스킹된 문자열, 발견한 종류 목록). allow: 가리지 않을 공개 연락처(관광공사 데이터의 업소 번호)."""
    if not text:
        return text, []
    found = []
    for name, pat in PII_PATTERNS:
        def repl(m: re.Match, name=name) -> str:
            if allow and re.sub(r"\D", "", m.group(0)) in allow:
                return m.group(0)
            if name == "계좌번호" and not _is_account(m):
                return m.group(0)
            found.append(name)
            return f"[{name}]"
        text = pat.sub(repl, text)
    return text, sorted(set(found))


def public_contacts() -> set[str]:
    """관광공사 수집 데이터에 공개된 업소 연락처(숫자만). 출력 단계에서만 허용."""
    global _PUBLIC
    if _PUBLIC is None:
        from . import data
        _PUBLIC = set()
        for p in data.all_places().values():
            intro = p.get("intro") or {}
            for v in (p.get("tel"), intro.get("infocenter"), intro.get("infocenterfood"), intro.get("infocenterlodging")):
                for m in re.finditer(r"0\d{1,3}[\s.)-]*\d{3,4}[\s.-]*\d{4}", v or ""):
                    _PUBLIC.add(re.sub(r"\D", "", m.group(0)))
    return _PUBLIC


_PUBLIC: set[str] | None = None


B64 = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")


def _decoded(text: str) -> list[str]:
    """base64로 감싼 우회 시도: 디코딩되는 토큰만 꺼낸다."""
    import base64
    out = []
    for tok in B64.findall(text or ""):
        try:
            out.append(base64.b64decode(tok + "=" * (-len(tok) % 4), validate=True).decode("utf-8"))
        except Exception:
            continue
    return out


def detect_injection(text: str) -> bool:
    """직접 인젝션: 원문·띄어쓰기 제거본(글자 사이 띄우기 우회)·base64 디코딩본을 모두 검사."""
    t = text or ""
    for cand in (re.sub(r"\s+", " ", t), re.sub(r"\s+", "", t), *_decoded(t)):
        if INJECTION.search(cand):
            return True
    return False


def safe_external(text: str, limit: int = 90) -> str:
    """간접 인젝션: 외부 데이터(장소 설명·이름)에 지시문이 보이면 프롬프트에 넣지 않는다."""
    return "(설명 생략: 지시문 의심)" if detect_injection(text) else (text or "")[:limit]


def check_output(text: str) -> bool:
    """True면 통과."""
    return not SECRET_OUT.search(text or "")


def mask_obj(obj):
    """trace 등 중첩 구조에 마스킹 적용."""
    if isinstance(obj, str):
        return mask_pii(obj)[0]
    if isinstance(obj, list):
        return [mask_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: mask_obj(v) for k, v in obj.items()}
    return obj
