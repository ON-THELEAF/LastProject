"""문서 만들기 탭의 양식 고르기: 원래 있던 양식(기본 양식) 또는 사용자가 넣은 새 워드 양식."""
import json
import zipfile
from pathlib import Path

import pytest

import bridgedocs as bd
from desktop.service import DesktopService

REG = "등기부등본(집합건물) 예시.pdf"


def make_docx(path: Path, text: str) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                                        f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return path


@pytest.fixture
def forms(tmp_path, monkeypatch):
    folder = tmp_path / "양식"
    monkeypatch.setenv("BD_FORMS", str(folder))
    monkeypatch.setattr(bd, "SRC", tmp_path / "문서 예시 없음")
    return folder


def data_file(tmp_path, doc_type=None) -> Path:
    p = tmp_path / "data.json"
    d = {"source": ["홍길동.pdf"], "fields": [{"key": "cert_holder", "value": "홍길동"}, {"key": "cert_title", "value": "정보처리기사"}]}
    p.write_text(json.dumps([{**d, "doc_type": doc_type}] if doc_type else [d], ensure_ascii=False), encoding="utf-8")
    return p


def test_builtin_templates_only_list_available_ones(forms):
    ids = {t["id"] for t in DesktopService().list_templates()}
    assert {"builtin:ledger", "builtin:store_lease"} <= ids and "builtin:registry" not in ids and "builtin:cert" not in ids
    forms.mkdir()
    (forms / REG).write_bytes(b"%PDF")
    assert "builtin:registry" in {t["id"] for t in DesktopService().list_templates()}


def test_added_word_template_is_kept_and_listed(forms, tmp_path):
    src = make_docx(tmp_path / "내 증명서.docx", "성명: {{이름}}")
    t = DesktopService().add_template(src)
    assert t["kind"] == "docx" and t["name"] == "내 증명서.docx" and Path(t["path"]).parent == forms
    assert t["id"] in {x["id"] for x in DesktopService().list_templates()}       # 프로그램을 다시 켜도 남아 있다
    t2 = DesktopService().add_template(src)                                       # 같은 이름은 덮어쓰지 않는다
    assert t2["name"] == "내 증명서 (2).docx"


def test_add_template_rejects_non_docx(forms, tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    with pytest.raises(ValueError, match="워드 양식"):
        DesktopService().add_template(tmp_path / "a.pdf")


def test_remove_template_only_user_ones(forms, tmp_path):
    svc = DesktopService()
    t = svc.add_template(make_docx(tmp_path / "x.docx", "{{이름}}"))
    svc.remove_template(t["id"])
    assert t["id"] not in {x["id"] for x in svc.list_templates()} and not Path(t["path"]).exists()
    with pytest.raises(ValueError, match="기본 양식"):
        svc.remove_template("builtin:cert")


def test_builtin_template_sets_type_and_rejects_other_type(forms, tmp_path):
    svc = DesktopService()
    docs = svc.prepare_make(data_file(tmp_path), doc_type="cert")                 # 데이터에 종류가 없으면 고른 양식의 종류
    assert docs[0]["doc_type"] == "cert" and any("만들지 않아요" in x for x in docs[0]["problems"])     # 증명서는 기본 양식으로 안 만듦
    with pytest.raises(ValueError, match="건축물대장"):
        svc.prepare_make(data_file(tmp_path, "cert"), doc_type="ledger")


def test_word_template_makes_docx(forms, tmp_path):
    svc = DesktopService()
    t = svc.add_template(make_docx(tmp_path / "증.docx", "성명: {{이름}} / 종목: {{자격·시험·과정}}"))
    docs = svc.prepare_make(data_file(tmp_path, "cert"), template=t["path"])
    res = svc.submit_make(docs, [{"name": d["name"], "make": True, "values": d["values"]} for d in docs], tmp_path / "out")
    out = res["results"][0]["path"]
    assert out.suffix == ".docx" and "홍길동" in zipfile.ZipFile(out).read("word/document.xml").decode("utf-8")
