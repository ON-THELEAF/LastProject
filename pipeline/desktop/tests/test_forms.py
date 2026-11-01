"""사용자 양식: exe에는 기본 양식(문서 예시/)을 넣지 않는다. 사용자가 프로그램 옆 '양식' 폴더에 PDF를 넣거나 워드 양식을 고른다."""
import json
from pathlib import Path

import pytest

import bridgedocs as bd
from desktop import offline
from desktop.service import DesktopService

REG = "등기부등본(집합건물) 예시.pdf"


@pytest.fixture
def forms(tmp_path, monkeypatch):
    folder = tmp_path / "양식"
    folder.mkdir()
    monkeypatch.setenv("BD_FORMS", str(folder))
    monkeypatch.setattr(bd, "SRC", tmp_path / "문서 예시 없음")
    return folder


def registry_data(tmp_path) -> Path:
    p = tmp_path / "data.json"
    p.write_text(json.dumps([{"source": ["a.pdf"], "doc_type": "registry", "fields": [{"key": "owner_name", "value": "홍길동"}]}],
                            ensure_ascii=False), encoding="utf-8")
    return p


def test_setup_offline_points_forms_to_app_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("BD_FORMS", raising=False)
    offline.setup_offline(tmp_path)
    assert Path(bd.forms_dir()) == tmp_path / "양식"


def test_user_form_is_used(forms):
    (forms / REG).write_bytes(b"%PDF")
    assert bd.form_file("registry") == forms / REG


def test_missing_form_says_where_to_put_it(forms, tmp_path):
    docs = DesktopService().prepare_make(registry_data(tmp_path))
    msg = " ".join(docs[0]["problems"])
    assert str(forms) in msg and REG in msg and "워드 양식" in msg, msg


def test_template_keys_show_names_usable_in_word_form():
    keys = DesktopService().template_keys("lease")
    assert {"보증금", "deposit_amount", "파일", "오늘"} <= set(keys)
    with pytest.raises(ValueError, match="모르는 문서 종류"):
        DesktopService().template_keys("없는종류")
