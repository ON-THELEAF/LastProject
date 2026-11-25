"""저장·불러오기: 새 문서 종류 목록(JSON)·워드 양식 폴더 위치·결과 JSON 다시 저장  (bridgedocs.py에서 나눔)"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # 프로젝트 맨 위


def custom_path():
    """사용자가 확정한 새 문서 종류(종류 이름 → 칸 이름들)를 저장하는 파일. 프로그램에서는 exe 옆."""
    return Path(os.environ.get("BD_CUSTOM") or ROOT / "새 문서 종류.json")


def _custom_raw(strict=False):
    """파일 그대로 {종류 이름: [{"label", "kind"}] 또는 옛 형식 [칸 이름]}.
    strict=True면 파일이 깨졌을 때 {} 대신 ValueError(저장하다 기존 종류를 지우지 않게)."""
    p = custom_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError) as err:
        if strict:
            raise ValueError(f"{p.name} 파일이 깨져 있어 저장하지 않았어요 — 파일을 고치거나 옮긴 뒤 다시 하세요 ({err})") from err
        return {}
    return {k: list(v) for k, v in data.items() if isinstance(v, list)} if isinstance(data, dict) else {}


def custom_types():
    """사용자 양식 {종류 이름: [칸 이름]} (화면이 쓰는 모양)."""
    return {k: [f["label"] if isinstance(f, dict) else str(f) for f in v] for k, v in _custom_raw().items()}


def custom_forms():
    """사용자 양식 {종류 이름: 양식}. 칸마다 종류(kind)가 있어 기본 양식과 같은 검사를 받는다."""
    return {k: custom_form(k, v) for k, v in _custom_raw().items()}


def save_custom_type(name, labels, kinds=None):
    """새 문서 종류와 사용자가 확정한 칸 이름들을 저장한다(다음에 같은 종류가 오면 이 칸들을 읽음).
    kinds: 칸 이름 → 칸 종류(date·amount·name 등). 없는 칸은 이름으로 짐작해 함께 저장한다."""
    name = (name or "").strip()
    labels = list(dict.fromkeys(str(x).strip() for x in labels if str(x).strip()))
    if not name or not labels:
        raise ValueError("문서 종류 이름과 칸을 하나 이상 정하세요")
    if resolve_type(name):
        raise ValueError(f"'{name}'은 이미 있는 문서 종류예요")
    kinds = kinds or {}
    fields = [{"label": lab, "kind": field_kind(lab, kinds.get(lab))} for lab in labels]
    data = {**_custom_raw(strict=True), name: fields}
    p = custom_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)      # 읽는 쪽이 반쯤 쓴 파일을 보지 않게
    return custom_types()


def delete_custom_type(name):
    """확정해 둔 새 문서 종류를 지운다(다음부터는 처음 보는 종류로 읽음)."""
    data = _custom_raw(strict=True)
    if name not in data:
        raise ValueError(f"'{name}' 문서 종류가 없어요")
    del data[name]
    p = custom_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return custom_types()


def forms_dir():
    """사용자가 기본 양식 PDF를 넣는 폴더(BD_FORMS, 프로그램에서는 exe 옆 '양식'). 없으면 None."""
    return Path(os.environ["BD_FORMS"]) if os.environ.get("BD_FORMS") else None


def save_result(doc):
    """확인 중인 문서(사용자가 확정·고친 값 포함)를 읽을 때 만든 결과 JSON(json_path)에 다시 저장한다."""
    path = doc.get("json_path")
    if not path:
        return
    p = Path(path)
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: v for k, v in doc.items() if k != "json_path"}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    except OSError as err:
        raise ValueError(f"확인한 내용을 저장할 수 없음 — {p} ({err.strerror or err})") from err


# 함수 안에서만 쓰는 다른 기능의 이름: 맨 아래에서 불러온다(서로 부르는 기능끼리 순환해도 됨)
from DB.forms import KINDS, custom_form, field_kind, guess_kind  # noqa: E402,F401
from Fuc.make_doc.make_doc import resolve_type  # noqa: E402,F401
