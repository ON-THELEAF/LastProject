"""문서 읽기 진입점: 문서 입력·종류 판별·분석·자동 확정/확인 필요  (bridgedocs.py에서 나눔)"""
import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path


import pymupdf  # noqa: E402
from PIL import Image  # noqa: E402


# ============================================================================
# 엔진 진입점: 정답 없이 파일 → 값 + 자동 확정/확인 필요 (서버·화면이 쓰는 형식)
# ============================================================================
# 실행: python bridgedocs.py run <파일...> [--out 결과.json] [--deskew]
#   파일: PDF(여러 쪽 가능) 또는 쪽 이미지(png/jpg) 여러 장. 한 번에 문서 하나.
# 흐름: 쪽 이미지 → (선택) 기울기 보정 → 페이지 OCR → 문서 종류 판별(핵심 단어 먼저, 안 되면 AI) → B경로(OCR 줄 + OCR 없이 다시 읽기 + 이름·숫자 칸 확대 재판독)
#       → 규칙 검사(크롭 재질문 없음: 오탐만 늘어서 2026-10-24에 뺌) → JSON

# 문서 양식(종류·칸·질문·검사 규칙)은 코드에 두지 않는다: DB/기본 양식.json(기본) + 새 문서 종류.json(사용자). 아래는 그 파일에서 만든 표
from DB.forms import all_builtin_specs, builtin_forms, custom_form, joint_item, label_order, question  # noqa: E402

_FORMS = builtin_forms()
DOC_FIELDS = {t: {k: s["kind"] for k, s in f["fields"].items()} for t, f in _FORMS.items()}   # 기본 양식의 칸과 종류(kind)
COMPUTED = {k for k, s in all_builtin_specs().items() if s.get("computed")}     # 모델에 묻지 않고 엔진이 정하는 칸(문서 언어)
LANG_LABEL = {"ko": "한국어", "en": "영어"}
LANG_CODE = {v: k for k, v in LANG_LABEL.items()}
FIELD_LABEL = {k: s["label"] for k, s in all_builtin_specs().items()}
DOC_TYPE_WORDS = [(t, f["words"]) for t, f in _FORMS.items()]       # 앞이 우선(상가건물임대차를 주택임대차보다 먼저)
ANSWER_WORDS = DOC_TYPE_WORDS + [(t, f["answer_words"]) for t, f in _FORMS.items() if f.get("answer_words")]   # AI 답을 종류로 바꿀 때


def form_of(doc_type, custom_name=None):
    """문서 종류 → 양식(기본 양식이면 그 양식, 사용자 양식이면 저장해 둔 칸과 종류). 없으면 None."""
    if doc_type in _FORMS:
        return _FORMS[doc_type]
    return custom_forms().get(custom_name) if doc_type == "custom" and custom_name else None


def detect_doc_type(page_lines_list):
    text = squash(" ".join(L["text"] for lines in page_lines_list for L in lines)).casefold()
    for dtype, words in DOC_TYPE_WORDS:
        if any(w in text for w in words):
            return dtype
    return None


def _field_page(spec, page_lines_list):
    for i, lines in enumerate(page_lines_list):
        if any(w in squash(" ".join(L["text"] for L in lines)) for w in spec.get("page_words", [])):
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


JOINT_PRE = ("이것은 문서 이미지에 인쇄·기재된 글자를 그대로 옮겨 적는 작업입니다. 아래 항목마다 값을 찾아 "
             "JSON 객체 하나로만 답하세요(설명·문장 없이). 키는 그대로 쓰고, 값은 문자열로 쓰세요. "
             "문서에 없거나 읽을 수 없으면 값을 '없음'으로 쓰세요.\n")


def joint_question(fm, keys):
    """칸 여러 개를 한 번에 묻는 질문(칸마다 같은 설명·위치 힌트)."""
    items = "\n".join(f'- "{k}": {joint_item(fm["fields"][k])}' for k in keys)
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


def joint_answers(model, proc, fm, keys_by_page, pages, lines_list, is_docx, check):
    """문서를 두 번만 읽는다(쪽마다): 1차 = 사진 + OCR 줄, 2차 = 사진만. 1차 JSON에 빠진 칸만 칸마다 따로 묻는다.
    -> {칸: {"ans", "ans2"(없으면 None), "strict2": True}}"""
    out = {}
    for pi, keys in keys_by_page.items():
        q = joint_question(fm, keys)
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
                one = question(fm["fields"][k])
                first[k] = (ask_text(model, proc, docx_body(lines_list[0]) + "\n\n" + text_question(one)) if is_docx
                            else ask(model, proc, pages[pi], with_ocr(one, lines_list[pi]), max_new=64)[0])
            out[k] = {"ans": first[k], "ans2": second.get(k), "strict2": True, "norm_c": False}
    return out


# ───────── 문서 종류: AI가 판단(2026-10-28 결정). 답을 알아듣지 못하면 정해 둔 단어로 찾는다 ─────────
TYPE_PRE = "이 문서가 어떤 문서인지 판단하세요."
NEW_TYPE_Q = ("이 문서에서 꼭 필요한 항목(사람 이름·날짜·금액·주소·번호 등)을 골라, 항목 이름을 키로 문서에 적힌 값을 그대로 "
              "JSON 객체 하나로만 답하세요(설명·문장 없이). 항목은 15개 이하로 고르세요.")


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
    t = t or next((d for d, words in ANSWER_WORDS if any(w in sq.casefold() for w in words)), None)
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
    """처음 보는 문서 종류 읽기(모든 쪽): AI가 쪽마다 칸을 골라 읽고(합쳐 15개까지) 모두 사용자 확인.
    저장해 둔 사용자 양식은 form_of()로 read_fields()를 탄다. -> (fields, 처음 보는 종류인지=True)"""
    first, page_of = {}, {}
    for i in range(len(lines_list)):
        check()
        if len(first) >= 15:
            break
        if is_docx:
            got = parse_json_obj(ask_text(model, proc, docx_body(lines_list[i]) + "\n\n" + NEW_TYPE_Q, max_new=600))
        else:
            got = parse_json_obj(ask(model, proc, pages[i], with_ocr(NEW_TYPE_Q, lines_list[i]), max_new=600)[0])
        for k, v in got.items():
            if k not in first and len(first) < 15:
                first[k], page_of[k] = v, i + 1
    out = [{"key": lab, "label": lab, "kind": "text", "value": v, "status": "review",
            "reason": "처음 보는 문서 종류 — 칸 이름과 값을 확인하세요", "candidates": [v] if v else [],
            "page": page_of.get(lab, 1), "line_box": None, "compare": ""} for lab, v in first.items()]
    return out, True


class Cancelled(Exception):
    """사용자가 취소함: 읽던 문서는 버린다."""


def process_document(paths, model, proc, ocr, deskew=False, stop=None, mode="field", want=None):
    """정답 없이 문서 하나를 처리한다. -> dict(source, files, route, doc_type, pages, fields[...], summary) 또는 error.
    fields 각 항목: key, label, kind, value, status('auto'=자동 확정 | 'review'=사람 확인), reason, candidates, page, line_box.
    stop: 부를 때마다 True면 Cancelled를 던진다(AI에게 묻기 전마다 확인).
    mode: 'field' = 칸마다 묻기(+일부 칸 다시 읽기) | 'joint' = 쪽마다 두 번만 묻고 두 답이 다른 칸은 사용자 판단.
    want: 사용자가 읽기 전에 고른 칸 이름들. 있으면 문서 종류와 상관없이 그 칸만 읽는다(없으면 AI가 정한 칸)."""
    def check():
        if stop and stop():
            raise Cancelled()
    pages, lines_list, routes = read_pages(paths, ocr, deskew)
    check()
    is_docx = routes == ["docx"]
    base = {"source": [Path(p).name for p in paths], "files": [str(Path(p).resolve()) for p in paths], "deskew": deskew,
            "route": routes, "pages": len(lines_list)}
    # 종합설계 요약 2번: OCR 글자의 핵심 단어로 먼저 판별하고, 못 하면 AI에게 묻는다(사용자가 등록한 종류도 여기서)
    doc_type, custom_name = detect_doc_type(lines_list), None
    if doc_type is None:
        doc_type, custom_name = resolve_ai_type(ask_doc_type(model, proc, pages, lines_list, is_docx))
        check()
    if want:      # 사용자가 읽기 전에 고른 칸: 칸 이름으로 종류를 짐작해 기본 양식과 같은 검사를 받는다
        name = DOC_TYPE_LABEL.get(doc_type) or custom_name or "고른 칸"
        out = read_fields(custom_form(name, want), model, proc, pages, lines_list, is_docx, check)
        n_rev = sum(o["status"] == "review" for o in out)
        return {**base, "doc_type": doc_type if doc_type in DOC_TYPE_LABEL else "custom", "doc_name": name, "new_type": False,
                "fields": out, "summary": {"auto": len(out) - n_rev, "review": n_rev}}
    if doc_type is None:
        return {**base, "doc_type": None, "fields": [], "summary": {"auto": 0, "review": 0},
                "error": "문서 종류를 알 수 없음(AI도 정해 둔 단어로도 판단하지 못함)"}
    fm = form_of(doc_type, custom_name)
    if fm is None:      # 처음 보는 종류: AI가 칸을 골라 읽고 모두 사용자 확인
        out, new = custom_fields(model, proc, custom_name, pages, lines_list, is_docx, check)
        n_rev = sum(o["status"] == "review" for o in out)
        return {**base, "doc_type": "custom", "doc_name": custom_name, "new_type": new, "fields": out,
                "summary": {"auto": len(out) - n_rev, "review": n_rev}}
    out = read_fields(fm, model, proc, pages, lines_list, is_docx, check, mode)
    n_rev = sum(o["status"] == "review" for o in out)
    return {**base, "doc_type": doc_type, "doc_name": fm["name"], "new_type": False, "fields": out,
            "summary": {"auto": len(out) - n_rev, "review": n_rev}}


def read_fields(fm, model, proc, pages, lines_list, is_docx, check, mode="field"):
    """양식(기본·사용자 구분 없음)의 칸을 읽고 환각검증을 거친 칸 목록.
    mode: 'field' = 칸마다 묻기(+모든 칸 OCR 없이 다시 읽기, 이름·숫자 칸 확대 재판독) | 'joint' = 쪽마다 두 번만 묻기."""
    names = [f"p{i + 1}" for i in range(len(lines_list))]
    cache = dict(zip(names, lines_list))
    specs = fm["fields"]
    fields, answers = {}, {}
    if mode == "joint":
        by_page = {}
        for key, sp in specs.items():
            if not sp.get("computed"):
                by_page.setdefault(_field_page(sp, lines_list), []).append(key)
        got = joint_answers(model, proc, fm, by_page, pages, lines_list, is_docx, check)
        page_of = {k: pi for pi, keys in by_page.items() for k in keys}
        for key in got:
            answers[("doc", key)] = got[key]
        for key, sp in specs.items():     # 칸 순서는 양식의 순서대로
            if key in page_of:
                fields[key] = {"value": "", "kind": sp["kind"], "page": page_of[key] + 1, "region": None, "b_skip": False, "spec": sp}
    for key, sp in specs.items():
        if sp.get("computed") or mode == "joint":
            continue
        kind = sp["kind"]
        check()
        pi = _field_page(sp, lines_list)
        q = question(sp)
        if is_docx:     # 쪽 이미지가 없으니 글자만 묻는다. 두 번째 판독(OCR 없이 이미지로 다시 묻기)은 할 수 없다
            row = {"ans": ask_text(model, proc, docx_body(lines_list[0]) + "\n\n" + text_question(q)), "norm_c": False}
        else:
            row = {"ans": ask(model, proc, pages[pi], with_ocr(q, lines_list[pi]), max_new=64)[0], "norm_c": False}
            # 종합설계 요약 3번 ② 모든 칸을 OCR 없이 다시 읽기, ③ 이름·기관명(과 숫자 칸)은 그 영역을 확대해 다시 읽기
            v = clean(kind, row["ans"], sp)
            try:
                row["ans2"] = ask(model, proc, pages[pi], q, max_new=64)[0]
                if v not in ("", "없음") and sp.get("zoom") == "number":
                    row["zoom"] = zoom_number(model, proc, pages[pi], lines_list[pi], kind, v)
                elif v not in ("", "없음") and zooms_name(sp):
                    row["zoom"] = zoom_name(model, proc, pages[pi], lines_list[pi], kind, v)
            except Exception:   # 두 번째 판독 실패: 그 칸은 사용자 확인
                row["second_failed"] = True
        answers[("doc", key)] = row
        fields[key] = {"value": "", "kind": kind, "page": pi + 1, "region": None, "b_skip": False, "spec": sp}
    gt = {"doc": {"doc_type": fm["id"], "rules": fm["rules"], "pages": names, "fields": fields}}
    chk = compute_checks(gt, ["doc"], answers, cache)
    out = []
    for key, f in fields.items():
        c = chk[("doc", key)]
        lines = lines_list[f["page"] - 1]
        occ = [i for i, _ in occurrences(f["kind"], c["value"], lines) if i >= 0] if c["value"] and not is_docx else []
        box = [round(lines[occ[0]][k]) for k in ("x0", "y0", "x1", "y1")] if occ else None
        failed = answers[("doc", key)].get("second_failed")
        review = c["pol"] or garbled(answers[("doc", key)]["ans"]) or failed
        ans2 = answers[("doc", key)].get("ans2")
        # 두 번째 판독이 다른 칸 값(이름 자리에 주소 등)을 답하면 후보 버튼에 넣지 않는다
        second = clean(f["kind"], ans2, f["spec"]) if ans2 else ""
        cands = list(dict.fromkeys(v for v in (c["value"], second if format_ok(f["kind"], second, f["spec"]) else "", c.get("suggest") or "") if v)) if review else []
        out.append({"key": key, "label": f["spec"]["label"], "kind": f["kind"], "value": c["value"],
                    "status": "review" if review else "auto",
                    "reason": (c["reason"] or ("두 번째 판독 실패 — 확인하세요" if failed else "")) if review else "",
                    "candidates": cands, "page": f["page"], "line_box": box,
                    # 확인 화면에 보여 줄 비교 값 하나(2026-10-28 결정): 읽은 값과 다른 첫 후보
                    "compare": next((x for x in cands if x != c["value"]), "")})
    for key, sp in specs.items():
        if sp.get("computed") == "lang":
            out.append(lang_field(lines_list, key, sp["label"]))
    return out


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


DOC_TYPE_LABEL = {t: _FORMS[t]["name"] for t in label_order()}      # 화면·목록에 보일 순서
STATUS_LABEL = {"auto": "자동 확정", "confirmed": "사용자 확인", "edited": "사용자 수정", "review": "확인 필요"}
EXPORT_COLUMNS = ["파일", "문서 종류", "항목", "값", "상태", "처음 읽은 값", "확인 사유", "쪽"]


# 함수 안에서만 쓰는 다른 기능의 이름: 맨 아래에서 불러온다(서로 부르는 기능끼리 순환해도 됨)
from DB.storage import custom_forms, custom_types  # noqa: E402,F401
from Fuc.docx_read.docx_read import DOCX_KEEP, DOCX_MAX_CHARS, ask_text, docx_lines  # noqa: E402,F401
from Fuc.hallucination_check.hallucination_check import compute_checks, format_ok, lang_field, occurrences, squash, zoom_name, zoom_number, zooms_name  # noqa: E402,F401
from Fuc.make_doc.make_doc import resolve_type  # noqa: E402,F401
from Fuc.path_a_ocr.path_a_ocr import lines_xy, make_ocr, ocr_items  # noqa: E402,F401
from Fuc.path_b_vlm.path_b_vlm import ask, garbled, load, with_ocr  # noqa: E402,F401
from Fuc.pdf_text.pdf_text import extract_text_layer  # noqa: E402,F401
from Fuc.read_pipeline.preprocess import preprocess, render  # noqa: E402,F401
from Fuc.scoring.scoring import clean  # noqa: E402,F401
from Fuc.view.view import render_view  # noqa: E402,F401
