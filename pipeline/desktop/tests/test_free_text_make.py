"""보통 문장으로 쓴 txt → AI가 문서 종류와 칸 값을 뽑아 문서 만들기."""
import json
from pathlib import Path

import pytest

import bridgedocs as bd
from desktop.fake import FakeService
from desktop.service import DesktopService
from test_service_contract import FakeEngine

TEXT = ("김철수가 이영희에게 가게를 빌려준다. 보증금은 5천만 원, 월세는 100만 원, 관리비 10만 원이다. "
        "기간은 2025년 1월 1일부터 2026년 12월 31일까지.")
ANSWER = {"st_lessor": "김철수", "st_lessee": "이영희", "st_deposit": "50000000", "st_rent": "1000000",
          "st_mgmt_fee": "100000", "st_start": "2025-01-01", "st_end": "2026-12-31"}


def fake_ask(prompt):
    if "다음 중" in prompt:
        return "상가건물 임대차계약서"
    return "```json\n" + json.dumps({**ANSWER, "st_lessee": "박영수"}, ensure_ascii=False) + "\n```"


def test_text_record_asks_type_then_fields():
    rec = bd.text_record(TEXT, fake_ask, name="메모")
    assert rec["doc_type"] == "store_lease" and rec["name"] == "메모"
    assert rec["values"]["st_lessor"] == "김철수" and rec["values"]["st_end"] == "2026-12-31"
    assert any("박영수" in n for n in rec["notes"])          # 글에 없는 이름 = AI가 지어냈을 수 있음
    assert not any("김철수" in n for n in rec["notes"])


def test_text_record_unknown_type_is_korean_error():
    with pytest.raises(ValueError, match="어떤 문서"):
        bd.text_record(TEXT, lambda p: "위임장" if "다음 중" in p else "{}")


@pytest.fixture
def real_svc(tmp_path, monkeypatch):
    from desktop import offline
    models = tmp_path / "models"
    for rel in offline.required_model_dirs():
        (models / rel).mkdir(parents=True)
    monkeypatch.setattr(bd, "ask_text", lambda model, proc, prompt, max_new=64: fake_ask(prompt))
    return DesktopService(models_dir=models, engine=FakeEngine())


def test_prose_txt_makes_document(real_svc, tmp_path):
    p = tmp_path / "가게 계약 메모.txt"
    p.write_text(TEXT, encoding="utf-8")
    docs = real_svc.prepare_make(p)
    assert len(docs) == 1 and docs[0]["doc_type"] == "store_lease" and docs[0]["name"] == "가게 계약 메모"
    assert docs[0]["values"]["st_deposit"].replace(",", "") == "50000000"
    assert any("AI가 글에서" in n for n in docs[0]["notes"])
    r = real_svc.submit_make(docs, [{"name": d["name"], "make": True, "values": d["values"]} for d in docs], tmp_path / "out")
    assert r["problems"] == {} and Path(r["results"][0]["path"]).name == "가게 계약 메모.pdf"


def test_structured_txt_does_not_ask_ai(real_svc, tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "ask_text", lambda *a, **k: pytest.fail("표 형식 txt는 AI에게 묻지 않는다"))
    p = tmp_path / "kv.txt"
    p.write_text("문서 종류: 상가건물 임대차계약서\n임대인: 김철수\n", encoding="utf-8")
    assert real_svc.prepare_make(p)[0]["values"]["st_lessor"] == "김철수"


def test_practice_version_explains_prose_txt(tmp_path):
    p = tmp_path / "메모.txt"
    p.write_text(TEXT, encoding="utf-8")
    with pytest.raises(ValueError, match="연습판"):
        FakeService(delay=0).prepare_make(p)
