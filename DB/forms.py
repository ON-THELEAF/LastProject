"""문서 양식(문서 종류·칸·칸 종류·AI 질문·검사 규칙) 불러오기. 코드에는 양식을 두지 않는다.
- 기본 양식: DB/기본 양식.json (부동산·증명서 6종류. 고치려면 이 파일만 고친다)
- 사용자 양식: 새 문서 종류.json (storage.custom_types). 칸마다 이름과 종류(kind)를 저장하고, 종류가 없으면 칸 이름으로 짐작한다.
두 가지 모두 같은 모양 {"id", "name", "words", "fields": {key: 칸 설정}, "rules": [...]} 으로 돌려주므로 읽기·검사는 구분 없이 같은 길을 탄다."""
import json
import re
from functools import lru_cache
from pathlib import Path

BUILTIN_FILE = Path(__file__).with_name("기본 양식.json")
KINDS = ("name", "person", "date", "amount", "number", "text", "flag", "owners", "grade", "choice")
PRE = ("이것은 문서 이미지에 인쇄·기재된 글자를 그대로 옮겨 적는 작업입니다. "
       "설명이나 문장 없이 값만 한 줄로 답하세요. 문서에 없거나 읽을 수 없으면 '없음'이라고만 답하세요.\n")
KIND_ASK = {"date": " YYYY-MM-DD 형식으로 적으세요.", "amount": " 쉼표 포함 숫자로만 적으세요(예: 120,000,000).",
            "number": " 숫자만 적으세요(예: 84.97).", "name": " 사람·회사·기관의 이름만 적으세요.", "flag": " '있음' 또는 '없음'만 적으세요."}
# 사용자 양식의 칸 이름은 문서에 그대로 적혀 있지 않을 수 있다('임대차 시작일' ↔ '…까지 임차인에게 인도하고')
SAME_MEANING = " 칸 이름이 문서에 그대로 없으면 뜻이 같은 항목의 값을 적으세요."


@lru_cache(maxsize=1)
def _builtin():
    return json.loads(BUILTIN_FILE.read_text(encoding="utf-8"))


def _shape(form):
    """JSON의 칸 목록 → {key: 칸 설정} (순서 유지)."""
    return {**form, "fields": {f["key"]: f for f in form["fields"]}, "rules": form.get("rules", [])}


def builtin_forms():
    """기본 양식 {id: 양식}. 순서 = 핵심 단어로 판별하는 순서(앞이 우선: 상가건물임대차를 주택임대차보다 먼저)."""
    return {f["id"]: _shape(f) for f in _builtin()["forms"]}


def label_words():
    """라벨로 쓰이는 낱말 전부(값 왼쪽 라벨 찾기용): 칸들의 aliases + 따로 적은 낱말."""
    words = {a for f in builtin_forms().values() for s in f["fields"].values() for a in s.get("aliases", [])}
    return sorted(words | set(_builtin().get("label_words", [])), key=len, reverse=True)


def guess_kind(label):
    """칸 이름으로 칸 종류를 짐작한다(사용자가 종류를 정하지 않은 칸). 모르면 text."""
    s = re.sub(r"\s", "", label or "")
    if re.search(r"(일자|날짜|일시|년월일|기간)$", s) or (s.endswith("일") and not s.endswith(("요일", "휴일"))):
        return "date"
    if re.search(r"(금액|대금|가격|보증금|최고액|비용|차임|월세|임대료|금$|료$|비$|액$)", s):
        return "amount"
    if re.search(r"(면적|㎡|수량|개수|층수)", s):
        return "number"
    if re.search(r"(성명|이름|소유자|대표자|명의인)", s) or (s.endswith("인") and len(s) <= 4):     # 임대인·매수인·대리인
        return "name"
    if re.search(r"(유무|여부)$", s):
        return "flag"
    return "text"


def custom_form(name, fields):
    """사용자 양식 하나. fields: [{"label", "kind"?}] 또는 칸 이름 목록(옛 형식)."""
    out = {}
    for f in fields:
        label = (f.get("label") if isinstance(f, dict) else str(f)).strip()
        kind = f.get("kind") if isinstance(f, dict) and f.get("kind") in KINDS else guess_kind(label)
        out[label] = {"key": label, "label": label, "kind": kind}
    return {"id": "custom", "name": name, "words": [], "fields": out, "rules": []}


def question(spec):
    """칸 하나를 묻는 질문. 양식에 질문이 있으면 그대로(+위치 힌트), 없으면 칸 이름과 종류로 만든다."""
    if spec.get("q"):
        return PRE + spec["q"] + " " + spec.get("hint", "")
    return PRE + f"문서에 적힌 '{spec['label']}'의 값을 적으세요." + SAME_MEANING + KIND_ASK.get(spec["kind"], "")


def joint_item(spec):
    """여러 칸을 한 번에 묻는 질문의 한 줄 설명."""
    if spec.get("q"):
        return f'{spec["q"]} {spec.get("hint", "")}'.rstrip()
    return f"문서에 적힌 '{spec['label']}'의 값" + KIND_ASK.get(spec["kind"], "")


def all_builtin_specs():
    """기본 양식의 모든 칸 {key: 칸 설정} (칸 key는 양식끼리 겹치지 않는다)."""
    return {k: s for f in builtin_forms().values() for k, s in f["fields"].items()}


def label_order():
    """화면·목록에 보일 기본 양식 순서."""
    return _builtin().get("label_order") or [f["id"] for f in _builtin()["forms"]]
