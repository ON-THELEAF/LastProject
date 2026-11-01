"""과제 E: txt 내보내기, JSON은 내보내지 않고 저장 위치를 알려 주기, 기존 파일에 이어 붙이기."""
from pathlib import Path

import pytest

import bridgedocs
from desktop import export
from test_export import doc, field


def done(**kw):
    return doc([field(status="auto", **kw)])


def test_txt_is_tab_separated_with_header(tmp_path):
    out = export.export_files([done()], ["txt"], tmp_path / "확정")
    assert out[0].name == "확정.txt"
    lines = out[0].read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].split("\t") == bridgedocs.EXPORT_COLUMNS
    assert lines[1].split("\t")[2:4] == ["보증금", "1,000"]


def test_json_is_not_exported_and_says_where_it_is(tmp_path):
    d = {**done(), "json_path": str(tmp_path / "결과" / "계약서 1.json")}
    with pytest.raises(ValueError, match="JSON") as e:
        export.export_files([d], ["json"], tmp_path / "확정")
    assert str(tmp_path / "결과") in str(e.value)
    assert export.json_folders([d, d]) == [str(tmp_path / "결과")]
    assert "json" not in export.FORMATS


@pytest.mark.parametrize("kind", ["xlsx", "csv", "txt"])
def test_append_adds_rows_without_second_header(tmp_path, kind):
    stem = tmp_path / "누적"
    export.export_files([done(value="1,000")], [kind], stem)
    out = export.export_files([done(value="2,000")], [kind], stem, append=True)
    rows = export.read_rows(out[0])
    assert rows[0] == bridgedocs.EXPORT_COLUMNS
    assert [r[3] for r in rows[1:]] == ["1,000", "2,000"]


def test_append_refuses_missing_file_and_other_doc_type(tmp_path):
    with pytest.raises(ValueError, match="이어 붙일 파일이 없"):
        export.export_files([done()], ["csv"], tmp_path / "없음", append=True)
    export.export_files([done()], ["csv"], tmp_path / "임대차")
    other = {**done(), "doc_type": "sale"}
    with pytest.raises(ValueError, match="다른 문서 종류"):
        export.export_files([other], ["csv"], tmp_path / "임대차", append=True)


def test_append_refuses_file_with_other_columns(tmp_path):
    p = tmp_path / "남의파일.csv"
    p.write_text("이름,나이\n홍길동,3\n", encoding="utf-8-sig")
    with pytest.raises(ValueError, match="열이 달라"):
        export.export_files([done()], ["csv"], tmp_path / "남의파일", append=True)
