"""과제 B: 문서 종류는 AI가 판단, 처음 보는 종류는 AI가 칸을 정하고 사용자가 확정하면 저장, 중·일 증명서는 사용자 확인."""
import json

import pytest
from PIL import Image

import bridgedocs as bd
from test_joint import LINES, TRUE


def _line(t, i):
    return {"text": t, "x0": 0, "x1": 500, "y0": i * 20, "y1": i * 20 + 10, "cy": i * 20 + 5,
            "parts": [{"text": t, "x0": 0, "x1": 500, "y0": i * 20, "y1": i * 20 + 10}]}


@pytest.fixture
def engine(monkeypatch, tmp_path):
    """read_pages·ask를 바꿔 끼운다. answers: 질문 종류별 답(type/joint/ocr-joint)."""
    monkeypatch.setenv("BD_CUSTOM", str(tmp_path / "새 문서 종류.json"))
    img = Image.new("RGB", (100, 100), "white")
    state = {"lines": LINES, "type": "주택임대차계약서", "first": TRUE, "second": TRUE, "calls": []}
    monkeypatch.setattr(bd, "read_pages", lambda paths, ocr, deskew=False: ([img], [state["lines"]], ["ocr"]))
    monkeypatch.setattr(bd, "reread_cjk", lambda pages, lines_list, routes: lines_list)

    def fake_ask(model, proc, image, prompt, max_new=64):
        state["calls"].append(prompt)
        if prompt.startswith(bd.TYPE_PRE) or bd.TYPE_PRE in prompt:
            return state["type"], (100, 100)
        vals = state["first"] if "[OCR 시작]" in prompt else state["second"]
        return json.dumps(vals, ensure_ascii=False), (100, 100)
    monkeypatch.setattr(bd, "ask", fake_ask)
    return state


def run(tmp_path):
    return bd.process_document([tmp_path / "a.png"], None, None, None, mode="joint")


def test_ai_decides_type_over_keywords(engine, tmp_path):
    engine["type"] = "매매계약서"                     # 글자에는 '주택임대차'가 있어도 AI 판단을 따른다
    engine["first"] = engine["second"] = {"seller_name": "김철수"}
    assert run(tmp_path)["doc_type"] == "sale"


def test_unclear_type_answer_falls_back_to_keywords(engine, tmp_path):
    engine["type"] = ""
    assert run(tmp_path)["doc_type"] == "lease"


def test_new_type_ai_picks_fields_for_user_to_confirm(engine, tmp_path):
    engine["lines"] = [_line(t, i) for i, t in enumerate(["위 임 장", "위임인 김철수", "수임인 이영희"])]
    engine["type"] = "위임장"
    engine["first"] = engine["second"] = {"위임인": "김철수", "수임인": "이영희"}
    r = run(tmp_path)
    assert r["doc_type"] == "custom" and r["doc_name"] == "위임장" and r["new_type"] is True
    assert [f["label"] for f in r["fields"]] == ["위임인", "수임인"]
    assert all(f["status"] == "review" for f in r["fields"])     # 처음 보는 종류는 모두 사용자 확인


def test_saved_new_type_reuses_confirmed_fields(engine, tmp_path):
    bd.save_custom_type("위임장", ["위임인", "수임인", "위임일"])
    engine["lines"] = [_line(t, i) for i, t in enumerate(["위 임 장", "위임인 김철수", "수임인 이영희", "위임일 2025-01-02"])]
    engine["type"] = "위임장"
    engine["first"] = {"위임인": "김철수", "수임인": "이영희", "위임일": "2025-01-02"}
    engine["second"] = {"위임인": "김철수", "수임인": "이영회", "위임일": "2025-01-02"}
    r = run(tmp_path)
    assert r["new_type"] is False and [f["label"] for f in r["fields"]] == ["위임인", "수임인", "위임일"]
    st = {f["label"]: f["status"] for f in r["fields"]}
    assert st == {"위임인": "auto", "수임인": "review", "위임일": "auto"}
    assert "위임장" in bd.custom_types()


def test_japanese_certificate_needs_user_check(engine, tmp_path):
    engine["lines"] = [_line(t, i) for i, t in enumerate(["合格証明書", "氏名 やまだ たろう", "令和5年6月13日"])]
    engine["type"] = "자격증·수료증·성적증명서"
    engine["first"] = engine["second"] = {"cert_holder": "やまだ たろう"}
    r = run(tmp_path)
    assert r["doc_type"] == "cert"
    assert all(f["status"] == "review" for f in r["fields"])
    assert any("일본어" in f["reason"] for f in r["fields"])
