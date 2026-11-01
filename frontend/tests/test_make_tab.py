import json
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "frontend"), str(ROOT / "pipeline")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import bridgedocs as bd  # noqa: E402
from desktop.service import DesktopService  # noqa: E402
from make_tab import FILLED_NOTE, MakeTab  # noqa: E402

app = QApplication.instance() or QApplication([])


@pytest.fixture
def tab(tmp_path, monkeypatch):
    monkeypatch.setenv("BD_FORMS", str(tmp_path / "양식"))
    monkeypatch.setattr(bd, "SRC", tmp_path / "없음")
    return MakeTab(DesktopService())


def data(tmp_path, holder="홍길동"):
    p = tmp_path / "data.json"
    p.write_text(json.dumps([{"source": ["a.pdf"], "fields": [{"key": "cert_holder", "value": holder}, {"key": "cert_title", "value": "정보처리기사"}]}],
                            ensure_ascii=False), encoding="utf-8")
    return p


def store_data(tmp_path, lessor="김철수"):
    p = tmp_path / "store.json"
    p.write_text(json.dumps([{"source": ["가게.pdf"], "doc_type": "store_lease",
                              "fields": [{"key": "st_lessor", "value": lessor}, {"key": "st_deposit", "value": "50,000,000"}]}],
                            ensure_ascii=False), encoding="utf-8")
    return p


def test_builtin_template_flow_edit_and_make(tab, tmp_path):
    tab.set_data(store_data(tmp_path))
    tab.select_template("builtin:store_lease")
    tab.load()
    top = tab.tree.topLevelItem(0)
    assert top.checkState(0) == Qt.Checked
    lessor = next(top.child(j) for j in range(top.childCount()) if top.child(j).data(0, Qt.UserRole) == "st_lessor")
    assert lessor.text(1) == "김철수" and lessor.text(2) == ""
    rent = next(top.child(j) for j in range(top.childCount()) if top.child(j).data(0, Qt.UserRole) == "st_rent")
    assert rent.text(1) == ""                                                             # 빈칸은 임의 값 없이 빈칸
    assert not any(top.child(j).text(2) == FILLED_NOTE for j in range(top.childCount()))
    lessor.setText(1, "김민지")                                                           # 화면에서 고친 값으로 만든다
    res = tab.make(tmp_path / "out")
    assert res["results"][0]["path"].exists() and "1건을 만들었어요" in tab.message.toPlainText()


def test_new_word_template_added_and_used(tab, tmp_path):
    src = tmp_path / "내 양식.docx"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("word/document.xml", "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
                                        "<w:body><w:p><w:r><w:t>{{이름}}</w:t></w:r></w:p></w:body></w:document>")
    tab.add_template(src)
    assert tab.template()["name"] == "내 양식.docx"
    d = json.loads(data(tmp_path).read_text(encoding="utf-8"))
    (tmp_path / "data.json").write_text(json.dumps([{**d[0], "doc_type": "cert"}], ensure_ascii=False), encoding="utf-8")
    tab.set_data(tmp_path / "data.json")
    tab.load()
    assert "{{이름}}" in tab.hint.text()
    out = tab.make(tmp_path / "out")["results"][0]["path"]
    assert out.suffix == ".docx" and "홍길동" in zipfile.ZipFile(out).read("word/document.xml").decode("utf-8")


def test_errors_are_shown_not_raised(tab, tmp_path):
    tab.load()
    assert "데이터 파일을 먼저" in tab.message.toPlainText()
    tab.set_data(store_data(tmp_path))
    tab.select_template("builtin:store_lease")
    tab.load()
    top = tab.tree.topLevelItem(0)
    for j in range(top.childCount()):
        if top.child(j).data(0, Qt.UserRole) == "st_start":
            top.child(j).setText(1, "어제")
    res = tab.make(tmp_path / "out")
    assert res["problems"] and "고칠 칸이 있어" in tab.message.toPlainText() and not (tmp_path / "out").exists()
