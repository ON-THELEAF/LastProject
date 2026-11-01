import json
from pathlib import Path

import pytest

from desktop.worker import Engine, ReadJob


class FakeEngine:
    def __init__(self):
        self.calls = 0

    def get(self):
        self.calls += 1
        return "model", "proc", "ocr"


def ok_doc(paths):
    return {"source": [Path(p).name for p in paths], "doc_type": "lease", "route": ["ocr"],
            "fields": [{"key": "k", "label": "보증금", "kind": "amount", "value": "1", "status": "auto"}],
            "summary": {"auto": 1, "review": 0}}


def make_files(folder, names):
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for n in names:
        (folder / n).write_bytes(b"x")
        out.append(folder / n)
    return out


def run_job(files, out_dir, process, on_done=None):
    job = ReadJob(files, out_dir, False, FakeEngine(), process=process)
    got = {"done": [], "failed": [], "finished": None, "progress": []}
    job.doc_done.connect(lambda d: got["done"].append(d))
    if on_done:
        job.doc_done.connect(lambda d: on_done(job))
    job.doc_failed.connect(lambda n, r: got["failed"].append((n, r)))
    job.finished.connect(lambda a, b: got.__setitem__("finished", (a, b)))
    job.progress.connect(lambda s: got["progress"].append(s))
    job.run()
    return got


class OOM(RuntimeError):
    pass


def test_one_failure_does_not_stop_others(tmp_path):
    files = make_files(tmp_path / "in", ["a.pdf", "b.pdf", "c.pdf"])

    def process(paths, model, proc, ocr, deskew=False, **k):
        if paths[0].name == "b.pdf":
            raise OOM("CUDA out of memory. Tried to allocate 2.00 GiB")
        return ok_doc(paths)
    got = run_job(files, tmp_path / "out", process)
    assert len(got["done"]) == 2
    assert got["failed"][0][0] == "b" and "GPU 메모리" in got["failed"][0][1]
    assert got["finished"] == (2, 1)
    assert got["progress"]


def test_engine_error_result_and_valueerror_are_failures(tmp_path):
    files = make_files(tmp_path / "in", ["a.pdf", "b.pdf"])

    def process(paths, *a, **k):
        if paths[0].name == "a.pdf":
            raise ValueError("쪽을 읽을 수 없음")
        return {**ok_doc(paths), "doc_type": None, "error": "문서 종류를 알 수 없음"}
    got = run_job(files, tmp_path / "out", process)
    assert got["failed"] == [("a", "쪽을 읽을 수 없음"), ("b", "문서 종류를 알 수 없음")]
    assert got["finished"] == (0, 2)


def test_json_saved_per_doc(tmp_path):
    files = make_files(tmp_path / "in", ["a.pdf", "b.pdf"])
    out = tmp_path / "out"
    seen = []

    def process(paths, *a, **k):
        seen.append(sorted(p.name for p in out.glob("*.json")) if out.exists() else [])
        return ok_doc(paths)
    run_job(files, out, process)
    assert seen == [[], ["a.json"]]
    assert json.loads((out / "a.json").read_text(encoding="utf-8"))["doc_type"] == "lease"


def test_cancel_stops_before_next(tmp_path):
    files = make_files(tmp_path / "in", ["a.pdf", "b.pdf", "c.pdf"])
    got = run_job(files, tmp_path / "out", lambda p, *a, **k: ok_doc(p), on_done=lambda job: job.cancel())
    assert len(got["done"]) == 1 and got["finished"] == (1, 0)


def test_korean_space_path_and_page_grouping(tmp_path):
    files = make_files(tmp_path / "바탕 화면", ["계약서 1.pdf", "대장_p2.png", "대장_p1.png"])
    calls = []

    def process(paths, *a, **k):
        calls.append([p.name for p in paths])
        return ok_doc(paths)
    out = tmp_path / "결과 폴더"
    run_job(files, out, process)
    assert calls == [["계약서 1.pdf"], ["대장_p1.png", "대장_p2.png"]]
    assert sorted(p.name for p in out.glob("*.json")) == ["계약서 1.json", "대장.json"]


def test_same_name_in_two_folders_does_not_overwrite(tmp_path):
    files = make_files(tmp_path / "가", ["계약서.pdf"]) + make_files(tmp_path / "나", ["계약서.pdf"])
    out = tmp_path / "out"
    run_job(files, out, lambda p, *a, **k: ok_doc(p))
    assert sorted(p.name for p in out.glob("*.json")) == ["계약서 (2).json", "계약서.json"]


def test_engine_loads_once(monkeypatch):
    import bridgedocs
    n = {"load": 0, "ocr": 0}
    monkeypatch.setattr(bridgedocs, "load", lambda name, dtype: (n.__setitem__("load", n["load"] + 1), ("m", "p"))[1])
    monkeypatch.setattr(bridgedocs, "make_ocr", lambda: (n.__setitem__("ocr", n["ocr"] + 1), "o")[1])
    e = Engine()
    assert e.get() == ("m", "p", "o") and e.get() == ("m", "p", "o")
    assert n == {"load": 1, "ocr": 1}


def test_engine_load_failure_fails_all_docs(tmp_path):
    files = make_files(tmp_path / "in", ["a.pdf", "b.pdf"])

    class Broken:
        def get(self):
            raise OSError("모델 파일 없음")
    job = ReadJob(files, tmp_path / "out", False, Broken(), process=lambda *a, **k: pytest.fail("불리면 안 됨"))
    got = []
    job.doc_failed.connect(lambda n, r: got.append((n, r)))
    fin = []
    job.finished.connect(lambda a, b: fin.append((a, b)))
    job.run()
    assert fin == [(0, 2)] and all("모델을 불러오지 못함" in r for _, r in got)


def test_save_failure_is_reported_and_finished_always_emits(tmp_path):
    files = make_files(tmp_path / "in", ["a.pdf", "b.pdf"])
    blocker = tmp_path / "결과"
    blocker.write_text("폴더 자리에 파일")      # 결과 폴더를 만들 수 없음
    got = run_job(files, blocker, lambda p, *a, **k: ok_doc(p))
    assert got["finished"] == (0, 2)
    assert all("저장할 수 없음" in r for _, r in got["failed"])


def test_cancel_before_start_skips_model_load(tmp_path):
    files = make_files(tmp_path / "in", ["a.pdf"])
    eng = FakeEngine()
    job = ReadJob(files, tmp_path / "out", False, eng, process=lambda *a, **k: pytest.fail("불리면 안 됨"))
    fin = []
    job.finished.connect(lambda a, b: fin.append((a, b)))
    job.cancel()
    job.run()
    assert fin == [(0, 0)] and eng.calls == 0


def test_done_doc_knows_where_its_json_is(tmp_path):
    files = make_files(tmp_path / "in", ["a.pdf"])
    got = run_job(files, tmp_path / "out", lambda p, *a, **k: ok_doc(p))
    assert Path(got["done"][0]["json_path"]) == tmp_path / "out" / "a.json"
