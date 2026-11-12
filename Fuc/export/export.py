"""판별 화면의 값 검사·판정·내보내기(xlsx·csv·txt). JSON은 문서를 읽을 때 이미 저장되므로 내보내지 않고 위치만 알려 준다. HTML 판별 화면의 JS(parseValue·decide·toRows·toXlsx)와 같은 규칙.

판정 함수는 원본 dict를 바꾸지 않고 새 dict를 돌려준다(화면이 되돌리기·다시 그리기를 쉽게).
정규식은 re.ASCII: JS의 \\d처럼 0-9만 숫자로 본다(전각 숫자 거부).
"""
import os
import re
import zipfile
from datetime import date
from pathlib import Path

import bridgedocs as bd

FORMATS = ("xlsx", "csv", "txt")


def parse_value(kind: str, s: str) -> tuple[str | None, str]:
    """사용자가 직접 입력한 값 → (정리된 값, 오류 문구). 오류가 없으면 문구는 ""."""
    s = (s or "").strip()
    if not s:
        return None, "빈 값"
    if kind == "date":
        m = re.search(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})", s, re.ASCII)
        if not m:
            return None, "날짜는 2025-06-13처럼 연-월-일로 입력"
        try:
            d = date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None, "없는 날짜"
        return d.isoformat(), ""
    if kind == "amount":
        return (re.search(r"[\d,]+", s, re.ASCII)[0], "") if re.fullmatch(r"[\d,]+\s*원?", s, re.ASCII) else (None, "금액은 숫자로 입력(쉼표 가능, 예: 100,000,000)")
    if kind == "flag":
        return ("있음", "") if "있" in s else ("없음", "") if "없" in s else (None, "'있음' 또는 '없음'으로 입력")
    if kind == "name":
        return (s, "") if len(re.findall(r"[가-힣]", s)) >= 2 else (None, "이름은 한글 2자 이상")
    if kind == "number":
        return (re.search(r"[\d.]+", s, re.ASCII)[0], "") if re.fullmatch(r"\d+(\.\d+)?\s*(㎡|점)?", s, re.ASCII) else (None, "숫자로 입력(예: 84.97)")
    return s, ""


def decide(field: dict, value: str, status: str) -> dict:
    """칸 판정. 처음 읽은 값(read_value)과 되돌릴 값(orig)은 처음 한 번만 기록한다."""
    return {"read_value": field["value"], "orig": {"value": field["value"], "status": field["status"]},
            **field, "value": value, "status": status}


def decide_all(doc: dict) -> dict:
    """'한 번에 확정' 버튼: 이 문서의 확인 필요 칸을 지금 보이는 값 그대로 모두 확정한 새 문서."""
    return {**doc, "fields": [decide(f, f["value"], "confirmed") if f["status"] == "review" else f for f in doc["fields"]]}


def undo(field: dict) -> dict:
    if "orig" not in field:
        return dict(field)
    rest = {k: v for k, v in field.items() if k not in ("orig", "read_value")}
    return {**rest, **field["orig"]}


def review_left(docs: list[dict]) -> int:
    return sum(f["status"] == "review" for d in docs for f in d["fields"])


def export_rows(docs: list[dict]) -> list[list]:
    rows = [list(bd.EXPORT_COLUMNS)]
    for d in docs:
        for f in d["fields"]:
            rows.append([", ".join(d.get("source") or []), d.get("doc_name") or bd.DOC_TYPE_LABEL.get(d["doc_type"], d["doc_type"]), f["label"], f["value"],
                         bd.STATUS_LABEL.get(f["status"], f["status"]), f.get("read_value", f["value"]), f.get("reason"), f.get("page")])
    return rows


def write_csv(rows: list[list], path: Path) -> None:
    def q(v):
        v = "" if v is None else str(v)
        return '"' + v.replace('"', '""') + '"' if re.search(r'[",\r\n]', v) else v
    Path(path).write_text("﻿" + "\r\n".join(",".join(map(q, r)) for r in rows), encoding="utf-8", newline="")  # BOM: 엑셀에서 한글이 안 깨짐


def _x(s) -> str:
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(s))
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def write_xlsx(rows: list[list], path: Path, sheet: str = "추출 결과") -> None:
    def cell(v, i, r):
        ref = f"{chr(65 + i)}{r}"   # 열은 8개뿐(A~H)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return f'<c r="{ref}"><v>{v}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_x("" if v is None else v)}</t></is></c>'
    body = "".join(f'<row r="{ri}">' + "".join(cell(v, i, ri) for i, v in enumerate(row)) + "</row>" for ri, row in enumerate(rows, 1))
    X = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    M = "http://schemas.openxmlformats.org/"
    R = M + "officeDocument/2006/relationships"
    parts = {
        "[Content_Types].xml": f'{X}<Types xmlns="{M}package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": f'{X}<Relationships xmlns="{M}package/2006/relationships"><Relationship Id="rId1" Type="{R}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": f'{X}<workbook xmlns="{M}spreadsheetml/2006/main" xmlns:r="{R}"><sheets><sheet name="{_x(sheet)}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": f'{X}<Relationships xmlns="{M}package/2006/relationships"><Relationship Id="rId1" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'{X}<worksheet xmlns="{M}spreadsheetml/2006/main"><sheetData>{body}</sheetData></worksheet>',
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts.items():
            z.writestr(name, text)


TXT_SEPS = {"탭": "\t", "쉼표": ",", "세미콜론": ";", "세로줄": "|"}


def write_txt(rows: list[list], path: Path, sep: str = "\t") -> None:
    """구분 기호(기본 탭)로 나눈 글자 파일(메모장·엑셀에서 열림). 값 안의 구분 기호·줄바꿈은 빈칸으로 바꾼다."""
    def c(v):
        return re.sub("[" + re.escape(sep) + r"\r\n]+", " ", "" if v is None else str(v))
    Path(path).write_text("\ufeff" + "\r\n".join(sep.join(map(c, r)) for r in rows), encoding="utf-8", newline="")


def read_rows(path: Path, sep: str = "\t") -> list[list[str]]:
    """내보낸 파일(xlsx·csv·txt) → 행 목록(글자). txt는 sep으로 나눈다."""
    path = Path(path)
    if path.suffix.lower() == ".txt":
        return [line.split(sep) for line in path.read_text(encoding="utf-8-sig").splitlines()]
    return bd.read_table(path)


def json_folders(docs: list[dict]) -> list[str]:
    """문서를 읽을 때 저장한 JSON이 있는 폴더들(화면이 'JSON은 여기 있어요'로 보여 줌)."""
    return sorted({str(Path(d["json_path"]).parent) for d in docs if d.get("json_path")})


def _appended(path: Path, rows: list[list], sep: str = "\t") -> list[list]:
    """기존 파일 행 + 새 행(머리줄 빼고). 열이나 문서 종류가 다르면 ValueError."""
    if not path.exists():
        raise ValueError(f"이어 붙일 파일이 없어요 — {path.name}")
    try:
        old = read_rows(path, sep)
    except (OSError, ValueError, zipfile.BadZipFile, UnicodeDecodeError, IndexError) as err:
        raise ValueError(f"{path.name} 를 읽을 수 없어요 — 이 프로그램으로 내보낸 파일인지 확인하세요 ({err})") from err
    if not old or [str(x) for x in old[0]] != list(bd.EXPORT_COLUMNS):
        raise ValueError(f"{path.name} 는 열이 달라 이어 붙일 수 없어요 — 이 프로그램으로 내보낸 파일(같은 구분 기호)을 고르세요")
    had = {r[1] for r in old[1:] if len(r) > 1 and r[1]}
    new = {r[1] for r in rows[1:]}
    if had and new - had:
        raise ValueError(f"{path.name} 에는 다른 문서 종류({', '.join(sorted(had))})가 들어 있어요 — 같은 종류 파일에만 이어 붙일 수 있어요")
    return old + rows[1:]


def export_files(docs: list[dict], kinds: list[str], path_stem: Path, append: bool = False, sep: str = "\t",
                 force: bool = False, only: list[str] | None = None) -> list[Path]:
    """고른 형식마다 파일 하나. append=True면 같은 이름의 기존 파일 뒤에 이어 붙인다(임시 파일에 먼저 쓰고 성공하면 바꿔치기).
    확인 필요 칸이 남았거나(force=True면 사용자가 승인한 것으로 보고 내보냄), 형식을 안 고르거나, JSON을 고르면
    ValueError(화면에 그대로 보여 줄 한국어). only: 이 칸 이름들만 내보낸다. sep: TXT 구분 기호."""
    if "json" in kinds:
        where = ", ".join(json_folders(docs)) or "문서 읽기 결과 폴더"
        raise ValueError(f"JSON은 문서를 읽을 때 이미 저장돼 있어요 — {where}")
    kinds = [k for k in FORMATS if k in kinds]
    if not kinds:
        raise ValueError("형식을 하나 이상 고르세요")
    left = review_left(docs)
    if left and not force:
        raise ValueError(f"남은 확인 {left}칸 — 다 끝내야 내보낼 수 있어요")
    if only is not None:
        keep = set(only)
        docs = [{**d, "fields": [f for f in d["fields"] if f["label"] in keep]} for d in docs]
        if not any(d["fields"] for d in docs):
            raise ValueError("내보낼 칸을 하나 이상 고르세요")
    path_stem = Path(path_stem)
    name = re.sub(r"\.(xlsx|csv|txt|json)$", "", path_stem.name, flags=re.I)     # 저장 창이 붙인 확장자는 뗀다
    name = re.sub(r'[\\/:*?"<>|]', "", name).strip(" .") or "확정"
    if name.upper().split(".")[0] in bd.WIN_RESERVED:
        name = "_" + name
    rows = export_rows(docs)
    targets = {k: path_stem.parent / f"{name}.{k}" for k in kinds}
    content = {k: _appended(t, rows, sep) if append else rows for k, t in targets.items()}     # 다 확인한 뒤에 쓴다(일부만 쓰지 않게)
    writers = {"xlsx": write_xlsx, "csv": write_csv, "txt": lambda r, p: write_txt(r, p, sep)}
    out, target = [], path_stem.parent
    try:
        target.mkdir(parents=True, exist_ok=True)
        for k in kinds:
            target = targets[k]
            tmp = target.with_name(target.name + ".tmp")     # 쓰다 실패해도 원래 파일은 그대로
            try:
                writers[k](content[k], tmp)
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
            out.append(target)
    except OSError as err:
        raise ValueError(f"{target.name} 를 저장할 수 없음 — 엑셀 등에서 열려 있으면 닫고 다시 시도하세요 ({err.strerror or err})") from err
    return out
