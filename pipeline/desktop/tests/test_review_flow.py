"""과제 D: 확인 필요 칸에는 비교할 값 하나, 문서 하나를 한 번에 확정."""
import bridgedocs as bd
from desktop import export
from test_joint import lease  # noqa: F401  (fixture)


def test_review_field_has_one_compare_value(lease, tmp_path):
    r = bd.process_document([tmp_path / "a.png"], None, None, None, mode="joint")
    f = next(x for x in r["fields"] if x["key"] == "lessee_name")
    assert f["compare"] == "이영회" and f["compare"] != f["value"]
    assert all(x["compare"] == "" for x in r["fields"] if x["status"] == "auto")


def test_decide_all_confirms_every_review_field_without_touching_input():
    doc = {"doc_type": "lease", "fields": [{"key": "a", "value": "1", "status": "review"},
                                           {"key": "b", "value": "2", "status": "auto"},
                                           {"key": "c", "value": "3", "status": "edited", "read_value": "0", "orig": {"value": "0", "status": "review"}}]}
    out = export.decide_all(doc)
    assert [f["status"] for f in out["fields"]] == ["confirmed", "auto", "edited"]
    assert out["fields"][0]["read_value"] == "1"
    assert doc["fields"][0]["status"] == "review"                # 원본은 그대로
    assert export.review_left([out]) == 0
