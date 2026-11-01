"""DesktopService(진짜)와 FakeService(연습용)가 같은 약속을 지키는지 같은 테스트로 확인한다."""
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

from desktop.fake import FakeService
from desktop.service import DesktopService

DATA = ("문서 종류,파일,임대인,임차인,보증금,차임(월세),관리비,임대차 시작일,임대차 종료일\n"
        "상가건물 임대차계약서,가게1,김철수,이영희,50000000,1000000,100000,없음,2026-12-31\n"
        "상가건물 임대차계약서,가게2,박민수,최지우,30000000,,,2025-03-01,2027-02-28\n"
        "건축물대장,대장1,,,,,,,\n")


def fake_process(paths, *a, **k):
    return {"source": [Path(p).name for p in paths], "files": [str(p) for p in paths], "route": ["ocr"], "doc_type": "lease",
            "fields": [{"key": "deposit_amount", "label": "보증금", "kind": "amount", "value": "1,000", "status": "review",
                        "reason": "r", "candidates": ["1,000"], "page": 1, "line_box": None, "compare": ""}], "summary": {"auto": 0, "review": 1}}


class FakeEngine:
    def get(self):
        return None, None, None


@pytest.fixture(params=["real", "fake"])
def svc(request, tmp_path):
    if request.param == "real":
        models = tmp_path / "models"
        from desktop import offline
        for rel in offline.required_model_dirs():
            (models / rel).mkdir(parents=True)
        return DesktopService(models_dir=models, engine=FakeEngine(), process=fake_process)
    return FakeService(delay=0)


@pytest.fixture
def data_csv(tmp_path):
    p = tmp_path / "바탕 화면" / "데이터 1.csv"
    p.parent.mkdir()
    p.write_text(DATA, encoding="utf-8-sig")
    return p


def test_status_shape(svc):
    s = svc.status()
    assert set(s) == {"models_ok", "missing", "message"} and s["models_ok"] is True and isinstance(s["message"], str)


def test_status_missing_models_is_korean(tmp_path):
    s = DesktopService(models_dir=tmp_path / "없음").status()
    assert s["models_ok"] is False and s["missing"] and "모델" in s["message"] and "문서 만들기" in s["message"]


def test_labels_and_suffixes(svc):
    lab = svc.labels()
    assert lab["status"]["review"] == "확인 필요" and lab["doc_type"]["lease"] == "주택임대차계약서"
    assert {"field", "route", "kind_hint"} <= set(lab)
    assert ".pdf" in svc.supported_suffixes()


def wait_read(svc, files, out_dir):
    app = QCoreApplication.instance() or QCoreApplication([])
    got = {"done": [], "failed": [], "finished": None}
    job = svc.start_read(files, out_dir, deskew=False)
    job.doc_done.connect(lambda d: got["done"].append(d))
    job.doc_failed.connect(lambda n, r: got["failed"].append((n, r)))
    job.finished.connect(lambda a, b: got.__setitem__("finished", (a, b)))
    end = time.time() + 30
    while got["finished"] is None and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    svc.wait_all()
    return got


def test_start_read_runs_in_thread_and_saves(svc, tmp_path):
    files = []
    for n in ("a.pdf", "b.pdf", "c.pdf"):
        (tmp_path / n).write_bytes(b"x")
        files.append(tmp_path / n)
    got = wait_read(svc, files, tmp_path / "결과 폴더")
    assert got["finished"] is not None and sum(got["finished"]) == 3
    for d in got["done"]:
        assert {"source", "doc_type", "fields", "summary"} <= set(d)
        f = d["fields"][0]
        assert {"key", "label", "kind", "value", "status", "reason", "candidates", "page", "line_box", "compare"} <= set(f)
    assert len(list((tmp_path / "결과 폴더").glob("*.json"))) == len(got["done"])      # 문서마다 결과 JSON을 바로 저장


def test_fake_has_failure_and_review_for_practice(tmp_path):
    (tmp_path / "아무거나.pdf").write_bytes(b"x")
    got = wait_read(FakeService(delay=0), [tmp_path / "아무거나.pdf"], tmp_path / "out")
    assert got["failed"] and got["done"]
    assert any(f["status"] == "review" for d in got["done"] for f in d["fields"])
    imgs = FakeService(delay=0).page_images(got["done"][0])
    assert imgs and {"src", "w", "h"} <= set(imgs[0])


def test_judge_and_export(svc, tmp_path):
    d = fake_process([Path("계약서.pdf")])
    with pytest.raises(ValueError, match="남은 확인"):
        svc.export([d], ["csv"], tmp_path / "확정")
    v, err = svc.parse_value("amount", "2,000원")
    d = {**d, "fields": [svc.decide(d["fields"][0], v, "edited")]}
    assert svc.review_left([d]) == 0
    out = svc.export([d], ["xlsx", "txt"], tmp_path / "확정")
    assert [p.name for p in out] == ["확정.xlsx", "확정.txt"]
    out = svc.export([d], ["xlsx"], tmp_path / "확정", append=True)
    assert len(svc.read_rows(out[0])) == 3
    assert svc.json_folders([{**d, "json_path": str(tmp_path / "r" / "a.json")}]) == [str(tmp_path / "r")]


def test_prepare_make_shape(svc, data_csv):
    docs = svc.prepare_make(data_csv)
    assert [d["name"] for d in docs] == ["가게1", "가게2", "대장1"]
    for d in docs:
        assert {"name", "doc_type", "values", "filled", "problems", "notes"} <= set(d)
    assert any("건너뜀" in n for n in docs[0]["notes"])     # '없음' 날짜는 건너뜀
    assert all(d["filled"] == [] for d in docs)               # 빈칸은 임의 값으로 채우지 않는다
    assert docs[2]["values"]["bldg_owner"] == "" and docs[0]["values"]["st_start"] == ""


def test_prepare_make_bad_inputs_are_korean_errors(svc, tmp_path):
    with pytest.raises(ValueError, match="데이터 파일이 없음"):
        svc.prepare_make(tmp_path / "없는.csv")
    empty = tmp_path / "빈.csv"
    empty.write_text("문서 종류,파일\n", encoding="utf-8")
    with pytest.raises(ValueError, match="비어 있음"):
        svc.prepare_make(empty)
    with pytest.raises(ValueError, match="양식"):
        svc.prepare_make(empty, template=tmp_path / "양식.txt")


def test_submit_make_blocks_on_problems_then_builds(svc, data_csv, tmp_path):
    docs = svc.prepare_make(data_csv)
    posted = [{"name": d["name"], "make": True, "values": dict(d["values"])} for d in docs]
    posted[0]["values"]["st_end"] = "어제"
    out = tmp_path / "새 문서"
    r = svc.submit_make(docs, posted, out)
    assert r["problems"] and "가게1" in r["problems"] and r["results"] == []
    assert not out.exists() or not any(out.iterdir())
    posted[0]["values"]["st_end"] = "2026-12-31"
    posted[2]["make"] = False
    r = svc.submit_make(docs, posted, out)
    assert r["problems"] == {}
    made = [x for x in r["results"] if x["path"]]
    assert sorted(Path(x["path"]).name for x in made) == ["가게1.pdf", "가게2.pdf"]
    assert all({"name", "path", "message"} <= set(x) for x in r["results"])


def test_wait_all_does_not_deadlock_on_close(svc, tmp_path):
    # 창 닫기: 이벤트를 처리하지 않고 바로 기다려도 멈추지 않아야 한다
    QCoreApplication.instance() or QCoreApplication([])
    (tmp_path / "a.pdf").write_bytes(b"x")
    job = svc.start_read([tmp_path / "a.pdf"], tmp_path / "out")
    t0 = time.time()
    svc.wait_all(ms=20000)
    assert time.time() - t0 < 15
    assert not svc.busy()


class FailingEngine:
    def get(self):
        raise OSError("깨진 모델")


def test_signals_connected_late_are_not_lost(tmp_path):
    from desktop import offline
    models = tmp_path / "models"
    for rel in offline.required_model_dirs():
        (models / rel).mkdir(parents=True)
    app = QCoreApplication.instance() or QCoreApplication([])
    (tmp_path / "a.pdf").write_bytes(b"x")
    svc = DesktopService(models_dir=models, engine=FailingEngine(), process=fake_process)
    job = svc.start_read([tmp_path / "a.pdf"], tmp_path / "out")
    time.sleep(0.2)                       # 화면이 늦게 연결하는 상황
    fin = []
    job.finished.connect(lambda a, b: fin.append((a, b)))
    end = time.time() + 10
    while not fin and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    svc.wait_all()
    assert fin == [(0, 1)]


def test_read_refused_when_models_missing(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"x")
    svc = DesktopService(models_dir=tmp_path / "없음", engine=FakeEngine(), process=fake_process)
    with pytest.raises(ValueError, match="모델 파일이 없습니다"):
        svc.start_read([tmp_path / "a.pdf"], tmp_path / "out")


def test_bad_inputs_refused_also_by_fake(svc, tmp_path):
    with pytest.raises(ValueError, match="파일을 고르세요"):
        svc.start_read([], tmp_path / "out")
    (tmp_path / "메모.txt").write_text("x")
    with pytest.raises(ValueError, match="지원하지 않는"):
        svc.start_read([tmp_path / "메모.txt"], tmp_path / "out")


def test_template_is_remembered_from_prepare(svc, data_csv, tmp_path):
    import inspect
    assert "template" not in inspect.signature(svc.submit_make).parameters   # 준비할 때 고른 양식만 쓴다
    docs = svc.prepare_make(data_csv)
    assert all(d["template_path"] is None for d in docs)


def test_nothing_selected_and_unwritable_out_are_korean_errors(svc, data_csv, tmp_path):
    docs = svc.prepare_make(data_csv)
    with pytest.raises(ValueError, match="만들 문서를"):
        svc.submit_make(docs, [{"name": d["name"], "make": False, "values": d["values"]} for d in docs], tmp_path / "o")
    blocker = tmp_path / "막힘"
    blocker.write_text("x")
    posted = [{"name": docs[1]["name"], "make": True, "values": docs[1]["values"]}]
    with pytest.raises(ValueError, match="폴더"):
        svc.submit_make(docs, posted, blocker)


def test_export_locked_file_is_korean_error(svc, tmp_path, monkeypatch):
    from desktop import export
    d = {**fake_process([Path("x.pdf")])}
    d["fields"] = [{**d["fields"][0], "status": "auto"}]
    def locked(rows, path):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(export, "write_csv", locked)
    with pytest.raises(ValueError, match="열려 있으면 닫고"):
        svc.export([d], ["csv"], tmp_path / "확정")


def test_labels_are_copies(svc):
    import bridgedocs
    svc.labels()["status"]["review"] = "바뀜"
    assert bridgedocs.STATUS_LABEL["review"] == "확인 필요"


def test_service_import_sets_offline_first():
    import subprocess, sys
    code = ("import os, sys\n"
            f"sys.path.insert(0, {str(Path(__file__).parents[2])!r})\n"
            "for k in ('HF_HUB_OFFLINE','HF_HOME'): os.environ.pop(k, None)\n"
            "import desktop.service\nimport huggingface_hub.constants as c\n"
            "print(os.environ['HF_HUB_OFFLINE'], c.HF_HUB_OFFLINE)\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert r.stdout.split()[-2:] == ["1", "True"], r.stderr[-1500:]
