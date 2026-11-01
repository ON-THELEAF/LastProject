"""과제 C: 문서를 두 번만 읽고(사진+OCR, 사진만) 두 답이 다른 칸은 사용자 판단."""
import pytest
from PIL import Image

import bridgedocs as bd

LINES = [{"text": t, "x0": 0, "x1": 500, "y0": i * 20, "y1": i * 20 + 10, "cy": i * 20 + 5,
          "parts": [{"text": t, "x0": 0, "x1": 500, "y0": i * 20, "y1": i * 20 + 10}]}
         for i, t in enumerate(["주택임대차표준계약서", "임대인 김철수", "임차인 이영희", "보증금 금 일억원정(₩100,000,000)",
                                "차임(월세) 금 500,000원", "2024년 3월 1일까지 임차인에게 인도하고", "임대차기간은 인도일로부터 2026년 2월 28일까지로 한다"])]
TRUE = {"lessor_name": "김철수", "lessee_name": "이영희", "deposit_amount": "100,000,000", "monthly_rent": "500,000",
        "contract_start_date": "2024-03-01", "contract_end_date": "2026-02-28"}


def test_parse_joint_reads_json_inside_text():
    ans = '```json\n{"lessor_name": "김철수", "deposit_amount": 100000, "other": "x"}\n```'
    assert bd.parse_joint(ans, ["lessor_name", "deposit_amount"]) == {"lessor_name": "김철수", "deposit_amount": "100000"}
    assert bd.parse_joint("모르겠어요", ["lessor_name"]) == {}
    assert bd.parse_joint('{"lessor_name": "김철수",', ["lessor_name"]) == {}


def test_joint_question_lists_every_key_with_hint():
    q = bd.joint_question("lease", ["lessor_name", "deposit_amount"])
    assert '"lessor_name"' in q and '"deposit_amount"' in q and "JSON" in q and bd.HINT["deposit_amount"] in q


@pytest.fixture
def lease(monkeypatch):
    img = Image.new("RGB", (100, 100), "white")
    monkeypatch.setattr(bd, "read_pages", lambda paths, ocr, deskew=False: ([img], [LINES], ["ocr"]))
    monkeypatch.setattr(bd, "reread_cjk", lambda pages, lines_list, routes: lines_list)     # 중·일 OCR 모델을 불러오지 않게
    calls = []

    def fake_ask(model, proc, image, prompt, max_new=64):
        calls.append(prompt)
        vals = dict(TRUE)
        if "[OCR 시작]" not in prompt:          # 2차(사진만): 임차인을 다르게 읽음
            vals["lessee_name"] = "이영회"
        return __import__("json").dumps(vals, ensure_ascii=False), (100, 100)
    monkeypatch.setattr(bd, "ask", fake_ask)
    return calls


def test_joint_mode_asks_twice_and_flags_disagreement(lease, tmp_path):
    r = bd.process_document([tmp_path / "a.png"], None, None, None, mode="joint")
    asks = [c for c in lease if bd.TYPE_PRE not in c]
    assert len(asks) == 2                                    # 칸 6개인데 두 번만 묻는다(문서 종류 질문 빼고)
    by = {f["key"]: f for f in r["fields"]}
    assert [f["key"] for f in r["fields"]] == list(bd.DOC_FIELDS["lease"])
    assert by["lessee_name"]["status"] == "review" and "두 번 읽은 값이 다름" in by["lessee_name"]["reason"]
    assert set(by["lessee_name"]["candidates"]) >= {"이영희", "이영회"}
    assert by["lessor_name"]["status"] == "auto" and by["deposit_amount"]["status"] == "auto"


def test_joint_mode_falls_back_per_field_when_json_breaks(monkeypatch, lease, tmp_path):
    def broken(model, proc, image, prompt, max_new=64):
        lease.append(prompt)
        if bd.TYPE_PRE in prompt:
            return "주택임대차계약서", (100, 100)
        if "JSON" in prompt:
            return "죄송합니다", (100, 100)
        return "김철수", (100, 100)
    monkeypatch.setattr(bd, "ask", broken)
    r = bd.process_document([tmp_path / "a.png"], None, None, None, mode="joint")
    assert len([c for c in lease if bd.TYPE_PRE not in c]) == 2 + 6       # 두 번 + 빠진 칸마다 한 번
    assert {f["key"] for f in r["fields"]} == set(bd.DOC_FIELDS["lease"])


def test_stop_cancels_between_questions(lease, tmp_path):
    n = {"checks": 0}

    def stop():
        n["checks"] += 1
        return n["checks"] > 3      # 종류 질문·1차를 묻고 나서 멈춤
    with pytest.raises(bd.Cancelled):
        bd.process_document([tmp_path / "a.png"], None, None, None, stop=stop, mode="joint")
    assert len([c for c in lease if bd.TYPE_PRE not in c]) == 1
