"""채점: 답변 후처리와 정답 비교(A·B 경로 공용)  (bridgedocs.py에서 나눔)"""
import re


# ============================================================================
# 채점: 답변 후처리와 정답 비교(A·B 공용)  (이전: scoring.py)
# ============================================================================
# A/B 경로 공용 채점. 정확 일치 / 정규화 일치 / (구)포함 일치를 따로 센다 + 답변 후처리(clean) 적용본도 같이 센다.
#
# Reality Checker 지적 반영: '정답이 답에 들어 있으면 통과' 방식은 관대하므로 contains는 참고용으로만 두고,
# 금액은 첫 숫자 토큰으로 비교하며 틀리면 자릿수 배율(10배·100배)로 분류한다.
# clean()은 필드 '종류'와 답변 문자열만 보고 값을 추출한다(정답은 보지 않음).

REFUSAL = ("도와드릴 수 없", "제한적입니다", "답변할 수 없", "불가능합니다", "죄송")


def _digits(s):
    # 첫 숫자 토큰만 본다: 칸 가장자리 노이즈("(64,000,000) 9.2")를 이어 붙여 100배 오류로 오판하지 않도록.
    m = re.search(r"\d[\d,]*", s or "")
    return int(m.group().replace(",", "")) if m else None


NUM_RE = re.compile(r"\d+(?:\.\d+)?")
SHARE_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


def parse_owners(s):
    """'김재은(15/100), 최승우(85/100)' 또는 '김재은 15/100, 최승우 85/100' → [(이름, '15/100'), ...]. 지분이 없으면 ''."""
    out = []
    for part in re.split(r"[,，;、\n]|\s및\s", s or ""):
        m = SHARE_RE.search(part)
        name = re.sub(r"[^가-힣A-Za-z]", "", SHARE_RE.sub("", part))     # 주민번호 앞자리('4**') 같은 조각은 이름이 아니다
        if name:
            out.append((name, f"{m[1]}/{m[2]}" if m else ""))
    return out


def owners_text(pairs):
    """[(이름, 지분)] → 화면에 보일 값. 혼자 다 가진 경우(1/1)는 이름만."""
    if len(pairs) == 1 and pairs[0][1] in ("", "1/1"):
        return pairs[0][0]
    return ", ".join(f"{n}({s})" if s else n for n, s in pairs)


GRADE_DROP = re.compile(r"topik|level|band|score|급|점|\s|[()（）]", re.I)
CHOICES = ("자격증", "수료증", "성적증명서", "기타")
# 증명서 종류 낱말(소문자). 앞에 있는 종류가 우선: 성적표에도 '합격'·'certificate'가, 수료증에도 '인정'이 나온다
CHOICE_WORDS = ((("수료", "completion"), "수료증"), (("성적", "score", "report", "transcript"), "성적증명서"),
                (("자격", "합격", "인정", "certificate"), "자격증"))


SEAL_TAIL = re.compile(r"\s*[\[(（]([^\[\]()（）]{0,2})[\])）]?\s*$")


def strip_seal(a):
    """이름 뒤 도장 표시 조각을 뺀다(스캔 잡음으로 '[E]', '(제)'처럼 읽힘). 법인 표시 (주)·(재)는 남긴다."""
    m = SEAL_TAIL.search(a)
    return a[:m.start()] if m and m[1].strip() not in ("주", "재", "유", "사") and len(a) > m.end() - m.start() + 1 else a


def person_key(s):
    """외국어 이름 비교용: 글자만(대소문자·공백·가운뎃점 무시)."""
    return "".join(ch for ch in (s or "").casefold() if ch.isalpha())


def grade_key(s):
    """등급 비교용: 5급 = Level 5 → '5', 6.5 → '6.5'."""
    return GRADE_DROP.sub("", s or "").casefold()


def norm(kind, s):
    s = (s or "").strip()
    if kind == "amount":
        return _digits(s)
    if kind == "person":
        return person_key(s)
    if kind == "grade":
        return grade_key(s)
    if kind == "number":
        m = NUM_RE.search(s.replace(",", ""))
        return float(m.group()) if m else None
    if kind == "owners":
        return tuple((n, sh if sh != "1/1" else "") for n, sh in parse_owners(s))
    if kind == "date":
        g = re.findall(r"\d+", s)[:3]
        return tuple(int(x) for x in g) if len(g) == 3 else None
    if kind == "name":     # 이름은 한글만(칸 경계의 "(" 같은 인쇄 문자 제거). 정확 일치 지표는 그대로 엄격하다.
        return "".join(re.findall(r"[가-힣]", s))
    if kind == "masked":   # 마스킹 뒤쪽 ******은 OCR이 불안정(실측)하고 읽을 필요도 없어서 보이는 앞 8자(yymmdd-g)만 비교
        return re.sub(r"\s+", "", s)[:8]
    return re.sub(r"\s+", "", s)


def clean(kind, ans):
    """VLM 답변 후처리. 인용부호·접두어 제거, 거부 응답은 빈 값, 종류별로 값만 추출."""
    a = (ans or "").strip().strip("`*")
    a = re.sub(r"^(답|정답|Answer)\s*[:：]\s*", "", a)
    if any(w in a for w in REFUSAL):
        return ""
    if kind == "amount":
        first = re.search(r"\d[\d,]*\s*(?:억|천만|만|천)?(?:\s*\d[\d,]*\s*(?:억|천만|만|천)?)*", re.sub(r"\(.*?\)", "", a))
        if first and re.search(r"\d\s*(억|천만|만)", first.group()):      # '1억 2,000만원' → 120,000,000 (1원으로 만들지 않도록). 괄호 안 금액은 다시 쓴 것이라 뺀다
            total = 0
            for num, unit in re.findall(r"(\d[\d,]*)\s*(억|천만|만|천)?", first.group()):
                total += int(num.replace(",", "")) * {"억": 10**8, "천만": 10**7, "만": 10**4, "천": 10**3}.get(unit, 1)
            return f"{total:,}"
        m = re.search(r"\d[\d,]*", a)
        return m.group().rstrip(",") if m else a
    if kind == "date":
        m = re.search(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})", a)
        if m:
            return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}"
        ds = any_dates(a)      # 'March 12, 2025' 같은 답
        return f"{ds[0][0]}-{ds[0][1]:02d}-{ds[0][2]:02d}" if ds else a
    if kind == "choice":     # 영어로 답해도(Certificate of Completion) 세 종류 중 하나로
        low = a.casefold()
        for words, val in CHOICE_WORDS + ((("기타",), "기타"),):
            if any(w in low for w in words):
                return val
        return a
    if kind == "person":
        a = re.sub(r"^(성명|이름|Name)\s*[:：]?\s*", "", a, flags=re.I)
        return strip_seal(a).strip("\"“”'")
    if kind == "flag":
        return "있음" if "있" in a else "없음" if "없" in a else a
    if kind == "number":
        m = NUM_RE.search(a.replace(",", ""))
        return m.group() if m else a
    if kind == "owners":
        if a in ("없음", ""):
            return a
        a = re.sub(r"^(소유자|성명)\s*[:：]?\s*", "", a)
        return owners_text(parse_owners(a)) or a
    if kind == "name":
        q = re.search(r"[\"“'‘]([가-힣]{2,5})[\"”'’]", a)
        a = re.sub(r"^(성명|이름|임차인|임대인|매수인|매도인)\s*(은|는|:|：)?\s*", "", a)    # "성명: 원도윤" 같은 라벨 접두어 제거
        a = re.sub(r"\s*\(\s*인\s*\)\s*$", "", a)     # 서명란의 도장 표시 "방유준(인)" → "방유준" (괄호 있을 때만)
        return q[1] if q and len(a) > 6 else a.strip("\"“”'")
    if kind == "text":
        a = strip_seal(a)
        return re.sub(r"^[\[〔(]?\s*집합건물\s*[\]〕)]?\s*", "", a).strip()
    return a


# 함수 안에서만 쓰는 다른 기능의 이름: 맨 아래에서 불러온다(서로 부르는 기능끼리 순환해도 됨)
from Fuc.hallucination_check.hallucination_check import any_dates  # noqa: E402,F401
