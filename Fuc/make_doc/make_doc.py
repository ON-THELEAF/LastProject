"""읽기·내보내기·문서 관리 화면이 함께 쓰는 도우미: 문서 종류 표기 읽기, 표 파일(CSV·TXT·엑셀) 읽기, 워드 양식 빈칸 찾기.
문서 만들기(데이터 → 워드 양식 채우기)는 2026-11-25에 지웠다(엔진·서버만, 화면 Front/는 팀원 담당)."""
import io
import re
import zipfile
from pathlib import Path


KIND_HINT = {"date": "날짜(예: 2025-06-13)", "amount": "금액(숫자, 예: 100,000,000)", "name": "한글 이름", "number": "숫자(예: 84.97)",
             "owners": "소유자(예: 홍길동 또는 홍길동(1/2), 김민지(1/2))", "person": "이름", "choice": "정해 둔 값 중 하나",
             "flag": "'있음' 또는 '없음'", "grade": "등급·점수", "text": "글자"}


WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def resolve_type(s):
    """'매매계약서'·'sale' 같은 문서 종류 표기 → 엔진의 문서 종류 키. 모르면 None."""
    s = (s or "").strip()
    if s in DOC_FIELDS:
        return s
    return next((k for k, lab in DOC_TYPE_LABEL.items() if s and (s == lab or s in lab.split("·"))), None)


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


SLOT = r"\{\{\s*(.*?)\s*\}\}"            # 양식의 빈칸 {{칸 이름}} ({{}}처럼 비워도 됨)
SLOT_PART = r"word/(document|header\d*|footer\d*)\.xml"


def template_slots(template):
    """양식 docx의 빈칸 이름들을 문서에 나오는 순서대로(문서 관리 화면에 보여 줌)."""
    import html
    out = []
    with zipfile.ZipFile(template) as z:
        for item in z.infolist():
            if re.fullmatch(SLOT_PART, item.filename):
                for p in re.findall(r"<w:p[ >].*?</w:p>", z.read(item.filename).decode("utf-8"), re.S):
                    text = "".join(html.unescape(t) for t in re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", p, re.S))
                    out += re.findall(SLOT, text)
    return out


# 함수 안에서만 쓰는 다른 기능의 이름: 맨 아래에서 불러온다(서로 부르는 기능끼리 순환해도 됨)
from Fuc.read_pipeline.read_pipeline import DOC_FIELDS, DOC_TYPE_LABEL  # noqa: E402,F401
