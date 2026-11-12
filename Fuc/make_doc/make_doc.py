"""문서 만들기: 데이터(JSON·CSV·엑셀) → 워드 양식 채우기·PDF  (bridgedocs.py에서 나눔)"""
import argparse
import io
import json
import re
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path


# ============================================================================
# 문서 만들기: 데이터(JSON·CSV·엑셀) → 문서  (교수님 면담 2번: 추출의 반대 방향)
# ============================================================================
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


def prepare_docs(recs, template=None):
    """make 1단계: 표의 줄마다 만들 문서의 최종 값을 정한다. 아직 파일은 쓰지 않는다(사용자 확인 전).
    데이터에 없는 칸은 임의 값으로 채우지 않고 빈칸으로 둔다(2026-10-28 결정, filled는 항상 빈 목록).
    워드 양식(.docx)이 있어야 만든다(기본 양식 PDF는 없앰, 2026-11-12).
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
        if not template:
            doc["problems"].append("워드 양식(.docx)을 고르세요")
            continue
        doc["values"] = values
    return docs


TEXT_LIMIT = 4000       # AI에게 넘기는 글 길이(글자). 더 길면 앞부분만
FREE_TYPE_Q = "아래 글은 어떤 문서를 만들기 위한 내용이에요. 다음 중 무엇인지 이름만 한 줄로 답하세요(설명 없이).\n목록: {names}\n\n글:\n{text}"
FREE_FIELDS_Q = ("아래 글은 '{doc}'를 만들 내용이에요. 글에서 각 항목의 값을 찾아 JSON 객체 하나로만 답하세요(설명 없이). "
                 "글에 없는 항목은 \"\"로 두고 지어내지 마세요. 금액은 숫자만(예: 50000000), 날짜는 YYYY-MM-DD.\n항목:\n{items}\n\n글:\n{text}")


def text_record(text, ask, dtype=None, name="문서", types=None):
    """보통 문장으로 쓴 글 → 만들 문서 하나(load_records와 같은 모양 + notes). ask(질문) -> AI 답.
    dtype이 없으면 AI가 types(기본: 증명서를 뺀 문서 종류) 중에서 고른다.
    shortcut: 글 하나 = 문서 하나. 한 글에 여러 계약을 쓰면 첫 번째만 — 필요하면 문단마다 나눠 묻기."""
    text = (text or "").strip()[:TEXT_LIMIT]
    if not text:
        raise ValueError("글이 비어 있어요")
    types = types or [t for t in DOC_FIELDS if t != "cert"]
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
        if not d["template"]:      # 다시 검사해도 워드 양식 없이는 만들지 않는다
            problems.append("워드 양식(.docx)을 고르세요")
        out.append({**d, "values": values, "problems": problems})
    return out


def write_docs(docs, template, out_dir):
    """make 3단계: 확인된 값으로 파일을 쓴다. 빈칸은 빈칸으로 둔다.
    문제가 있는 문서가 있거나 하나라도 만들지 못하면 ValueError로 멈춘다(2026-10-28 결정). -> [(이름, 경로, '')]"""
    bad = [d["name"] for d in docs if d["problems"]]
    if bad:
        raise ValueError(f"확인할 칸이 있는 문서: {', '.join(bad)}")
    res = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for d in docs:
        try:
            path = out_dir / f"{d['name']}.docx"
            fill_docx(template, template_values(d, d["values"]), path)
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
    ap.add_argument("--template", type=Path, required=True, help="워드 양식(.docx). 안의 {{칸 이름}}을 값으로 바꾼다(예: {{이름}}, {{취득일}})")
    ap.add_argument("--type", help="표에 '문서 종류' 열이 없을 때 문서 종류(예: cert, 자격증, lease)")
    ap.add_argument("--out", type=Path, default=Path("새문서"), help="만든 문서를 둘 폴더")
    ap.add_argument("--yes", action="store_true", help="확인 화면 없이 바로 만든다(시험·자동화용)")
    ap.add_argument("--no-open", action="store_true", help="확인 화면 주소만 출력하고 브라우저를 열지 않음")
    args = ap.parse_args()
    if not args.data.exists():
        sys.exit(f"오류: 데이터 파일이 없음 — {args.data}")
    if (not args.template.exists() or args.template.suffix.lower() != ".docx"):
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
    docs = prepare_docs(recs, args.template)

    def build(posted):
        todo = apply_edits(docs, posted)
        bad = {d["name"]: d["problems"] for d in todo if d["problems"]}
        if bad:
            return {"problems": bad}
        res = write_docs(todo, args.template, args.out)
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
        res = write_docs(apply_edits(docs, [{"name": d["name"], "make": True, "values": d["values"]} for d in docs]), args.template, args.out)
        for n, p, m in res:
            print(f"만듦: {p}" + (f" ({m})" if m else "") if p else f"건너뜀: {n} — {m}")
        print(f"문서 {sum(p is not None for _, p, _ in res)}개 → {args.out.resolve()}")
    else:
        serve_confirm(docs, build, open_browser=not args.no_open)


# ============================================================================


# 함수 안에서만 쓰는 다른 기능의 이름: 맨 아래에서 불러온다(서로 부르는 기능끼리 순환해도 됨)
from Fuc.hallucination_check.hallucination_check import format_ok, squash  # noqa: E402,F401
from Fuc.read_pipeline.read_pipeline import COMPUTED, DOC_FIELDS, DOC_TYPE_LABEL, FIELD_LABEL, LANG_LABEL, parse_joint, resolve_ai_type  # noqa: E402,F401
from Fuc.scoring.scoring import clean  # noqa: E402,F401
