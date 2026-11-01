"""과제 F: 빈칸은 임의 값 없이 빈칸, 증명서 만들기 없음, '견본' 줄·워드 미입력 알림 없음, 고칠 곳 박스 미리보기, txt 데이터."""
import json
from pathlib import Path

import pytest

import bridgedocs as bd
from desktop.service import DesktopService

ST = "문서 종류,파일,임대인,임차인,보증금,차임(월세),관리비,임대차 시작일,임대차 종료일\n" \
     "상가건물 임대차계약서,가게1,김철수,이영희,50000000,,,2025-01-01,2026-12-31\n"


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("BD_FORMS", str(tmp_path / "양식"))
    return DesktopService(models_dir=tmp_path)


def csv_file(tmp_path, text=ST, name="data.csv"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8-sig")
    return p


def test_missing_values_stay_blank_not_random(svc, tmp_path):
    d = svc.prepare_make(csv_file(tmp_path))[0]
    assert d["values"]["st_rent"] == "" and d["values"]["st_mgmt_fee"] == ""
    assert d["values"]["st_lessor"] == "김철수" and d["filled"] == []
    assert not d["problems"]


def test_blank_values_still_make_a_file(svc, tmp_path):
    docs = svc.prepare_make(csv_file(tmp_path))
    r = svc.submit_make(docs, [{"name": "가게1", "make": True, "values": docs[0]["values"]}], tmp_path / "out")
    assert r["problems"] == {} and r["results"][0]["path"].exists()


def test_no_certificate_making(svc, tmp_path):
    assert "builtin:cert" not in {t["id"] for t in svc.list_templates()}
    p = tmp_path / "c.json"
    p.write_text(json.dumps([{"source": ["a.pdf"], "doc_type": "cert", "fields": [{"key": "cert_holder", "value": "홍길동"}]}],
                            ensure_ascii=False), encoding="utf-8")
    d = svc.prepare_make(p)[0]
    assert any("증명서" in x for x in d["problems"])


def test_no_specimen_line_on_made_pdf(svc, tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(bd, "stamp_specimen", lambda img: called.append(1) or img, raising=False)
    docs = svc.prepare_make(csv_file(tmp_path))
    svc.submit_make(docs, [{"name": "가게1", "make": True, "values": docs[0]["values"]}], tmp_path / "out")
    assert called == []


def test_word_template_does_not_report_unfilled_names(svc, tmp_path):
    from test_make_templates import make_docx
    t = svc.add_template(make_docx(tmp_path / "양.docx", "임대인 {{임대인}} / {{없는칸}}"))
    docs = svc.prepare_make(csv_file(tmp_path), template=t["path"])
    r = svc.submit_make(docs, [{"name": "가게1", "make": True, "values": docs[0]["values"]}], tmp_path / "out")
    assert r["results"][0]["message"] == ""


def test_preview_boxes_blank_and_wrong_fields(svc, tmp_path):
    docs = svc.prepare_make(csv_file(tmp_path))
    vals = {**docs[0]["values"], "st_start": "어제"}
    pv = svc.preview_make(docs[0], vals)
    assert pv["pages"] and {"src", "w", "h"} <= set(pv["pages"][0])
    marks = {m["key"]: m for m in pv["marks"]}
    assert set(marks) == {"st_rent", "st_mgmt_fee", "st_start"}
    assert "형식" in marks["st_start"]["reason"] and "비어" in marks["st_rent"]["reason"]
    assert all(len(m["box"]) == 4 and m["page"] == 1 for m in marks.values())


def test_txt_data_with_guessed_separator(svc, tmp_path):
    for sep in ("\t", ";", "|"):
        p = csv_file(tmp_path, ST.replace(",", sep), name=f"d{ord(sep)}.txt")
        d = svc.prepare_make(p)[0]
        assert d["values"]["st_lessee"] == "이영희", sep


def test_txt_key_value_lines(svc, tmp_path):
    p = csv_file(tmp_path, "문서 종류: 상가건물 임대차계약서\n임대인: 김철수\n보증금 : 50000000\n아무말: 무시\n", name="kv.txt")
    d = svc.prepare_make(p)[0]
    assert d["values"]["st_lessor"] == "김철수" and d["values"]["st_deposit"] == "50000000" and d["values"]["st_lessee"] == ""


def test_certificate_stays_blocked_after_confirm_screen(svc, tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps([{"source": ["a.pdf"], "doc_type": "cert", "fields": [{"key": "cert_holder", "value": "홍길동"}]}],
                            ensure_ascii=False), encoding="utf-8")
    docs = svc.prepare_make(p)
    r = svc.submit_make(docs, [{"name": docs[0]["name"], "make": True, "values": docs[0]["values"]}], tmp_path / "out")
    assert r["problems"] and r["results"] == [] and not (tmp_path / "out").exists()


@pytest.mark.parametrize("doc_type,vals", [("store_lease", {"st_lessor": "김철수", "st_deposit": "50,000,000"}),
                                           ("ledger", {"bldg_owner": "김철수"})])
def test_blank_spots_are_empty_on_the_made_page(doc_type, vals):
    import numpy as np
    pages, spots, blank = bd.render_doc(doc_type, vals)
    assert spots and all(spots[k] for k in blank if k != "bldg_violation")
    for k, where in spots.items():
        for pno, (x0, y0, x1, y1) in where:
            ink = np.asarray(pages[pno - 1].convert("L"))[y0 + 6:y1 - 6, x0 + 6:x1 - 6]     # 상자는 6px 칸에 맞춰 커서 옆 글자·선이 걸침
            assert (ink < 128).sum() <= 10, k     # 빈칸 자리에 임의 값 글자가 남지 않음(값 글자면 수십~수백 픽셀)
