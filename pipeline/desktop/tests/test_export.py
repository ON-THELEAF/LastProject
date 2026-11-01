import json

import pytest

import bridgedocs
from desktop import export


@pytest.mark.parametrize("kind,s,want", [
    ("date", "2025.6.13", ("2025-06-13", "")),
    ("date", "2025년 6월 3일", ("2025-06-03", "")),
    ("date", "2025-02-30", (None, "없는 날짜")),
    ("date", "어제", (None, "날짜는 2025-06-13처럼 연-월-일로 입력")),
    ("amount", "100,000,000원", ("100,000,000", "")),
    ("amount", "1억", (None, "금액은 숫자로 입력(쉼표 가능, 예: 100,000,000)")),
    ("flag", "있음", ("있음", "")),
    ("flag", "모름", (None, "'있음' 또는 '없음'으로 입력")),
    ("name", "김", (None, "이름은 한글 2자 이상")),
    ("name", " 홍길동 ", ("홍길동", "")),
    ("number", "84.97㎡", ("84.97", "")),
    ("text", "아무 글", ("아무 글", "")),
    ("text", "   ", (None, "빈 값")),
])
def test_parse_value_matches_js(kind, s, want):
    assert export.parse_value(kind, s) == want


def field(**kw):
    return {"key": "k", "label": "보증금", "kind": "amount", "value": "1,000", "status": "review", "reason": "r", "page": 1, **kw}


def test_decide_is_immutable_and_keeps_first_read():
    f = field()
    g = export.decide(f, "2,000", "edited")
    assert f == field()
    assert (g["value"], g["status"], g["read_value"]) == ("2,000", "edited", "1,000")
    h = export.decide(g, "3,000", "edited")
    assert h["read_value"] == "1,000" and h["orig"] == {"value": "1,000", "status": "review"}
    assert export.undo(h) == field()


def doc(fields):
    return {"source": ["계약서 1.pdf"], "doc_type": "lease", "fields": fields, "images": ["x"]}


def test_review_left_counts():
    assert export.review_left([doc([field(), field(status="auto")])]) == 1


def test_rows_columns_and_values():
    rows = export.export_rows([doc([export.decide(field(), "2,000", "edited")])])
    assert rows[0] == bridgedocs.EXPORT_COLUMNS
    assert rows[1] == ["계약서 1.pdf", "주택임대차계약서", "보증금", "2,000", "사용자 수정", "1,000", "r", 1]


def test_files_roundtrip_and_refuse_unfinished(tmp_path):
    docs = [doc([field(value="1,000, \"따옴표\"", status="auto")])]
    out = export.export_files(docs, ["xlsx", "csv", "txt"], tmp_path / "바탕 화면" / "확정")
    assert [p.suffix for p in out] == [".xlsx", ".csv", ".txt"]
    xl = bridgedocs.read_xlsx(out[0])
    assert xl[1][3] == "1,000, \"따옴표\"" and xl[1][7] == "1"
    raw = out[1].read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf" and "보증금" in raw.decode("utf-8-sig")
    assert export.read_rows(out[2])[1][3] == "1,000, \"따옴표\""
    with pytest.raises(ValueError, match="남은 확인 1칸"):
        export.export_files([doc([field()])], ["csv"], tmp_path / "x")
    with pytest.raises(ValueError, match="형식"):
        export.export_files(docs, [], tmp_path / "x")


def test_export_name_is_sanitized(tmp_path):
    out = export.export_files([doc([field(status="auto")])], ["csv"], tmp_path / '확정<1>?')
    assert out[0].name == "확정1.csv" and out[0].parent == tmp_path


def test_fullwidth_digits_rejected_like_js():
    assert export.parse_value("amount", "１００")[0] is None
    assert export.parse_value("date", "２０２５-０６-１３")[0] is None


def test_export_name_strips_extension_and_reserved(tmp_path):
    d = [doc([field(status="auto")])]
    assert export.export_files(d, ["xlsx"], tmp_path / "확정.xlsx")[0].name == "확정.xlsx"
    assert export.export_files(d, ["csv"], tmp_path / "CON")[0].name == "_CON.csv"
