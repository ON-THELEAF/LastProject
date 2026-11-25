"""환각검증: 값이 페이지에 있는지·라벨 줄 검사·크롭 재질문·두 번째 판독·이름 확대 재판독  (bridgedocs.py에서 나눔)"""
import difflib
import os
import re
import unicodedata
from datetime import date


from PIL import Image  # noqa: E402


from DB.forms import all_builtin_specs, builtin_forms, label_words  # noqa: E402
from Fuc.scoring.scoring import person_key  # noqa: E402,F401


# ───────── 위치(bbox) 유틸 ─────────


# ───────── 실행 모드 ─────────


# ───────── 좌표 없는 검증(check) + 라벨 줄 잘라서 재질문(crop) ─────────
# 3B가 낸 bbox는 쓸 수 없었다(중심 적중 21%). 그래서 위치 대신 "B가 낸 값이 페이지에 실제로 있고, 맞는 라벨 줄에 있는가"를 본다.
# 오류의 절반 이상이 '다른 줄 값 복사'(계약금 자리에 매매대금 등)였기 때문이다.
# 칸마다의 라벨·특별 검사는 코드가 아니라 양식(DB/기본 양식.json, 사용자 양식)의 칸 설정에 있다:
#   aliases(값 왼쪽 라벨) · last_row/first_date(가장 아래 '소유자' 줄의 첫 날짜) · flag_word(있음/없음을 확인할 낱말)
#   empty_ok('없음'이 맞을 수 있는 칸: 페이지에 이 낱말이 보이면 '없음'은 틀림) · need_on_page(값이 페이지에 그대로 있어야 함)
#   decoy_words(취득일과 헷갈리는 날짜 자리) · role_order(서명란 몇 번째 성명 줄) · id(번호: 영문·숫자만 비교)
#   latest_near(그 낱말 줄 근처의 가장 최근 날짜) · above_word(그 제목 줄보다 위의 값만) · last_payment(그 낱말 줄의 마지막 지급일)
VOCAB = label_words()
SPECS = all_builtin_specs()      # 옛 정답 파일처럼 칸 설정 없이 key만 올 때 쓰는 기본 양식 칸 설정
DATE_ANY = re.compile(r"(?<!\d)((?:19|20)\d{2})(?:\s*년\s*|\s*[.\-/]\s*|\s+)(\d{1,2})(?:\s*월\s*|\s*[.\-/]\s*|\s+)(\d{1,2})(?!\d)")
# 페이지 OCR 캐시 키에 들어간다: OCR 설정이 바뀌면 옛 캐시를 쓰지 않도록 올린다
OCR_DET = "PP-OCRv5_server_det" if os.environ.get("BD_OCR") == "server" else "PP-OCRv5_mobile_det"


def squash(s):
    return re.sub(r"\s+", "", s or "")


def spaced(s):
    """OCR이 글자 사이에 공백을 넣거나("잔 금") 빼는 것에 상관없이 찾도록 글자 사이에 \\s*를 끼운 패턴"""
    return r"\s*".join(re.escape(c) for c in squash(s))


def role_lines(lines):
    """'성명 + 이름'이 적힌 줄의 번호들(위에서 아래 순서). 대리인 줄은 뺀다: 대리인 이름이 적힌 문서에서
    매도인 대리인이 '두 번째 성명 줄'이 되어 매수인을 오탐했다(사용자 매매 샘플 u05, 2026-10-23)."""
    return [i for i, L in enumerate(lines) if re.search(r"성명\s*[가-힣]{2,}", L["text"]) and "대리인" not in squash(L["text"])]


def format_ok(kind, v):
    """값의 모양이 필드 종류에 맞는가(서식 불문). 문장·설명이 답으로 나오면 걸린다."""
    if kind == "name":
        return bool(re.fullmatch(r"[가-힣]{2,20}", squash(v)))      # 법인 소유자("주식회사…개발")도 있어 20자까지
    if kind == "date":
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v)
        return bool(m) and 1 <= int(m[2]) <= 12 and 1 <= int(m[3]) <= 31
    if kind == "amount":
        return bool(re.fullmatch(r"\d[\d,]*", v))
    if kind == "number":
        return bool(re.fullmatch(r"\d+(\.\d+)?", v))
    if kind == "person":
        return 2 <= len(person_key(v)) <= 60 and not re.search(r"\d", v)
    if kind == "choice":
        return v in CHOICES
    if kind in ("grade", "text") and ("\n" in v.strip() or len(v) > 80):     # 값 하나를 물었는데 문서 전체를 옮겨 적은 답
        return False
    if kind == "owners":
        pairs = parse_owners(v)
        return bool(pairs) and all(re.fullmatch(r"[가-힣]{2,20}", n) for n, _ in pairs) and share_sum_ok(pairs)
    return True


def share_sum_ok(pairs):
    """공유 지분은 합이 1이어야 한다(한 명뿐이고 지분이 없으면 통과)."""
    from fractions import Fraction
    if len(pairs) == 1 and not pairs[0][1]:
        return True
    if any(not s for _, s in pairs):
        return False
    try:
        return sum(Fraction(*map(int, s.split("/"))) for _, s in pairs) == 1
    except (ValueError, ZeroDivisionError):
        return False


def dates_in(text):
    """텍스트 안의 날짜 매치(월 1~12, 일 1~31만 인정)"""
    text = text.replace("_", " ")      # 양식의 밑줄 빈칸을 OCR이 '_'로 읽는다('_2025_년_6_월 _9_일'). 길이가 같아 위치는 그대로
    return [m for m in DATE_ANY.finditer(text) if 1 <= int(m[2]) <= 12 and 1 <= int(m[3]) <= 31]


EN_MON = {m[:3].lower(): i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                                   "September", "October", "November", "December"], 1)}
_MON = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
DATE_FORMS = [   # (패턴, 연·월·일 그룹 순서) — 한국식은 dates_in이 맡는다
    (re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r",?\s+((?:19|20)\d{2})", re.I), "dmy"),
    (re.compile(_MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+((?:19|20)\d{2})", re.I), "mdy"),
]


def any_dates(text):
    """텍스트 안의 날짜(한국어·영어 표기) → [(연, 월, 일, 시작 위치)]. 없는 날짜는 뺀다."""
    out = [(int(m[1]), int(m[2]), int(m[3]), m.start()) for m in dates_in(text)]
    for pat, order in DATE_FORMS:
        for m in pat.finditer(text):
            if order == "dmy":
                y, mo, d = int(m[3]), EN_MON[m[2][:3].lower()], int(m[1])
            else:
                y, mo, d = int(m[3]), EN_MON[m[1][:3].lower()], int(m[2])
            if 1 <= mo <= 12 and 1 <= d <= 31 and (y, mo, d) not in [o[:3] for o in out]:
                out.append((y, mo, d, m.start()))
    return out


def lang_field(page_lines_list, key, label):
    """'문서 언어' 칸(양식에서 computed: lang): 한글이 10자 이상이면 한국어, 아니면 영어."""
    text = " ".join(L["text"] for lines in page_lines_list for L in lines)
    code = "ko" if sum("가" <= c <= "힣" for c in text) >= 10 else "en"
    return {"key": key, "label": label, "kind": "text", "value": LANG_LABEL[code],
            "status": "auto", "reason": "", "candidates": [], "page": 1, "line_box": None, "compare": ""}


def occurrences(kind, v, lines):
    """페이지 줄에서 값 v가 나오는 곳 -> [(줄 번호, 줄 안 시작 위치)]. 줄 번호 -1 = 한 줄에서는 못 찾았지만 페이지 전체에는 있음(여러 줄에 걸친 긴 값)"""
    out = []
    if kind == "owners":      # 첫 소유자 이름의 위치(상자 표시용). 모든 이름 확인은 check_field가 한다
        pairs = parse_owners(v)
        return occurrences("name", pairs[0][0], lines) if pairs else []
    if kind == "number":
        want = norm("number", v)
        for i, L in enumerate(lines):
            out += [(i, m.start()) for m in NUM_RE.finditer(L["text"].replace(",", "")) if want is not None and float(m.group()) == want]
        return out
    if kind == "amount":
        want = norm("amount", v)
        for i, L in enumerate(lines):
            for m in re.finditer(r"\d[\d,]*", L["text"]):
                if want is not None and int(m.group().replace(",", "") or 0) == want:
                    out.append((i, m.start()))
    elif kind == "date":
        want = norm("date", v)
        for i, L in enumerate(lines):
            out += [(i, pos) for y, mo, d, pos in any_dates(L["text"]) if (y, mo, d) == want]
    elif kind == "grade":     # 등급은 낱말 단위로(증명서 번호 'N2…' 안의 '1급'을 등급으로 세지 않도록). 5급 = Level 5
        key = norm(kind, v)
        out += [(i, 0) for i, L in enumerate(lines) if key and any(grade_key(t) == key for t in re.split(r"[\s:：,/|]+", L["text"]))]
    elif kind == "person":    # 고유 이름: 글자만 비교(대소문자·공백 무시). 거친 스캔에서 OCR이 한 글자쯤 흐리게 읽어도 찾은 것으로 본다
        key = norm(kind, v)
        out += [(i, 0) for i, L in enumerate(lines) if key and key in norm(kind, L["text"])]
    else:      # name / text: OCR 공백 차이를 무시하고 찾는다
        h = "".join(re.findall(r"[가-힣]", v)) if kind == "name" else squash(v)
        if len(h) >= 2 if kind == "name" else h:
            pat = re.compile("(?=" + spaced(h) + ")")
            for i, L in enumerate(lines):
                out += [(i, m.start()) for m in pat.finditer(L["text"])]
            if not out and kind != "name" and h in squash(" ".join(L["text"] for L in lines)):
                out.append((-1, 0))
            if not out and kind != "name":     # 표 칸 안에서 줄이 바뀐 값('숙박시설(생활' / '숙박시설)'): 앞부분은 한 줄, 뒷부분은 다음 줄에
                sq = [squash(L["text"]) for L in lines]
                if any(h[:k] in sq[i] and h[k:] in sq[i + 1] for i in range(len(sq) - 1) for k in range(2, len(h) - 1)):
                    out.append((-1, 0))
    return out


NAME_PREFIX = ("성명", "이름", "name", "소지자", "수험자", "수료자")
NAME_SUFFIX = ("님", "귀하", "(인)")
KO_PARTICLE = re.compile(r"(에|을|를|이|가|의|은|는|과|와|으로|로|에서)(?![가-힣a-z])")


def standalone(kind, v, lines):
    """값이 어느 줄에서든 따로 선 낱말로 보이면 True. '김민' ⊂ '김민준', '정보처리' ⊂ '정보처리기사'처럼 더 긴 낱말 안에만 있으면 False."""
    if kind == "name":
        chars = re.findall(r"[가-힣]", v)
    else:
        chars = list(person_key(v))
    pat = re.compile(r"\W*".join(re.escape(c) for c in chars))
    labels = NAME_PREFIX + tuple(a.casefold() for a in VOCAB)
    for L in lines:
        t = L["text"].casefold()
        for m in pat.finditer(t):
            a, b = m.start(), m.end()
            left_ok = a == 0 or not t[a - 1].isalpha() or squash(t[:a]).endswith(labels)
            right_ok = (b == len(t) or not t[b].isalpha() or t[b:].startswith(NAME_SUFFIX)
                        or (re.match(r"[가-힣]", t[b - 1]) and KO_PARTICLE.match(t, b)))          # …능력에 합격
            if left_ok and right_ok:
                return True
    return False


def governing(text, pos):
    """줄 안에서 값 위치(pos) 바로 왼쪽에 있는 라벨 -> (라벨, 라벨 끝 위치)"""
    best, bend = None, -1
    for a in VOCAB:
        for m in re.finditer(spaced(a), text):
            if bend < m.end() <= pos:
                best, bend = a, m.end()
    return best, bend


def check_field(kind, key, value, lines, spec=None):
    """-> dict(exists, state, label_ok(None=라벨 없음/해당 없음), kw(키워드로 확인함), ys(성명 줄 y), reason)
    state: pass / contradict(그 자리가 읽혔는데 값이 다름·답이 없음) / unknown(OCR이 그 부분을 못 읽어 판단 불가)
    spec: 양식의 칸 설정."""
    spec = spec or {}
    v = clean(kind, value)

    def out(exists, state, label_ok=None, ys=None, reason="", kw=False, suggest=None):
        return {"exists": exists, "state": state, "label_ok": label_ok, "ys": ys, "reason": reason, "kw": kw, "suggest": suggest}

    if kind == "flag":
        word = spec.get("flag_word")
        if not word:
            return out(False, "unknown", reason="있음/없음을 확인할 낱말이 양식에 없음")
        expect = word in squash(" ".join(L["text"] for L in lines))
        ok = ("있" in v) == expect
        return out(ok, "pass" if ok else "contradict", reason=f"{word} 키워드 " + ("있음" if expect else "없음"), kw=True)
    if kind == "choice":
        if not format_ok(kind, v):
            return out(False, "contradict", reason="문서 종류를 고르지 못함: " + v[:20])
        # 위에서부터 처음 종류 낱말이 나온 줄(대개 제목)로 정한다. 칸 이름('합격연월일')에 끌려가지 않도록
        seen = next(([val for words, val in CHOICE_WORDS if any(w in sq for w in words)] for sq in (squash(L["text"]).casefold() for L in lines)
                     if any(w in sq for words, _ in CHOICE_WORDS for w in words)), [])
        if not seen:
            return out(False, "unknown", reason="페이지에서 증명서 종류 낱말을 못 찾음")
        ok = v == seen[0]
        return out(ok, "pass" if ok else "contradict", kw=True, reason="" if ok else f"페이지 낱말로는 {seen[0]}", suggest=None if ok else seen[0])
    if v in ("", "없음"):
        if spec.get("empty_ok"):
            words = spec["empty_ok"]
            has = next((w for w in words if w.casefold() in squash(" ".join(L["text"] for L in lines)).casefold()), None)
            return out(not has, "contradict" if has else "pass", reason=f"'없음'인데 페이지에 '{has}' 칸이 있음" if has else "", kw=True)
        return out(False, "contradict", reason="빈 값(B가 답하지 못함)")
    if not format_ok(kind, v):
        why = "공유 지분 합이 1이 아님" if kind == "owners" and parse_owners(v) else "형식 불일치(" + kind + "): " + v[:20]
        suggest = None
        if kind == "owners" and len(parse_owners(v)) > 1:
            # 모델이 공동 소유자의 지분을 빼먹는 일이 잦다(합성 15/100·85/100). 페이지의 지분 칸 수가 이름 수와 같으면 위에서부터 짝지어 후보로
            shares = [f"{m[1]}/{m[2]}" for L in lines for m in SHARE_RE.finditer(L["text"]) if int(m[2]) and int(m[1]) <= int(m[2])]
            names = [n for n, _ in parse_owners(v)]
            if len(shares) == len(names) and share_sum_ok(list(zip(names, shares))):
                suggest = owners_text(list(zip(names, shares)))
        return out(False, "contradict", reason=why, suggest=suggest)
    if kind == "owners":
        pairs = parse_owners(v)
        missing = [n for n, _ in pairs if not occurrences("name", n, lines)]
        if missing:
            return out(False, "unknown", reason="페이지에서 못 찾은 소유자: " + ", ".join(missing))
        # 소유자현황의 지분 칸 수보다 적게 답했으면 공동 소유자를 빠뜨린 것이다
        shares = [m for L in lines for m in SHARE_RE.finditer(L["text"]) if int(m[2]) and int(m[1]) <= int(m[2])]
        if len(shares) > len(pairs):
            return out(True, "contradict", reason=f"지분 칸이 {len(shares)}개인데 소유자는 {len(pairs)}명(공동 소유자 누락?)")
        for n, sh in pairs:
            if not sh:
                continue
            near = [f"{m[1]}/{m[2]}" for i, _ in occurrences("name", n, lines) for L in lines[i:i + 2] for m in SHARE_RE.finditer(L["text"])]
            if near and sh not in near:
                return out(True, "contradict", reason=f"{n}의 지분이 페이지({near[0]})와 다름")
        return out(True, "pass", label_ok=None)
    occ = occurrences(kind, v, lines)
    if not occ and kind == "date" and spec.get("need_on_page"):
        # 월·일은 맞고 연도만 다른 날짜가 페이지에 있으면 연도만 잘못 읽은 것이다.
        # 증명서만: 임대차는 시작일·종료일의 월·일이 같아서 오탐한다(합성 임대차 s07, 2026-10-25)
        y, mo, d = norm("date", v)
        near = [f"{yy}-{mm:02d}-{dd:02d}" for L in lines for yy, mm, dd, _ in any_dates(L["text"]) if (mm, dd) == (mo, d) and yy != y]
        if near:
            return out(False, "contradict", reason=f"페이지에는 연도가 다른 같은 날짜가 있음({near[0]})", suggest=near[0])
    if not occ and spec.get("need_on_page"):
        return out(False, "contradict", reason="페이지에서 값을 못 찾음(증명서 칸은 페이지에 그대로 있어야 함)")
    if spec.get("id") and occ:
        pat = re.compile(r"(?<![0-9A-Za-z])" + spaced(squash(v)) + r"(?![0-9A-Za-z])")
        if not any(pat.search(lines[i]["text"]) for i, _ in occ if i >= 0):
            return out(True, "contradict", reason="더 긴 번호의 일부로만 보임(값이 잘렸을 수 있음)")
    if kind in ("name", "person") and any(i >= 0 for i, _ in occ) and not standalone(kind, v, lines):
        return out(True, "contradict", reason="더 긴 이름의 일부로만 보임(값이 잘렸을 수 있음)")
    if spec.get("latest_near") and occ:
        # 소유자현황의 변동일자: '소유권…' 줄이나 그 바로 위 줄(이름·지분·날짜 줄)에 있어야 하고, 여러 명이면 가장 최근 날짜
        sq = [squash(L["text"]) for L in lines]
        near = [i for i in range(len(lines)) if any(spec["latest_near"] in x for x in sq[i:i + 4])]      # 표 칸 줄바꿈으로 '소유권이전'이 2~3줄 아래
        if near:
            occ = [o for o in occ if o[0] in near]
            ds = [(y, mo, d) for i in near for y, mo, d, _ in any_dates(lines[i]["text"])]
            if occ and norm("date", v) < max(ds):
                y, mo, d = max(ds)
                return out(True, "contradict", reason="가장 최근 변동일이 아님", suggest=f"{y}-{mo:02d}-{d:02d}")
    if spec.get("above_word") and occ:
        # 전유부분 면적: '공용부분' 제목 줄보다 위에 있어야 한다(아래는 계단실·주차장 같은 공용부분 면적)
        head = next((i for i, L in enumerate(lines) if spec["above_word"] in squash(L["text"])), None)
        if head is not None:
            occ = [o for o in occ if 0 <= o[0] < head]
    if spec.get("decoy_words") and occ and all(i >= 0 and any(w in squash(lines[i]["text"]).casefold() for w in spec["decoy_words"]) for i, _ in occ):
        return out(True, "contradict", label_ok=False, reason="생년월일·발급일 자리의 날짜(취득일이 아닐 수 있음)")
    if spec.get("last_row"):
        idx = [i for i, L in enumerate(lines) if spec["last_row"] in squash(L["text"])]
        last = idx[-1] if idx else None
        occ = [o for o in occ if o[0] == last]
        if spec.get("first_date"):      # 같은 줄에 날짜가 둘이면 첫 번째가 접수일
            ds = dates_in(lines[last]["text"]) if last is not None else []
            if ds:
                occ = [o for o in occ if o[1] == ds[0].start()]
                if not occ:
                    return out(False, "contradict", reason="소유자 줄의 첫 날짜(접수일)와 다름")
    if not occ:
        return out(False, "unknown", reason="페이지에서 값을 못 찾음")
    if spec.get("last_payment"):
        # 잔금은 마지막 지급이다: '지불' 줄에 더 늦은 날짜가 있으면 중도금 날짜를 옮겨 쓴 것으로 본다.
        # 라벨 칸('잔 금')을 OCR이 못 읽는 양식에서 이 오류를 놓쳤다(사용자 매매 샘플, 2026-10-23).
        pays = [tuple(int(x) for x in m.groups()) for L in lines if spec["last_payment"] in squash(L["text"]) for m in DATE_ANY.finditer(L["text"])]
        mine = tuple(int(x) for x in re.findall(r"\d+", v)[:3])
        if pays and mine < max(pays):
            return out(True, "pass", label_ok=False, reason="잔금일보다 늦은 지급일이 있음(잔금은 마지막 지급)")
    if all(i < 0 for i, _ in occ):
        return out(True, "pass")      # 여러 줄에 걸친 값: 존재만 확인
    al = spec.get("aliases")
    if not al:
        return out(True, "pass")
    gov, good, trunc = [], [], False
    for i, p in occ:
        if i < 0:
            continue
        g, e = governing(lines[i]["text"], p)
        gov.append(g)
        if g in al:
            # 한글로 시작하는 text 값은 라벨 바로 뒤에서 시작해야 한다: 사이에 글자가 있으면 값 앞부분이 잘린 것일 수 있다("국민은행" vs "주식회사국민은행")
            if kind == "text" and re.match(r"[가-힣]", squash(v)) and squash(lines[i]["text"][e:p]):
                trunc = True
            else:
                good.append(i)
    if any(g is None for g in gov) and not good:
        return out(True, "pass", reason="왼쪽에 라벨 없음")
    order = spec.get("role_order")
    if order is not None and good:      # 서명란 순서: 이 이름이 자기 역할 줄(매도인=첫째, 매수인=둘째)에 있어야 한다
        rl = role_lines(lines)
        want = rl[order] if order < len(rl) else None
        if want is not None and want not in good:
            return out(True, "pass", label_ok=False, reason=f"서명란 순서: {order + 1}번째 성명 줄에 없음")
    return out(True, "pass", label_ok=bool(good), ys=min((lines[i]["cy"] for i in good), default=None),
               reason="" if good else ("라벨과 값 사이에 다른 글자(값이 잘렸을 수 있음)" if trunc else "왼쪽 라벨=" + "/".join(sorted({str(g) for g in gov}))))


def score_ok(g, rng):
    """등급·점수 g가 시험의 범위(min~max, step 단위) 안인가."""
    try:
        x = float(g)
    except ValueError:
        return False
    steps = (x - rng["min"]) / rng["step"]
    return rng["min"] <= x <= rng["max"] and abs(steps - round(steps)) < 1e-9


def cross_pairs(rules, vals, ys, kinds=None):
    """칸 사이 규칙(양식의 rules). 어긋나면 [(칸a, 칸b, 사유)] — 어느 쪽이 틀렸는지는 arbitrate()가 가린다.
    규칙: less(a < b여야 함) · differ(keys의 값이 서로 달라야 함) · order(a의 서명 줄이 b보다 위) · not_future(a가 오늘보다 뒤가 아님)
    · score_range(title 칸에 시험 이름이 있으면 a 칸의 점수가 그 시험 범위 안)."""
    kinds = kinds or {}

    def kind(k):
        return kinds.get(k, "text")

    p = []
    for rule in rules or []:
        t, a, b = rule["type"], rule.get("a"), rule.get("b")
        if t == "less":
            x, y = norm(kind(a), vals.get(a, "")), norm(kind(b), vals.get(b, ""))
            if x and y and x >= y:
                p.append((a, b, rule["why"]))
        elif t == "differ":
            keys = [k for k in rule["keys"] if vals.get(k) not in (None, "", "없음")]
            nk = kind(rule["keys"][0])
            for i, x in enumerate(keys):
                for y in keys[i + 1:]:
                    if norm(nk, vals[x]) == norm(nk, vals[y]):
                        p.append((x, y, rule["why"]))
        elif t == "order":
            sy, by = ys.get(a), ys.get(b)
            if sy is not None and by is not None and sy >= by:
                p.append((a, b, rule["why"]))
        elif t == "not_future":
            d = norm("date", vals.get(a, ""))
            if d and d > tuple(date.today().timetuple()[:3]):
                p.append((a, a, rule["why"]))
        elif t == "score_range":
            title, g = (vals.get(rule["title"]) or "").casefold(), grade_key(vals.get(a, ""))
            for rng in rule["ranges"]:
                if re.search(re.escape(rng["word"].casefold()) + r"(?![a-z])", title) and g and g != grade_key("없음") and not score_ok(g, rng):
                    p.append((a, a, rng["why"]))
    return p


def arbitrate(pairs, own):
    """교차 규칙은 '적어도 한쪽이 틀렸다'는 제약일 뿐이다. 한쪽에만 단독 증거(라벨 위반·크롭 불일치 등)가 있으면 그쪽만, 아니면 둘 다 표시한다."""
    flagged = {}
    for a, b, why in pairs:
        ea, eb = own.get(a, False), own.get(b, False)
        blamed = (a,) if ea and not eb else (b,) if eb and not ea else (a, b)
        for k in blamed:
            flagged[k] = why + ("" if len(blamed) == 1 else " (어느 쪽인지 불명: 둘 다 표시)")
    return flagged


# ───────── 두 번째 판독(OCR 줄 없이 다시 묻기) ─────────
# OCR이 틀리게 읽은 글자를 B가 그대로 따라 쓰면(롯데캐슬→못데캐슬) 규칙 검사로 못 잡는다. 이미지로만 한 번 더 읽어 비교한다.
# 종합설계 요약 3번 ②: 모든 칸을 OCR 없이 한 번 더 읽어 비교한다(2026-11-25 요약에 맞춤. 전에는 주소 칸만).
# - 이름이 아닌 칸: 두 답이 다르면 사람 확인(주소: 확인용 샘플에서 오류 검출 4/7→6/7, 2026-10-24)
# - 이름: 자모 하나만 다를 때(한 획 정도: 문소율/문소을)만 사용자 판별. 아예 다른 이름은 이 규칙으로는 경고하지 않는다
#   (OCR 줄 없이 읽은 이름은 다른 사람 이름을 고르는 경우가 많아 전부 경고하면 오탐이 너무 많았다). 이름은 ③ 확대 재판독이 맡는다
NAME_KINDS = ("name", "person", "owners")
# 종합설계 요약 3번 ③: 확대해 다시 읽는 칸은 양식의 칸 설정 zoom으로 정한다
#   zoom: "number"(번호·등급·날짜: 확대해 다시 읽은 값과 한 글자라도 다르면 사용자 확인) | "name"(이름·기관명)
#   이름 칸(name·owners)은 따로 적지 않아도 확대한다. 시험·과정 이름처럼 긴 값은 OCR 조각 하나에 안 담겨 하지 않는다


def zooms_name(spec):
    return spec.get("zoom") == "name" or spec.get("kind") in ("name", "owners")
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"


def jamo(s):
    """한글 음절을 초성·중성·종성으로 풀어 쓴 문자열(받침 없음은 빼고). 그 밖의 글자는 그대로."""
    out = []
    for ch in s:
        c = ord(ch) - 0xAC00
        if 0 <= c < 11172:
            out += [_CHO[c // 588], _JUNG[c % 588 // 28]] + ([_JONG[c % 28]] if c % 28 else [])
        else:
            out.append(ch)
    return "".join(out)


def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def second_read_issue(key, kind, ans, ans2, strict=False, spec=None):
    """두 번째 판독과 비교해 사람 확인이 필요하면 사유 문자열, 아니면 ''.
    strict: 두 번 읽기(joint) 방식 — 두 값이 다르기만 하면 사용자 판단(2026-10-28 사용자 결정)."""
    if ans2 is None or ans is None:
        return ""
    a, b = norm(kind, clean(kind, ans)), norm(kind, clean(kind, ans2))
    if not a or not b or a == b:
        return ""
    if strict:
        return f"두 번 읽은 값이 다름({clean(kind, ans)[:20]} / {clean(kind, ans2)[:20]})"
    if kind not in NAME_KINDS:
        if kind != "flag" and clean(kind, ans2).strip() in ("", "없음"):
            return ""       # 다시 읽기에서 값을 못 찾은 것은 반대 증거가 아니다
        if isinstance(a, str) and isinstance(b, str) and squash(unicodedata.normalize("NFKC", a)) == squash(unicodedata.normalize("NFKC", b)):
            return ""       # 모양만 다른 같은 글자(Ⅰ/I, 전각 숫자, 띄어쓰기)
        if (spec or {}).get("id") and alnum_key(a) == alnum_key(b):
            return ""
        return f"OCR 없이 다시 읽은 값과 다름({clean(kind, ans2)[:30]})"
    if kind == "name" and len(a) == len(b) and 1 <= edit_distance(jamo(a), jamo(b)) <= 3:
        return f"이름 {'한 획' if edit_distance(jamo(a), jamo(b)) == 1 else '몇 획'} 차이 — 사용자 확인({a} / {b})"
    if kind == "person":      # 한글 이름은 한 획 차이만(외국어 이름은 ③ 확대 재판독이 맡는다)
        if re.fullmatch(r"[가-힣]+", a) and re.fullmatch(r"[가-힣]+", b):
            d = edit_distance(jamo(a), jamo(b))
            return f"이름 {'한 획' if d == 1 else '몇 획'} 차이 — 사용자 확인({a} / {b})" if len(a) == len(b) and 1 <= d <= 3 else ""
        return ""
    if kind == "owners" and len(a) == len(b):
        d = sum(edit_distance(jamo(x[0]), jamo(y[0])) for x, y in zip(a, b))
        if d == 1:
            return f"소유자 이름 한 획 차이 — 사용자 확인({owners_text(a)} / {owners_text(b)})"
    return ""


# ───────── 이름 칸 확대 재판독 ─────────
# OCR이 한 획 틀리게 읽은 이름(굿타임→곳타임)을 B가 그대로 따라 쓰면 규칙으로 못 잡는다. OCR 없이 페이지 전체를 다시 묻는 방식은
# 이름 자리에 주소를 답하는 일이 잦았다(실제 건축물대장 5건, 2026-10-25). 그 이름이 적힌 OCR 조각만 3배로 키워 글자를 옮겨 적게 하면 맞게 읽었다.
ZOOM_Q = "이 이미지에 적힌 글자를 한 글자씩 그대로 옮겨 적으세요. 설명 없이 글자만 답하세요."


def best_window(v, t):
    """t 안에서 v와 자모가 가장 가까운, v와 같은 길이의 조각(확대한 칸에 옆 글자가 같이 찍혀도 이름 부분만 비교)"""
    if len(t) <= len(v):
        return t
    return min((t[i:i + len(v)] for i in range(len(t) - len(v) + 1)), key=lambda w: edit_distance(jamo(v), jamo(w)))


def alnum_key(s):
    """번호 비교용: 영문·숫자만(대소문자·기호 무시)."""
    return re.sub(r"[^0-9a-z]", "", (s or "").casefold())


def zoom_box(lines, name, keyf=person_key):
    """값과 가장 비슷한 OCR 조각의 상자. 비슷한 조각이 없으면 None."""
    parts = [p for L in lines for p in L.get("parts", [])]
    score = lambda p: difflib.SequenceMatcher(None, name, keyf(p["text"])).ratio()
    best = max(parts, key=score, default=None)
    return best if best and score(best) >= 0.5 else None


def zoom_raw(model, proc, img, part, pad=8, scale=3):
    """OCR 조각 상자만 잘라 3배로 키워 글자를 옮겨 적게 한 답(원문)."""
    box = (max(0, int(part["x0"]) - pad), max(0, int(part["y0"]) - pad), min(img.width, int(part["x1"]) + pad), min(img.height, int(part["y1"]) + pad))
    crop = img.crop(box)
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    return ask(model, proc, crop, ZOOM_Q, max_new=48)[0]


def zoom_number(model, proc, img, lines, kind, value):
    """숫자 칸(증명서 번호·등급·취득일)의 확대 재판독 값. OCR이 숫자 하나를 잘못 읽고(885→865) AI가 따라 쓴 경우를 잡는다.
    칸을 못 찾거나 다시 읽은 글자에서 비교할 값을 못 뽑으면 None(판단 보류)."""
    parts = [q for L in lines for q in L.get("parts", [])]
    if kind == "date":
        want = norm("date", value)
        part = next((q for q in parts if any(d[:3] == want for d in any_dates(q["text"]))), None)
        if part is None:
            return None
        ds = any_dates(zoom_raw(model, proc, img, part))
        got = next((d for d in ds if d[:3] == want), ds[0] if ds else None)
        return f"{got[0]}-{got[1]:02d}-{got[2]:02d}" if got else None
    if spec["kind"] == "grade":
        want = grade_key(value)
        part = next((q for q in parts if any(grade_key(t) == want for t in re.split(r"[\s:：,/|]+", q["text"]))), None)
        if part is None:
            return None
        toks = [t for t in re.split(r"[\s:：,/|]+", zoom_raw(model, proc, img, part)) if re.search(r"\d", t)]
        return value if any(grade_key(t) == want for t in toks) else (toks[0] if toks else None)
    k = alnum_key(value)          # 번호(id)
    part = zoom_box(lines, k, alnum_key)
    if part is None or len(k) < 4:
        return None
    t = alnum_key(zoom_raw(model, proc, img, part))
    w = best_window(k, t)
    return w if t and edit_distance(k, w) <= 2 else None      # 많이 다르면 엉뚱한 칸을 자른 것 — 판단 보류


def _zoom_word(model, proc, img, lines, word):
    """이름 하나를 OCR 조각에서 찾아 확대해 다시 읽은 값(person_key 꼴). 못 찾거나 많이 다르면 None(판단 보류)."""
    k = person_key(word)
    part = zoom_box(lines, k) if len(k) >= 2 else None
    if part is None:
        return None
    t = person_key(zoom_raw(model, proc, img, part))
    w = best_window(k, t)
    return w if t and edit_distance(jamo(k), jamo(w)) <= 3 else None


def zoom_name(model, proc, img, lines, kind, value):
    """이름·기관명 칸 확대 재판독 값(종합설계 요약 3번 ③). 소유자 여럿이면 이름마다 확대하고 지분은 그대로."""
    if kind != "owners":
        return _zoom_word(model, proc, img, lines, value)
    got = []
    for name, share in parse_owners(value):
        z = _zoom_word(model, proc, img, lines, name)
        if z is None:
            return None
        got.append((z, share))
    return owners_text(got) if got else None


def _name_keys(kind, s):
    return [person_key(n) for n, _ in parse_owners(s)] if kind == "owners" else [person_key(re.sub(r"[(（]\s*(주|유|사)\s*[)）]", "", s))]


def _same_name(a, z):
    """확대한 조각에 이름 일부만 찍혀도(앞뒤가 잘림) 같은 것으로 본다. 글자가 바뀐 것(굿/곳)만 다름."""
    return a == z or len(z) >= 2 and (z in a or a in z)


def zoom_issue(key, kind, ans, z, spec=None):
    """확대해 다시 읽은 값(z)과 다르면 사유, 아니면 ''."""
    if z is None or ans is None:
        return ""
    spec = spec or {}
    if spec.get("zoom") == "number":
        a, b = norm(kind, clean(kind, ans)), norm(kind, clean(kind, z))
        x, y = (alnum_key(a), alnum_key(b)) if spec.get("id") else (a, b)
        return f"확대해 다시 읽은 값과 다름({clean(kind, z)[:20]})" if a and b and x != y else ""
    a, b = _name_keys(kind, clean(kind, ans)), _name_keys(kind, z)
    same = len(a) == len(b) and all(_same_name(x, y) for x, y in zip(a, b))
    return "" if same else f"확대해 다시 읽은 이름과 다름({z[:20]})"


def compute_checks(gt, docs, answers, cache, crops=None):
    """-> {(doc,key): dict(b_ok, value, l1, l2, l3, lc, pol, status, reason, ...)}
    l1·l2·l3·lc는 이전과 같은 정의(교차 규칙은 짝 둘 다 표시)라 이전 수치와 비교할 수 있다.
    pol = '사람이 봐야 하는 문항' 정책: 라벨 위반 · 값이 있어야 할 자리에서 다른 값(contradict) · 크롭 불일치 · 책임 가린 교차 규칙 · 값을 페이지에서 못 찾음.
    status = 검토 / 검증통과(크롭 일치 · 맞는 라벨 · 키워드 확인 중 하나) / 미검증통과(확인 수단이 없었음)"""
    crops = crops or {}
    res = {}
    for name in docs:
        doc, vals, ys, per, sps = gt[name], {}, {}, {}, {}
        for key, f in doc["fields"].items():
            r = answers.get((name, key))
            if f["b_skip"] or r is None:
                continue
            sp = sps[key] = f.get("spec") or SPECS.get(key, {})   # 옛 정답 파일은 칸 설정 없이 key만 온다
            c = check_field(f["kind"], key, r["ans"], cache[doc["pages"][f["page"] - 1]], sp)
            if (f["kind"] in NAME_KINDS and c["reason"].startswith("페이지에서 값을 못 찾음")
                    and (r.get("zoom") and not zoom_issue(key, f["kind"], r["ans"], r["zoom"], sp)
                         or r.get("strict2") and r.get("ans2")
                         and norm(f["kind"], clean(f["kind"], r["ans2"])) == norm(f["kind"], clean(f["kind"], r["ans"])))):
                # OCR이 흐리게 읽어 페이지 글자에 없어도, 칸을 확대해 따로 읽은 값이 같으면 인쇄된 값으로 본다
                c = {**c, "exists": True, "state": "pass", "reason": ""}
            vals[key], per[key] = clean(f["kind"], r["ans"]), c
            if c["ys"] is not None:
                ys[key] = c["ys"]
        rules = doc["rules"] if "rules" in doc else builtin_forms().get(doc["doc_type"], {}).get("rules", [])
        pairs = cross_pairs(rules, vals, ys, {k: f["kind"] for k, f in doc["fields"].items()})
        old = {k for a, b, _ in pairs for k in (a, b)}
        # 크롭 답이 다를 때, 그 크롭 답이 페이지 OCR에서도 보일 때만 경고한다(발표 7장의 오탐 줄이기 계획).
        # Qwen3-VL-4B는 이름 크롭에 '성명'·주소·한 글자 오독을 자주 답해 정답을 오탐했다(사용자 매매 샘플, 2026-10-23).
        def crop_disagrees(k):
            cm = crops.get((name, k), {})
            if "agree" not in cm or cm["agree"]:
                return False
            f = doc["fields"][k]
            seen = cm["crop"] not in VOCAB and format_ok(f["kind"], cm["crop"]) and occurrences(f["kind"], cm["crop"], cache[doc["pages"][f["page"] - 1]])
            return bool(seen)
        crop_bad = {k: crop_disagrees(k) for k in per}
        second = {k: zoom_issue(k, doc["fields"][k]["kind"], answers[(name, k)].get("ans"), answers[(name, k)].get("zoom"), sps[k])
                  or second_read_issue(k, doc["fields"][k]["kind"], answers[(name, k)].get("ans"), answers[(name, k)].get("ans2"),
                                       strict=answers[(name, k)].get("strict2", False), spec=sps[k]) for k in per}
        # '페이지에서 값을 못 찾음'(unknown)도 사람 확인: 예전엔 OCR이 놓쳤을 수 있다고 통과시켰지만, 오류 주입 시험(2026-10-25)에서
        # 한 글자 틀린 금액·날짜·주소·이름이 모두 이 길로 자동 확정됐다(부동산 20건 중 13건 놓침)
        own = {k: c["label_ok"] is False or c["state"] in ("contradict", "unknown") or crop_bad[k] or bool(second[k]) for k, c in per.items()}
        cf = arbitrate(pairs, own)
        for key, c in per.items():
            f = doc["fields"][key]
            cm = crops.get((name, key), {})
            l1 = not c["exists"]
            l2 = l1 or c["label_ok"] is False
            l3 = l2 or key in old
            pol = own[key] or key in cf
            confirmed = cm.get("agree") is True or c["label_ok"] is True or (c["kw"] and c["state"] == "pass")
            res[(name, key)] = {"b_ok": answers[(name, key)]["norm_c"], "value": vals[key], "l1": l1, "l2": l2, "l3": l3,
                                "lc": l3 or crop_bad[key], "pol": pol, "status": "검토" if pol else "검증통과" if confirmed else "미검증통과",
                                "state": c["state"], "reason": c["reason"] or cf.get(key, "") or ("크롭 재질문 불일치" if crop_bad[key] else "") or second[key],
                                "kind": f["kind"], "gt": f["value"], "suggest": c.get("suggest")}
    return res


# 함수 안에서만 쓰는 다른 기능의 이름: 맨 아래에서 불러온다(서로 부르는 기능끼리 순환해도 됨)
from Fuc.path_b_vlm.path_b_vlm import ask  # noqa: E402,F401
from Fuc.read_pipeline.read_pipeline import LANG_LABEL  # noqa: E402,F401
from Fuc.scoring.scoring import CHOICES, CHOICE_WORDS, NUM_RE, SHARE_RE, clean, grade_key, norm, owners_text, parse_owners  # noqa: E402,F401
