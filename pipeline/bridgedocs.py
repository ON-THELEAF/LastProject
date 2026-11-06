"""BridgeDocs 읽기·검증 엔진.

사용: python bridgedocs.py <명령> [옵션]
  python bridgedocs.py run    # 문서 입력 → 분석 → 환각검증 → 판별 화면(브라우저)에서 확인·데이터 추출
  python bridgedocs.py make   # 데이터(JSON·CSV·엑셀) → 문서(기본 양식 PDF 또는 워드 양식 채우기)

프로그램(윈도우 화면)은 pipeline/desktop/service.py를 거쳐 이 엔진을 부른다.
명령마다 옵션은 `python bridgedocs.py <명령> --help` 로 본다.
"""
import argparse
import base64
import difflib
import io
import json
import os
import random
import re
import sys
import tempfile
import time
import unicodedata
import zipfile
from datetime import date, timedelta
from pathlib import Path

# ponytail: Colab(리눅스 x86)에서 oneDNN 가속 경로가 "ConvertPirAttribute2RuntimeAttribute not support" 오류를 낸다(실측).
# 맥(ARM)에는 없는 경로라 맥에서는 안 났다. 속도는 조금 느려질 수 있지만 가속을 꺼서 우회한다.
os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
os.environ.setdefault("FLAGS_use_mkldnn", "0")

# Windows: torch와 paddle이 이름이 같은 CUDA DLL(cuDNN·cuBLAS)을 따로 갖고 있어, paddle을 먼저 불러오면 torch가 깨진다(실측).
# torch를 먼저 불러오면 둘 다 동작한다(cuDNN은 torch와 같은 9.10으로 맞춤, 설치는 README 참고).
if sys.platform == "win32":
    try:
        import torch  # noqa: F401
    except ImportError:
        pass  # torch가 없는 환경(A경로만 쓸 때)은 순서 문제가 없다

import pymupdf  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402


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


GRADE_DROP = re.compile(r"hsk|topik|jlpt|level|band|score|급|级|級|점|\s|[()（）]", re.I)
CHOICES = ("자격증", "수료증", "성적증명서", "기타")
# 증명서 종류 낱말(소문자). 앞에 있는 종류가 우선: 성적표에도 '합격'·'certificate'가, 수료증에도 '인정'이 나온다
CHOICE_WORDS = ((("수료", "completion", "修了", "结业", "結業"), "수료증"), (("성적", "score", "report", "transcript", "成绩", "成績"), "성적증명서"),
                (("자격", "합격", "인정", "certificate", "認定", "合格", "证书", "證書"), "자격증"))


SEAL_TAIL = re.compile(r"\s*[\[(（]([^\[\]()（）]{0,2})[\])）]?\s*$")


def strip_seal(a):
    """이름 뒤 도장 표시 조각을 뺀다(스캔 잡음으로 '[E]', '(제)', '[団'처럼 읽힘). 법인 표시 (주)·(재)·(株)는 남긴다."""
    m = SEAL_TAIL.search(a)
    return a[:m.start()] if m and m[1].strip() not in ("주", "재", "株", "유", "사") and len(a) > m.end() - m.start() + 1 else a


def person_key(s):
    """외국어 이름 비교용: 글자만(대소문자·공백·가운뎃점 무시)."""
    return "".join(ch for ch in (s or "").casefold() if ch.isalpha())


def grade_key(s):
    """등급 비교용: 五级 = 5级 = 5급 = Level 5 → '5'. N2 → 'n2', 6.5 → '6.5'."""
    s = "".join(str(ZH_NUM_VAL[c]) if c in ZH_NUM_VAL else c for c in (s or ""))
    return GRADE_DROP.sub("", s).casefold()


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
        m = re.search(r"(\d{4})\s*[-./년年]\s*(\d{1,2})\s*[-./월月]\s*(\d{1,2})", a)
        if m:
            return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}"
        ds = any_dates(a)      # 'March 12, 2025', '令和7年3月12日' 같은 답
        return f"{ds[0][0]}-{ds[0][1]:02d}-{ds[0][2]:02d}" if ds else a
    if kind == "choice":     # 원문 언어로 답해도(结业证书, 認定書, Certificate of Completion) 세 종류 중 하나로
        low = a.casefold()
        for words, val in CHOICE_WORDS + ((("기타",), "기타"),):
            if any(w in low for w in words):
                return val
        return a
    if kind == "person":
        a = re.sub(r"^(성명|이름|Name|氏名|姓名)\s*[:：]?\s*", "", a, flags=re.I)
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


# ============================================================================
# A경로: PaddleOCR 규칙 OCR  (이전: path_a.py)
# ============================================================================
# A경로(규칙 기반 OCR, PaddleOCR 한국어) — 이 파일 하나로 실행·재사용한다.
#
# - lease/sale(고정 서식): 양식에 '저장된 칸 좌표'(ground_truth의 region = 서식 칸)를 잘라서 읽는다.
#   이 PoC에서는 좌표를 make_samples.py에서 사람이 정해 두었다(제품 설계는 B경로 bbox 자동학습 + 관리자 승인).
#   ±3px 이동만 있고 실제 스캔의 기울기·확대가 없어 낙관적이다.
# - registry(가변 행): 전체 페이지 OCR 후 '갑구/을구' 앵커 기준으로 행을 재구성해 값을 뽑는다.
# - read_crop(): 임의의 bbox 영역만 잘라 OCR로 다시 읽는다 → path_b.py의 환각검증(VLM이 낸 위치를 OCR로 재확인)이 쓴다.
#
# 실행: python bridgedocs.py a [--samples samples] [--doc s03_registry --doc s06_lease]

HERE = Path(__file__).parent


def make_ocr():
    from paddleocr import PaddleOCR
    # ponytail: 서버용 검출 모델(PP-OCRv5_server_det)은 전체 페이지 1장에 120~170초(M1 CPU)라 모바일 검출 모델을 쓴다.
    # 주의: 모델 이름을 하나라도 직접 지정하면 lang='korean'이 무시돼 인식 모델이 한국어가 아니게 된다(실측) → rec도 명시.
    # BD_OCR=server 이면 서버용 검출 모델(GPU에서는 빠름). 인식 모델은 한국어 전용 모바일 그대로.
    return PaddleOCR(lang="korean", text_detection_model_name=OCR_DET,
                     text_recognition_model_name="korean_PP-OCRv5_mobile_rec",
                     use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
                     text_det_limit_type="max", text_det_limit_side_len=1280)


def ocr_items(ocr, path):
    r = ocr.predict(str(path))[0]
    boxes = r.get("rec_boxes")
    if boxes is None:
        boxes = r["dt_polys"]
    out = []
    scores = r.get("rec_scores")
    scores = [1.0] * len(r["rec_texts"]) if scores is None else scores
    for t, b, sc in zip(r["rec_texts"], boxes, scores):
        b = [float(v) for v in b] if len(b) == 4 and not hasattr(b[0], "__len__") else [min(p[0] for p in b), min(p[1] for p in b), max(p[0] for p in b), max(p[1] for p in b)]
        out.append({"text": t, "x0": b[0], "x1": b[2], "y0": b[1], "y1": b[3], "cx": (b[0] + b[2]) / 2, "cy": (b[1] + b[3]) / 2, "score": float(sc)})
    return out


_CJK_OCR = None


def make_cjk_ocr():
    """중국어·일본어·영어 OCR(PP-OCRv5 서버 인식 모델). 한국어 모델은 가나·한자를 읽지 못한다. 처음 필요할 때 한 번만 띄운다."""
    global _CJK_OCR
    if _CJK_OCR is None:
        from paddleocr import PaddleOCR
        _CJK_OCR = PaddleOCR(text_detection_model_name=OCR_DET, text_recognition_model_name="PP-OCRv5_server_rec",
                             use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
                             text_det_limit_type="max", text_det_limit_side_len=1280)
    return _CJK_OCR


def ocr_quality(items):
    """OCR이 자신 있게 읽은 글자 수(신뢰도 × 글자 수의 합). 글자 체계가 맞지 않는 모델은 대부분을 빈칸으로 내서 작다
    (일본어 증명서: 한국어 모델 평균 신뢰도는 0.95로 높았지만 읽은 글자가 거의 없었다)."""
    return sum(i.get("score", 1.0) * len(i["text"].strip()) for i in items)


def lines_xy(items, tol=10):
    """lines_of와 같지만 줄마다 위치(x0,x1,y0,y1,cy)도 돌려준다 — 라벨 근접·줄 잘라내기 검증이 쓴다."""
    rows = []
    for it in sorted(items, key=lambda i: i["cy"]):
        if rows and abs(rows[-1][0]["cy"] - it["cy"]) < tol:
            rows[-1].append(it)
        else:
            rows.append([it])
    out = []
    for r in rows:
        r = sorted(r, key=lambda i: i["x0"])
        out.append({"text": " ".join(i["text"] for i in r), "x0": r[0]["x0"], "x1": max(i["x1"] for i in r),
                    "y0": min(i["y0"] for i in r), "y1": max(i["y1"] for i in r), "cy": sum(i["cy"] for i in r) / len(r),
                    "parts": [{"text": i["text"], "x0": i["x0"], "y0": i["y0"], "x1": i["x1"], "y1": i["y1"], "score": i.get("score", 1.0)} for i in r]})   # 조각 상자: 이름 칸 확대 재판독용
    return out


# ============================================================================
# PDF 글자 레이어 경로  (이전: text_layer.py)
# ============================================================================
# PDF 글자 레이어 → path_a.ocr_items 와 같은 형식의 items 로 바꾸고, 그 글자 레이어를 믿어도 되는지 쪽 단위로 진단한다.
# (OCR·VLM 없이 PDF 안의 글자와 좌표를 바로 쓰는 경로. 스캔·사진·글자가 깨진 PDF 는 usable=False 로 걸러 OCR 경로로 넘긴다.)
#
# 핵심 함수
#   route_pdf(path)               : 문서 판정(text/mixed/ocr)과 쪽별 진단·items, 등기부면 필드까지 — 파이프라인이 부르는 입구
#   extract_text_layer(path, dpi) : 쪽별 {items, diag, usable, reasons}
#   diagnose_page / usable / warnings : 쪽 진단 지표, 사용 가능 판정, 경고(말소선·외곽선 글자·PUA 등)
#   text_items(page, dpi)         : 좌표 기반으로 줄을 다시 묶은 items — path_a.lines_xy 에 그대로 넣을 수 있다(path_b.check_field 도 동작)
#   render_crosscheck(...)        : 글자 레이어 값을 쪽 렌더링→OCR 로 다시 읽어 맞는지 확인(글자 매핑이 그럴듯하게 틀린 PDF 는 이것만 잡는다)
# 실행: python bridgedocs.py layer <pdf>
# 임계값 TH 는 샘플 3개와 합성 사례로 정했다 — 실제 발급본·스캔본으로 다시 맞춰야 한다.

PUA = [(0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD)]


def is_hangul(c):
    o = ord(c)
    return 0xAC00 <= o <= 0xD7A3 or 0x3131 <= o <= 0x318E or 0x1100 <= o <= 0x11FF


def is_pua(c):
    o = ord(c)
    return any(a <= o <= b for a, b in PUA)


def is_bad(c):
    """깨진 매핑 신호: U+FFFD, 비정상 제어문자(탭·개행 제외), 미할당/비문자(Cn)"""
    o = ord(c)
    return c == "�" or (o < 32 and c not in "\t\n\r") or 0x7F <= o <= 0x9F or unicodedata.category(c) == "Cn"


def chars_of(page, strike=True, flat=True):
    """texttrace 를 글자 단위로 푼다. 공백은 버린다. 반환: 글자 dict 목록(콘텐츠 스트림 순서 seq 보존)."""
    out, seq = [], 0
    pr = page.rect
    for s in page.get_texttrace():
        for uni, gid, org, bb in s["chars"]:
            c = chr(uni) if uni and uni > 0 else "�"   # 매핑 없는 글리프(ToUnicode 없음) = 0 → 깨짐으로 센다
            seq += 1
            if c.isspace():
                continue
            x0, y0, x1, y1 = bb
            off = x1 <= pr.x0 or x0 >= pr.x1 or y1 <= pr.y0 or y0 >= pr.y1
            out.append({"c": c, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "base": org[1], "size": s["size"], "type": s["type"],
                        "color": s["color"], "opacity": s["opacity"], "font": s["font"], "seq": seq, "off": off, "struck": False, "wm": False, "flat": False})
    out = _dedupe(out)
    _mark_watermark(out)
    if flat and out:
        _mark_flat(page, out)
    if strike and out:
        _mark_struck(page, out)
    return out


def _dedupe(chars):
    """같은 글자가 같은 자리에 2번 그려진 것(한글/HWP 의 굵게 흉내, 발급본 제목 등)을 1개로. MuPDF get_text 는 이걸 알아서 지우지만 texttrace 는 안 지운다."""
    seen, out = {}, []
    for c in chars:
        k = (c["c"], round(c["x0"]), round(c["base"]))
        hit = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for o in seen.get((c["c"], k[1] + dx, k[2] + dy), ()):
                    if abs(o["x0"] - c["x0"]) < 0.2 * c["size"] and abs(o["base"] - c["base"]) < 0.2 * c["size"]:
                        hit = True
        if not hit:
            seen.setdefault(k, []).append(c)
            out.append(c)
    return out


def _mark_watermark(chars):
    """'열람용' 같은 큰 연회색 글자(쪽 가운데 대각/가로 워터마크). 값 추출에서 빼야 한다."""
    for c in chars:
        col = c["color"]
        c["wm"] = c["size"] >= 30 and min(col) >= 0.6 and max(col) - min(col) < 0.05


def _mark_struck(page, chars):
    """글자 가운데를 가로지르는 얇은 가로선(말소선)이 있는 글자에 struck=True. 등기부의 '실선으로 그어진 부분은 말소사항'."""
    lines = []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                if abs(a.y - b.y) < 0.6 and abs(a.x - b.x) > 2:
                    lines.append((min(a.x, b.x), max(a.x, b.x), a.y))
            elif it[0] == "re":
                r = it[1]
                if r.height < 1.2 and r.width > 2:
                    lines.append((r.x0, r.x1, (r.y0 + r.y1) / 2))
    if not lines:
        return
    pm = page.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
    k, W, H, buf = 150 / 72, pm.width, pm.height, pm.samples

    def line_visible(ch, y):
        """그 높이의 화면 픽셀이 글자 폭 전체에서 이어진 잉크인가(획이 아니라 선이 실제로 보이는지; 흰 상자로 덮인 선은 제외).
        가는 선은 안티앨리어싱으로 2행에 나뉘므로 열마다 y±1 행의 최솟값을 본다."""
        yy = int(y * k)
        xs = range(max(0, int(ch["x0"] * k)), min(W, int(ch["x1"] * k) + 1))
        if not xs or not (1 <= yy < H - 1):
            return False
        dark = sum(min(buf[(yy + dy) * W + x] for dy in (-1, 0, 1)) < 210 for x in xs)
        return dark / len(xs) >= 0.9

    for ch in chars:
        if ch["wm"]:
            continue
        h = ch["y1"] - ch["y0"]
        cy = (ch["y0"] + ch["y1"]) / 2
        cx = (ch["x0"] + ch["x1"]) / 2
        # 글자 중간 띠(±22% 높이)를 지나고 글자 중심 x 를 덮는 선
        if any(a - 0.5 <= cx <= b + 0.5 and b - a >= 1.5 * (ch["x1"] - ch["x0"]) and abs(y - cy) <= 0.22 * h and line_visible(ch, y) for a, b, y in lines):
            ch["struck"] = True


def _union_area(rects, W, H, step=4):
    """이미지 bbox 합집합 면적 비율(격자 근사)"""
    if not rects:
        return 0.0
    gx, gy = int(W / step) + 1, int(H / step) + 1
    cell = bytearray(gx * gy)
    for x0, y0, x1, y1 in rects:
        for j in range(max(0, int(y0 / step)), min(gy, int(y1 / step) + 1)):
            base = j * gx
            for i in range(max(0, int(x0 / step)), min(gx, int(x1 / step) + 1)):
                cell[base + i] = 1
    return sum(cell) / (gx * gy)


def _mark_flat(page, chars, dpi=100):
    """렌더링한 화면에서 글자 상자 안이 '평평'(잉크 없음)한 글자에 flat=True — 흰 글씨/가려진 글씨/렌더 안 되는 글씨.
    정부 임대차 표준서식의 흰색 안내문('이름 또는 법인명 기재')이 실제로 이 경우다."""
    pm = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    k = dpi / 72
    W, H, buf = pm.width, pm.height, pm.samples
    for ch in chars:
        ch["flat"] = False
        if ch["off"]:
            continue
        x0, x1 = max(0, int(ch["x0"] * k)), min(W, int(ch["x1"] * k) + 1)
        y0, y1 = max(0, int(ch["y0"] * k)), min(H, int(ch["y1"] * k) + 1)
        if x1 <= x0 or y1 <= y0:
            continue
        vals = [buf[y * W + x] for y in range(y0, y1) for x in range(x0, x1)]
        white = min(ch["color"]) > 0.9                       # 흰 글씨는 '밝은 배경 위'면 숨은 글자(어두운 띠 위의 흰 제목은 정상)
        med = sorted(vals)[len(vals) // 2]
        ch["flat"] = max(vals) - min(vals) < 24 or (white and med > 200)


def diagnose_page(page, chars=None, render_check=True):
    chars = chars_of(page) if chars is None else chars
    n = len(chars)
    W, H = page.rect.width, page.rect.height
    txt = [c["c"] for c in chars]
    hang = sum(is_hangul(c) for c in txt)
    letters = sum(c.isalnum() for c in txt)
    imgs = []
    for im in page.get_image_info():
        r = pymupdf.Rect(im["bbox"]) & page.rect
        if not r.is_empty:
            imgs.append((r.x0, r.y0, r.x1, r.y1))
    fonts = {f[3].split("+")[-1]: f[2] for f in page.get_fonts()}
    type3_names = {k for k, v in fonts.items() if v == "Type3"}
    n_t3 = sum(1 for c in chars if c["font"].split("+")[-1] in type3_names)
    white = sum(1 for c in chars if all(v > 0.95 for v in c["color"]))
    draws = page.get_drawings()
    # 글자를 도형(외곽선)으로 바꾼 흔적: 작은 채움 곡선 경로 수. 이 샘플에서 HWP 내보내기 매매계약서는 라벨·제목 일부가 이렇게 나온다
    glyph_paths = sum(1 for x in draws if x["type"] in ("f", "fs") and any(it[0] == "c" for it in x["items"])
                      and 1 < x["rect"].width < 30 and x["rect"].height < 30)
    d = {
        "n_chars": n,
        "hangul_ratio": hang / n if n else 0.0,
        "alnum_ratio": letters / n if n else 0.0,
        "pua_ratio": sum(is_pua(c) for c in txt) / n if n else 0.0,
        "bad_ratio": sum(is_bad(c) for c in txt) / n if n else 0.0,
        "mode3_ratio": sum(1 for c in chars if c["type"] == 3) / n if n else 0.0,
        "transparent_ratio": sum(1 for c in chars if c["opacity"] < 0.05) / n if n else 0.0,
        "white_ratio": white / n if n else 0.0,
        "offpage_ratio": sum(c["off"] for c in chars) / n if n else 0.0,
        "flat_ratio": (sum(c["flat"] for c in chars) / n if n else 0.0) if render_check else None,
        "image_cover": _union_area(imgs, W, H),
        "n_images": len(imgs),
        "type3_char_ratio": n_t3 / n if n else 0.0,
        "n_drawings": len(draws),
        "glyph_paths": glyph_paths,
        "outlined_ratio": glyph_paths / (n + glyph_paths) if (n + glyph_paths) else 0.0,
        "struck_ratio": sum(c["struck"] for c in chars) / n if n else 0.0,
        "fonts": fonts,
    }
    # 글자 밀도: 쪽 면적(100pt^2)당 글자 수. 외곽선(글자→도형) PDF 는 n_chars≈0 인데 드로잉이 많다
    d["chars_per_100pt2"] = n / (W * H / 100)
    return d


# ponytail: 임계값은 이 3개 샘플 + 합성 실험(make_layer_fixtures.py)에서 정했다. 실제 발급본·스캔본 표본으로 다시 맞춰야 한다.
TH = {"min_chars": 40, "min_hangul": 0.30, "max_pua": 0.10, "max_bad": 0.01, "max_mode3": 0.02, "max_flat": 0.10,
      "max_off": 0.02, "max_img_cover": 0.60, "max_white": 0.30, "max_outlined": 0.30}


def usable(d, th=TH):
    """쪽 단위 판정 -> (사용 가능 bool, 사유 목록). 하나라도 걸리면 OCR 로 넘긴다."""
    why = []
    if d["n_chars"] < th["min_chars"]:
        if d["image_cover"] < 0.05 and d["glyph_paths"] < 5:
            return True, []      # 정말 빈 쪽(여백 쪽)은 OCR 로 보낼 이유가 없다
        why.append(f"글자 {d['n_chars']}개 < {th['min_chars']}")
    if d["outlined_ratio"] > th["max_outlined"]:
        why.append(f"글자가 외곽선 도형으로 바뀐 쪽(도형 글자 {d['glyph_paths']}개 / 글자 {d['n_chars']}개)")
    elif d["hangul_ratio"] < th["min_hangul"] and d["alnum_ratio"] < 0.5:      # 한글 서식인데 한글이 거의 없으면 매핑 의심
        why.append(f"한글 {d['hangul_ratio']:.0%} / 영숫자 {d['alnum_ratio']:.0%}")
    if d["pua_ratio"] > th["max_pua"]:
        why.append(f"PUA {d['pua_ratio']:.1%}")
    if d["bad_ratio"] > th["max_bad"]:
        why.append(f"깨진 글자 {d['bad_ratio']:.1%}")
    if d["mode3_ratio"] > th["max_mode3"] or d["transparent_ratio"] > th["max_mode3"]:
        why.append(f"보이지 않는 글자(mode3 {d['mode3_ratio']:.0%}/투명 {d['transparent_ratio']:.0%}) = 스캔 OCR 층 또는 숨김")
    if d["flat_ratio"] is not None and d["flat_ratio"] > th["max_flat"]:
        why.append(f"화면에 안 그려지는 글자 {d['flat_ratio']:.0%}")
    if d["offpage_ratio"] > th["max_off"]:
        why.append(f"쪽 밖 글자 {d['offpage_ratio']:.0%}")
    if d["image_cover"] > th["max_img_cover"]:
        why.append(f"이미지가 쪽의 {d['image_cover']:.0%} 덮음")
    return (not why), why


# ───────── 줄 묶기 → path_a.ocr_items 형식 ─────────
def text_items(page, dpi=150, chars=None, row_tol=0.45, gap_em=0.9, skip_hidden=True, drop_struck=False, drop_wm=True):
    """글자 → (1) 기준선 y 로 행 묶기 (2) 행 안에서 x 순 정렬 (3) 간격이 gap_em×글자크기 이상이면 조각(셀) 분리.
    반환 items 는 path_a.ocr_items 와 같은 키(text,x0,x1,y0,y1,cx,cy)+ struck/size. 좌표는 dpi 기준 픽셀.
    HWP 내보내기처럼 글자가 뒤섞인 스트림도 좌표로 정렬하므로 순서가 복원된다(행 묶기 오차만 없으면)."""
    chars = chars_of(page) if chars is None else chars
    if skip_hidden:
        chars = [c for c in chars if c["type"] != 3 and c["opacity"] >= 0.05 and not c["off"] and not c["flat"]]
    if drop_struck:
        chars = [c for c in chars if not c["struck"]]
    if drop_wm:
        chars = [c for c in chars if not c["wm"]]
    # 행 묶기: 기준선 오름차순으로 훑으며, 현재 행의 기준선 평균과 (row_tol × 글자크기) 이내면 같은 행
    rows = []
    for ch in sorted(chars, key=lambda c: (c["base"], c["x0"])):
        for r in rows[-3:][::-1]:            # 최근 행 몇 개만 본다(위에서 아래로 정렬돼 있으므로)
            if abs(ch["base"] - r["base"]) <= row_tol * max(ch["size"], r["size"]):
                r["ch"].append(ch)
                r["base"] = (r["base"] * (len(r["ch"]) - 1) + ch["base"]) / len(r["ch"])
                r["size"] = max(r["size"], ch["size"])
                break
        else:
            rows.append({"base": ch["base"], "size": ch["size"], "ch": [ch]})
    k = dpi / 72
    items = []
    for r in rows:
        cs = sorted(r["ch"], key=lambda c: c["x0"])
        seg = [cs[0]]
        segs = []
        for c in cs[1:]:
            gap = c["x0"] - seg[-1]["x1"]
            if gap > gap_em * max(c["size"], seg[-1]["size"]):
                segs.append(seg)
                seg = [c]
            else:
                seg.append(c)
        segs.append(seg)
        for sg in segs:
            x0, x1 = min(c["x0"] for c in sg), max(c["x1"] for c in sg)
            y0, y1 = min(c["y0"] for c in sg), max(c["y1"] for c in sg)
            # 같은 행의 조각이 lines_xy 의 cy 허용오차(10px)로 한 줄이 되도록 행 공통 cy(기준선 기반)를 쓴다
            cy = (r["base"] - 0.3 * r["size"]) * k
            items.append({"text": _join(sg), "x0": x0 * k, "x1": x1 * k, "y0": y0 * k, "y1": y1 * k,
                          "cx": (x0 + x1) / 2 * k, "cy": cy, "size": r["size"], "struck": sum(c["struck"] for c in sg) > len(sg) / 2})
    return items


def _join(seg):
    """조각 안 글자를 이어 붙이되, 글자 사이 간격이 큰 곳(공백 문자였던 곳)에는 공백 1개를 넣는다."""
    s = seg[0]["c"]
    for a, b in zip(seg, seg[1:]):
        if b["x0"] - a["x1"] > 0.25 * max(a["size"], b["size"]):
            s += " "
        s += b["c"]
    return s


def extract_text_layer(path, dpi=150, render_check=True):
    doc = pymupdf.open(path)
    if doc.needs_pass:
        return [{"page": 0, "error": "password"}]
    res = []
    for p in doc:
        ch = chars_of(p)
        d = diagnose_page(p, ch, render_check)
        ok, why = usable(d)
        res.append({"page": p.number + 1, "diag": d, "usable": ok, "reasons": why,
                    "items": text_items(p, dpi, ch) if ok else [], "scale": dpi / 72})
    return res


# ============================================================================
# 등기부 PDF 글자 레이어 필드 추출  (이전: registry_layer.py)
# ============================================================================
# 등기부 PDF(글자 레이어)에서 필드 추출 — 표의 세로선으로 열(셀)을 나누고 말소선(취소선)이 그어진 항목은 현행에서 뺀다.
# 말소선 때문에 '압류' 글자가 있어도 말소된 것이면 seizure_flag=없음, seizure_struck_only=True 로 알린다(사람 확인 대상).
# 사용: python bridgedocs.py registry <pdf>


# ============================================================================
# B경로: VLM 답변 · 환각검증 · 크롭 재질문  (이전: path_b.py)
# ============================================================================
# B경로(VLM, Qwen2.5-VL) — 이 파일 하나로 실행한다. GPU(Colab T4)에서 transformers로 돌린다.
#
# 모드
#   answer  : 필드별로 질문해 값만 받는다(프롬프트 v1/v2/v3 비교용, --prompt)
#   verify  : 값 + 위치(bbox)를 같이 받고, 그 위치를 A경로의 OCR로 다시 읽어 B의 값과 비교한다(기획서 4-2 환각검증).
#             B가 위치를 얼마나 정확히 내는지(GT 칸과의 겹침)도 함께 잰다.
#   answer --ocr : answer 에 페이지 OCR 줄(글자·순서)을 프롬프트로 같이 준다(다른 칸 값 복사·숫자 오독을 줄이려는 실험). 결과 파일 이름 끝에 _ocr
#   check   : (GPU 불필요, OCR만) B 값이 페이지에 있는지·맞는 라벨 줄에 있는지·교차 규칙에 맞는지 검증한다. 좌표를 쓰지 않는다.
#   crop    : 라벨 줄만 잘라 B에게 같은 질문을 다시 하고, 전체 페이지 답과 비교한다.
#   --diff A.json B.json : 두 answer 결과를 같은 문항끼리 비교(고쳐진 것·새로 틀린 것)
#   --compare : results/ 의 answer 결과 파일들(프롬프트별)을 표로 비교한다(GPU 불필요).
#
# 실행(Colab): python bridgedocs.py b --mode answer --prompt v2
#              python bridgedocs.py b --mode verify
#              python bridgedocs.py b --compare
# 주의: 이 모델을 fp16으로 돌릴 때 문서 1장이 통째로 "!!!!"로 깨진 적이 있다(T4). 깨진 응답은 지표에서 빼고 건수만 보여 준다.
# bf16은 원본 해상도(1240x1754)에서 T4 메모리가 부족했다.

HERE = Path(__file__).parent

# ───────── 프롬프트 3단계 ─────────
# v1: 기존(기준선) / v2: 형식 강제 + 필드 의미 명확화(서식 위치 힌트 없음) / v3: v2 + 서식 위치 힌트(서식을 아는 경우의 최선, 낙관적)
V1 = {
    "registry": {
        "property_address": "이 등기사항증명서 맨 위 대괄호 줄에 적힌 부동산 소재지를 그대로 적어줘. '[집합건물]'은 빼고 주소만 답해.",
        "owner_name": "갑구(소유권에 관한 사항)에서 가장 마지막 소유자의 이름이 뭐야? 이름만 답해.",
        "ownership_change_date": "갑구에서 가장 마지막 소유자 행의 접수일이 언제야? YYYY-MM-DD 형식으로만 답해.",
        "seizure_flag": "갑구에 압류 또는 가압류 등기가 있어? '있음' 또는 '없음'으로만 답해.",
        "mortgage_amount": "을구에서 근저당권의 채권최고액이 얼마야? 숫자와 '원'만 답해. 근저당이 없으면 '없음'이라고만 답해.",
        "mortgage_holder": "을구에서 근저당권자가 누구야? 이름만 답해. 근저당이 없으면 '없음'이라고만 답해.",
    },
    "lease": {
        "lessor_name": "맨 위 '임대인( )과 임차인( )' 문장에서 임대인 이름이 뭐야? 이름만 답해.",
        "lessee_name": "맨 위 '임대인( )과 임차인( )' 문장에서 임차인 이름이 뭐야? 이름만 답해.",
        "deposit_amount": "제1조 표의 '보증금' 칸에 적힌 ₩ 뒤 숫자 금액이 얼마야? 숫자와 '원'만 답해.",
        "monthly_rent": "제1조 표의 '차임(월세)' 칸에 적힌 금액이 얼마야? 숫자와 '원'만 답해.",
        "contract_start_date": "제2조에서 임차주택을 임차인에게 인도하는 날짜가 언제야? YYYY-MM-DD 형식으로만 답해.",
        "contract_end_date": "제2조에서 임대차기간이 끝나는 날짜가 언제야? YYYY-MM-DD 형식으로만 답해.",
    },
    "sale": {
        "seller_name": "매도인의 성명이 뭐야? 이름만 답해.",
        "buyer_name": "매수인의 성명이 뭐야? 이름만 답해.",
        "sale_price": "'매매대금' 칸의 ₩ 뒤 숫자 금액이 얼마야? 숫자와 '원'만 답해.",
        "down_payment": "'계약금' 칸에 적힌 금액이 얼마야? 숫자와 '원'만 답해.",
        "balance_due_date": "'잔금' 칸에 적힌 지불 날짜가 언제야? YYYY-MM-DD 형식으로만 답해.",
        "handover_date": "제2조에 적힌 부동산 인도일이 언제야? YYYY-MM-DD 형식으로만 답해.",
    },
}
PRE = ("이것은 문서 이미지에 인쇄·기재된 글자를 그대로 옮겨 적는 작업입니다. "
       "설명이나 문장 없이 값만 한 줄로 답하세요. 문서에 없거나 읽을 수 없으면 '없음'이라고만 답하세요.\n")
V2Q = {  # 필드 의미만 분명히(위치 힌트 없음)
    "registry": {
        "property_address": "이 등기사항증명서의 부동산 소재지 주소를 적으세요. '[집합건물]' 같은 표기는 빼고 주소만 적으세요.",
        "owner_name": "현재(가장 최근에 등기된) 소유자의 이름만 적으세요. 이전 소유자나 주소는 제외합니다.",
        "ownership_change_date": "현재 소유자의 소유권이 접수된 날짜를 YYYY-MM-DD 형식으로 적으세요. 등기원인(매매 등)의 날짜가 아니라 접수일입니다.",
        "seizure_flag": "압류 또는 가압류 등기가 하나라도 있으면 '있음', 없으면 '없음'만 적으세요.",
        "mortgage_amount": "근저당권의 채권최고액을 쉼표 포함 숫자로만 적으세요(예: 120,000,000). 근저당 기록이 없으면 '없음'.",
        "mortgage_holder": "근저당권자(은행·회사) 이름만 적으세요. 채무자 이름이 아닙니다. 근저당 기록이 없으면 '없음'.",
    },
    "lease": {
        "lessor_name": "이 임대차계약서의 임대인 이름만 적으세요. 임차인 이름이 아닙니다.",
        "lessee_name": "이 임대차계약서의 임차인 이름만 적으세요. 임대인 이름이 아닙니다.",
        "deposit_amount": "보증금 금액을 쉼표 포함 숫자로만 적으세요(예: 120,000,000). 월세가 아닙니다.",
        "monthly_rent": "차임(월세) 금액을 쉼표 포함 숫자로만 적으세요(예: 1,200,000). 보증금이 아닙니다.",
        "contract_start_date": "임차주택을 임차인에게 인도하는 날짜(임대차기간의 시작)를 YYYY-MM-DD 형식으로 적으세요.",
        "contract_end_date": "임대차기간이 끝나는 날짜를 YYYY-MM-DD 형식으로 적으세요.",
    },
    "sale": {
        "seller_name": "이 매매계약서의 매도인 이름만 적으세요. 매수인 이름이 아닙니다.",
        "buyer_name": "이 매매계약서의 매수인 이름만 적으세요. 매도인 이름이 아닙니다.",
        "sale_price": "매매대금 금액을 쉼표 포함 숫자로만 적으세요(예: 450,000,000). 계약금이 아닙니다.",
        "down_payment": "계약금 금액을 쉼표 포함 숫자로만 적으세요(예: 45,000,000). 매매대금 전체가 아닙니다.",
        "balance_due_date": "잔금을 지불하는 날짜를 YYYY-MM-DD 형식으로 적으세요.",
        "handover_date": "부동산 인도일을 YYYY-MM-DD 형식으로 적으세요.",
    },
}
# 추가 문서 2종(2026-10-24): 집합건축물대장(전유부) · 상가건물 임대차 표준계약서
V2Q["ledger"] = {
    "bldg_address": "이 건축물대장의 대지위치를 적으세요. 지번과 도로명주소는 빼고 대지위치 칸의 값만 적으세요.",
    "bldg_owner": ("소유자현황 표에 적힌 소유자를 모두 적으세요. 한 명이면 성명(명칭)만, 여러 명(공유)이면 "
                   "'성명(지분), 성명(지분)' 형식으로 적으세요(예: 홍길동(1/2), 김민지(1/2)). 주소·주민등록번호는 빼세요."),
    "bldg_owner_date": "소유자현황 표의 변동일자를 YYYY-MM-DD 형식으로 적으세요. 여러 명이면 가장 최근 날짜입니다. 발급일·가격 기준일이 아닙니다.",
    "bldg_main_use": "전유부분 중 '주' 구분 행의 용도만 적으세요(예: 아파트). 공용부분의 용도가 아닙니다.",
    "bldg_area": "전유부분 중 '주' 구분 행의 면적(㎡)을 숫자만 적으세요(예: 84.97). 공용부분 면적이 아닙니다.",
    "bldg_violation": "이 건축물대장에 '위반건축물' 표시가 있으면 '있음', 없으면 '없음'만 적으세요.",
}
V2Q["store_lease"] = {
    "st_lessor": "이 상가건물 임대차계약서의 임대인 이름만 적으세요. 임차인 이름이 아닙니다.",
    "st_lessee": "이 상가건물 임대차계약서의 임차인 이름만 적으세요. 임대인 이름이 아닙니다.",
    "st_deposit": "보증금 금액을 쉼표 포함 숫자로만 적으세요(예: 50,000,000). 환산보증금·계약금이 아닙니다.",
    "st_rent": "차임(월세) 금액을 쉼표 포함 숫자로만 적으세요(예: 2,500,000). 관리비·보증금이 아닙니다.",
    "st_mgmt_fee": "관리비 금액을 쉼표 포함 숫자로만 적으세요(예: 300,000). 차임(월세)이 아닙니다.",
    "st_start": "임차 상가건물을 임차인에게 인도하는 날짜(임대차기간의 시작)를 YYYY-MM-DD 형식으로 적으세요.",
    "st_end": "임대차기간이 끝나는 날짜를 YYYY-MM-DD 형식으로 적으세요.",
}
V2Q["cert"] = {
    "cert_kind": "이 문서가 자격증(합격증·인정서 포함), 수료증, 성적증명서(점수 보고서) 중 무엇인지 그 이름 하나만 적으세요. 셋 다 아니면 '기타'.",
    "cert_holder": "이 증명서를 받은 사람(취득자·수험자·수료자)의 이름을 문서에 적힌 글자 그대로 적으세요. 번역하거나 로마자로 바꾸지 마세요. 발급기관장·서명자 이름이 아닙니다.",
    "cert_title": "자격·시험·과정의 이름을 문서에 적힌 그대로 적으세요(예: 정보처리기사, TOEIC, 日本語能力試験). 문서 제목('자격증', 'Certificate')만 적지 마세요.",
    "cert_grade": "이 증명서의 등급 또는 점수 값 하나만 짧게 적으세요(예: 1급, N2, 850, 6.5, 五级). 다른 글자는 적지 마세요. 등급·점수가 없으면 '없음'.",
    "cert_issuer": "이 증명서를 발급한 기관 이름을 문서에 적힌 그대로 적으세요.",
    "cert_date": "자격을 취득(합격·수료)한 날짜를 YYYY-MM-DD 형식으로 적으세요. 성적표는 시험일입니다. 생년월일·발급일이 아닙니다(그 날짜가 없을 때만 발급일).",
    "cert_no": "증명서 번호(자격번호·인정번호·등록번호)를 문서에 적힌 그대로 적으세요. 없으면 '없음'.",
}
HINT = {
    "bldg_address": "(위쪽 표의 '대지위치' 칸)",
    "bldg_owner": "('소유자현황' 표의 '성명(명칭)' 칸과 '소유권 지분' 칸)",
    "bldg_owner_date": "('소유자현황' 표의 '변동일자' 칸)",
    "bldg_main_use": "('전유부분' 표의 '주' 행 '용도' 칸)",
    "bldg_area": "('전유부분' 표의 '주' 행 '면적(㎡)' 칸)",
    "bldg_violation": "(문서 오른쪽 위 표시)",
    "st_lessor": "(맨 위 '임대인( )과 임차인( )' 문장의 '임대인(' 바로 뒤 괄호 안)",
    "st_lessee": "(맨 위 '임대인( )과 임차인( )' 문장의 '임차인(' 바로 뒤 괄호 안)",
    "st_deposit": "(제1조 표의 '보증금' 줄, '원정(₩' 뒤)",
    "st_rent": "(제1조 표의 '차임(월세)' 줄, '원정(₩' 뒤)",
    "st_mgmt_fee": "(제1조 표의 '관리비' 줄, '원정(₩' 뒤)",
    "st_start": "(제2조 '…년 …월 …일까지 임차인에게 인도하고' 부분)",
    "st_end": "(제2조 '임대차기간은 인도일로부터 …년 …월 …일까지로 한다' 부분)",
    "property_address": "(문서 맨 위 '[집합건물]' 뒤에 적힌 줄)",
    "owner_name": "(갑구 표의 가장 아래 행, '권리자 및 기타사항' 칸에서 '소유자' 다음에 적힌 이름)",
    "ownership_change_date": "(갑구 표의 가장 아래 소유권 행, '접수' 칸 첫 줄)",
    "seizure_flag": "(갑구 표의 '등기목적' 칸에 '압류' 또는 '가압류'가 적힌 행)",
    "mortgage_amount": "(을구 표 '권리자 및 기타사항' 칸의 '채권최고액 금' 뒤)",
    "mortgage_holder": "(을구 표의 '근저당권자' 뒤)",
    "lessor_name": "(맨 위 '임대인( )과 임차인( )' 문장의 '임대인(' 바로 뒤 괄호 안)",
    "lessee_name": "(맨 위 '임대인( )과 임차인( )' 문장의 '임차인(' 바로 뒤 괄호 안)",
    "deposit_amount": "(제1조 표의 '보증금' 줄, '원정(₩' 뒤)",
    "monthly_rent": "(제1조 표의 '차임(월세)' 줄, '금' 뒤)",
    "contract_start_date": "(제2조 첫 문장 '…년 …월 …일까지 임차인에게 인도하고' 부분)",
    "contract_end_date": "(제2조 둘째 줄 '임대차기간은 인도일로부터 …년 …월 …일까지로 한다' 부분)",
    "seller_name": "(아래쪽 서명란 '매도인' 블록의 '성명' 칸)",
    "buyer_name": "(아래쪽 서명란 '매수인' 블록의 '성명' 칸)",
    "sale_price": "('매매대금' 줄, '원정(₩' 뒤)",
    "down_payment": "('계약금' 줄에 숫자로 적힌 금액)",
    "balance_due_date": "('잔금' 줄의 '…년 …월 …일에 지불한다' 부분)",
    "handover_date": "(제2조 '위 부동산의 인도일은' 뒤)",
}
V2 = {t: {k: PRE + q for k, q in d.items()} for t, d in V2Q.items()}
V3 = {t: {k: q + " " + HINT.get(k, "") for k, q in d.items()} for t, d in V2.items()}   # 증명서는 양식이 제각각이라 위치 힌트 없음
PROMPTS = {"v1": V1, "v2": V2, "v3": V3}

OCR_NOTE = ("아래는 이 이미지를 OCR로 읽은 글자입니다(위에서 아래 순서, 한 줄에 한 번호). "
            "OCR은 글자가 틀리거나 빠질 수 있으니 이미지와 함께 보고 판단하세요. 같은 줄에 있는 라벨과 값은 서로 짝입니다.\n")


def with_ocr(prompt, lines):
    """질문 앞에 페이지 OCR 줄 목록을 붙인다."""
    body = "\n".join(f"{i + 1}: {L['text']}" for i, L in enumerate(lines))
    return f"{OCR_NOTE}[OCR 시작]\n{body}\n[OCR 끝]\n\n{prompt}"


# ───────── 모델 ─────────
def load(name, dtype):
    import torch
    from transformers import AutoModelForImageTextToText, AutoProcessor
    # 모델 종류(Qwen2.5-VL, Qwen3-VL 등)에 맞는 클래스를 transformers가 고른다
    model = AutoModelForImageTextToText.from_pretrained(name, dtype=getattr(torch, dtype), device_map="auto")
    return model, AutoProcessor.from_pretrained(name)


def ask(model, proc, img, prompt, max_new=64):
    """-> (답변 문자열, 모델이 실제로 본 이미지 크기(w, h)). bbox 좌표는 이 크기 기준이다."""
    import torch
    msgs = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": prompt}]}]
    text = proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    inputs = proc(text=[text], images=[img], return_tensors="pt").to(model.device)
    grid = inputs["image_grid_thw"][0]
    patch = getattr(proc.image_processor, "patch_size", 14)   # Qwen2.5-VL 14px, Qwen3-VL 16px
    seen = (int(grid[2]) * patch, int(grid[1]) * patch)       # 패치 크기 × 개수 = 리사이즈된 크기
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=max_new, do_sample=False)
    return proc.batch_decode(out[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0].strip(), seen


def garbled(ans):
    return ans.count("!") >= max(8, len(ans) // 2)


# ───────── 위치(bbox) 유틸 ─────────


# ───────── 실행 모드 ─────────


# ───────── 좌표 없는 검증(check) + 라벨 줄 잘라서 재질문(crop) ─────────
# 3B가 낸 bbox는 쓸 수 없었다(중심 적중 21%). 그래서 위치 대신 "B가 낸 값이 페이지에 실제로 있고, 맞는 라벨 줄에 있는가"를 본다.
# 오류의 절반 이상이 '다른 줄 값 복사'(계약금 자리에 매매대금 등)였기 때문이다. 아래 라벨·규칙은 서식 스키마(서식별로 정해 두는 값)다.
ALIASES = {
    "owner_name": ["소유자"], "mortgage_amount": ["채권최고액"], "mortgage_holder": ["근저당권자"],
    "lessor_name": ["임대인"], "lessee_name": ["임차인"], "deposit_amount": ["보증금"], "monthly_rent": ["차임", "월세"],
    "contract_start_date": ["상태로"], "contract_end_date": ["인도일로부터"],
    "seller_name": ["성명"], "buyer_name": ["성명"], "sale_price": ["매매대금"], "down_payment": ["계약금"],
    "balance_due_date": ["잔금"], "handover_date": ["인도일은"],
    "bldg_address": ["대지위치"],
    "st_lessor": ["임대인"], "st_lessee": ["임차인"], "st_deposit": ["보증금"], "st_rent": ["차임", "월세"],
    "st_mgmt_fee": ["관리비"], "st_start": ["상태로"], "st_end": ["인도일로부터"],
}
VOCAB = sorted({a for v in ALIASES.values() for a in v} | {"채무자", "영수자", "중도금", "융자금", "임대보증금", "주민등록번호", "전화", "권리자", "소유자",
                "환산보증금", "착공일", "허가일", "변동일자"},
               key=len, reverse=True)
LAST_OWNER = {"owner_name", "ownership_change_date"}      # 갑구에서는 '가장 아래 소유자 줄'이 현재 소유자
FIRST_DATE = {"ownership_change_date"}                      # 그 줄의 첫 날짜 = 접수일(둘째 = 등기원인일)
DATE_ANY = re.compile(r"(?<!\d)((?:19|20)\d{2})(?:\s*년\s*|\s*[.\-/]\s*|\s+)(\d{1,2})(?:\s*월\s*|\s*[.\-/]\s*|\s+)(\d{1,2})(?!\d)")
FLAG_WORD = {"seizure_flag": "압류", "bldg_violation": "위반건축물"}   # 있음/없음 칸을 페이지 글자로 확인하는 키워드
# '없음'이 정상일 수 있는 칸: 페이지에 이 칸 이름이 보이면 '없음'은 틀린 답이다
EMPTY_OK = {"mortgage_amount": ["채권최고액"], "mortgage_holder": ["채권최고액"],
            "cert_grade": ["등급", "점수", "급수", "Score", "Band", "Grade", "Level", "Result", "レベル", "级别", "級別", "成绩"],
            "cert_no": ["번호", "No.", "Number", "番号", "编号", "編號"]}
# 시험별 등급·점수 범위(포트폴리오·국제처에 자주 오는 시험만). 범위를 벗어나면 잘못 읽은 값이다
GRADE_RANGE = [("toeic", lambda g: g.isdigit() and 10 <= int(g) <= 990 and int(g) % 5 == 0, "TOEIC 점수는 10~990, 5점 단위"),
               ("ielts", lambda g: re.fullmatch(r"\d(\.[05])?", g) is not None and float(g) <= 9, "IELTS 밴드는 0~9, 0.5 단위"),
               ("topik", lambda g: g in "123456" and len(g) == 1, "TOPIK 등급은 1~6급"),
               ("日本語能力", lambda g: g in ("n1", "n2", "n3", "n4", "n5"), "JLPT 레벨은 N1~N5"),
               ("hsk", lambda g: g.isdigit() and 1 <= int(g) <= 9, "HSK 등급은 1~9급"),
               ("汉语水平", lambda g: g.isdigit() and 1 <= int(g) <= 9, "HSK 등급은 1~9급"),
               ("컴퓨터활용능력", lambda g: g in ("1", "2"), "컴퓨터활용능력은 1·2급")]
# 증명서는 양식이 제각각이라 라벨 검사를 못 한다. 대신 값이 페이지 글자에 그대로 있어야 한다(없는 등급을 지어낸 경우: 수료증에 'N2')
NEED_ON_PAGE = {"cert_holder", "cert_title", "cert_grade", "cert_issuer", "cert_date", "cert_no"}   # 시험·과정 이름도(오류 주입에서 유일하게 놓친 칸)
# 증명서에서 취득일과 헷갈리는 날짜의 칸 이름(공백 없이, 소문자)
DATE_DECOY = ["생년월일", "발급일", "dateofbirth", "birth", "dateofissue", "issued", "生年月日", "発行日", "出生日期", "发证日期"]
ROLE_ORDER = {"seller_name": 0, "buyer_name": 1}     # 서명란: '성명 이름' 줄에서 매도인이 첫째, 매수인이 둘째
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
ERA = {"令和": 2018, "平成": 1988, "昭和": 1925}
ZH_NUM_VAL = {c: i for i, c in enumerate("一二三四五六七八九", 1)}
_MON = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
DATE_FORMS = [   # (패턴, 연·월·일 그룹 순서) — 한국식은 dates_in이 맡는다
    (re.compile(r"((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?"), "ymd"),
    (re.compile(r"(令和|平成|昭和)\s*(\d{1,2}|元)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?"), "era"),
    (re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r",?\s+((?:19|20)\d{2})", re.I), "dmy"),
    (re.compile(_MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+((?:19|20)\d{2})", re.I), "mdy"),
]
# shortcut: 한자 숫자 날짜(二〇二五年三月十二日)는 읽지 못한다. 실제 중국 증명서에서 나오면 여기에 추가.


def any_dates(text):
    """텍스트 안의 날짜(한국어·영어·일본어·중국어 표기) → [(연, 월, 일, 시작 위치)]. 없는 날짜는 뺀다."""
    out = [(int(m[1]), int(m[2]), int(m[3]), m.start()) for m in dates_in(text)]
    for pat, order in DATE_FORMS:
        for m in pat.finditer(text):
            if order == "ymd":
                y, mo, d = int(m[1]), int(m[2]), int(m[3])
            elif order == "era":
                y, mo, d = ERA[m[1]] + (1 if m[2] == "元" else int(m[2])), int(m[3]), int(m[4])
            elif order == "dmy":
                y, mo, d = int(m[3]), EN_MON[m[2][:3].lower()], int(m[1])
            else:
                y, mo, d = int(m[3]), EN_MON[m[1][:3].lower()], int(m[2])
            if 1 <= mo <= 12 and 1 <= d <= 31 and (y, mo, d) not in [o[:3] for o in out]:
                out.append((y, mo, d, m.start()))
    return out


JA_ONLY = ("証", "検", "験", *ERA)        # 일본식 한자·연호(중국어는 证/證, 检/檢, 验/驗)
ZH_ONLY = set("证书级汉语试绩兹发颁")      # 간체자: 일본어에는 안 쓴다


def lang_guess(page_lines_list):
    """-> (언어 코드, 확실한지). 한자만 있고 일본식·간체 신호가 둘 다 없으면 ('zh', False)."""
    text = " ".join(L["text"] for lines in page_lines_list for L in lines)
    kana = sum("぀" <= c <= "ヿ" for c in text)
    hangul = sum("가" <= c <= "힣" for c in text)
    han = sum("一" <= c <= "鿿" for c in text)
    if kana >= 3:
        return "ja", True
    if hangul >= 10 and hangul >= han:
        return "ko", True
    if han < 5:
        return "en", True
    ja, zh = any(w in text for w in JA_ONLY), any(c in ZH_ONLY for c in text)
    return ("ja", True) if ja and not zh else ("zh", zh and not ja)


def lang_field(page_lines_list):
    """증명서 '문서 언어' 칸. 일본어인지 중국어인지 가릴 신호가 없으면 사람이 고르게 한다."""
    code, sure = lang_guess(page_lines_list)
    other = LANG_LABEL["zh" if code == "ja" else "ja"]
    review = {"status": "review", "reason": "한자만 있어 일본어·중국어를 가리지 못함", "candidates": [LANG_LABEL["ja"], LANG_LABEL["zh"]],
              "compare": other}
    return {"key": "cert_lang", "label": FIELD_LABEL["cert_lang"], "kind": "text", "value": LANG_LABEL[code],
            "status": "auto", "reason": "", "candidates": [], "page": 1, "line_box": None, "compare": "", **({} if sure else review)}


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
    elif kind == "grade":     # 등급은 낱말 단위로(증명서 번호 'N2…' 안의 'N2'를 등급으로 세지 않도록). 五级 = 5级 = Level 5
        key = norm(kind, v)
        out += [(i, 0) for i, L in enumerate(lines) if key and any(grade_key(t) == key for t in re.split(r"[\s:：,/|]+", L["text"]))]
    elif kind == "person":    # 고유 이름: 글자만 비교(대소문자·공백 무시). 거친 스캔에서 OCR이 한 글자쯤 흐리게 읽어도 찾은 것으로 본다
        # \ud55c \uae00\uc790 \ub2e4\ub978 \uac83(\u4f50\u85e4\u5065\u592a \u2260 \u4f50\u85e4\u5065\u5927)\uc740 \ucc3e\uc740 \uac83\uc774 \uc544\ub2c8\ub2e4. OCR\uc774 \ud750\ub9ac\uac8c \uc77d\uc740 \uacbd\uc6b0\ub294 compute_checks\uac00 \ud655\ub300 \uc7ac\ud310\ub3c5\uc73c\ub85c \uac00\ub9b0\ub2e4
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


NAME_PREFIX = ("성명", "이름", "name", "氏名", "姓名", "名前", "소지자", "수험자", "수료자")
NAME_SUFFIX = ("殿", "様", "님", "귀하", "(인)", "印")
KO_PARTICLE = re.compile(r"(에|을|를|이|가|의|은|는|과|와|으로|로|에서)(?![가-힣a-z])")
HIRAGANA = re.compile(r"[ぁ-ゟ]")


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
            left_ok = (a == 0 or not t[a - 1].isalpha() or squash(t[:a]).endswith(labels)
                       or (HIRAGANA.match(t[a - 1]) and not HIRAGANA.match(t[a])))            # 者は日本語…
            right_ok = (b == len(t) or not t[b].isalpha() or t[b:].startswith(NAME_SUFFIX)
                        or (re.match(r"[가-힣]", t[b - 1]) and KO_PARTICLE.match(t, b))          # …능력에 합격
                        or (HIRAGANA.match(t[b]) and not HIRAGANA.match(t[b - 1])))            # …試験に合格
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


def check_field(kind, key, value, lines):
    """-> dict(exists, state, label_ok(None=라벨 없음/해당 없음), kw(키워드로 확인함), ys(성명 줄 y), reason)
    state: pass / contradict(그 자리가 읽혔는데 값이 다름·답이 없음) / unknown(OCR이 그 부분을 못 읽어 판단 불가)"""
    v = clean(kind, value)

    def out(exists, state, label_ok=None, ys=None, reason="", kw=False, suggest=None):
        return {"exists": exists, "state": state, "label_ok": label_ok, "ys": ys, "reason": reason, "kw": kw, "suggest": suggest}

    if kind == "flag":
        word = FLAG_WORD.get(key, "압류")
        expect = word in squash(" ".join(L["text"] for L in lines))
        ok = ("있" in v) == expect
        return out(ok, "pass" if ok else "contradict", reason=f"{word} 키워드 " + ("있음" if expect else "없음"), kw=True)
    if kind == "choice":
        if not format_ok(kind, v):
            return out(False, "contradict", reason="문서 종류를 고르지 못함: " + v[:20])
        # 위에서부터 처음 종류 낱말이 나온 줄(대개 제목)로 정한다. 칸 이름('合格·修了日', '考试/结业日期')에 끌려가지 않도록
        seen = next(([val for words, val in CHOICE_WORDS if any(w in sq for w in words)] for sq in (squash(L["text"]).casefold() for L in lines)
                     if any(w in sq for words, _ in CHOICE_WORDS for w in words)), [])
        if not seen:
            return out(False, "unknown", reason="페이지에서 증명서 종류 낱말을 못 찾음")
        ok = v == seen[0]
        return out(ok, "pass" if ok else "contradict", kw=True, reason="" if ok else f"페이지 낱말로는 {seen[0]}", suggest=None if ok else seen[0])
    if v in ("", "없음"):
        if key in EMPTY_OK:
            words = EMPTY_OK[key]
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
    if not occ and kind == "date" and key in NEED_ON_PAGE:
        # 월·일은 맞고 연도만 다른 날짜가 페이지에 있으면 연도 환산 실수(令和·平成 연호)다.
        # 증명서만: 임대차는 시작일·종료일의 월·일이 같아서 오탐한다(합성 임대차 s07, 2026-10-25)
        y, mo, d = norm("date", v)
        near = [f"{yy}-{mm:02d}-{dd:02d}" for L in lines for yy, mm, dd, _ in any_dates(L["text"]) if (mm, dd) == (mo, d) and yy != y]
        if near:
            return out(False, "contradict", reason=f"페이지에는 연도가 다른 같은 날짜가 있음({near[0]})", suggest=near[0])
    if not occ and key in NEED_ON_PAGE:
        return out(False, "contradict", reason="페이지에서 값을 못 찾음(증명서 칸은 페이지에 그대로 있어야 함)")
    if key == "cert_no" and occ:
        pat = re.compile(r"(?<![0-9A-Za-z])" + spaced(squash(v)) + r"(?![0-9A-Za-z])")
        if not any(pat.search(lines[i]["text"]) for i, _ in occ if i >= 0):
            return out(True, "contradict", reason="더 긴 번호의 일부로만 보임(값이 잘렸을 수 있음)")
    if kind in ("name", "person") and any(i >= 0 for i, _ in occ) and not standalone(kind, v, lines):
        return out(True, "contradict", reason="더 긴 이름의 일부로만 보임(값이 잘렸을 수 있음)")
    if key == "bldg_owner_date" and occ:
        # 소유자현황의 변동일자: '소유권…' 줄이나 그 바로 위 줄(이름·지분·날짜 줄)에 있어야 하고, 여러 명이면 가장 최근 날짜
        sq = [squash(L["text"]) for L in lines]
        near = [i for i in range(len(lines)) if any("소유권" in x for x in sq[i:i + 4])]      # 표 칸 줄바꿈으로 '소유권이전'이 2~3줄 아래
        if near:
            occ = [o for o in occ if o[0] in near]
            ds = [(y, mo, d) for i in near for y, mo, d, _ in any_dates(lines[i]["text"])]
            if occ and norm("date", v) < max(ds):
                y, mo, d = max(ds)
                return out(True, "contradict", reason="가장 최근 변동일이 아님", suggest=f"{y}-{mo:02d}-{d:02d}")
    if key == "bldg_area" and occ:
        # 전유부분 면적: '공용부분' 제목 줄보다 위에 있어야 한다(아래는 계단실·주차장 같은 공용부분 면적)
        head = next((i for i, L in enumerate(lines) if "공용부분" in squash(L["text"])), None)
        if head is not None:
            occ = [o for o in occ if 0 <= o[0] < head]
    if key == "cert_date" and occ and all(i >= 0 and any(w in squash(lines[i]["text"]).casefold() for w in DATE_DECOY) for i, _ in occ):
        return out(True, "contradict", label_ok=False, reason="생년월일·발급일 자리의 날짜(취득일이 아닐 수 있음)")
    if key in LAST_OWNER:
        idx = [i for i, L in enumerate(lines) if "소유자" in squash(L["text"])]
        last = idx[-1] if idx else None
        occ = [o for o in occ if o[0] == last]
        if key in FIRST_DATE:      # 같은 줄에 날짜가 둘이면 첫 번째가 접수일
            ds = dates_in(lines[last]["text"]) if last is not None else []
            if ds:
                occ = [o for o in occ if o[1] == ds[0].start()]
                if not occ:
                    return out(False, "contradict", reason="소유자 줄의 첫 날짜(접수일)와 다름")
    if not occ:
        return out(False, "unknown", reason="페이지에서 값을 못 찾음")
    if key == "balance_due_date":
        # 잔금은 마지막 지급이다: '지불' 줄에 더 늦은 날짜가 있으면 중도금 날짜를 옮겨 쓴 것으로 본다.
        # 라벨 칸('잔 금')을 OCR이 못 읽는 양식에서 이 오류를 놓쳤다(사용자 매매 샘플, 2026-10-23).
        pays = [tuple(int(x) for x in m.groups()) for L in lines if "지불" in squash(L["text"]) for m in DATE_ANY.finditer(L["text"])]
        mine = tuple(int(x) for x in re.findall(r"\d+", v)[:3])
        if pays and mine < max(pays):
            return out(True, "pass", label_ok=False, reason="잔금일보다 늦은 지급일이 있음(잔금은 마지막 지급)")
    if all(i < 0 for i, _ in occ):
        return out(True, "pass")      # 여러 줄에 걸친 값: 존재만 확인
    al = ALIASES.get(key)
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
    if key in ROLE_ORDER and good:      # 서명란 순서: 이 이름이 자기 역할 줄(매도인=첫째, 매수인=둘째)에 있어야 한다
        rl = role_lines(lines)
        want = rl[ROLE_ORDER[key]] if ROLE_ORDER[key] < len(rl) else None
        if want is not None and want not in good:
            return out(True, "pass", label_ok=False, reason=f"서명란 순서: {ROLE_ORDER[key] + 1}번째 성명 줄에 없음")
    return out(True, "pass", label_ok=bool(good), ys=min((lines[i]["cy"] for i in good), default=None),
               reason="" if good else ("라벨과 값 사이에 다른 글자(값이 잘렸을 수 있음)" if trunc else "왼쪽 라벨=" + "/".join(sorted({str(g) for g in gov}))))


def cross_pairs(doc_type, vals, ys):
    """필드 사이 규칙. 어긋나면 [(필드a, 필드b, 사유)] — 어느 쪽이 틀렸는지는 arbitrate()가 가린다."""
    p = []
    if doc_type == "sale":
        d, s = norm("amount", vals.get("down_payment", "")), norm("amount", vals.get("sale_price", ""))
        if d and s and d >= s:
            p.append(("down_payment", "sale_price", "계약금이 매매대금 이상"))
        if vals.get("seller_name") and norm("name", vals["seller_name"]) == norm("name", vals.get("buyer_name", "")):
            p.append(("seller_name", "buyer_name", "매도인=매수인"))
        sy, by = ys.get("seller_name"), ys.get("buyer_name")
        if sy is not None and by is not None and sy >= by:
            p.append(("seller_name", "buyer_name", "서명란 순서(매도인이 위) 어긋남"))
    if doc_type == "lease" and vals.get("lessor_name") and norm("name", vals["lessor_name"]) == norm("name", vals.get("lessee_name", "")):
        p.append(("lessor_name", "lessee_name", "임대인=임차인"))
    if doc_type == "store_lease":
        if vals.get("st_lessor") and norm("name", vals["st_lessor"]) == norm("name", vals.get("st_lessee", "")):
            p.append(("st_lessor", "st_lessee", "임대인=임차인"))
        dep, rent = norm("amount", vals.get("st_deposit", "")), norm("amount", vals.get("st_rent", ""))
        if dep and rent and rent >= dep:
            p.append(("st_rent", "st_deposit", "차임이 보증금 이상"))
    if doc_type == "cert":
        title, g = (vals.get("cert_title") or "").casefold(), grade_key(vals.get("cert_grade", ""))
        for word, ok, why in GRADE_RANGE:
            if re.search(re.escape(word.casefold()) + r"(?![a-z])", title) and g and g != grade_key("없음") and not ok(g):
                p.append(("cert_grade", "cert_grade", why))
        names = [k for k in ("cert_holder", "cert_title", "cert_issuer") if vals.get(k) not in (None, "", "없음")]
        for i, a in enumerate(names):
            for b_ in names[i + 1:]:
                if norm("person", vals[a]) == norm("person", vals[b_]):
                    p.append((a, b_, "두 칸의 값이 같음(다른 칸 값을 옮겨 쓴 것일 수 있음)"))
        d = norm("date", vals.get("cert_date", ""))
        if d and d > tuple(date.today().timetuple()[:3]):
            p.append(("cert_date", "cert_date", "취득일이 오늘보다 뒤"))
    if doc_type == "registry" and vals.get("owner_name") and norm("name", vals["owner_name"]) == norm("name", vals.get("mortgage_holder", "")):
        p.append(("owner_name", "mortgage_holder", "소유자=근저당권자"))
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
# - 주소: 두 답이 다르면 사람 확인(확인용 샘플에서 오류 검출 4/7→6/7, 2026-10-24)
# - 이름: 자모 하나만 다를 때(한 획 정도: 문소율/문소을)만 사용자 판별. 아예 다른 이름은 이 규칙으로는 경고하지 않는다
#   (OCR 줄 없이 읽은 이름은 다른 사람 이름을 고르는 경우가 많아 전부 경고하면 오탐이 너무 많았다)
SECOND_READ_ANY = {"property_address"}
ZOOM_NUMBER = {"cert_no", "cert_grade", "cert_date"}      # 숫자 칸: 확대해 다시 읽은 값과 한 글자라도 다르면 사용자 확인
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


def second_read_issue(key, kind, ans, ans2, strict=False):
    """두 번째 판독과 비교해 사람 확인이 필요하면 사유 문자열, 아니면 ''.
    strict: 두 번 읽기(joint) 방식 — 두 값이 다르기만 하면 사용자 판단(2026-10-28 사용자 결정)."""
    if ans2 is None or ans is None:
        return ""
    a, b = norm(kind, clean(kind, ans)), norm(kind, clean(kind, ans2))
    if not a or not b or a == b:
        return ""
    if strict:
        return f"두 번 읽은 값이 다름({clean(kind, ans)[:20]} / {clean(kind, ans2)[:20]})"
    if key in SECOND_READ_ANY:
        return f"OCR 없이 다시 읽은 값과 다름({clean(kind, ans2)[:30]})"
    if key in ZOOM_NUMBER:
        x, y = (alnum_key(a), alnum_key(b)) if key == "cert_no" else (a, b)
        return f"확대해 다시 읽은 값과 다름({clean(kind, ans2)[:20]})" if x != y else ""
    if kind == "name" and len(a) == len(b) and 1 <= edit_distance(jamo(a), jamo(b)) <= 3:
        return f"이름 {'한 획' if edit_distance(jamo(a), jamo(b)) == 1 else '몇 획'} 차이 — 사용자 확인({a} / {b})"
    if kind == "person":      # 한글 이름은 한 획 차이만, 외국어 이름은 다르면 사람 확인(한 획 기준이 없음)
        if re.fullmatch(r"[가-힣]+", a) and re.fullmatch(r"[가-힣]+", b):
            d = edit_distance(jamo(a), jamo(b))
            return f"이름 {'한 획' if d == 1 else '몇 획'} 차이 — 사용자 확인({a} / {b})" if len(a) == len(b) and 1 <= d <= 3 else ""
        return f"확대해 다시 읽은 이름과 다름({clean(kind, ans2)[:20]})"
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


def zoom_number(model, proc, img, lines, key, value):
    """숫자 칸(증명서 번호·등급·취득일)의 확대 재판독 값. OCR이 숫자 하나를 잘못 읽고(885→865) AI가 따라 쓴 경우를 잡는다.
    칸을 못 찾거나 다시 읽은 글자에서 비교할 값을 못 뽑으면 None(판단 보류)."""
    parts = [q for L in lines for q in L.get("parts", [])]
    if key == "cert_date":
        want = norm("date", value)
        part = next((q for q in parts if any(d[:3] == want for d in any_dates(q["text"]))), None)
        if part is None:
            return None
        ds = any_dates(zoom_raw(model, proc, img, part))
        got = next((d for d in ds if d[:3] == want), ds[0] if ds else None)
        return f"{got[0]}-{got[1]:02d}-{got[2]:02d}" if got else None
    if key == "cert_grade":
        want = grade_key(value)
        part = next((q for q in parts if any(grade_key(t) == want for t in re.split(r"[\s:：,/|]+", q["text"]))), None)
        if part is None:
            return None
        toks = [t for t in re.split(r"[\s:：,/|]+", zoom_raw(model, proc, img, part)) if re.search(r"\d", t)]
        return value if any(grade_key(t) == want for t in toks) else (toks[0] if toks else None)
    k = alnum_key(value)          # cert_no
    part = zoom_box(lines, k, alnum_key)
    if part is None or len(k) < 4:
        return None
    t = alnum_key(zoom_raw(model, proc, img, part))
    w = best_window(k, t)
    return w if t and edit_distance(k, w) <= 2 else None      # 많이 다르면 엉뚱한 칸을 자른 것 — 판단 보류


def compute_checks(gt, docs, answers, cache, crops=None):
    """-> {(doc,key): dict(b_ok, value, l1, l2, l3, lc, pol, status, reason, ...)}
    l1·l2·l3·lc는 이전과 같은 정의(교차 규칙은 짝 둘 다 표시)라 이전 수치와 비교할 수 있다.
    pol = '사람이 봐야 하는 문항' 정책: 라벨 위반 · 값이 있어야 할 자리에서 다른 값(contradict) · 크롭 불일치 · 책임 가린 교차 규칙 · 값을 페이지에서 못 찾음.
    status = 검토 / 검증통과(크롭 일치 · 맞는 라벨 · 키워드 확인 중 하나) / 미검증통과(확인 수단이 없었음)"""
    crops = crops or {}
    res = {}
    for name in docs:
        doc, vals, ys, per = gt[name], {}, {}, {}
        for key, f in doc["fields"].items():
            r = answers.get((name, key))
            if f["b_skip"] or r is None:
                continue
            c = check_field(f["kind"], key, r["ans"], cache[doc["pages"][f["page"] - 1]])
            if (f["kind"] in ("person", "name", "owners") and c["reason"].startswith("페이지에서 값을 못 찾음") and r.get("ans2")
                    and norm(f["kind"], clean(f["kind"], r["ans2"])) == norm(f["kind"], clean(f["kind"], r["ans"]))):
                # OCR이 흐리게 읽어(일본어 蓮→莲, センター→セ一) 페이지 글자에 없어도, 칸을 확대해 따로 읽은 값이 같으면 인쇄된 값으로 본다
                c = {**c, "exists": True, "state": "pass", "reason": ""}
            vals[key], per[key] = clean(f["kind"], r["ans"]), c
            if c["ys"] is not None:
                ys[key] = c["ys"]
        pairs = cross_pairs(doc["doc_type"], vals, ys)
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
        second = {k: second_read_issue(k, doc["fields"][k]["kind"], answers[(name, k)].get("ans"), answers[(name, k)].get("ans2"),
                                       strict=answers[(name, k)].get("strict2", False)) for k in per}
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


# ============================================================================
# 합성 샘플 생성  (이전: make_samples.py)
# ============================================================================
# 실제 양식 3종 기반 샘플 생성기 (v2).
#
# - 등기부등본: 사용자가 가져온 집합건물 열람용 PDF(2·3쪽)를 렌더링한 뒤 값을 지우고 다시 씀 + 갑구/을구 행 추가
# - 주택임대차 표준계약서: 원본 PDF 1쪽의 빈 칸에 값을 채움
# - 부동산매매계약서: 사용자가 한글에서 내보낸 PDF 1쪽의 빈 칸에 값을 채움
#
# GT 형식: samples/ground_truth.json
#   {doc: {doc_type, pages:[png...], fields:{key:{value, kind, page, region(px)|None, b_skip}}}}
# region은 lease/sale에서는 "양식에 고정된 칸"(A경로가 저장해 두는 좌표), registry에서는 실제로 쓴 글자의 bbox.
#
# 실행: pipeline/.venv311/bin/python bridgedocs.py pipeline/make_samples

ROOT = Path(__file__).parent
SRC = ROOT.parent / "문서 예시"
# 맥 글꼴이 없으면(Windows) 맑은 고딕·바탕을 쓴다. 글꼴이 다르면 같은 시드라도 맥과 다른 이미지가 나온다.
_WIN_FONTS = Path("C:/Windows/Fonts")
SANS = "/System/Library/Fonts/AppleSDGothicNeo.ttc" if Path("/System/Library/Fonts/AppleSDGothicNeo.ttc").exists() else str(_WIN_FONTS / "malgun.ttf")
SERIF = "/System/Library/Fonts/Supplemental/AppleMyungjo.ttf" if Path("/System/Library/Fonts/Supplemental/AppleMyungjo.ttf").exists() else str(_WIN_FONTS / "batang.ttc")
INK = (25, 25, 70)       # 손으로 채운 값(남색)
BLACK = (20, 20, 20)

SUR = list("김이박최정강조윤장임한오서신권황안송류홍전고문양손배백허유남심노하곽성차주우구민나진지엄채원천방공")
GIVEN = ["민준", "서연", "하준", "지우", "도윤", "서윤", "예준", "지호", "수아", "시우", "하은", "주원", "지안", "유준",
         "채원", "건우", "소율", "이준", "다은", "현우", "나윤", "준서", "하린", "승민", "아린", "태오", "예린", "재윤"]
BANKS = ["주식회사국민은행", "주식회사하나은행", "주식회사우리은행", "주식회사신한은행", "농협은행주식회사"]
CITY = [("서울특별시", ["강남구", "마포구", "송파구", "성동구"], ["역삼동", "상암동", "잠실동", "성수동"]),
        ("경기도 수원시", ["팔달구", "영통구", "권선구"], ["고등동", "원천동", "서둔동"]),
        ("인천광역시", ["연수구", "남동구"], ["송도동", "구월동"]),
        ("부산광역시", ["해운대구", "수영구"], ["우동", "광안동"])]
APT = ["푸르지오자이", "래미안", "힐스테이트", "e편한세상", "롯데캐슬", "아이파크"]


def rname(rng):
    return rng.choice(SUR) + rng.choice(GIVEN)


def rrn_masked(rng):
    return f"{rng.randint(60, 99)}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}-{rng.choice([1, 2])}******"


def raddr(rng):
    city, gus, dongs = rng.choice(CITY)
    return f"{city} {rng.choice(gus)} {rng.choice(dongs)} {rng.randint(1, 900)}"


def rbuilding(rng):
    return f"{raddr(rng)} {rng.choice(APT)} 제{rng.randint(101, 150)}동 제{rng.randint(2, 18)}층 제{rng.randint(1, 18)}0{rng.randint(1, 9)}호"


def kor_money(n):
    """120000000 -> 일억이천만 (계약서 '금 ___원정' 칸에 쓰는 한글 금액)"""
    digs, sm, big = "영일이삼사오육칠팔구", ["", "십", "백", "천"], ["", "만", "억", "조"]
    groups, out = [], ""
    while n:
        groups.append(n % 10000)
        n //= 10000
    for gi in range(len(groups) - 1, -1, -1):
        g, part = groups[gi], ""
        for pos in range(3, -1, -1):
            d = g // 10 ** pos % 10
            if d:
                part += ("" if d == 1 and pos else digs[d]) + sm[pos]
        out += part + (big[gi] if part else "")
    return out


def pick(v, key, make):
    """문서 만들기: 데이터에 값이 있으면 그 값, 없으면(빈칸·'없음') make()로 무작위 값."""
    x = (v or {}).get(key)
    return make() if x in (None, "", "없음") else x


def pick_amount(v, key, make):
    return int(norm("amount", str(pick(v, key, make))) or 0)


def pick_date(v, key, make):
    x = pick(v, key, make)
    return x if isinstance(x, date) else date(*norm("date", clean("date", str(x))))


def ymd_ko(d, sep=""):
    return f"{d.year}년{sep}{d.month}월{sep}{d.day}일"


# 문서 만들기의 빈칸 그리기. render_doc이 생성기를 부르는 동안만 채운다.
# shortcut: 모듈 전역이라 두 문서를 동시에(스레드 둘) 그리면 섞인다 — 만들기를 작업 스레드로 옮기면 인자로 넘긴다.
_DRAW = {"blank": set(), "mark": {}}
MARK_COLORS = [(255, 0, 0), (0, 170, 0), (0, 0, 255), (255, 0, 255), (0, 190, 190), (255, 140, 0), (140, 0, 255), (130, 90, 0)]


def blank_color(key):
    """key 칸이 빈칸이 아니면 None, 빈칸이면 위치 찾기 색(RGB) 또는 'skip'(그리지 않음)."""
    keys = (key,) if isinstance(key, str) else key or ()
    hit = [k for k in keys if k in _DRAW["blank"]]
    if not hit:
        return None
    return _DRAW["mark"].get(hit[0], "skip")


def hv(text, *keys):
    """HTML 양식의 값 글자. 빈칸이면 흰 글자(자리는 그대로 차지해 표 모양이 바뀌지 않음), 위치 찾기 때는 칸 색 글자."""
    col = blank_color(keys)
    if col is None:
        return text
    rgb = (255, 255, 255) if col == "skip" else col
    return f'<span style="color:#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}">{text}</span>'


class Canvas:
    """pt 좌표로 글자를 쓰고 GT 필드를 기록한다. s = px/pt."""

    def __init__(self, img, s, page_no):
        self.img, self.s, self.page_no = img, s, page_no
        self.d = ImageDraw.Draw(img)
        self.fields = {}

    def font(self, path, size):
        return ImageFont.truetype(path, round(size * self.s))

    def text(self, txt, x, y, size=9.5, path=SANS, anchor="lm", fill=INK, key=None):
        """key: 이 글자가 나타내는 칸(여럿이면 튜플). 그 칸이 빈칸이면 그리지 않거나(문서) 칸 색으로 그린다(위치 찾기)."""
        f, s = self.font(path, size), self.s
        col = blank_color(key)
        if col != "skip":
            self.d.text((x * s, y * s), txt, font=f, fill=col or fill, anchor=anchor)
        return list(self.d.textbbox((x * s, y * s), txt, font=f, anchor=anchor))

    def width(self, txt, size, path=SANS):
        return self.d.textlength(txt, font=self.font(path, size)) / self.s

    def rect_px(self, r):
        return [round(v * self.s) for v in r]

    def rec(self, key, value, kind, region, b_skip=False):
        self.fields[key] = {"value": value, "kind": kind, "page": self.page_no, "region": region, "b_skip": b_skip}

    def whiteout(self, r):
        self.d.rectangle([v * self.s for v in r], fill=(255, 255, 255))

    def line(self, x0, y0, x1, y1, w=0.8, fill=BLACK):
        self.d.line([x0 * self.s, y0 * self.s, x1 * self.s, y1 * self.s], fill=fill, width=max(1, round(w * self.s / 2)))


def render(page, dpi=150):
    pix = page.get_pixmap(dpi=dpi)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples), pix.width / page.rect.width


# ───────────────────────── 등기부등본 ─────────────────────────
COLS = [24, 75, 177, 259, 340, 574]   # 갑구/을구 표의 세로선 x(pt) — 원본 PDF 드로잉에서 읽은 값


def reg_row(c, top, h, cells):
    """표에 행 하나를 덧그린다. cells = [col0..col4] 각각 (줄 리스트)."""
    for x in COLS:
        c.line(x, top, x, top + h)
    c.line(COLS[0], top + h, COLS[-1], top + h)
    for ci, lines in enumerate(cells):
        x = (COLS[ci] + COLS[ci + 1]) / 2 if ci in (0,) else COLS[ci] + 5
        for li, ln in enumerate(lines):
            c.text(ln, x, top + 11 + li * 12.5, 8.6, SERIF, "mm" if ci == 0 else "lm", BLACK)


def build_registry(rng, variant, v=None):
    if v:     # 데이터로 만들 때: 압류가 있으면 3행 변형, 근저당만 있으면 2행, 둘 다 없으면 1행
        seiz, mort = v.get("seizure_flag") == "있음", v.get("mortgage_amount") not in (None, "", "없음")
        variant = 2 if seiz else 1 if mort else 0
    doc = pymupdf.open(form_file("registry"))
    (p2, s), (p3, _) = render(doc[1]), render(doc[2])
    c2, c3 = Canvas(p2, s, 1), Canvas(p3, s, 2)
    addr = pick(v, "property_address", lambda: rbuilding(rng))
    for c in (c2, c3):                               # 두 쪽 모두 상단 소재지 줄 교체
        c.whiteout((20, 71, 580, 90))
        c.text("[집합건물] ", 24, 80, 9.3, SERIF, "lm", BLACK)
        bb = c.text(addr, 24 + c.width("[집합건물] ", 9.3, SERIF), 80, 9.3, SERIF, "lm", BLACK, key="property_address")
    c2.rec("property_address", addr, "text", bb)
    # 표제부(전유) 건물번호 칸도 맞춰준다
    unit = addr[addr.index("제", addr.index("동 ")):] if "동 " in addr and "제" in addr[addr.index("동 "):] else ""
    c2.whiteout((160, 414, 245, 430))
    c2.text(unit.split("동 ")[-1], 162, 422, 9.0, SERIF, "lm", BLACK, key="property_address")

    n_rows = {0: 1, 1: 2, 2: 3, 3: 1, 4: 2}[variant]
    has_mortgage = variant in (1, 2, 3)
    has_seizure = variant == 2
    owners, rrns = [rname(rng) for _ in range(3)], [rrn_masked(rng) for _ in range(3)]
    d0 = date(2018, 1, 1) + timedelta(days=rng.randint(0, 900))
    dates = [d0, d0 + timedelta(days=rng.randint(300, 900)), d0 + timedelta(days=rng.randint(901, 1300))]
    if v:     # 현재 소유자 행에 데이터 값을 넣고, 앞 행 날짜는 그보다 이르게
        ci = n_rows - 1 if n_rows < 3 else 1
        owners[ci] = pick(v, "owner_name", lambda: owners[ci])
        dates[ci] = pick_date(v, "ownership_change_date", lambda: dates[ci])
        for k in range(ci - 1, -1, -1):
            dates[k] = dates[k + 1] - timedelta(days=rng.randint(300, 900))
        if ci + 1 < 3:
            dates[ci + 1] = dates[ci] + timedelta(days=rng.randint(100, 400))

    # 갑구 1행(소유권보존) — 원본 칸 지우고 다시 쓰기
    c2.whiteout((178, 606, 258, 640))
    c2.whiteout((341, 606, 573, 640))

    cur_idx = n_rows - 1 if n_rows < 3 else 1        # 3행 변형: 마지막 행은 가압류, 현재 소유자는 2행

    def owner_block(c, x, y, name, rrn, address, row):
        c.text("소유자 ", x, y, 8.6, SERIF, "lm", BLACK)
        nx = x + c.width("소유자 ", 8.6, SERIF)
        nb = c.text(name, nx, y, 8.6, SERIF, "lm", BLACK, key="owner_name" if row == cur_idx else None)
        rx = nx + c.width(name + "  ", 8.6, SERIF)
        rb = c.text(rrn, rx, y, 8.6, SERIF, "lm", BLACK)
        c.text(address, x + 20, y + 12.5, 8.6, SERIF, "lm", BLACK)
        return nb, rb

    def dates_block(c, x, y, dt, row):
        bb = c.text(ymd_ko(dt), x, y, 8.6, SERIF, "lm", BLACK, key="ownership_change_date" if row == cur_idx else None)
        c.text(f"제{rng.randint(10000, 99999)}호", x, y + 12.5, 8.6, SERIF, "lm", BLACK)
        return bb

    nb = rb = db = None
    nb, rb = owner_block(c2, 346, 617, owners[0], rrns[0], raddr(rng), 0)
    db = dates_block(c2, 182, 617, dates[0], 0)
    if cur_idx == 0:
        cur = (nb, rb, db)
    top = 642
    if n_rows >= 2:
        reg_row(c2, top, 40, [[], [], [], [], []])
        c2.text("2", (COLS[0] + COLS[1]) / 2, top + 11, 8.6, SERIF, "mm", BLACK)
        c2.text("소유권이전", 80, top + 11, 8.6, SERIF, "lm", BLACK)
        db = dates_block(c2, 182, top + 11, dates[1], 1)
        c2.text(ymd_ko(dates[1] - timedelta(days=rng.randint(5, 40))), 264, top + 11, 8.6, SERIF, "lm", BLACK,
                key="ownership_change_date" if cur_idx == 1 else None)
        c2.text("매매", 264, top + 23.5, 8.6, SERIF, "lm", BLACK)
        nb, rb = owner_block(c2, 346, top + 11, owners[1], rrns[1], raddr(rng), 1)
        if cur_idx == 1:
            cur = (nb, rb, db)
        top += 40
    if n_rows >= 3:
        reg_row(c2, top, 53, [[], [], [], [], []])
        c2.text("3", (COLS[0] + COLS[1]) / 2, top + 11, 8.6, SERIF, "mm", BLACK)
        c2.text("가압류", 80, top + 11, 8.6, SERIF, "lm", BLACK)
        c2.text(ymd_ko(dates[2]), 182, top + 11, 8.6, SERIF, "lm", BLACK)
        c2.text(f"제{rng.randint(10000, 99999)}호", 182, top + 23.5, 8.6, SERIF, "lm", BLACK)
        for li, ln in enumerate([ymd_ko(dates[2] - timedelta(days=3)), "서울중앙지방법원의", "가압류결정"]):
            c2.text(ln, 264, top + 11 + li * 12.5, 8.0, SERIF, "lm", BLACK)
        for li, ln in enumerate([f"채권금액 금{rng.randint(1, 9) * 10000000:,}원", f"채권자 {rng.choice(BANKS)}", "서울특별시 중구 을지로 66"]):
            c2.text(ln, 346, top + 11 + li * 12.5, 8.6, SERIF, "lm", BLACK)
    c2.rec("owner_name", owners[cur_idx], "name", cur[0])
    c2.rec("owner_rrn_masked", rrns[cur_idx], "masked", cur[1], b_skip=True)
    c2.rec("ownership_change_date", dates[cur_idx].isoformat(), "date", cur[2])
    c2.rec("seizure_flag", "있음" if has_seizure else "없음", "flag", None)

    # 을구
    if has_mortgage:
        c3.whiteout((20, 114, 580, 200))
        head = ["순위번호", "등 기 목 적", "접 수", "등 기 원 인", "권리자 및 기타사항"]
        c3.d.rectangle([COLS[0] * s, 116 * s, COLS[-1] * s, 142 * s], outline=BLACK, width=1)
        for ci, h in enumerate(head):
            c3.text(h, (COLS[ci] + COLS[ci + 1]) / 2, 129, 8.6, SERIF, "mm", BLACK)
        for x in COLS[1:-1]:
            c3.line(x, 116, x, 142)
        mdate = dates[cur_idx] + timedelta(days=rng.randint(20, 400))
        amount = pick_amount(v, "mortgage_amount", lambda: rng.randint(1, 15) * 30000000)
        bank = pick(v, "mortgage_holder", lambda: rng.choice(BANKS))
        reg_row(c3, 142, 66, [[], [], [], [], []])
        c3.text("1", (COLS[0] + COLS[1]) / 2, 153, 8.6, SERIF, "mm", BLACK)
        c3.text("근저당권설정", 80, 153, 8.6, SERIF, "lm", BLACK)
        c3.text(ymd_ko(mdate), 182, 153, 8.6, SERIF, "lm", BLACK)
        c3.text(f"제{rng.randint(10000, 99999)}호", 182, 165.5, 8.6, SERIF, "lm", BLACK)
        c3.text(ymd_ko(mdate - timedelta(days=1)), 264, 153, 8.0, SERIF, "lm", BLACK)
        c3.text("설정계약", 264, 165.5, 8.0, SERIF, "lm", BLACK)
        c3.text("채권최고액 금", 346, 153, 8.6, SERIF, "lm", BLACK)
        ab = c3.text(f"{amount:,}", 346 + c3.width("채권최고액 금", 8.6, SERIF), 153, 8.6, SERIF, "lm", BLACK, key="mortgage_amount")
        c3.text("원", 346 + c3.width(f"채권최고액 금{amount:,}", 8.6, SERIF), 153, 8.6, SERIF, "lm", BLACK)
        c3.text("채무자 ", 346, 165.5, 8.6, SERIF, "lm", BLACK)
        c3.text(owners[cur_idx], 346 + c3.width("채무자 ", 8.6, SERIF), 165.5, 8.6, SERIF, "lm", BLACK, key="owner_name")
        c3.text("근저당권자 ", 346, 178, 8.6, SERIF, "lm", BLACK)
        hb = c3.text(bank, 346 + c3.width("근저당권자 ", 8.6, SERIF), 178, 8.6, SERIF, "lm", BLACK, key="mortgage_holder")
        c3.text("서울특별시 영등포구 국제금융로8길 26", 366, 190.5, 8.6, SERIF, "lm", BLACK)
        c3.text("-- 이 하 여 백 --", 302, 222, 9, SERIF, "mm", BLACK)
        c3.text("관할등기소 수원지방법원 등기국", 410, 245, 9, SERIF, "lm", BLACK)
        c3.rec("mortgage_amount", f"{amount:,}", "amount", ab)
        c3.rec("mortgage_holder", bank, "text", hb)
    else:
        c3.rec("mortgage_amount", "없음", "text", None)
        c3.rec("mortgage_holder", "없음", "text", None)
    return [p2, p3], {**c2.fields, **c3.fields}


# ───────────────────────── 주택임대차표준계약서 ─────────────────────────
def build_lease(rng, _, v=None):
    doc = pymupdf.open(form_file("lease"))
    img, s = render(doc[0])
    c = Canvas(img, s, 1)

    def put_center(key, value, kind, region, size=10.5):
        x0, y0, x1, y1 = region
        c.text(value, (x0 + x1) / 2, (y0 + y1) / 2, size, SANS, "mm", key=key)
        c.rec(key, value, kind, c.rect_px(region))

    def put_amount(key, v, region):
        x0, y0, x1, y1 = region
        c.text(f"{v:,}", x0 + 3, (y0 + y1) / 2, 10.5, SANS, "lm", key=key)
        c.rec(key, f"{v:,}", "amount", c.rect_px(region))

    def put_ymd(key, dt, centers, y, region):
        for txt, cx in zip((str(dt.year), str(dt.month), str(dt.day)), centers):
            c.text(txt, cx, y, 10.5, SANS, "mm", key=key)
        c.rec(key, dt.isoformat(), "date", c.rect_px(region))

    lessor, lessee = pick(v, "lessor_name", lambda: rname(rng)), pick(v, "lessee_name", lambda: rname(rng))
    put_center("lessor_name", lessor, "name", (98, 92, 172, 106))
    put_center("lessee_name", lessee, "name", (254, 92, 324, 106))
    c.whiteout((104, 140, 162, 154))                                   # "(도로명주소)" 안내 문구
    c.text(rbuilding(rng).split(" 제")[0], 108, 146.5, 9.5)
    c.text("대", 152, 162.5, 9.5)
    c.text("201487.7", 560, 162.5, 9.5, anchor="rm")
    c.text("철근콘크리트구조 아파트", 160, 179.5, 9.5)
    c.text("84.97", 560, 179.5, 9.5, anchor="rm")
    c.whiteout((104, 191, 262, 204))
    c.text(f"제{rng.randint(101, 150)}동 제{rng.randint(2, 18)}층 제{rng.randint(1, 18)}0{rng.randint(1, 9)}호", 108, 196.5, 9.5)
    c.text("84.97", 560, 196.5, 9.5, anchor="rm")
    c.line(108, 218, 114, 214.5, 1.2, INK); c.line(114, 214.5, 120, 208, 1.2, INK)   # 신규 계약 체크

    deposit = pick_amount(v, "deposit_amount", lambda: rng.randint(2, 90) * 10000000)
    rent = pick_amount(v, "monthly_rent", lambda: rng.randint(6, 50) * 50000)
    c.text(kor_money(deposit), 125, 405.5, 10.5, key="deposit_amount")
    put_amount("deposit_amount", deposit, (326, 398, 456, 413))
    down = deposit // 10
    c.text(kor_money(down), 122, 425.5, 9.5, key="deposit_amount")         # 계약금·중도금·잔금은 보증금에서 나온다
    c.text(f"{down:,}", 238, 425.5, 10, anchor="lm", key="deposit_amount")
    mid_d = date(2024, 1, 1) + timedelta(days=rng.randint(0, 800))
    start = pick_date(v, "contract_start_date", lambda: mid_d + timedelta(days=rng.randint(30, 120)))
    mid_d = min(mid_d, start - timedelta(days=30))
    end = pick_date(v, "contract_end_date", lambda: date(start.year + 2, start.month, start.day) - timedelta(days=1))
    for row_y, amt, dt in ((444.5, deposit // 2 - down, mid_d), (464.5, deposit - deposit // 2, start - timedelta(days=1))):
        c.text(kor_money(amt), 122, row_y, 9.5, key="deposit_amount")
        c.text(f"{amt:,}", 238, row_y, 10, anchor="lm", key="deposit_amount")
        for txt, cx in zip((str(dt.year), str(dt.month), str(dt.day)), (343, 403, 456)):
            c.text(txt, cx, row_y, 10, anchor="mm", key="contract_start_date")     # 날짜는 시작일에서 나온다
    put_amount("monthly_rent", rent, (118, 476, 199, 491))
    c.text(str(rng.choice([1, 5, 10, 15, 25])), 270, 483.5, 10.5, anchor="mm")
    put_ymd("contract_start_date", start, (444, 489, 527), 654, (422, 646, 543, 660))
    put_ymd("contract_end_date", end, (291, 350, 409), 666, (262, 661, 435, 675))
    return [img], c.fields


# ───────────────────────── 부동산매매계약서 (사용자가 HWP에서 내보낸 PDF 1쪽에 값 채우기) ─────────────────────────
def build_sale(rng, _, v=None):
    doc = pymupdf.open(form_file("sale"))      # 2쪽 중 1쪽만 사용(2쪽은 HWP에 들어 있는 부분 중복본)
    img, s = render(doc[0])
    c = Canvas(img, s, 1)

    def amount(key, v, region, size=9.5):
        x0, y0, x1, y1 = region
        c.text(f"{v:,}", x0 + 3, (y0 + y1) / 2, size, SANS, "lm", key=key)
        c.rec(key, f"{v:,}", "amount", c.rect_px(region))

    def ymd(key, dt, centers, y, region, src=None):
        for txt, cx in zip((str(dt.year), str(dt.month), str(dt.day)), centers):
            c.text(txt, cx, y, 9.5, SANS, "mm", key=key or src)
        if key:
            c.rec(key, dt.isoformat(), "date", c.rect_px(region))

    seller, buyer = pick(v, "seller_name", lambda: rname(rng)), pick(v, "buyer_name", lambda: rname(rng))
    sale = pick_amount(v, "sale_price", lambda: rng.randint(10, 150) * 10000000)
    down = pick_amount(v, "down_payment", lambda: sale // 10)
    mid = (sale - down) // 2
    bal_amt = sale - down - mid
    bal = pick_date(v, "balance_due_date", lambda: date(2024, 1, 1) + timedelta(days=rng.randint(60, 950)))
    start = bal - timedelta(days=rng.randint(60, 150))
    hand = pick_date(v, "handover_date", lambda: bal + timedelta(days=rng.randint(0, 20)))

    c.text(rbuilding(rng).split(" 제")[0] + f" 제{rng.randint(101, 150)}동 제{rng.randint(2, 18)}층", 100, 115.5, 9.5)   # 소재지(장식)
    c.text("대", 152, 146, 9.5); c.text("201487.7", 540, 146, 9.5, anchor="rm")
    c.text("철근콘크리트구조 아파트", 150, 164, 9.5); c.text("84.97", 540, 164, 9.5, anchor="rm")

    # 매매대금 / 계약금: "금 [한글] 원정(₩ [숫자])" 형태
    c.text(kor_money(sale), 118, 202.5, 9.5, key="sale_price")
    amount("sale_price", sale, (410, 197, 536, 210))
    c.text(kor_money(down), 114, 217.5, 9.0, key="down_payment")
    c.text(f"({down:,})", 176, 217.5, 9.0, key="down_payment")
    c.rec("down_payment", f"{down:,}", "amount", c.rect_px((172, 212, 246, 224)))
    c.text(seller, 428, 217.5, 9.5, key="seller_name")                                                            # 영수자
    # 중도금 1행 / 잔금 (금액 + 날짜)
    c.text(f"{mid:,}", 118, 246.5, 9.5, key=("sale_price", "down_payment"))           # 중도금·잔금은 매매대금·계약금에서 나온다
    ymd(None, start - timedelta(days=rng.randint(10, 40)), (376, 421, 460), 246.5, None, src="balance_due_date")
    c.text(f"{bal_amt:,}", 118, 276.5, 9.5, key=("sale_price", "down_payment"))
    ymd("balance_due_date", bal, (376, 421, 460), 276.5, (357, 271, 492, 283))
    ymd("handover_date", hand, (223, 267, 303), 299, (202, 293, 318, 305))
    ymd(None, start, (441, 477, 512), 578, None, src="balance_due_date")                                               # 계약일

    # 서명란: 매도인(y 584~) / 매수인(y 639~)
    for name, top, key in ((seller, 584, "seller_name"), (buyer, 639, "buyer_name")):
        c.text(raddr(rng), 138, top + 8.5, 9.0)
        c.text(rrn_masked(rng), 138, top + 26.5, 9.5)
        c.text(f"010-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}", 352, top + 26.5, 9.0)
        c.text(name, 477, top + 26.5, 10, anchor="mm", key=key)
        c.rec(key, name, "name", c.rect_px((449, top + 20, 506, top + 34)))
    return [img], c.fields


# ───────────────────────── 열화 + 저장 ─────────────────────────
# ───────────────────────── 추가 양식 2종 (HTML → PDF → 이미지) ─────────────────────────
# 실제 양식 PDF가 없는 문서는 공식 서식의 칸 구성을 따라 HTML 표로 그린다(PyMuPDF Story).
# 칸 좌표(region)는 만들지 않는다 → A경로 칸 읽기 대상이 아니고 B경로·환각검증만 본다.
_HTML_CSS = """
@font-face {font-family: K; src: url(malgun.ttf);}
@font-face {font-family: K; src: url(malgunbd.ttf); font-weight: bold;}
* {font-family: K; font-size: 8.6pt; line-height: 1.35;}
h1 {font-size: 15pt; text-align: center; margin: 4px 0 6px 0;}
table {border-collapse: collapse; width: 100%; margin-bottom: 6px;}
th, td {border: 0.8px solid #000; padding: 3px 4px;}
th {background-color: #e8e8e8; font-weight: normal; text-align: center;}
.v {color: #191946;} .note {font-size: 7.4pt; color: #333;} .sec {font-weight: bold; margin: 6px 0 2px 0;}
.stamp {color: #b00000; border: 1.5px solid #b00000; font-weight: bold; padding: 2px 6px;}
"""


def render_html(html, css=""):
    """HTML 한 덩어리 → 쪽 이미지 목록(150dpi). css는 기본 글꼴·표 모양 뒤에 덧붙인다."""
    story = pymupdf.Story(html=html, user_css=_HTML_CSS + css, archive=pymupdf.Archive(str(_WIN_FONTS)))
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    page_rect = pymupdf.paper_rect("a4")
    more = 1
    while more:
        dev = writer.begin_page(page_rect)
        more, _ = story.place(page_rect + (36, 36, -36, -36))
        story.draw(dev)
        writer.end_page()
    writer.close()
    doc = pymupdf.open("pdf", buf.getvalue())
    return [render(pg)[0] for pg in doc]


def _f(value, kind, b_skip=False):
    return {"value": value, "kind": kind, "page": 1, "region": None, "b_skip": b_skip}


USES = ["아파트", "아파트(일부공유면적포함)", "오피스텔", "다세대주택", "숙박시설(생활숙박시설)", "제1종근린생활시설(소매점)"]
SHARES = {2: [("1/2", "1/2"), ("15/100", "85/100"), ("3/10", "7/10"), ("40/100", "60/100")], 3: [("1/3", "1/3", "1/3"), ("50/100", "25/100", "25/100")]}


def _dot2(d):
    return f"{d.year}.{d.month}.{d.day}."      # 실제 대장 표기(2019.12.20.)


def ledger_values(rng):
    """건축물대장 칸 값 하나를 무작위로 만든다(문서 만들기 make 명령과 같은 형식)."""
    city, gus, dongs = rng.choice(CITY)
    n = rng.choices([1, 2, 3], [70, 25, 5])[0]
    shares = rng.choice(SHARES[n]) if n > 1 else ("1/1",)
    d = date(rng.randint(1995, 2026), rng.randint(1, 12), rng.randint(1, 28))
    owners = []
    for i in range(n):
        who = "주식회사" + rng.choice(APT) + "개발" if n == 1 and rng.random() < 0.1 else rname(rng)
        owners.append((who, shares[i]))
    return {"bldg_address": f"{city} {rng.choice(gus)} {rng.choice(dongs)}", "bldg_owner": owners_text(owners),
            "bldg_owner_date": d.isoformat(), "bldg_main_use": rng.choice(USES),
            "bldg_area": f"{rng.randint(24, 140)}.{rng.randint(10, 99)}", "bldg_violation": "있음" if rng.random() < 0.2 else "없음"}


def ledger_pages(v, rng):
    """집합건축물대장(전유부, 갑) 2쪽. 실제 정부24 발급본처럼 왼쪽 전유·공용부분, 오른쪽 소유자현황(현소유자만),
    2쪽에는 공동주택가격 기준일(날짜가 많아 헷갈리는 자리)을 둔다."""
    import html
    v = {k: html.escape(str(x), quote=False) for k, x in v.items()}     # make 데이터 값이 HTML 태그로 해석되지 않도록
    owners = parse_owners(v["bldg_owner"]) or [(v["bldg_owner"], "")]
    od = date.fromisoformat(v["bldg_owner_date"])
    site = v["bldg_address"]
    gu = site.split()[1] if len(site.split()) > 1 else "중구"
    floor = rng.randint(2, 25)
    road = ["중앙로", "월드컵북로", "송파대로", "광안해변로"]
    head = (f'<table><tr><th>건물ID</th><td class="v">22200{rng.randint(10**9, 10**10 - 1)}</td><th>고유번호</th>'
            f'<td class="v">{rng.randint(1100000000, 4900000000)}-3-{rng.randint(10**7, 10**8 - 1)}</td><th>명칭</th>'
            f'<td class="v">{rng.choice(APT)} 제{rng.randint(101, 130)}동</td><th>호명칭</th><td class="v">{floor}{rng.randint(1, 9):02d}호</td></tr>'
            f'<tr><th>대지위치</th><td class="v" colspan="3">{hv(site, "bldg_address")}</td><th>지번</th><td class="v">{rng.randint(1, 900)}</td>'
            f'<th>도로명주소</th><td class="v">{hv(f"{site.split()[0]} {gu} {rng.choice(road)} {rng.randint(1, 300)}", "bldg_address")}</td></tr></table>')
    left = [f'<td class="v">주</td><td class="v">{floor}층</td><td class="v">철근콘크리트구조</td><td class="v">{hv(v["bldg_main_use"], "bldg_main_use")}</td><td class="v">{hv(v["bldg_area"], "bldg_area")}</td>',
            '<td></td><td></td><td colspan="2" style="text-align:center">- 이하여백 -</td><td></td>',
            '<th colspan="5">공 용 부 분</th>', '<th>구분</th><th>층별</th><th>구조</th><th>용도</th><th>면적(㎡)</th>']
    for use in rng.sample(["계단실,승강기", "지하주차장", "기계실,전기실", "관리사무소,경로당"], 3):
        left.append(f'<td class="v">{rng.choice("주부")}</td><td class="v">{rng.choice(["지1", "각층", "지2,지1"])}</td>'
                    f'<td class="v">철근콘크리트구조</td><td class="v">{use}</td><td class="v">{rng.randint(1, 80)}.{rng.randint(10, 99)}</td>')
    right = []
    for i, (name, share) in enumerate(owners):
        d = od if i == 0 or rng.random() < 0.7 else od - timedelta(days=rng.randint(30, 900))
        right.append(f'<td class="v">{hv(name, "bldg_owner")}</td><td class="v" rowspan="2">{raddr(rng)}</td>'
                     f'<td class="v" rowspan="2">{hv(share or "1/1", "bldg_owner")}</td><td class="v">{hv(_dot2(d), "bldg_owner_date")}</td>')
        right.append(f'<td class="v">{rrn_masked(rng)}</td><td class="v">소유권이전</td>')
    right.append('<td colspan="4" style="text-align:center">- 이하여백 -<br>※ 이 건축물대장은 현소유자만 표시한 것입니다.</td>')
    rows = "".join(f"<tr>{l}{r}</tr>" for l, r in zip(left + [""] * max(0, len(right) - len(left)), right + [""] * max(0, len(left) - len(right))))
    p1 = f"""
<p>문서확인번호 : {rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}-{rng.randint(10000, 99999)}</p>
<p style="text-align:right">{'<span class="stamp">위반건축물</span>' if v["bldg_violation"] == "있음" and blank_color("bldg_violation") is None else '&nbsp;'}</p>
<h1>집합건축물대장(전유부, 갑)</h1>
<p class="note">■ 건축물대장의 기재 및 관리 등에 관한 규칙 [별지 제5호서식] &nbsp;&nbsp;&nbsp; (2쪽 중 제1쪽)</p>
{head}
<table><tr><th colspan="5">전 유 부 분</th><th colspan="4">소 유 자 현 황</th></tr>
<tr><th>구분</th><th>층별</th><th>※구조</th><th>용도</th><th>면적(㎡)</th><th>성명(명칭) / 주민(법인)등록번호</th><th>주소</th><th>소유권 지분</th><th>변동일자 / 변동원인</th></tr>
{rows}</table>
<p>이 등(초)본은 건축물대장의 원본내용과 틀림없음을 증명합니다.</p>
<p style="text-align:right">발급일 : 2026년 10월 {rng.randint(1, 25)}일 &nbsp; 담당자 : 부동산정보과 &nbsp; 전화 : 02-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}</p>
<p style="text-align:center">{hv(gu + "청장", "bldg_address")}</p>
"""
    prices = "".join(f'<tr><td class="v">{y}.1.1.</td><td class="v">{rng.randint(200, 3000) * 1_000_000:,}</td></tr>' for y in range(2026, 2016, -1))
    p2 = f"""<p style="text-align:right">(2쪽 중 제2쪽)</p>{head}
<p class="sec">공동주택 (아파트) 가격 (단위 : 원)</p><table><tr><th>기 준 일</th><th>공동주택 (아파트) 가격</th></tr>{prices}</table>
<p class="sec">변동사항</p><table><tr><th>변동일</th><th>변동내용 및 원인</th><th>그 밖의 기재사항</th></tr><tr><td></td><td>- 이하여백 -</td><td></td></tr></table>"""
    return render_html(p1)[:1] + render_html(p2)[:1]


def build_ledger(rng, _, v=None):
    v = {**ledger_values(rng), **{k: x for k, x in (v or {}).items() if x not in (None, "")}}
    kinds = DOC_FIELDS["ledger"]
    return ledger_pages(v, rng), {k: _f(v[k], kinds[k]) for k in kinds}


def build_store_lease(rng, _, v=None):
    """상가건물 임대차 표준계약서 1쪽. 금액 4종(보증금·계약금·차임·관리비)과 환산보증금(보증금+차임×100)이 함께 있다."""
    lessor = pick(v, "st_lessor", lambda: rname(rng))
    lessee = pick(v, "st_lessee", lambda: rname(rng))
    while lessee == lessor and not (v or {}).get("st_lessee"):
        lessee = rname(rng)
    deposit = pick_amount(v, "st_deposit", lambda: rng.choice(range(10, 301, 5)) * 1_000_000)
    rent = pick_amount(v, "st_rent", lambda: rng.choice(range(5, 81)) * 100_000)
    mgmt = pick_amount(v, "st_mgmt_fee", lambda: rng.choice(range(1, 16)) * 50_000)
    down = deposit // 10
    start = pick_date(v, "st_start", lambda: date(rng.randint(2024, 2026), rng.randint(1, 12), rng.randint(1, 28)))
    end = pick_date(v, "st_end", lambda: date(start.year + rng.choice([2, 2, 3, 5]), start.month, start.day) - timedelta(days=rng.choice([0, 1])))
    pay_day = rng.randint(1, 28)
    mid = rng.random() < 0.4
    bal = deposit - down - (deposit // 5 if mid else 0)
    bal_d = start - timedelta(days=rng.randint(0, 3))
    conv = deposit + rent * 100

    def won(n, *keys):
        return f'금 <span class="v">{hv(kor_money(n), *keys)}</span> 원정(₩<span class="v">{hv(f"{n:,}", *keys)}</span>)'

    def ymd(d, *keys):
        return "".join(f'<span class="v">{hv(str(x), *keys)}</span>{u}' for x, u in ((d.year, "년 "), (d.month, "월 "), (d.day, "일")))
    addr = f"{raddr(rng)} {rng.choice(['센트럴상가', '스퀘어몰', '프라자', '타워'])} 제{rng.randint(1, 3)}층 제{rng.randint(101, 120)}호"
    signed = start - timedelta(days=rng.randint(20, 60))
    html = f"""
<p class="note">이 계약서는 「상가건물 임대차보호법」을 기준으로 만들었습니다. 작성시 【작성요령】(별지)을 꼭 확인하시기 바랍니다.</p>
<h1>상가건물 임대차 표준계약서</h1>
<p style="text-align:right">□보증금 있는 월세 &nbsp; □전세 &nbsp; □월세</p>
<p>임대인(<span class="v"> {hv(lessor, "st_lessor")} </span>)과 임차인(<span class="v"> {hv(lessee, "st_lessee")} </span>)은 아래와 같이 임대차 계약을 체결한다</p>
<p class="sec">[임차 상가건물의 표시]</p>
<table><tr><th>소재지</th><td colspan="3" class="v">{addr}</td></tr>
<tr><th>토지</th><td>지목 <span class="v">대</span></td><th>면적</th><td class="v">{rng.randint(300, 9000)}.{rng.randint(1, 9)} ㎡</td></tr>
<tr><th>건물</th><td>구조·용도 <span class="v">철근콘크리트구조 / 제1종근린생활시설</span></td><th>면적</th><td class="v">{rng.randint(800, 9000)}.{rng.randint(1, 9)} ㎡</td></tr>
<tr><th>임차할부분</th><td class="v">{rng.randint(1, 3)}층 일부</td><th>면적</th><td class="v">{rng.randint(20, 200)}.{rng.randint(10, 99)} ㎡</td></tr></table>
<p class="sec">[계약내용]</p>
<p>제1조(보증금과 차임) 위 상가건물의 임대차에 관하여 임대인과 임차인은 합의에 의하여 보증금 및 차임을 아래와 같이 지급하기로 한다.</p>
<table><tr><th>보증금</th><td>{won(deposit, "st_deposit")}</td></tr>
<tr><th>계약금</th><td>{won(down, "st_deposit")}은 계약시에 지급하고 수령함. 수령인 (<span class="v"> {hv(lessor, "st_lessor")} </span> 인)</td></tr>
<tr><th>중도금</th><td>{won(deposit // 5, "st_deposit") + '은 ' + ymd(start - timedelta(days=30), "st_start") + '에 지급하며' if mid else '금 &nbsp;&nbsp;&nbsp;&nbsp; 원정(₩ &nbsp;&nbsp;&nbsp;&nbsp; )은 &nbsp;&nbsp; 년 &nbsp; 월 &nbsp; 일에 지급하며'}</td></tr>
<tr><th>잔금</th><td>{won(bal, "st_deposit")}은 {ymd(bal_d, "st_start")}에 지급한다</td></tr>
<tr><th>차임(월세)</th><td>{won(rent, "st_rent")}은 매월 <span class="v">{pay_day}</span>일에 지급한다. 부가세 □불포함 □포함</td></tr>
<tr><th>환산보증금</th><td>{won(conv, "st_deposit", "st_rent")}</td></tr>
<tr><th>관리비</th><td>{won(mgmt, "st_mgmt_fee")}은 매월 차임과 함께 지급한다.</td></tr></table>
<p class="note">유의사항: ① 당해 계약이 환산보증금을 초과하는 임대차인 경우 확정일자를 부여받을 수 없고, 전세권 등을 설정할 수 있습니다.</p>
<p>제2조(임대차기간) 임대인은 임차 상가건물을 임대차 목적대로 사용·수익할 수 있는 상태로 {ymd(start, "st_start")}까지 임차인에게 인도하고, 임대차기간은 인도일로부터 {ymd(end, "st_end")}까지로 한다.</p>
<p>제3조(임차목적) 임차인은 임차 상가건물을 <span class="v">{rng.choice(['음식점', '카페', '소매점', '학원', '사무실'])}</span>(업종)을 위한 용도로 사용한다.</p>
<p>제4조(사용·관리·수선) ① 임차인은 임대인의 동의 없이 임차 상가건물의 구조·용도 변경 및 전대나 임차권 양도를 할 수 없다.
② 임대인은 계약 존속 중 임차 상가건물을 사용·수익에 필요한 상태로 유지하여야 하고, 임차인은 임대인이 임차 상가건물의 보존에 필요한 행위를 하는 때 이를 거절하지 못한다.</p>
<p>제5조(계약의 해제) 임차인이 임대인에게 중도금(중도금이 없을 때는 잔금)을 지급하기 전까지, 임대인은 계약금의 배액을 상환하고, 임차인은 계약금을 포기하고 계약을 해제할 수 있다.</p>
<p>본 계약을 증명하기 위하여 계약 당사자가 이의 없음을 확인하고 각각 서명·날인 후 임대인, 임차인, 개업공인중개사는 매 장마다 간인하여, 각각 1통씩 보관한다. &nbsp; <span class="v">{signed.year}</span>년 <span class="v">{signed.month}</span>월 <span class="v">{signed.day}</span>일</p>
<table><tr><th rowspan="2">임대인</th><th>주소</th><td colspan="5" class="v">{raddr(rng)}</td></tr>
<tr><th>주민등록번호</th><td class="v">{rrn_masked(rng)}</td><th>전화</th><td class="v">010-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}</td><th>성명</th><td class="v">{hv(lessor, "st_lessor")} (인)</td></tr>
<tr><th rowspan="2">임차인</th><th>주소</th><td colspan="5" class="v">{raddr(rng)}</td></tr>
<tr><th>주민등록번호</th><td class="v">{rrn_masked(rng)}</td><th>전화</th><td class="v">010-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}</td><th>성명</th><td class="v">{hv(lessee, "st_lessee")} (인)</td></tr></table>
"""
    fields = {"st_lessor": _f(lessor, "name"), "st_lessee": _f(lessee, "name"), "st_deposit": _f(f"{deposit:,}", "amount"),
              "st_rent": _f(f"{rent:,}", "amount"), "st_mgmt_fee": _f(f"{mgmt:,}", "amount"),
              "st_start": _f(start.isoformat(), "date"), "st_end": _f(end.isoformat(), "date")}
    pages = render_html(html)
    return pages[:1], fields


# ───────── 자격증·수료증·성적증명서(한국어·영어·일본어·중국어) ─────────
# 국제처(다국어 증명서)와 전공·교양 포트폴리오(자격증)가 같이 쓰는 문서 종류. 양식이 기관마다 달라 칸 좌표 없이 질문으로 읽는다.
# 아래 기관·시험 이름은 시험용 가짜 문서를 만들 때만 쓴다(실제 기관 양식을 흉내 내지 않는 단순한 틀).
JA_SUR, JA_GIVEN = "佐藤 鈴木 高橋 田中 伊藤 渡辺 山本 中村 小林 加藤".split(), "健太 翔太 美咲 陽菜 大輝 結衣 拓海 さくら 蓮 葵".split()
ZH_SUR, ZH_GIVEN = "王 李 张 刘 陈 杨 赵 黄 周 吴".split(), "伟 芳 娜 敏 静 磊 洋 婷 浩然 子涵 欣怡 宇轩".split()
EN_FIRST, EN_LAST = "Emily Michael Sarah David Olivia James Sophia Daniel".split(), "Johnson Smith Brown Garcia Miller Davis Wilson Taylor".split()
ROMA_SUR = {"김": "KIM", "이": "LEE", "박": "PARK", "최": "CHOI", "정": "JUNG", "강": "KANG", "조": "CHO", "윤": "YOON"}
ZH_NUM = "一二三四五六七八九"
EN_MONTH = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
CERT_FONT = {"ko": "K", "en": "E", "ja": "J", "zh": "Z"}
CERT_CSS = """@font-face {font-family: J; src: url(YuGothM.ttc);} @font-face {font-family: Z; src: url(msyh.ttc);}
@font-face {font-family: E; src: url(georgia.ttf);} @font-face {font-family: E; src: url(georgiab.ttf); font-weight: bold;}
.frame {border: 3px double #333; padding: 18px 26px;} .big {font-size: 24pt; text-align: center; letter-spacing: 4px; margin: 10px 0 18px 0;}
.mid, .mid .v {font-size: 11pt; line-height: 1.8;} td, th {font-size: 10pt;} .right {text-align: right;} .center {text-align: center;} .seal {color: #b00000; font-weight: bold;}"""
# (언어, 문서 종류, 시험·과정 이름 후보, 등급 후보, 발급기관 후보)
CERT_KINDS = [
    ("ko", "자격증", ["정보처리기사", "빅데이터분석기사", "산업안전기사", "전기기사"], [""], ["한국산업인력공단"]),
    ("ko", "자격증", ["컴퓨터활용능력"], ["1급", "2급"], ["대한상공회의소"]),
    ("ko", "성적증명서", ["한국어능력시험(TOPIK)"], [f"{i}급" for i in range(1, 7)], ["국립국제교육원"]),
    ("ko", "수료증", ["인공지능 기초 과정", "데이터 분석 실무 과정", "글로벌 리더십 과정"], [""], ["한빛대학교 평생교육원", "새솔대학교 국제교육원"]),
    ("en", "성적증명서", ["TOEIC"], [str(rs) for rs in range(450, 991, 5)], ["ETS Global"]),
    ("en", "성적증명서", ["IELTS Academic"], ["5.5", "6.0", "6.5", "7.0", "7.5", "8.0"], ["IELTS Test Centre"]),
    ("en", "수료증", ["Data Science Bootcamp", "Global Business Program", "Summer English Program"], [""], ["Northfield University", "Lakeside College"]),
    ("ja", "자격증", ["日本語能力試験"], ["N1", "N2", "N3", "N4", "N5"], ["国際日本語教育協会"]),
    ("ja", "수료증", ["日本語研修コース", "ビジネス日本語講座"], [""], ["東和大学 国際センター", "青葉大学 留学生センター"]),
    ("zh", "성적증명서", ["汉语水平考试(HSK)"], [f"{ZH_NUM[i]}级" for i in range(2, 6)], ["中文国际测试中心"]),
    ("zh", "수료증", ["汉语进修项目", "中国文化研修班"], [""], ["华东大学 国际教育学院", "北方师范大学 留学生院"]),
]


def cert_name(rng, lang):
    if lang == "ja":
        return f"{rng.choice(JA_SUR)} {rng.choice(JA_GIVEN)}"
    if lang == "zh":
        return rng.choice(ZH_SUR) + rng.choice(ZH_GIVEN)
    if lang == "en":
        if rng.random() < 0.5:      # 한국 학생의 영문 성적표(로마자 대문자)
            sur = rng.choice(list(ROMA_SUR))
            return f"{ROMA_SUR[sur]} {rng.choice(['MINJUN', 'SEOYEON', 'JIHO', 'HAEUN', 'DOYUN', 'YUNA'])}"
        return f"{rng.choice(EN_FIRST)} {rng.choice(EN_LAST)}"
    return rname(rng)


def cert_date_text(d, lang, rng):
    """언어별 날짜 표기(일본 연호·영문 월 이름 포함) — 엔진이 이 표기들을 모두 날짜로 알아봐야 한다."""
    if lang == "ko":
        return f"{d.year}년 {d.month}월 {d.day}일" if rng.random() < 0.7 else f"{d.year}. {d.month:02d}. {d.day:02d}."
    if lang == "en":
        return f"{EN_MONTH[d.month - 1]} {d.day}, {d.year}" if rng.random() < 0.5 else f"{d.day} {EN_MONTH[d.month - 1]} {d.year}"
    if lang == "ja" and d.year >= 2019 and rng.random() < 0.5:
        return f"令和{d.year - 2018}年{d.month}月{d.day}日"
    return f"{d.year}年{d.month}月{d.day}日"


def cert_values(rng, kind_idx=None):
    """증명서 칸 값 하나를 무작위로 만든다(문서 만들기 make 명령과 같은 형식)."""
    lang, ckind, titles, grades, issuers = CERT_KINDS[rng.randrange(len(CERT_KINDS)) if kind_idx is None else kind_idx]
    got = date(rng.randint(2019, 2026), rng.randint(1, 12), rng.randint(1, 28))
    if got > date(2026, 10, 20):
        got = date(2026, rng.randint(1, 9), got.day)
    holder = cert_name(rng, "en" if lang == "ko" and ckind == "성적증명서" else lang)     # TOPIK은 외국인 → 로마자 이름
    no = {"ko": f"{got.year % 100}{rng.randint(10**8, 10**9 - 1)}", "en": f"{rng.choice('ABCKM')}{rng.randint(10**7, 10**8 - 1)}",
          "ja": f"N{rng.randint(10**7, 10**8 - 1)}", "zh": f"H{got.year}{rng.randint(10**6, 10**7 - 1)}"}[lang]
    grade = rng.choice(grades)
    return {"cert_kind": ckind, "cert_holder": holder, "cert_title": rng.choice(titles), "cert_grade": grade or "없음",
            "cert_issuer": rng.choice(issuers), "cert_date": got.isoformat(), "cert_no": no, "cert_lang": LANG_LABEL[lang]}


L10N = {   # 언어별 칸 이름과 문장
    "ko": {"name": "성 명", "birth": "생년월일", "title": "자격종목", "grade": "등 급", "no": "자격번호", "date": "합격연월일", "issued": "발급일",
           "head": {"자격증": "자 격 증", "수료증": "수 료 증", "성적증명서": "성 적 증 명 서"},
           "body": "위 사람은 {title}{what}하였음을 증명합니다."},
    "en": {"name": "Name", "birth": "Date of Birth", "title": "Program / Test", "grade": "Score", "no": "Certificate No.", "date": "Date Completed",
           "issued": "Date of Issue", "head": {"자격증": "CERTIFICATE", "수료증": "CERTIFICATE OF COMPLETION", "성적증명서": "SCORE REPORT"},
           "body": "This is to certify that the above person has {what} {title}."},
    "ja": {"name": "氏 名", "birth": "生年月日", "title": "試験・課程", "grade": "レベル", "no": "認定番号", "date": "合格・修了日", "issued": "発行日",
           "head": {"자격증": "認 定 書", "수료증": "修 了 証 書", "성적증명서": "成 績 証 明 書"},
           "body": "上記の者は{title}に{what}したことを証明します。"},
    "zh": {"name": "姓 名", "birth": "出生日期", "title": "考试 / 项目", "grade": "级 别", "no": "证书编号", "date": "考试 / 结业日期", "issued": "发证日期",
           "head": {"자격증": "证 书", "수료증": "结 业 证 书", "성적증명서": "成 绩 报 告"},
           "body": "兹证明上述人员已{what}{title}。"},
}
WHAT = {"ko": {"자격증": "에 합격", "수료증": "을 수료", "성적증명서": "에 응시"}, "en": {"자격증": "passed", "수료증": "successfully completed", "성적증명서": "taken"},
        "ja": {"자격증": "合格", "수료증": "修了", "성적증명서": "受験"}, "zh": {"자격증": "通过", "수료증": "完成", "성적증명서": "参加"}}


def cert_pages(v, rng):
    """증명서 1쪽. 반은 테두리가 있는 증서 모양, 반은 표 모양 성적표. 생년월일·발급일이 함께 있어 날짜를 헷갈리기 쉽다."""
    import html
    v = {k: html.escape(str(x), quote=False) for k, x in v.items()}     # make 데이터 값이 HTML 태그로 해석되지 않도록
    lang = LANG_CODE.get(v.get("cert_lang", ""), "ko")
    t = L10N[lang]
    got = date.fromisoformat(v["cert_date"])
    issued = got + timedelta(days=rng.randint(5, 60))
    birth = date(rng.randint(1985, 2006), rng.randint(1, 12), rng.randint(1, 28))
    dt = lambda d: cert_date_text(d, lang, rng)
    grade = "" if v.get("cert_grade", "없음") == "없음" else v["cert_grade"]
    rows = [(t["name"], v["cert_holder"]), (t["birth"], dt(birth)), (t["title"], v["cert_title"])]
    rows += [(t["grade"], grade)] if grade else []
    rows += [(t["no"], v["cert_no"])] if v.get("cert_no", "없음") != "없음" else []
    rows += [(t["date"], dt(got))]
    kind = v.get("cert_kind") if v.get("cert_kind") in t["head"] else "자격증"
    body = t["body"].format(title=v["cert_title"], what=WHAT[lang][kind])
    font = CERT_FONT[lang]
    if rng.random() < 0.5:
        lines = "".join(f'<p class="mid">{k} : <span class="v">{val}</span></p>' for k, val in rows)
        html = (f'<div class="frame" style="font-family:{font}"><p class="big">{t["head"][kind]}</p>'
                f'{lines}<p class="mid" style="margin-top:18px">{body}</p><p class="center mid" style="margin-top:24px">{t["issued"]} : {dt(issued)}</p>'
                f'<p class="center mid" style="font-size:14pt">{v["cert_issuer"]} <span class="seal">[印]</span></p></div>')
    else:
        cells = "".join(f'<tr><th style="font-family:{font}">{k}</th><td class="v" style="font-family:{font}">{val}</td></tr>' for k, val in rows)
        html = (f'<div style="font-family:{font}"><p class="big">{t["head"][kind]}</p><p class="center mid">{v["cert_issuer"]}</p>'
                f'<table>{cells}</table><p class="mid">{body}</p><p class="right mid">{t["issued"]} : {dt(issued)}</p>'
                f'<p class="right mid">{v["cert_issuer"]} <span class="seal">[印]</span></p></div>')
    return render_html(html, CERT_CSS)[:1]


def build_cert(rng, i, v=None):
    v = {**cert_values(rng, i % len(CERT_KINDS)), **{k: x for k, x in (v or {}).items() if x not in (None, "")}}
    kinds = DOC_FIELDS["cert"]
    return cert_pages(v, rng), {k: _f(v[k], kinds[k]) for k in kinds}


def deskew_angle(img, max_deg=3.0, step=0.1):
    """글자 줄이 가장 또렷하게 가로로 늘어서는 각도(도). 가로 투영(행별 검은 점 수)의 분산이 가장 큰 각도를 고른다."""
    import numpy as np
    g = img.convert("L")
    g = g.resize((g.width // 2, g.height // 2))
    ink = g.point(lambda x: 255 if x < 160 else 0)     # 먼저 흑백으로 바꾼 뒤 모든 각도를 같은 방식(NEAREST)으로 돌린다.
    best, best_v = 0.0, -1.0                            # (돌리지 않은 0도만 선명해서 늘 0도가 뽑히던 문제를 막음)
    for k in range(int(-max_deg / step), int(max_deg / step) + 1):
        a = k * step
        r = np.asarray(ink.rotate(a, resample=Image.NEAREST, fillcolor=0)) > 0
        v = float(np.var(r.sum(axis=1)))
        if v > best_v:
            best, best_v = a, v
    return best


def preprocess(img):
    """스캔 전처리: 기울기 보정 + 대비 늘리기(기획서 1-4). -> (보정된 이미지, 보정 각도)"""
    from PIL import ImageOps
    a = deskew_angle(img)
    out = img.rotate(a, resample=Image.BICUBIC, fillcolor="white") if abs(a) >= 0.05 else img
    return ImageOps.autocontrast(out.convert("L"), cutoff=1).convert("RGB"), a


# ============================================================================
# docx 직접 읽기: 이미지로 바꾸지 않고 docx 안의 글자를 꺼낸다(본문 문단·표의 한 행을 한 줄로, 취소선 글자는 뺌)
# ============================================================================

WNS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
       "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"}


# 긴 문서(임대차 표준계약서 약 5,800토큰)는 4,400토큰을 넘으면서 GPU 메모리(12GB, 화면이 약 2GB 사용)를 넘쳐
# 질문 하나에 55초가 걸렸다(2026-10-23). 값이 없는 긴 조항 줄을 빼고 줄을 200자로 잘라 약 2,000토큰으로 줄인다.
DOCX_MAX_CHARS = 4000
DOCX_KEEP = re.compile(r"\d{2,}|성\s*명|임대인|임차인|이름|소유자|매도인|매수인|근저당|압류|집합건물")


def _w(tag):
    p, t = tag.split(":")
    return "{%s}%s" % (WNS[p], t)


def _run_text(r):
    rp = r.find(_w("w:rPr"))
    if rp is not None:
        for s in (rp.find(_w("w:strike")), rp.find(_w("w:dstrike"))):
            if s is not None and s.get(_w("w:val")) not in ("0", "false"):
                return ""      # 취소선 = 말소된 글자
    return "".join((t.text or "") if t.tag == _w("w:t") else " " for t in r if t.tag in (_w("w:t"), _w("w:tab")))


def _para_text(p):
    """문단의 글자. 글상자 안 글자는 넣지 않는다(직접 자식 런만 본다)."""
    runs = [c for c in p if c.tag == _w("w:r")] + [r for c in p if c.tag in (_w("w:hyperlink"), _w("w:ins")) for r in c if r.tag == _w("w:r")]
    return re.sub(r"\s+", " ", "".join(_run_text(r) for r in runs)).strip()


def docx_lines(path):
    """-> OCR 줄과 같은 모양의 줄 목록 [{text, x0, x1, y0, y1, cy}]. 좌표는 순서만 뜻하는 가짜 값이다."""
    import xml.etree.ElementTree as ET
    import zipfile
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    xml = re.sub(r"<mc:Fallback>.*?</mc:Fallback>", "", xml, flags=re.S)     # 같은 글상자의 예전 형식 사본
    root = ET.fromstring(xml)
    texts = []

    def walk(el):
        for ch in el:
            if ch.tag == _w("w:p"):
                texts.append(_para_text(ch))
            elif ch.tag == _w("w:tbl"):
                for tr in ch.findall(_w("w:tr")):
                    cells = [" ".join(t for t in (_para_text(p) for p in tc.iter(_w("w:p"))) if t) for tc in tr.findall(_w("w:tc"))]
                    texts.append(" ".join(c for c in cells if c))
            elif ch.tag == _w("w:sdt"):
                walk(ch.find(_w("w:sdtContent")) if ch.find(_w("w:sdtContent")) is not None else ch)
    walk(root.find(_w("w:body")))
    boxes = []
    for a in root.iter(_w("wp:anchor")):
        ph, pv = a.find(_w("wp:positionH")), a.find(_w("wp:positionV"))
        t = " ".join(x for x in (_para_text(p) for p in a.iter(_w("w:p"))) if x)
        if t and ph is not None and pv is not None and ph.find(_w("wp:posOffset")) is not None and pv.find(_w("wp:posOffset")) is not None:
            boxes.append((int(pv.find(_w("wp:posOffset")).text), int(ph.find(_w("wp:posOffset")).text), t))
    rows = []
    for y, x, t in sorted(boxes):
        if rows and abs(rows[-1][0] - y) < 60000:      # 약 1.7mm 안이면 같은 줄
            rows[-1][1].append((x, t))
        else:
            rows.append((y, [(x, t)]))
    if rows:
        texts.append("[글상자에 채워진 값 — 위에서 아래 순서]")
        texts += [" ".join(t for _, t in sorted(r)) for _, r in rows]
    texts = [t for t in texts if t]
    return [{"text": t, "x0": 0, "x1": 1000, "y0": i * 20, "y1": i * 20 + 10, "cy": i * 20 + 5} for i, t in enumerate(texts)]


def ask_text(model, proc, prompt, max_new=64):
    import torch
    msgs = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
    text = proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    inputs = proc(text=[text], return_tensors="pt").to(model.device)
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=max_new, do_sample=False)
    return proc.batch_decode(out[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0].strip()


# ============================================================================
# 엔진 진입점: 정답 없이 파일 → 값 + 자동 확정/확인 필요 (서버·화면이 쓰는 형식)
# ============================================================================
# 실행: python bridgedocs.py run <파일...> [--out 결과.json] [--deskew]
#   파일: PDF(여러 쪽 가능) 또는 쪽 이미지(png/jpg) 여러 장. 한 번에 문서 하나.
# 흐름: 쪽 이미지 → (선택) 기울기 보정 → 페이지 OCR → 문서 종류 판별 → B경로(OCR 줄 + 주소·이름 두 번째 판독)
#       → 규칙 검사(크롭 재질문 없음: 오탐만 늘어서 2026-10-24에 뺌) → JSON

DOC_FIELDS = {   # 문서 종류별 칸과 종류(kind). 정답 파일 없이도 엔진이 무엇을 물을지 안다
    "registry": {"property_address": "text", "owner_name": "name", "ownership_change_date": "date", "seizure_flag": "flag",
                 "mortgage_amount": "amount", "mortgage_holder": "text"},
    "lease": {"lessor_name": "name", "lessee_name": "name", "deposit_amount": "amount", "monthly_rent": "amount",
              "contract_start_date": "date", "contract_end_date": "date"},
    "sale": {"seller_name": "name", "buyer_name": "name", "sale_price": "amount", "down_payment": "amount",
             "balance_due_date": "date", "handover_date": "date"},
    "ledger": {"bldg_address": "text", "bldg_owner": "owners", "bldg_owner_date": "date", "bldg_main_use": "text",
               "bldg_area": "number", "bldg_violation": "flag"},
    "store_lease": {"st_lessor": "name", "st_lessee": "name", "st_deposit": "amount", "st_rent": "amount", "st_mgmt_fee": "amount",
                    "st_start": "date", "st_end": "date"},
    "cert": {"cert_kind": "choice", "cert_holder": "person", "cert_title": "person", "cert_grade": "grade", "cert_issuer": "person",
             "cert_date": "date", "cert_no": "text", "cert_lang": "text"},
}
COMPUTED = {"cert_lang"}     # 모델에 묻지 않고 엔진이 정하는 칸(문서 언어)
LANG_LABEL = {"ko": "한국어", "en": "영어", "ja": "일본어", "zh": "중국어"}
LANG_CODE = {v: k for k, v in LANG_LABEL.items()}
FIELD_LABEL = {
    "property_address": "소재지", "owner_name": "소유자", "ownership_change_date": "소유권 접수일", "seizure_flag": "압류·가압류",
    "mortgage_amount": "채권최고액", "mortgage_holder": "근저당권자",
    "lessor_name": "임대인", "lessee_name": "임차인", "deposit_amount": "보증금", "monthly_rent": "차임(월세)",
    "contract_start_date": "임대차 시작일", "contract_end_date": "임대차 종료일",
    "seller_name": "매도인", "buyer_name": "매수인", "sale_price": "매매대금", "down_payment": "계약금",
    "balance_due_date": "잔금일", "handover_date": "인도일",
    "bldg_address": "대지위치", "bldg_owner": "소유자", "bldg_owner_date": "소유권 변동일", "bldg_main_use": "주 용도",
    "bldg_area": "전용면적(㎡)", "bldg_violation": "위반건축물",
    "st_lessor": "임대인", "st_lessee": "임차인", "st_deposit": "보증금", "st_rent": "차임(월세)", "st_mgmt_fee": "관리비",
    "st_start": "임대차 시작일", "st_end": "임대차 종료일",
    "cert_kind": "증명서 종류", "cert_holder": "이름", "cert_title": "자격·시험·과정", "cert_grade": "등급·점수", "cert_issuer": "발급기관",
    "cert_date": "취득일", "cert_no": "증명서 번호", "cert_lang": "문서 언어",
}
DOC_TYPE_WORDS = [("store_lease", ["상가건물임대차"]), ("lease", ["주택임대차"]), ("ledger", ["건축물대장"]),
                  ("registry", ["등기사항", "갑구", "을구"]), ("sale", ["매매계약서", "매도인"]),
                  ("cert", ["자격증", "수료증", "합격증", "성적증명", "인증서", "certificate", "certify", "scorereport", "testreport",
                            "transcript", "diploma", "証明", "認定", "修了", "合格", "证书", "證書", "证明", "成绩", "結業", "结业"])]
FIELD_PAGE_WORDS = {"owner_name": ["갑구", "소유자"], "ownership_change_date": ["갑구", "소유자"], "seizure_flag": ["갑구"],
                    "mortgage_amount": ["을구", "근저당"], "mortgage_holder": ["을구", "근저당"]}


def detect_doc_type(page_lines_list):
    text = squash(" ".join(L["text"] for lines in page_lines_list for L in lines)).casefold()
    for dtype, words in DOC_TYPE_WORDS:
        if any(w in text for w in words):
            return dtype
    return None


def _field_page(key, page_lines_list):
    for i, lines in enumerate(page_lines_list):
        if any(w in squash(" ".join(L["text"] for L in lines)) for w in FIELD_PAGE_WORDS.get(key, [])):
            return i
    return 0


def load_pages(paths):
    """PDF·이미지 경로들 → 쪽 이미지 목록(150dpi)."""
    pages = []
    for p in map(Path, paths):
        if p.suffix.lower() == ".pdf":
            pages += [render(pg)[0] for pg in pymupdf.open(p)]
        else:
            pages.append(Image.open(p).convert("RGB"))
    return pages


def docx_body(lines):
    """docx 줄 → 질문에 붙일 본문. 긴 문서는 GPU 메모리가 넘치지 않게 값이 있을 만한 줄만 200자씩 남긴다."""
    if sum(len(L["text"]) for L in lines) > DOCX_MAX_CHARS:
        lines = [dict(L, text=L["text"][:200]) for L in lines if DOCX_KEEP.search(L["text"])]
    return "아래는 워드(docx) 문서에서 꺼낸 글자입니다(한 줄에 한 번호, 표는 한 행씩).\n" + "\n".join(f"{i + 1}. {L['text']}" for i, L in enumerate(lines))


def text_question(q):
    """이미지용 질문 → 글자만 주는 질문."""
    return q.replace("문서 이미지에 인쇄·기재된", "문서에 기재된").replace("이미지", "문서")


def read_pages(paths, ocr, deskew=False):
    """문서 → (쪽 이미지 목록, 쪽별 글자 줄, 쪽별 읽은 길). 읽은 길: 'pdf_text'(PDF 안 글자) | 'ocr' | 'docx'.
    PDF는 쪽마다 안의 글자를 믿을 수 있는지 진단해 믿을 수 있으면 그 글자, 아니면 그 쪽만 OCR. 좌표는 모두 150dpi 쪽 이미지 기준."""
    if len(paths) == 1 and Path(paths[0]).suffix.lower() == ".docx":
        return [], [docx_lines(paths[0])], ["docx"]
    layer = {}
    for p in paths:
        if Path(p).suffix.lower() == ".pdf":
            layer[str(p)] = extract_text_layer(p)
    pages, lines_list, routes = [], [], []
    for p in paths:
        if str(p) in layer:
            info = layer[str(p)]
            if info and info[0].get("error"):
                raise ValueError(f"{Path(p).name}: PDF를 열 수 없음({info[0]['error']})")
            for pg, li in zip(pymupdf.open(p), info):
                img = render(pg)[0]
                if li["usable"] and li["items"]:
                    pages.append(img); lines_list.append(lines_xy(li["items"])); routes.append("pdf_text")
                else:
                    pages.append(preprocess(img)[0] if deskew else img); lines_list.append(None); routes.append("ocr")
        else:
            img = Image.open(p).convert("RGB")
            pages.append(preprocess(img)[0] if deskew else img); lines_list.append(None); routes.append("ocr")
    for i, pg in enumerate(pages):
        if lines_list[i] is None:
            tmp = Path(tempfile.gettempdir()) / "_bd_page.png"
            pg.save(tmp)
            lines_list[i] = lines_xy(ocr_items(ocr, tmp))
    return pages, lines_list, routes


def reread_cjk(pages, lines_list, routes):
    """중국어·일본어 문서일 수 있을 때: OCR로 읽은 쪽만 중·일 모델로 다시 읽어, 글자 신뢰도가 더 높은 쪽을 쓴다.
    (한국어 모델은 가나·한자를 엉뚱한 한글로 읽고, 중·일 모델은 한글을 읽지 못해 신뢰도로 갈린다)"""
    out = list(lines_list)
    for i, r in enumerate(routes):
        if r != "ocr":
            continue
        tmp = Path(tempfile.gettempdir()) / "_bd_page_cjk.png"
        pages[i].save(tmp)
        cjk = ocr_items(make_cjk_ocr(), tmp)
        if ocr_quality(cjk) > 1.15 * ocr_quality([q for L in lines_list[i] for q in L.get("parts", [])]):
            out[i] = lines_xy(cjk)
    return out


JOINT_PRE = ("이것은 문서 이미지에 인쇄·기재된 글자를 그대로 옮겨 적는 작업입니다. 아래 항목마다 값을 찾아 "
             "JSON 객체 하나로만 답하세요(설명·문장 없이). 키는 그대로 쓰고, 값은 문자열로 쓰세요. "
             "문서에 없거나 읽을 수 없으면 값을 '없음'으로 쓰세요.\n")


def joint_question(doc_type, keys):
    """칸 여러 개를 한 번에 묻는 질문(칸마다 같은 설명·위치 힌트)."""
    items = "\n".join(f'- "{k}": {V2Q[doc_type][k]} {HINT.get(k, "")}'.rstrip() for k in keys)
    return JOINT_PRE + items


def parse_joint(ans, keys):
    """한 번에 묻기 답 → {칸: 값 문자열}. 답에 없는 칸은 빠진다(JSON이 깨지면 {})."""
    m = re.search(r"\{.*\}", ans or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m[0])
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v)
            for k, v in data.items() if k in keys and v is not None}


def joint_answers(model, proc, doc_type, keys_by_page, pages, lines_list, is_docx, check):
    """문서를 두 번만 읽는다(쪽마다): 1차 = 사진 + OCR 줄, 2차 = 사진만. 1차 JSON에 빠진 칸만 칸마다 따로 묻는다.
    -> {칸: {"ans", "ans2"(없으면 None), "strict2": True}}"""
    out = {}
    for pi, keys in keys_by_page.items():
        q = joint_question(doc_type, keys)
        budget = 40 * len(keys) + 40
        check()
        if is_docx:
            first = parse_joint(ask_text(model, proc, docx_body(lines_list[0]) + "\n\n" + text_question(q), max_new=budget), keys)
            second = {}
        else:
            first = parse_joint(ask(model, proc, pages[pi], with_ocr(q, lines_list[pi]), max_new=budget)[0], keys)
            check()
            second = parse_joint(ask(model, proc, pages[pi], q, max_new=budget)[0], keys)
        for k in keys:
            if k not in first:     # JSON이 깨졌거나 칸이 빠짐: 그 칸만 예전처럼 따로 묻는다
                check()
                one = PROMPTS["v3"][doc_type][k]
                first[k] = (ask_text(model, proc, docx_body(lines_list[0]) + "\n\n" + text_question(one)) if is_docx
                            else ask(model, proc, pages[pi], with_ocr(one, lines_list[pi]), max_new=64)[0])
            out[k] = {"ans": first[k], "ans2": second.get(k), "strict2": True, "norm_c": False}
    return out


# ───────── 문서 종류: AI가 판단(2026-10-28 결정). 답을 알아듣지 못하면 정해 둔 단어로 찾는다 ─────────
TYPE_PRE = "이 문서가 어떤 문서인지 판단하세요."
NEW_TYPE_Q = ("이 문서에서 꼭 필요한 항목(사람 이름·날짜·금액·주소·번호 등)을 골라, 항목 이름을 키로 문서에 적힌 값을 그대로 "
              "JSON 객체 하나로만 답하세요(설명·문장 없이). 항목은 15개 이하로 고르세요.")


def custom_path():
    """사용자가 확정한 새 문서 종류(종류 이름 → 칸 이름들)를 저장하는 파일. 프로그램에서는 exe 옆."""
    return Path(os.environ.get("BD_CUSTOM") or ROOT / "새 문서 종류.json")


def custom_types(strict=False):
    """strict=True면 파일이 깨졌을 때 {} 대신 ValueError(저장하다 기존 종류를 지우지 않게)."""
    p = custom_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError) as err:
        if strict:
            raise ValueError(f"{p.name} 파일이 깨져 있어 저장하지 않았어요 — 파일을 고치거나 옮긴 뒤 다시 하세요 ({err})") from err
        return {}
    return {k: list(v) for k, v in data.items() if isinstance(v, list)} if isinstance(data, dict) else {}


def save_custom_type(name, labels):
    """새 문서 종류와 사용자가 확정한 칸 이름들을 저장한다(다음에 같은 종류가 오면 이 칸들을 읽음)."""
    name = (name or "").strip()
    labels = list(dict.fromkeys(str(x).strip() for x in labels if str(x).strip()))
    if not name or not labels:
        raise ValueError("문서 종류 이름과 칸을 하나 이상 정하세요")
    if resolve_type(name):
        raise ValueError(f"'{name}'은 이미 있는 문서 종류예요")
    data = {**custom_types(strict=True), name: labels}
    p = custom_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)      # 읽는 쪽이 반쯤 쓴 파일을 보지 않게
    return data


def type_question():
    names = list(DOC_TYPE_LABEL.values()) + list(custom_types())
    return (TYPE_PRE + " 아래 목록 중 하나면 그 이름을 그대로 적고, 목록에 없으면 문서 제목(문서 이름)을 적으세요. "
            "설명 없이 이름만 한 줄로 답하세요.\n목록: " + ", ".join(names))


def resolve_ai_type(ans):
    """AI 답 → (문서 종류 키 또는 'custom', 새 종류 이름). 알아듣지 못하면 (None, None)."""
    a = (ans or "").strip().splitlines()[0].strip(" '\"「」[]*.") if (ans or "").strip() else ""
    if not a or len(a) > 30 or garbled(a) or "{" in a:
        return None, None
    sq = squash(a)      # AI가 띄어쓰기만 다르게 답해도 알려진 종류로
    t = resolve_type(a) or next((k for k, lab in DOC_TYPE_LABEL.items()
                                 if any(x and squash(x) in sq for x in [lab, *lab.split("·")])), None)
    t = t or next((d for d, words in DOC_TYPE_WORDS + [("registry", ["등기부"])] if any(w in sq.casefold() for w in words)), None)
    if t:
        return t, None
    return "custom", a


def ask_doc_type(model, proc, pages, lines_list, is_docx):
    q = type_question()
    if is_docx:
        return ask_text(model, proc, docx_body(lines_list[0]) + "\n\n" + q, max_new=32)
    return ask(model, proc, pages[0], with_ocr(q, lines_list[0]), max_new=32)[0]


def parse_json_obj(ans):
    """답 안의 JSON 객체 → {이름: 값 문자열}(키를 미리 모를 때)."""
    m = re.search(r"\{.*\}", ans or "", re.S)
    try:
        data = json.loads(m[0]) if m else {}
    except ValueError:
        return {}
    return {str(k).strip(): v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v)
            for k, v in data.items() if str(k).strip() and v is not None} if isinstance(data, dict) else {}


def custom_fields(model, proc, name, pages, lines_list, is_docx, check):
    """새 문서 종류 읽기. 확정해 둔 칸이 있으면 그 칸을 두 번(사진+OCR, 사진만) 읽어 같으면 자동 확정,
    처음 보는 종류면 AI가 칸을 골라 읽고 모두 사용자 확인. shortcut: 1쪽만 읽는다 — 여러 쪽 새 문서가 생기면 쪽마다 묻는다.
    -> (fields, 처음 보는 종류인지)"""
    saved = custom_types().get(name)
    check()
    if saved:
        q = JOINT_PRE + "\n".join(f'- "{lab}": 문서에 적힌 \'{lab}\'의 값' for lab in saved)
        budget = 40 * len(saved) + 40
        if is_docx:
            first, second = parse_joint(ask_text(model, proc, docx_body(lines_list[0]) + "\n\n" + text_question(q), max_new=budget), saved), None
        else:
            first = parse_joint(ask(model, proc, pages[0], with_ocr(q, lines_list[0]), max_new=budget)[0], saved)
            check()
            second = parse_joint(ask(model, proc, pages[0], q, max_new=budget)[0], saved)
        labels = saved
    else:
        if is_docx:
            first = parse_json_obj(ask_text(model, proc, docx_body(lines_list[0]) + "\n\n" + NEW_TYPE_Q, max_new=600))
        else:
            first = parse_json_obj(ask(model, proc, pages[0], with_ocr(NEW_TYPE_Q, lines_list[0]), max_new=600)[0])
        second, labels = None, list(first)[:15]
    out = []
    for lab in labels:
        v1 = first.get(lab, "")
        v2 = second.get(lab) if second is not None else None
        same = v2 is not None and v1.strip() not in ("", "없음") and squash(v1) == squash(v2)
        reason = ("" if same else "처음 보는 문서 종류 — 칸 이름과 값을 확인하세요" if not saved
                  else f"두 번 읽은 값이 다름({v1[:20]} / {(v2 or '')[:20]})" if v2 else "한 번만 읽음 — 확인하세요")
        cands = [x for x in dict.fromkeys([v1, v2 or ""]) if x] if not same else []
        out.append({"key": lab, "label": lab, "kind": "text", "value": v1, "status": "auto" if same else "review", "reason": reason,
                    "candidates": cands, "page": 1, "line_box": None, "compare": next((x for x in cands if x != v1), "")})
    return out, not saved


class Cancelled(Exception):
    """사용자가 취소함: 읽던 문서는 버린다."""


def process_document(paths, model, proc, ocr, deskew=False, stop=None, mode="field"):
    """정답 없이 문서 하나를 처리한다. -> dict(source, files, route, doc_type, pages, fields[...], summary) 또는 error.
    fields 각 항목: key, label, kind, value, status('auto'=자동 확정 | 'review'=사람 확인), reason, candidates, page, line_box.
    stop: 부를 때마다 True면 Cancelled를 던진다(AI에게 묻기 전마다 확인).
    mode: 'field' = 칸마다 묻기(+일부 칸 다시 읽기) | 'joint' = 쪽마다 두 번만 묻고 두 답이 다른 칸은 사용자 판단."""
    def check():
        if stop and stop():
            raise Cancelled()
    pages, lines_list, routes = read_pages(paths, ocr, deskew)
    check()
    is_docx = routes == ["docx"]
    base = {"source": [Path(p).name for p in paths], "files": [str(Path(p).resolve()) for p in paths], "deskew": deskew,
            "route": routes, "pages": len(lines_list)}
    doc_type, custom_name = resolve_ai_type(ask_doc_type(model, proc, pages, lines_list, is_docx))
    check()
    if doc_type in (None, "cert") and "ocr" in routes:     # 중·일 문서일 수 있음: 그 글자를 더 잘 읽는 OCR로 다시
        lines_list = reread_cjk(pages, lines_list, routes)
    if doc_type is None:
        doc_type = detect_doc_type(lines_list)
    if doc_type == "custom":
        out, new = custom_fields(model, proc, custom_name, pages, lines_list, is_docx, check)
        n_rev = sum(o["status"] == "review" for o in out)
        return {**base, "doc_type": "custom", "doc_name": custom_name, "new_type": new, "fields": out,
                "summary": {"auto": len(out) - n_rev, "review": n_rev}}
    if doc_type is None:
        return {**base, "doc_type": None, "fields": [], "summary": {"auto": 0, "review": 0},
                "error": "문서 종류를 알 수 없음(AI도 정해 둔 단어로도 판단하지 못함)"}
    names = [f"p{i + 1}" for i in range(len(lines_list))]
    cache = dict(zip(names, lines_list))
    fields, answers = {}, {}
    if mode == "joint":
        by_page = {}
        for key in DOC_FIELDS[doc_type]:
            if key not in COMPUTED:
                by_page.setdefault(_field_page(key, lines_list), []).append(key)
        got = joint_answers(model, proc, doc_type, by_page, pages, lines_list, is_docx, check)
        page_of = {k: pi for pi, keys in by_page.items() for k in keys}
        for key in got:
            answers[("doc", key)] = got[key]
        for key, kind in DOC_FIELDS[doc_type].items():     # 칸 순서는 문서 종류의 순서대로
            if key in page_of:
                fields[key] = {"value": "", "kind": kind, "page": page_of[key] + 1, "region": None, "b_skip": False}
    for key, kind in DOC_FIELDS[doc_type].items():
        if key in COMPUTED or mode == "joint":
            continue
        check()
        pi = _field_page(key, lines_list)
        q = PROMPTS["v3"][doc_type][key]
        if is_docx:     # 쪽 이미지가 없으니 글자만 묻는다. 두 번째 판독(OCR 없이 이미지로 다시 묻기)은 할 수 없다
            row = {"ans": ask_text(model, proc, docx_body(lines_list[0]) + "\n\n" + text_question(q)), "norm_c": False}
        else:
            row = {"ans": ask(model, proc, pages[pi], with_ocr(q, lines_list[pi]), max_new=64)[0], "norm_c": False}
            # 이름은 다시 읽지 않는다: 페이지 대조에서 걸리면 바로 사용자 판별(2026-10-26 결정)
            if key in ZOOM_NUMBER and clean(kind, row["ans"]) not in ("", "없음"):
                row["ans2"] = zoom_number(model, proc, pages[pi], lines_list[pi], key, clean(kind, row["ans"]))
            elif key in SECOND_READ_ANY:
                row["ans2"] = ask(model, proc, pages[pi], q, max_new=64)[0]
        answers[("doc", key)] = row
        fields[key] = {"value": "", "kind": kind, "page": pi + 1, "region": None, "b_skip": False}
    gt = {"doc": {"doc_type": doc_type, "pages": names, "fields": fields}}
    chk = compute_checks(gt, ["doc"], answers, cache)
    out = []
    for key, f in fields.items():
        c = chk[("doc", key)]
        lines = lines_list[f["page"] - 1]
        occ = [i for i, _ in occurrences(f["kind"], c["value"], lines) if i >= 0] if c["value"] and not is_docx else []
        box = [round(lines[occ[0]][k]) for k in ("x0", "y0", "x1", "y1")] if occ else None
        review = c["pol"] or garbled(answers[("doc", key)]["ans"])
        ans2 = answers[("doc", key)].get("ans2")
        # 두 번째 판독이 다른 칸 값(이름 자리에 주소 등)을 답하면 후보 버튼에 넣지 않는다
        second = clean(f["kind"], ans2) if ans2 else ""
        cands = list(dict.fromkeys(v for v in (c["value"], second if format_ok(f["kind"], second) else "", c.get("suggest") or "") if v)) if review else []
        out.append({"key": key, "label": FIELD_LABEL.get(key, key), "kind": f["kind"], "value": c["value"],
                    "status": "review" if review else "auto", "reason": c["reason"] if review else "",
                    "candidates": cands, "page": f["page"], "line_box": box,
                    # 확인 화면에 보여 줄 비교 값 하나(2026-10-28 결정): 읽은 값과 다른 첫 후보
                    "compare": next((x for x in cands if x != c["value"]), "")})
    if "cert_lang" in DOC_FIELDS[doc_type]:
        lang = lang_field(lines_list)
        out.append(lang)
        if lang["value"] in (LANG_LABEL["ja"], LANG_LABEL["zh"]):     # 중·일 문서는 처음 읽은 값을 사용자가 확인(2026-10-28 결정)
            note = f"{lang['value']} 문서 — 처음 읽은 값을 확인하세요"
            out = [{**o, "status": "review", "reason": (o["reason"] + " / " if o["reason"] else "") + note,
                    "candidates": o["candidates"] or [o["value"]]} for o in out]
    n_rev = sum(o["status"] == "review" for o in out)
    return {**base, "doc_type": doc_type, "doc_name": DOC_TYPE_LABEL[doc_type], "new_type": False, "fields": out,
            "summary": {"auto": len(out) - n_rev, "review": n_rev}}


def group_documents(files):
    """명령줄 파일 → 문서 목록. 파일 하나가 문서 하나이고, 'X_p1.png', 'X_p2.png'처럼 쪽 번호가 붙은 이미지는 한 문서로 묶는다."""
    docs = {}
    for f in map(Path, files):
        m = re.fullmatch(r"(.+)_p(\d+)", f.stem)
        key = (str(f.parent / m[1]), "img") if m and f.suffix.lower() in (".png", ".jpg", ".jpeg") else (str(f), "one")
        docs.setdefault(key, []).append((int(m[2]) if key[1] == "img" else 0, f))
    return [[f for _, f in sorted(v)] for v in docs.values()]


ROUTE_LABEL = {"pdf_text": "PDF 안 글자", "ocr": "OCR", "docx": "docx 글자"}
SUPPORTED = (".pdf", ".png", ".jpg", ".jpeg", ".docx")


def main_run():
    ap = argparse.ArgumentParser(description="문서 입력 → 분석 → 환각검증 → 판별 화면(브라우저)에서 확인·내보내기")
    ap.add_argument("files", nargs="+", type=Path, help="PDF·이미지·docx 여러 개. 'X_p1.png, X_p2.png'는 한 문서로 묶음")
    ap.add_argument("--out", type=Path, default=Path("결과"), help="결과 폴더(문서별 JSON + 결과보기.html)")
    ap.add_argument("--deskew", action="store_true", help="스캔본 기울기 보정")
    ap.add_argument("--no-open", action="store_true", help="브라우저를 열지 않음")
    args = ap.parse_args()
    bad = [f for f in args.files if not f.exists() or f.suffix.lower() not in SUPPORTED]
    if bad:
        sys.exit("오류: 없거나 지원하지 않는 파일 — " + ", ".join(map(str, bad)) + f" (지원: {', '.join(SUPPORTED)})")
    groups = group_documents(args.files)
    model, proc = load("Qwen/Qwen3-VL-4B-Instruct", "bfloat16")
    ocr = make_ocr()
    args.out.mkdir(parents=True, exist_ok=True)
    docs = []
    for paths in groups:
        t0 = time.time()
        try:
            r = process_document(paths, model, proc, ocr, deskew=args.deskew)
        except ValueError as e:
            print("건너뜀:", e)
            continue
        stem = re.sub(r"_p\d+$", "", Path(paths[0]).stem)
        (args.out / f"{stem}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        route = "·".join(dict.fromkeys(ROUTE_LABEL[x] for x in r["route"]))
        if r.get("error"):
            print(f"건너뜀: {stem} — {r['error']}")
            continue
        print(f"{stem}: {DOC_TYPE_LABEL[r['doc_type']]} · {route} · 자동 확정 {r['summary']['auto']} / 확인 필요 {r['summary']['review']} · {time.time() - t0:.1f}초")
        docs.append(r)
    if not docs:
        sys.exit("오류: 처리된 문서가 없음")
    page = args.out / "결과보기.html"
    page.write_text(render_view(docs), encoding="utf-8")
    print("판별 화면:", page.resolve())
    if not args.no_open:
        import webbrowser
        webbrowser.open(page.resolve().as_uri())


DOC_TYPE_LABEL = {"registry": "등기사항전부증명서", "lease": "주택임대차계약서", "sale": "매매계약서",
                  "ledger": "건축물대장", "store_lease": "상가건물 임대차계약서", "cert": "자격증·수료증·성적증명서"}
STATUS_LABEL = {"auto": "자동 확정", "confirmed": "사용자 확인", "edited": "사용자 수정", "review": "확인 필요"}
EXPORT_COLUMNS = ["파일", "문서 종류", "항목", "값", "상태", "처음 읽은 값", "확인 사유", "쪽"]


# ============================================================================
# 결과 보기: 결과·확정 JSON → 인터넷 없이 여는 HTML 한 장(쪽 이미지 + 읽은 위치 + 칸 표 + 클릭 판별·내려받기)
# ============================================================================

VIEW_HTML = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BridgeDocs 결과 보기</title><style>
:root{--bg:#f6f8fa;--card:#fff;--fg:#1f2328;--muted:#656d76;--line:#d0d7de;--hl:#fff8c5}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--fg:#e6edf3;--muted:#8d96a0;--line:#30363d;--hl:#3b2e00}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 "Malgun Gothic",sans-serif}
main{max-width:1200px;margin:auto;padding:16px}h1{font-size:22px;margin:8px 0}.muted{color:var(--muted)}
.flow{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 12px}.flow span{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:4px 10px}
.bar{position:sticky;top:0;z-index:5;background:var(--bg);padding:8px 0;display:flex;flex-wrap:wrap;gap:10px;align-items:center;border-bottom:1px solid var(--line);margin-bottom:14px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:4px 12px}.stat b{font-size:18px;margin-left:6px}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;padding:4px 10px;cursor:pointer}
button:hover{border-color:#0969da}button:disabled{opacity:.45;cursor:not-allowed}button.main{background:#0969da;color:#fff;border-color:#0969da}
.doc{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:18px}
.doc h2{font-size:17px;margin:0 0 10px}.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.1fr);gap:14px}
@media (max-width:800px){.grid{grid-template-columns:1fr}}
.page{position:relative;margin-bottom:8px}.page img{width:100%;display:block;border:1px solid var(--line);border-radius:6px}
.box{position:absolute;border:3px solid;border-radius:3px;cursor:pointer}.box.on{box-shadow:0 0 0 4px rgba(255,200,0,.8)}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}
tr.f{cursor:pointer}tr.f.on{background:var(--hl)}td:first-child,th{white-space:nowrap}
.badge{color:#fff;border-radius:10px;padding:1px 8px;font-size:12px;white-space:nowrap}
.old{text-decoration:line-through;color:var(--muted);margin-left:6px}
.judge{margin-top:6px;display:flex;flex-wrap:wrap;gap:6px;align-items:center}.judge input{font:inherit;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);min-width:0;width:11em}
.err{color:#cf222e;font-size:13px;width:100%}
dialog{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:10px;padding:18px 20px;width:min(420px,calc(100vw - 32px));box-sizing:border-box}
dialog::backdrop{background:rgba(0,0,0,.45)}dialog h3{margin:0 0 4px}dialog label{display:block;margin:8px 0}
dialog label.name input{font:inherit;margin-left:6px;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);width:12em}
dialog .acts{display:flex;justify-content:flex-end;gap:8px;margin-top:12px}
</style></head><body><main>
<h1>BridgeDocs 결과 보기</h1>
<p class="muted">표의 줄이나 그림의 상자를 누르면 서로 위치를 짚어 줍니다. 빨간 "확인 필요" 칸은 후보를 누르거나 직접 입력해 확정하세요. 인터넷 없이 동작합니다.</p>
<div class="flow"><span>① 문서 입력 (쪽 이미지 · 기울기 보정 · 종류 판별)</span><span>② 문서 분석 (PDF 안 글자 · OCR · AI · docx)</span><span>③ 환각검증 및 사용자 판별</span><span>④ 데이터 추출 (엑셀 · CSV · JSON)</span></div>
<div class="bar" id="bar"></div><div id="docs"></div></main>
<dialog id="exp"><h3>내보내기</h3><p class="muted">만들 파일 형식을 고르세요. 고른 것마다 파일이 하나씩 내려받아집니다.</p>
<label><input type="checkbox" value="xlsx" checked> 엑셀 (.xlsx)</label>
<label><input type="checkbox" value="csv"> CSV (.csv) — 엑셀·한글에서 열림</label>
<label><input type="checkbox" value="json"> JSON (.json) — 다른 프로그램 연결용(위치·사유 등 전체 정보)</label>
<label class="name">파일 이름 <input id="expname" value="확정"></label>
<div class="err" id="experr"></div>
<div class="acts"><button id="expno">취소</button><button class="main" id="expgo">내보내기</button></div></dialog>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById("data").textContent);
const DOCS = DATA.docs, ST = DATA.status, COLOR = DATA.color;
const el = (tag, { dataset, ...props } = {}, ...kids) => {   // dataset은 읽기 전용이라 따로 넣는다
  const e = Object.assign(document.createElement(tag), props);
  Object.assign(e.dataset, dataset || {});
  e.append(...kids.filter(k => k != null));
  return e;
};

// 사용자가 직접 입력한 값 검사(없는 날짜, "1억" 같은 금액, 한 글자 이름 등)
function parseValue(kind, s) {
  s = s.trim();
  if (!s) return [null, "빈 값"];
  if (kind === "date") {
    const m = s.match(/(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})/);
    if (!m) return [null, "날짜는 2025-06-13처럼 연-월-일로 입력"];
    const d = new Date(+m[1], m[2] - 1, +m[3]);
    if (d.getMonth() !== m[2] - 1 || d.getDate() !== +m[3]) return [null, "없는 날짜"];
    return [`${m[1]}-${m[2].padStart(2, "0")}-${m[3].padStart(2, "0")}`, ""];
  }
  if (kind === "amount") return /^[\d,]+\s*원?$/.test(s) ? [s.match(/[\d,]+/)[0], ""] : [null, "금액은 숫자로 입력(쉼표 가능, 예: 100,000,000)"];
  if (kind === "flag") return s.includes("있") ? ["있음", ""] : s.includes("없") ? ["없음", ""] : [null, "'있음' 또는 '없음'으로 입력"];
  if (kind === "name") return (s.match(/[가-힣]/g) || []).length >= 2 ? [s, ""] : [null, "이름은 한글 2자 이상"];
  if (kind === "number") return /^\d+(\.\d+)?\s*(㎡|점)?$/.test(s) ? [s.match(/[\d.]+/)[0], ""] : [null, "숫자로 입력(예: 84.97)"];
  return [s, ""];
}

function decide(f, value, status) {
  if (!("read_value" in f)) f.read_value = f.value;
  if (!("orig" in f)) f.orig = { value: f.value, status: f.status };
  Object.assign(f, { value, status });
  render();
}
function undo(f) { Object.assign(f, f.orig); delete f.orig; delete f.read_value; render(); }

function focus(di, fi) {
  document.querySelectorAll(".on").forEach(n => n.classList.remove("on"));
  document.querySelectorAll(`[data-k="${di}-${fi}"]`).forEach(n => { n.classList.add("on"); });
  const box = document.querySelector(`.box[data-k="${di}-${fi}"]`);
  if (box) box.scrollIntoView({ block: "center", behavior: "smooth" });
}

function judgeControls(f) {
  const cands = f.candidates && f.candidates.length ? f.candidates : (f.value ? [f.value] : []);
  const err = el("div", { className: "err" });
  const input = el("input", { placeholder: "직접 입력", onclick: e => e.stopPropagation() });
  const typed = () => { const [v, e] = parseValue(f.kind, input.value); if (e) { err.textContent = e; return; } decide(f, v, cands.includes(v) ? "confirmed" : "edited"); };
  input.onkeydown = e => { if (e.key === "Enter") typed(); };
  return el("div", { className: "judge" },
    ...cands.map((c, i) => el("button", { textContent: `${i + 1}) ${c}`, title: "이 값으로 확정", onclick: e => { e.stopPropagation(); decide(f, c, "confirmed"); } })),
    input, el("button", { textContent: "입력", onclick: e => { e.stopPropagation(); typed(); } }), err);
}

function render() {
  const all = DOCS.flatMap(d => d.fields), cnt = s => all.filter(f => f.status === s).length, left = cnt("review");
  const bar = document.getElementById("bar");
  bar.replaceChildren(el("span", { className: "stat" }, "문서", el("b", { textContent: DOCS.length })),
    ...Object.keys(ST).filter(s => cnt(s)).map(s => el("span", { className: "stat", style: `border-color:${COLOR[s]}` }, ST[s], el("b", { textContent: cnt(s), style: `color:${COLOR[s]}` }))),
    el("span", { className: "muted", textContent: left ? `남은 확인 ${left}칸 — 다 끝내면 내보낼 수 있어요` : "모든 칸 확정 완료" }),
    el("button", { className: "main", textContent: "내보내기", disabled: !!left, onclick: () => { document.getElementById("experr").textContent = ""; document.getElementById("exp").showModal(); } }));
  const root = document.getElementById("docs");
  root.replaceChildren(...DOCS.map((d, di) => {
    const pages = (d.images || []).map((im, pi) => el("div", { className: "page" }, el("img", { src: "data:image/jpeg;base64," + im.src, alt: `${d.source[0]} ${pi + 1}쪽` }),
      ...d.fields.map((f, fi) => [f, fi]).filter(([f]) => f.page === pi + 1 && f.line_box).map(([f, fi]) => {
        const [x0, y0, x1, y1] = f.line_box;
        return el("div", { className: "box", title: `${f.label}: ${f.value}`, onclick: () => focus(di, fi), dataset: { k: `${di}-${fi}` },
          style: `left:${x0 / im.w * 100}%;top:${y0 / im.h * 100}%;width:${(x1 - x0) / im.w * 100}%;height:${(y1 - y0) / im.h * 100}%;border-color:${COLOR[f.status]}` });
      })));
    const rows = d.fields.map((f, fi) => {
      const val = el("td", {}, f.value ? f.value : el("span", { className: "muted", textContent: "(읽지 못함)" }),
        f.read_value != null && f.read_value !== f.value ? el("span", { className: "old", textContent: f.read_value }) : null);
      const note = el("td", {}, f.reason || "", f.status === "review" ? judgeControls(f) : null,
        f.orig ? el("div", {}, el("button", { textContent: "되돌리기", onclick: e => { e.stopPropagation(); undo(f); } })) : null);
      return el("tr", { className: "f", onclick: () => focus(di, fi), dataset: { k: `${di}-${fi}` } }, el("td", { textContent: f.label }), val,
        el("td", {}, el("span", { className: "badge", textContent: ST[f.status], style: `background:${COLOR[f.status]}` })), note, el("td", { textContent: f.page }));
    });
    const route = [...new Set((d.route || []).map(r => DATA.routeLabel[r] || r))].join(" · ");
    const isDocx = (d.route || []).join() === "docx";
    return el("section", { className: "doc" }, el("h2", {}, (DATA.docLabel[d.doc_type] || d.doc_type) + " ",
        el("span", { className: "muted", textContent: "· " + (d.source || []).join(", ") + (route ? ` · 읽은 길: ${route}` : "") })),
      el("div", { className: "grid" }, el("div", {}, ...(pages.length ? pages : [el("p", { className: "muted",
        textContent: isDocx ? "docx는 쪽 이미지 없이 문서 안의 글자로 읽었어요" : "원본 이미지를 찾을 수 없음" })])),
        el("div", {}, el("table", {}, el("tr", {}, ...["항목", "값", "상태", "사유 · 판별", "쪽"].map(t => el("th", { textContent: t }))), ...rows))));
  }));
}

const cleanDocs = () => DOCS.map(({ images, ...d }) => ({ ...d, fields: d.fields.map(({ orig, ...f }) => f),
  summary: Object.fromEntries(["auto", "confirmed", "edited"].map(s => [s, d.fields.filter(f => f.status === s).length])) }));
const toRows = () => [DATA.columns, ...DOCS.flatMap(d => d.fields.map(f => [(d.source || []).join(", "), DATA.docLabel[d.doc_type] || d.doc_type,
  f.label, f.value, ST[f.status], f.read_value ?? f.value, f.reason, f.page]))];
function toCsv(rows) {
  const q = v => /[",\r\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v);
  return rows.map(r => r.map(q).join(",")).join("\r\n");
}
// --- xlsx start: 라이브러리 없이(인터넷 불필요) xlsx = XML 5개를 무압축 zip으로 묶는다
const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
const crc32 = b => { let c = ~0; for (const x of b) c = CRC[(c ^ x) & 255] ^ (c >>> 8); return ~c >>> 0; };
function zipStore(files) {
  const enc = new TextEncoder(), local = [], central = [];
  let off = 0;
  for (const [name, text] of files) {
    const n = enc.encode(name), d = enc.encode(text), c = crc32(d);
    const h = new DataView(new ArrayBuffer(30)), e = new DataView(new ArrayBuffer(46));
    [[0, 0x04034b50, 4], [4, 20, 2], [6, 0x0800, 2], [12, 33, 2], [14, c, 4], [18, d.length, 4], [22, d.length, 4], [26, n.length, 2]]
      .forEach(([o, v, s]) => s === 4 ? h.setUint32(o, v, true) : h.setUint16(o, v, true));
    [[0, 0x02014b50, 4], [4, 20, 2], [6, 20, 2], [8, 0x0800, 2], [14, 33, 2], [16, c, 4], [20, d.length, 4], [24, d.length, 4], [28, n.length, 2], [42, off, 4]]
      .forEach(([o, v, s]) => s === 4 ? e.setUint32(o, v, true) : e.setUint16(o, v, true));
    local.push(new Uint8Array(h.buffer), n, d);
    central.push(new Uint8Array(e.buffer), n);
    off += 30 + n.length + d.length;
  }
  const size = central.reduce((s, a) => s + a.length, 0), end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
  end.setUint32(12, size, true); end.setUint32(16, off, true);
  return new Blob([...local, ...central, new Uint8Array(end.buffer)], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
}
function toXlsx(rows, sheet = "추출 결과") {
  const x = s => String(s).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const col = i => String.fromCharCode(65 + i);   // 열은 8개뿐(A~H)
  const cell = (v, i, r) => typeof v === "number" ? `<c r="${col(i)}${r}"><v>${v}</v></c>`
    : `<c r="${col(i)}${r}" t="inlineStr"><is><t xml:space="preserve">${x(v ?? "")}</t></is></c>`;
  const body = rows.map((row, ri) => `<row r="${ri + 1}">${row.map((v, i) => cell(v, i, ri + 1)).join("")}</row>`).join("");
  const X = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', M = "http://schemas.openxmlformats.org/", R = M + "officeDocument/2006/relationships";
  return zipStore([
    ["[Content_Types].xml", `${X}<Types xmlns="${M}package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>`],
    ["_rels/.rels", `${X}<Relationships xmlns="${M}package/2006/relationships"><Relationship Id="rId1" Type="${R}/officeDocument" Target="xl/workbook.xml"/></Relationships>`],
    ["xl/workbook.xml", `${X}<workbook xmlns="${M}spreadsheetml/2006/main" xmlns:r="${R}"><sheets><sheet name="${x(sheet)}" sheetId="1" r:id="rId1"/></sheets></workbook>`],
    ["xl/_rels/workbook.xml.rels", `${X}<Relationships xmlns="${M}package/2006/relationships"><Relationship Id="rId1" Type="${R}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>`],
    ["xl/worksheets/sheet1.xml", `${X}<worksheet xmlns="${M}spreadsheetml/2006/main"><sheetData>${body}</sheetData></worksheet>`]]);
}
// --- xlsx end
const EXPORTERS = {
  json: () => new Blob([JSON.stringify(cleanDocs(), null, 1)], { type: "application/json" }),
  csv: () => new Blob(["﻿" + toCsv(toRows())], { type: "text/csv" }),   // BOM: 엑셀에서 열어도 한글이 안 깨짐
  xlsx: () => toXlsx(toRows()),
};
function save(name, blob) { const a = el("a", { href: URL.createObjectURL(blob), download: name }); document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000); }
function doExport() {
  const picked = [...document.querySelectorAll("#exp input[type=checkbox]:checked")].map(c => c.value);
  if (!picked.length) { document.getElementById("experr").textContent = "형식을 하나 이상 고르세요"; return; }
  const base = document.getElementById("expname").value.trim().replace(/[\\/:*?"<>|]/g, "") || "확정";
  picked.forEach((fmt, i) => setTimeout(() => save(`${base}.${fmt}`, EXPORTERS[fmt]()), i * 300));   // 여러 개를 한꺼번에 받으면 브라우저가 막을 수 있어 간격을 둔다
  document.getElementById("exp").close();
}
document.getElementById("expgo").onclick = doExport;
document.getElementById("expno").onclick = () => document.getElementById("exp").close();
render();
</script></body></html>"""


def image_payload(img, width=900):
    """쪽 이미지 → {"src": base64 JPEG(가로 width), "w", "h"(원래 크기)}."""
    buf = io.BytesIO()
    img.convert("RGB").resize((width, round(img.height * width / img.width))).save(buf, "JPEG", quality=80)
    return {"src": base64.b64encode(buf.getvalue()).decode(), "w": img.width, "h": img.height}


def page_images(doc, width=900):
    """쪽 이미지(읽을 때와 같은 보정)를 base64 JPEG로. 상자는 브라우저가 원래 크기 비율로 그린다. 원본이 없으면 빈 목록."""
    files = doc.get("files") or []
    if not files or not all(Path(p).exists() for p in files):
        return []
    out = []
    if doc.get("route") == ["docx"]:
        return []
    routes = doc.get("route") or []
    for i, pg in enumerate(load_pages(files)):
        if doc.get("deskew") and (routes[i] if i < len(routes) else "ocr") == "ocr":     # PDF 안 글자로 읽은 쪽은 보정하지 않았다
            pg = preprocess(pg)[0]
        out.append(image_payload(pg, width))
    return out


def render_view(docs):
    """결과(또는 확정) 문서들 → 판별까지 할 수 있는 HTML 한 장. 값은 JSON으로 넣고 화면은 브라우저가 textContent로 그린다(문서 글자가 HTML로 해석되지 않음)."""
    payload = {"docs": [{**d, "images": page_images(d)} for d in docs], "status": STATUS_LABEL,
               "color": {"auto": "#1a7f37", "confirmed": "#0969da", "edited": "#bc4c00", "review": "#cf222e"},
               "docLabel": DOC_TYPE_LABEL, "routeLabel": ROUTE_LABEL, "columns": EXPORT_COLUMNS}
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")     # 값 속 "</script>"가 스크립트를 끝내지 않도록
    return VIEW_HTML.replace("__DATA__", data)


# ============================================================================
# 명령 선택
# ============================================================================
# 문서 만들기: 데이터(JSON·CSV·엑셀) → 문서  (교수님 면담 2번: 추출의 반대 방향)
# ============================================================================
# 실행: python bridgedocs.py make 데이터.csv [--template 양식.docx] [--type cert] [--out 새문서]
#   데이터: 판별 화면에서 내보낸 JSON·CSV·엑셀(한 줄에 칸 하나) 그대로, 또는 한 줄에 문서 하나인 표(열 = 칸 이름)
#   양식 없이: 문서 종류별 기본 양식으로 PDF / --template: 워드 양식 안의 {{칸 이름}}을 값으로 바꾼 docx
BUILDERS = {"registry": build_registry, "lease": build_lease, "sale": build_sale, "ledger": build_ledger,
            "store_lease": build_store_lease, "cert": build_cert}
NEEDS_FORM = {"registry": "등기부등본(집합건물) 예시.pdf", "lease": "주택임대차 표준계약서(원본게시용).pdf", "sale": "부동산매매계약서(한글).pdf"}


def forms_dir():
    """사용자가 기본 양식 PDF를 넣는 폴더(BD_FORMS, 프로그램에서는 exe 옆 '양식'). 없으면 None."""
    return Path(os.environ["BD_FORMS"]) if os.environ.get("BD_FORMS") else None


def form_file(doc_type):
    """기본 양식 PDF 경로: 사용자 양식 폴더를 먼저, 개발 PC의 문서 예시/를 다음에 본다. 없으면 None.
    생성기는 이 양식의 칸 위치에 맞춰 그리므로, 같은 공개 양식(파일 이름도 같게)이어야 한다."""
    name = NEEDS_FORM.get(doc_type)
    return next((d / name for d in (forms_dir(), SRC) if name and d and (d / name).exists()), None)
KIND_HINT = {"date": "날짜(예: 2025-06-13)", "amount": "금액(숫자, 예: 100,000,000)", "name": "한글 이름", "number": "숫자(예: 84.97)",
             "owners": "소유자(예: 홍길동 또는 홍길동(1/2), 김민지(1/2))", "person": "이름", "choice": "자격증·수료증·성적증명서·기타 중 하나",
             "flag": "'있음' 또는 '없음'", "grade": "등급·점수", "text": "글자"}


WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(name, n):
    """데이터의 '파일' 칸 → 안전한 파일 이름. 학생이 낸 표의 값이 그대로 오므로 금지 글자·예약 이름(CON)·긴 이름을 막는다."""
    name = Path(str(name)).name
    name = re.sub(r"\.(pdf|png|jpe?g|docx|json)$", "", name, flags=re.I)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:80]
    if name.upper().split(".")[0] in WIN_RESERVED:
        name = "_" + name
    return name or f"문서{n}"


def resolve_type(s):
    """'매매계약서'·'sale' 같은 문서 종류 표기 → 엔진의 문서 종류 키. 모르면 None."""
    s = (s or "").strip()
    if s in DOC_FIELDS:
        return s
    return next((k for k, lab in DOC_TYPE_LABEL.items() if s and (s == lab or s in lab.split("·"))), None)


def resolve_key(dtype, s):
    """칸 이름(화면 표기 '보증금' 또는 키 'deposit_amount') → 그 문서 종류의 키. 모르면 None."""
    s = (s or "").strip()
    return s if s in DOC_FIELDS[dtype] else next((k for k in DOC_FIELDS[dtype] if FIELD_LABEL.get(k) == s), None)


def read_xlsx(path):
    """xlsx 첫 시트 → 행 목록(글자). openpyxl 없이: 엑셀이 저장한 공유 문자열과 이 프로그램이 쓰는 inlineStr 둘 다 읽는다."""
    import xml.etree.ElementTree as ET
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            # 읽는 법 표시(<rPh>, 후리가나)는 빼고 본문 글자(<t>, <r><t>)만
            shared = ["".join((t.text or "") for t in [*si.findall(ns + "t"), *si.findall(f"{ns}r/{ns}t")])
                      for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(ns + "si")]
        sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]
        rows = []
        for row in ET.fromstring(z.read(sheet)).iter(ns + "row"):
            cells = {}
            for c in row.iter(ns + "c"):
                col = re.match(r"[A-Z]+", c.get("r", "A"))[0]
                idx = 0
                for ch in col:
                    idx = idx * 26 + ord(ch) - 64
                v = c.find(ns + "v")
                if c.get("t") == "s":
                    cells[idx - 1] = shared[int(v.text)] if v is not None and v.text else ""
                elif c.get("t") == "inlineStr":
                    cells[idx - 1] = "".join(t.text or "" for t in c.iter(ns + "t"))
                else:
                    cells[idx - 1] = v.text if v is not None and v.text else ""
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
    return rows


TXT_SEPS = ("\t", ",", ";", "|")
KV_LINE = re.compile(r"^\s*([^:：=]{1,30}?)\s*[:：=]\s*(.*?)\s*$")


def read_text_any(path):
    """UTF-8(BOM 포함)로 읽고, 안 되면 한국어 Windows 엑셀이 저장하는 CP949로 읽는다."""
    raw = Path(path).read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp949")


def read_txt_table(path):
    """txt 데이터 → 행 목록. 구분 기호(탭·쉼표·세미콜론·세로줄)는 첫 줄에 가장 많이 나오는 것으로 정한다.
    구분 기호가 없고 줄마다 '칸: 값'이면 문서 하나로 보고 [머리줄, 값 줄]로 바꾼다."""
    import csv
    lines = [L for L in read_text_any(path).splitlines() if L.strip()]
    if not lines:
        return []
    sep = max(TXT_SEPS, key=lines[0].count)
    if lines[0].count(sep) == 0 or sum(bool(KV_LINE.match(L)) for L in lines) * 2 > len(lines):   # '보증금: 100,000,000' 같은 줄
        pairs = [KV_LINE.match(L) for L in lines]
        pairs = [(m[1], m[2]) for m in pairs if m]
        return [[k for k, _ in pairs], [v for _, v in pairs]] if pairs else []
    return list(csv.reader(lines, delimiter=sep))


def read_table(path):
    import csv
    suffix = Path(path).suffix.lower()
    if suffix == ".xlsx":
        return read_xlsx(path)
    if suffix == ".txt":
        return read_txt_table(path)
    return list(csv.reader(io.StringIO(read_text_any(path), newline="")))


def load_records(path, dtype=None):
    """데이터 파일 → [{name, doc_type, values{키: 값}, unknown[모르는 칸]}].
    JSON(엔진·화면 결과), 한 줄에 칸 하나인 표(화면의 CSV·엑셀), 한 줄에 문서 하나인 표를 모두 받는다."""
    path = Path(path)
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        docs = data if isinstance(data, list) else [data]
        recs = []
        for i, d in enumerate(docs):
            dt = d.get("doc_type") or dtype      # 종류가 없는 데이터는 고른 종류로 읽는다(값이 '모르는 칸'으로 빠지지 않게)
            known = DOC_FIELDS.get(dt, {})
            fs = d.get("fields", [])
            recs.append({"name": safe_name((d.get("source") or [""])[0], i + 1), "doc_type": dt,
                         "values": {f["key"]: f["value"] for f in fs if f.get("key") in known},
                         "unknown": [f.get("label") or f.get("key", "?") for f in fs if f.get("key") not in known]})
        return recs
    rows = [r for r in read_table(path) if any(c.strip() for c in r)]
    if not rows:
        return []
    head = [h.strip() for h in rows[0]]
    col = {h: i for i, h in enumerate(head)}
    cell = lambda r, h: r[col[h]].strip() if h in col and col[h] < len(r) else ""
    recs = []
    if "항목" in col and "값" in col:           # 화면에서 내보낸 형식: 파일마다 묶는다
        by = {}
        for r in rows[1:]:
            name = safe_name(cell(r, "파일").split(",")[0].strip(), len(by) + 1)
            rec = by.setdefault(name, {"name": name, "doc_type": resolve_type(cell(r, "문서 종류")) or dtype, "values": {}, "unknown": []})
            key = resolve_key(rec["doc_type"], cell(r, "항목")) if rec["doc_type"] else None
            if key:
                rec["values"][key] = cell(r, "값")
            else:
                rec["unknown"].append(cell(r, "항목"))
        return list(by.values())
    for n, r in enumerate(rows[1:], 1):          # 한 줄에 문서 하나
        dt = resolve_type(cell(r, "문서 종류")) or dtype
        name = safe_name(cell(r, "파일"), n)
        rec = {"name": name, "doc_type": dt, "values": {}, "unknown": []}
        for h in head:
            if h in ("문서 종류", "파일"):
                continue
            key = resolve_key(dt, h) if dt else None
            if key:
                rec["values"][key] = cell(r, h)
            elif h and cell(r, h):      # 여러 문서 종류를 한 표에 쓰면 다른 종류의 열은 비어 있다 — 값이 있는 열만 알린다
                rec["unknown"].append(h)
        recs.append(rec)
    return recs


def check_record(rec):
    """값을 칸 종류에 맞게 다듬고(2025.6.13 → 2025-06-13) 형식이 틀린 칸을 알려 준다. -> (다듬은 값, 문제 목록)"""
    out, problems = {}, []
    for key, raw in rec["values"].items():
        kind = DOC_FIELDS[rec["doc_type"]][key]
        raw = str(raw).strip()
        if kind == "date" and raw == "없음":      # '없음' 날짜는 건너뛴다(빈칸과 같음). 기본 양식은 확인 화면에서 임의 값으로 보여 준다
            continue
        if key in COMPUTED or raw in ("", "없음"):
            out[key] = raw
            continue
        if kind == "date" and re.fullmatch(r"\d{5}(\.0)?", raw) and 20000 < float(raw) < 80000:      # 엑셀 날짜 칸은 일련번호로 저장된다
            raw = (date(1899, 12, 30) + timedelta(days=int(float(raw)))).isoformat()
        v = clean(kind, raw)
        ok = v in ("있음", "없음") if kind == "flag" else format_ok(kind, v)
        if kind == "amount" and not re.fullmatch(r"₩?\s*(?:[\d,]+\s*(?:억|천만|만|천)?\s*)+원?", raw):      # '1억'은 받고 '일억'은 알려 준다
            ok = False
        if kind == "date" and ok:
            try:
                date.fromisoformat(v)
            except ValueError:
                ok = False
        if not ok:
            problems.append(f"{FIELD_LABEL.get(key, key)} '{raw}' → {KIND_HINT.get(kind, kind)} 형식이 아님")
        out[key] = v
    return out, problems


def _xml_text(s):
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]", "", s)     # 엑셀에서 붙여 넣은 값의 \x0b 등은 워드 파일을 깨뜨린다
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fill_xml(xml, values):
    """워드 문서 XML의 {{칸 이름}}을 값으로 바꾼다. 워드는 한 낱말도 여러 조각(run)으로 나눠 저장하므로 문단 단위로 글자를 모아 바꾸고
    바뀐 문단은 첫 조각에 글자를 몰아 넣는다(그 문단 안의 글자 모양은 첫 조각 모양을 따른다). -> (새 XML, 못 바꾼 칸 이름들)"""
    import html
    missing = set()

    def para(m):
        p = m.group(0)
        ts = list(re.finditer(r"(<w:t(?:\s[^>]*)?>)(.*?)(</w:t>)", p, re.S))
        text = "".join(html.unescape(t[2]) for t in ts)
        if "{{" not in text:
            return p

        def sub(mm):
            if callable(values):        # 순서대로 채우기: 칸 이름과 상관없이 다음 값
                return str(values(mm[1]))
            if mm[1] in values:
                return str(values[mm[1]])
            missing.add(mm[1])
            return mm[0]
        new = re.sub(SLOT, sub, text)
        if new == text:
            return p
        out, last = [], 0
        for i, t in enumerate(ts):
            out.append(p[last:t.start()])
            body = _xml_text(new).replace("\n", '</w:t><w:br/><w:t xml:space="preserve">')     # 값 안의 줄바꿈 → 워드 줄바꿈
            out.append(('<w:t xml:space="preserve">' + body if i == 0 else t[1]) + t[3])
            last = t.end()
        return "".join(out) + p[last:]
    return re.sub(r"<w:p[ >].*?</w:p>", para, xml, flags=re.S), missing


SLOT = r"\{\{\s*(.*?)\s*\}\}"            # 양식의 빈칸 {{칸 이름}} ({{}}처럼 비워도 됨)
SLOT_PART = r"word/(document|header\d*|footer\d*)\.xml"


def template_slots(template):
    """양식 docx의 빈칸 이름들을 문서에 나오는 순서대로(fill_docx가 채우는 순서와 같음)."""
    import html
    out = []
    with zipfile.ZipFile(template) as z:
        for item in z.infolist():
            if re.fullmatch(SLOT_PART, item.filename):
                for p in re.findall(r"<w:p[ >].*?</w:p>", z.read(item.filename).decode("utf-8"), re.S):
                    text = "".join(html.unescape(t) for t in re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", p, re.S))
                    out += re.findall(SLOT, text)
    return out


def fill_docx(template, values, out_path):
    """양식 docx를 복사하며 본문·머리글·바닥글의 {{칸 이름}}을 바꾼다.
    values: {칸 이름: 값} 또는 순서대로 채울 때는 칸 이름 → 값 함수. -> 못 바꾼 칸 이름들"""
    missing = set()
    with zipfile.ZipFile(template) as zin, zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if re.fullmatch(SLOT_PART, item.filename):
                xml, miss = fill_xml(data.decode("utf-8"), values)
                data, missing = xml.encode("utf-8"), missing | miss
            zout.writestr(item, data)
    return missing


def template_values(rec, values):
    """양식에 쓸 수 있는 이름: 칸 표기('보증금')와 키('deposit_amount') 둘 다, 그리고 파일·문서 종류·오늘."""
    out = {"파일": rec["name"], "문서 종류": DOC_TYPE_LABEL.get(rec["doc_type"], ""), "오늘": date.today().isoformat()}
    for k, v in values.items():
        out[k] = out[FIELD_LABEL.get(k, k)] = v
    return out


def save_pdf(pages, path):
    pages[0].save(path, "PDF", resolution=150, save_all=True, append_images=pages[1:])


def prepare_docs(recs, template=None, seed=7):
    """make 1단계: 표의 줄마다 만들 문서의 최종 값을 정한다. 아직 파일은 쓰지 않는다(사용자 확인 전).
    데이터에 없는 칸은 임의 값으로 채우지 않고 빈칸으로 둔다(2026-10-28 결정, filled는 항상 빈 목록).
    기본 양식으로 증명서는 만들지 않는다(2026-10-28 결정).
    -> [dict(name, doc_type, values, filled, problems, notes, template)]"""
    used, docs = set(), []
    for rec in recs:
        name, k = rec["name"], 2
        while name.casefold() in used:        # 같은 이름이 두 번 나오면 앞 문서를 덮어쓰지 않도록 _2, _3
            name, k = f"{rec['name']}_{k}", k + 1
        used.add(name.casefold())
        doc = {"name": name, "doc_type": rec["doc_type"], "values": {}, "filled": [], "problems": [], "template": bool(template),
               "notes": list(rec.get("notes", [])) + ([f"모르는 칸은 뺌: {', '.join(rec['unknown'])}"] if rec.get("unknown") else [])}
        docs.append(doc)
        if rec["doc_type"] not in DOC_FIELDS:
            doc["problems"].append("문서 종류를 알 수 없음('문서 종류' 열이나 --type 필요)")
            continue
        values, problems = check_record(rec)
        doc["notes"] += [f"{FIELD_LABEL.get(k, k)} '없음' → 건너뜀" for k in rec["values"] if k not in values]
        if problems:
            doc["problems"], doc["values"] = problems, {k: str(x) for k, x in rec["values"].items()}
            continue
        if rec["doc_type"] == "cert":
            values["cert_lang"] = LANG_LABEL["ko"]
        if template:
            doc["values"] = values
            continue
        if rec["doc_type"] not in MAKE_TYPES:
            doc["problems"].append(f"{DOC_TYPE_LABEL.get(rec['doc_type'], rec['doc_type'])}는 기본 양식으로 만들지 않아요 — 워드 양식(.docx)을 고르세요")
            continue
        form = NEEDS_FORM.get(rec["doc_type"])
        if form and not form_file(rec["doc_type"]):
            doc["problems"].append(f"기본 양식 '{form}'이 없음 — {forms_dir() or SRC} 폴더에 넣거나 워드 양식(.docx)을 고르세요")
            continue
        doc["values"] = {k: str(values.get(k, "")) for k in DOC_FIELDS[rec["doc_type"]] if k not in COMPUTED}
    return docs


MAKE_TYPES = [t for t in BUILDERS if t != "cert"]     # 기본 양식으로 만드는 문서 종류(증명서는 만들지 않음, 2026-10-28)
TEXT_LIMIT = 4000       # AI에게 넘기는 글 길이(글자). 더 길면 앞부분만
FREE_TYPE_Q = "아래 글은 어떤 문서를 만들기 위한 내용이에요. 다음 중 무엇인지 이름만 한 줄로 답하세요(설명 없이).\n목록: {names}\n\n글:\n{text}"
FREE_FIELDS_Q = ("아래 글은 '{doc}'를 만들 내용이에요. 글에서 각 항목의 값을 찾아 JSON 객체 하나로만 답하세요(설명 없이). "
                 "글에 없는 항목은 \"\"로 두고 지어내지 마세요. 금액은 숫자만(예: 50000000), 날짜는 YYYY-MM-DD.\n항목:\n{items}\n\n글:\n{text}")


def text_record(text, ask, dtype=None, name="문서", types=None):
    """보통 문장으로 쓴 글 → 만들 문서 하나(load_records와 같은 모양 + notes). ask(질문) -> AI 답.
    dtype이 없으면 AI가 types(기본: 기본 양식으로 만드는 종류) 중에서 고른다.
    shortcut: 글 하나 = 문서 하나. 한 글에 여러 계약을 쓰면 첫 번째만 — 필요하면 문단마다 나눠 묻기."""
    text = (text or "").strip()[:TEXT_LIMIT]
    if not text:
        raise ValueError("글이 비어 있어요")
    types = types or MAKE_TYPES
    if not dtype:
        dtype, _ = resolve_ai_type(ask(FREE_TYPE_Q.format(names=", ".join(DOC_TYPE_LABEL[t] for t in types), text=text)))
        if dtype not in types:
            raise ValueError("글에서 어떤 문서를 만들지 알 수 없어요 — 양식을 직접 고르고 다시 불러오세요")
    keys = [k for k in DOC_FIELDS[dtype] if k not in COMPUTED]
    items = "\n".join(f'- "{k}": {FIELD_LABEL.get(k, k)}' + (" ('있음' 또는 '없음')" if DOC_FIELDS[dtype][k] == "flag" else "") for k in keys)
    vals = {k: v.strip() for k, v in parse_joint(ask(FREE_FIELDS_Q.format(doc=DOC_TYPE_LABEL[dtype], items=items, text=text)), keys).items()
            if v.strip() and v.strip() != '""'}
    if not vals:
        raise ValueError("글에서 문서에 넣을 값을 찾지 못했어요 — 이름·금액·날짜 등을 글에 적어 주세요")
    flat = squash(text)
    notes = ["AI가 글에서 뽑은 값이에요 — 만들기 전에 꼭 확인하세요"]
    notes += [f"{FIELD_LABEL.get(k, k)} '{v}'는 글에 없는 값일 수 있어요(AI가 지어냈는지 확인)"
              for k, v in vals.items() if DOC_FIELDS[dtype][k] in ("name", "text") and squash(v) not in flat]
    return {"name": name, "doc_type": dtype, "values": vals, "unknown": [], "notes": notes}


def mask_boxes(mask, cell=6, gap=1):
    """참/거짓 픽셀 표 → 붙어 있는 덩어리마다 [x0, y0, x1, y1](px). 가로로 gap칸 안에 있는 글자는 한 상자로 묶는다."""
    import numpy as np
    h, w = mask.shape[0] // cell, mask.shape[1] // cell
    g = mask[:h * cell, :w * cell].reshape(h, cell, w, cell).any(axis=(1, 3))
    near = g.copy()
    for s in range(1, gap + 1):
        near[:, s:] |= g[:, :-s]
        near[:, :-s] |= g[:, s:]
    seen, boxes = np.zeros_like(near), []
    for y, x in zip(*np.nonzero(near)):
        if seen[y, x]:
            continue
        stack, ys, xs = [(y, x)], [], []
        seen[y, x] = True
        while stack:
            cy, cx = stack.pop()
            ys.append(cy); xs.append(cx)
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < h and 0 <= nx < w and near[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        boxes.append([min(xs) * cell, min(ys) * cell, (max(xs) + 1) * cell, (max(ys) + 1) * cell])
    return boxes


def render_doc(doc_type, values, seed=7):
    """기본 양식 문서를 그린다. 빈칸(값 없음)은 그리지 않는다. 그 값에서 따라 나오는 글자(한글 금액·계약금 등)도 함께 비운다.
    빈칸 자리는 같은 문서를 한 번 더 그리며 빈칸 글자만 칸마다 다른 색으로 칠해 찾는다(쪽 모양은 두 번 모두 같다).
    -> (쪽 이미지들, {빈칸 키: [(쪽 번호, [x0, y0, x1, y1])]}, 빈칸 키들)"""
    import numpy as np
    build = BUILDERS[doc_type]
    given = {k: v for k, v in values.items() if str(v).strip()}
    blank = [k for k in DOC_FIELDS[doc_type] if k not in COMPUTED and k not in given]
    try:
        _DRAW.update(blank=set(blank), mark={})
        pages, _ = build(random.Random(seed), 0, given)
        if not blank:
            return pages, {}, []
        colors = dict(zip(blank, MARK_COLORS))
        _DRAW["mark"] = colors
        marked, _ = build(random.Random(seed), 0, given)
    finally:
        _DRAW.update(blank=set(), mark={})
    spots = {k: [] for k in blank}
    for pno, (p, q) in enumerate(zip(pages, marked), 1):
        a, b = np.asarray(p.convert("RGB")).astype(int), np.asarray(q.convert("RGB")).astype(int)
        changed = np.abs(a - b).sum(axis=2) > 60
        for k, col in colors.items():
            near = np.abs(b - np.array(col)).sum(axis=2) < 200     # 글자 가장자리는 흰색과 섞여 옅다
            spots[k] += [(pno, bx) for bx in mask_boxes(changed & near)]
    return pages, spots, blank


def apply_edits(docs, posted):
    """make 2단계: 확인 화면에서 보낸 값을 다시 검사한다. make=False인 문서는 뺀다. -> 만들 문서 목록(problems가 있으면 만들지 않음)"""
    by, out = {d["name"]: d for d in docs}, []
    for p in posted:
        d = by.get(p.get("name"))
        if d is None or not p.get("make"):
            continue
        known = DOC_FIELDS.get(d["doc_type"])
        if known is None:
            out.append(d)
            continue
        vals = {k: str(x) for k, x in (p.get("values") or {}).items() if k in known}
        values, problems = check_record({"name": d["name"], "doc_type": d["doc_type"], "values": vals, "unknown": []})
        if d["doc_type"] == "cert":
            values["cert_lang"] = LANG_LABEL["ko"]
        if not d["template"] and d["doc_type"] not in MAKE_TYPES:      # 다시 검사해도 기본 양식 금지는 남긴다
            problems.append(f"{DOC_TYPE_LABEL.get(d['doc_type'], d['doc_type'])}는 기본 양식으로 만들지 않아요 — 워드 양식(.docx)을 고르세요")
        out.append({**d, "values": values, "problems": problems})
    return out


def write_docs(docs, template, out_dir, seed=7):
    """make 3단계: 확인된 값으로 파일을 쓴다. 빈칸은 빈칸으로 둔다.
    문제가 있는 문서가 있거나 하나라도 만들지 못하면 ValueError로 멈춘다(2026-10-28 결정). -> [(이름, 경로, '')]"""
    bad = [d["name"] for d in docs if d["problems"]]
    if bad:
        raise ValueError(f"확인할 칸이 있는 문서: {', '.join(bad)}")
    res = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for d in docs:
        try:
            if template:
                path = out_dir / f"{d['name']}.docx"
                fill_docx(template, template_values(d, d["values"]), path)
            else:
                pages, _, _ = render_doc(d["doc_type"], d["values"], seed)
                path = out_dir / f"{d['name']}.pdf"
                save_pdf(pages, path)
        except (OSError, ValueError, KeyError, IndexError, TypeError, zipfile.BadZipFile) as e:
            raise ValueError(f"{d['name']} 문서를 만들지 못함({type(e).__name__}: {e})") from e
        res.append((d["name"], path, ""))
    return res


def confirm_html(docs, token):
    """make 확인 화면: 문서마다 칸 값을 보여 주고 고칠 수 있게 한다. 임의로 채운 칸·문제를 표시한다."""
    import html
    esc = lambda s: html.escape(str(s), quote=True)
    cards = []
    for n, d in enumerate(docs):
        kinds = DOC_FIELDS.get(d["doc_type"], {})
        rows = "".join(
            f'<tr class="{"fill" if k in d["filled"] else ""}"><th>{esc(FIELD_LABEL.get(k, k))}</th>'
            f'<td><input data-key="{esc(k)}" value="{esc(d["values"].get(k, ""))}" aria-label="{esc(FIELD_LABEL.get(k, k))}"></td>'
            f'<td class="mark">{"비어 있어 임의 값으로 채움 — 확인하세요" if k in d["filled"] else ""}</td></tr>'
            for k in kinds if k not in COMPUTED)
        probs = "".join(f"<li>{esc(p)}</li>" for p in d["problems"])
        notes = "".join(f"<li>{esc(p)}</li>" for p in d["notes"])
        cards.append(
            f'<section class="card" data-name="{esc(d["name"])}"><h2><label><input type="checkbox" class="make" {"" if d["problems"] else "checked"}> '
            f'{esc(d["name"])}</label> <small>{esc(DOC_TYPE_LABEL.get(d["doc_type"], d["doc_type"] or "알 수 없음"))}</small></h2>'
            f'<ul class="prob">{probs}</ul><ul class="note">{notes}</ul><table>{rows}</table></section>')
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>문서 만들기 확인</title><style>
:root{{--bg:#fff;--fg:#1a1a1a;--line:#ddd;--warn:#b45309;--warnbg:#fff7e6;--err:#b91c1c;--ok:#15803d}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151515;--fg:#eee;--line:#444;--warn:#fbbf24;--warnbg:#3a2a05;--err:#f87171;--ok:#4ade80}}}}
body{{background:var(--bg);color:var(--fg);font-family:"Malgun Gothic",sans-serif;max-width:900px;margin:0 auto;padding:16px}}
.card{{border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin:12px 0}} h2{{font-size:17px;margin:4px 0}}
table{{width:100%;border-collapse:collapse}} th{{text-align:left;width:9em;font-weight:normal;padding:4px}} td{{padding:4px}}
input:not([type]){{width:100%;box-sizing:border-box;padding:5px;font-size:15px;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:4px}}
tr.fill input{{background:var(--warnbg);border-color:var(--warn)}} .mark{{color:var(--warn);font-size:13px;width:15em}}
.prob{{color:var(--err)}} .note{{color:var(--warn);font-size:13px}} ul{{margin:4px 0;padding-left:20px}}
button{{font-size:16px;padding:10px 20px;margin:12px 0}} #msg{{white-space:pre-wrap}} .ok{{color:var(--ok)}}
</style></head><body><h1>문서 만들기 전 확인</h1>
<p>값을 확인하고 고친 뒤 [문서 만들기]를 누르세요. <b>노란 칸</b>은 데이터가 비어 있어 임의로 채운 값입니다. 체크를 끄면 그 문서는 만들지 않습니다.</p>
{"".join(cards)}
<button id="go">문서 만들기</button><div id="msg" role="status"></div>
<script>
document.getElementById("go").onclick = async () => {{
  const docs = [...document.querySelectorAll(".card")].map(c => ({{name: c.dataset.name, make: c.querySelector(".make").checked,
    values: Object.fromEntries([...c.querySelectorAll("input[data-key]")].map(i => [i.dataset.key, i.value]))}}));
  const msg = document.getElementById("msg");
  msg.textContent = "만드는 중…";
  try {{
    const r = await (await fetch("/make", {{method: "POST", headers: {{"Content-Type": "application/json", "X-Token": "{token}"}},
      body: JSON.stringify(docs)}})).json();
    if (r.problems) {{
      msg.innerHTML = "";
      for (const [name, ps] of Object.entries(r.problems)) {{
        const ul = [...document.querySelectorAll(".card")].find(c => c.dataset.name === name)?.querySelector(".prob");
        if (ul) {{ ul.replaceChildren(...ps.map(p => Object.assign(document.createElement("li"), {{textContent: p}}))); }}
      }}
      msg.textContent = "고칠 칸이 있습니다(빨간 글자). 고친 뒤 다시 누르세요.";
      return;
    }}
    msg.className = "ok";
    msg.textContent = r.lines.join("\\n") + "\\n\\n끝났습니다. 이 창을 닫아도 됩니다.";
    document.getElementById("go").disabled = true;
  }} catch (e) {{ msg.textContent = "보내지 못함: " + e; }}
}};
</script></body></html>"""


def serve_confirm(docs, on_submit, open_browser=True):
    """확인 화면을 이 PC 안에서만(127.0.0.1) 띄우고, [문서 만들기]를 받으면 on_submit(보낸 값) 결과를 돌려준다.
    다른 웹 페이지가 몰래 보내지 못하게 매번 새 토큰을 요구한다. 문서를 다 만들면 서버를 닫는다."""
    import http.server
    import secrets
    import webbrowser
    token, state = secrets.token_urlsafe(16), {"done": False}
    page = confirm_html(docs, token).encode("utf-8")

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send(200, page, "text/html; charset=utf-8") if self.path == "/" else self._send(404, b"", "text/plain")

        def do_POST(self):
            if self.path != "/make" or self.headers.get("X-Token") != token:
                return self._send(403, b"", "text/plain")
            try:
                posted = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 10_000_000)) or b"[]")
                result = on_submit(posted if isinstance(posted, list) else [])
            except (ValueError, TypeError) as e:
                result = {"problems": {"(전체)": [f"보낸 값을 읽지 못함: {e}"]}}
            state["done"] = "lines" in result
            self._send(200, json.dumps(result, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    url = f"http://127.0.0.1:{srv.server_port}/"
    print("확인 화면:", url, "(값을 확인하고 [문서 만들기]를 누르세요. 멈추려면 Ctrl+C)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        while not state["done"]:
            srv.handle_request()
    except KeyboardInterrupt:
        print("멈춤: 문서를 만들지 않았습니다")
    finally:
        srv.server_close()


def main_make():
    ap = argparse.ArgumentParser(description="데이터(JSON·CSV·엑셀) → 문서. 만들기 전에 확인 화면에서 값을 확인·수정한다")
    ap.add_argument("data", type=Path, help="데이터 파일(.json / .csv / .xlsx)")
    ap.add_argument("--template", type=Path, help="워드 양식(.docx). 안의 {{칸 이름}}을 값으로 바꾼다(예: {{이름}}, {{취득일}}). 없으면 기본 양식 PDF")
    ap.add_argument("--type", help="표에 '문서 종류' 열이 없을 때 문서 종류(예: cert, 자격증, lease)")
    ap.add_argument("--out", type=Path, default=Path("새문서"), help="만든 문서를 둘 폴더")
    ap.add_argument("--seed", type=int, default=7, help="데이터에 없는 값(주소·번호 등)을 정하는 난수 씨앗")
    ap.add_argument("--yes", action="store_true", help="확인 화면 없이 바로 만든다(시험·자동화용)")
    ap.add_argument("--no-open", action="store_true", help="확인 화면 주소만 출력하고 브라우저를 열지 않음")
    args = ap.parse_args()
    if not args.data.exists():
        sys.exit(f"오류: 데이터 파일이 없음 — {args.data}")
    if args.template and (not args.template.exists() or args.template.suffix.lower() != ".docx"):
        sys.exit(f"오류: 양식은 있는 .docx 파일이어야 함 — {args.template}")
    dtype = resolve_type(args.type) if args.type else None
    if args.type and not dtype:
        sys.exit(f"오류: 모르는 문서 종류 '{args.type}' (가능: {', '.join(DOC_FIELDS)})")
    try:
        recs = load_records(args.data, dtype)
    except (OSError, ValueError, IndexError, KeyError, zipfile.BadZipFile, UnicodeDecodeError) as e:
        sys.exit(f"오류: 데이터 파일을 읽을 수 없음 — {args.data.name} ({type(e).__name__}: {e})")
    if not recs:
        sys.exit("오류: 데이터가 비어 있음")
    docs = prepare_docs(recs, args.template, args.seed)

    def build(posted):
        todo = apply_edits(docs, posted)
        bad = {d["name"]: d["problems"] for d in todo if d["problems"]}
        if bad:
            return {"problems": bad}
        res = write_docs(todo, args.template, args.out, args.seed)
        lines = [f"만듦: {p}" + (f" ({m})" if m else "") if p else f"건너뜀: {n} — {m}" for n, p, m in res]
        lines.append(f"문서 {sum(p is not None for _, p, _ in res)}개 → {args.out.resolve()}")
        print("\n".join(lines))
        return {"lines": lines}

    for d in docs:
        for note in d["notes"]:
            print(f"참고: {d['name']} — {note}")
    if args.yes:
        for d in docs:
            if d["filled"]:
                print(f"참고: {d['name']} — 비어 있어 임의 값으로 채움: " + ", ".join(FIELD_LABEL.get(k, k) for k in d["filled"]))
        res = write_docs(apply_edits(docs, [{"name": d["name"], "make": True, "values": d["values"]} for d in docs]), args.template, args.out, args.seed)
        for n, p, m in res:
            print(f"만듦: {p}" + (f" ({m})" if m else "") if p else f"건너뜀: {n} — {m}")
        print(f"문서 {sum(p is not None for _, p, _ in res)}개 → {args.out.resolve()}")
    else:
        serve_confirm(docs, build, open_browser=not args.no_open)


# ============================================================================

COMMANDS = {
    "run": main_run,
    "make": main_make,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv.pop(1)
    sys.argv[0] = f"bridgedocs.py {cmd}"
    COMMANDS[cmd]()


if __name__ == "__main__":
    main()
