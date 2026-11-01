"""과제 A: 대기열, 묶기 미리보기, 지원하지 않는 파일만 돌려보내기, 즉시 취소."""
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

import bridgedocs as bd
from desktop.worker import ReadJob
from test_service_contract import FakeEngine, fake_process, svc  # noqa: F401  (fixture 재사용)


def _files(folder, names):
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        (folder / n).write_bytes(b"x")
    return [folder / n for n in names]


def _collect(job, got, tag):
    job.doc_done.connect(lambda d: got.append((tag, "done")))
    job.doc_failed.connect(lambda n, r: got.append((tag, "failed", n, r)))
    job.finished.connect(lambda a, b: got.append((tag, "finished", a, b)))


def _pump(svc, cond, sec=30):
    app = QCoreApplication.instance() or QCoreApplication([])
    end = time.time() + sec
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    svc.wait_all()


def test_second_read_while_busy_is_queued(svc, tmp_path):
    QCoreApplication.instance() or QCoreApplication([])
    a, b = _files(tmp_path, ["a.pdf", "b.pdf"])
    got = []
    j1 = svc.start_read([a], tmp_path / "out")
    j2 = svc.start_read([b], tmp_path / "out")
    assert (j1.queue_position, j2.queue_position) == (0, 1)
    _collect(j1, got, 1)
    _collect(j2, got, 2)
    _pump(svc, lambda: sum(g[1] == "finished" for g in got) == 2)
    fin = [g[0] for g in got if g[1] == "finished"]
    assert fin == [1, 2]                       # 두 번째는 첫 번째가 끝난 뒤에 돈다


def test_preview_groups_shows_bundles_and_rejected(svc, tmp_path):
    files = _files(tmp_path / "바탕 화면", ["대장_p2.png", "대장_p1.png", "계약서 1.pdf", "메모.txt"])
    pv = svc.preview_groups(files + [tmp_path / "없는.pdf"])
    assert [(g["name"], [Path(f).name for f in g["files"]]) for g in pv["groups"]] == \
        [("대장", ["대장_p1.png", "대장_p2.png"]), ("계약서 1", ["계약서 1.pdf"])]
    assert {Path(r["file"]).name for r in pv["rejected"]} == {"메모.txt", "없는.pdf"}
    assert all(r["reason"] for r in pv["rejected"])


def test_unsupported_file_is_returned_others_continue(svc, tmp_path):
    QCoreApplication.instance() or QCoreApplication([])
    files = _files(tmp_path, ["a.pdf", "메모.txt"])
    got = []
    job = svc.start_read(files, tmp_path / "out")
    _collect(job, got, 1)
    _pump(svc, lambda: any(g[1] == "finished" for g in got))
    failed = [g for g in got if g[1] == "failed"]
    assert any(g[2] == "메모.txt" and "지원하지 않는 파일" in g[3] for g in failed)
    assert any(g[1] == "done" for g in got)


def test_all_unsupported_is_still_refused(svc, tmp_path):
    (tmp_path / "메모.txt").write_text("x")
    with pytest.raises(ValueError, match="지원하지 않는"):
        svc.start_read([tmp_path / "메모.txt"], tmp_path / "out")


def test_cancel_stops_current_document_immediately(tmp_path):
    files = _files(tmp_path / "in", ["a.pdf", "b.pdf"])
    holder = {}

    def process(paths, *a, stop=None, **k):
        holder["job"].cancel()
        if stop and stop():
            raise bd.Cancelled()
        pytest.fail("취소를 확인하지 않음")
    job = ReadJob(files, tmp_path / "out", False, FakeEngine(), process=process)
    holder["job"] = job
    got = []
    _collect(job, got, 1)
    job.run()
    assert got == [(1, "finished", 0, 0)]                    # 성공·실패 신호 없음
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*.json"))
