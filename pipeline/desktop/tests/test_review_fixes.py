"""최종 리뷰에서 나온 문제 4건의 회귀 테스트."""
import pytest
from PySide6.QtCore import QCoreApplication, QObject, Slot

import bridgedocs as bd
from test_queue import _files, _pump
from test_service_contract import FakeEngine, fake_process, svc  # noqa: F401


@pytest.mark.parametrize("ans, want", [("주택 임대차 계약서", "lease"), ("매매 계약서", "sale"),
                                       ("상가건물임대차계약서", "store_lease"), ("부동산등기부등본", "registry")])
def test_ai_type_with_spacing_differences_is_still_known(ans, want):
    assert bd.resolve_ai_type(ans) == (want, None)


def test_broken_custom_file_is_not_overwritten(tmp_path, monkeypatch):
    p = tmp_path / "새 문서 종류.json"
    monkeypatch.setenv("BD_CUSTOM", str(p))
    p.write_text('{"위임장": ["위임인"', encoding="utf-8")
    with pytest.raises(ValueError, match="깨져"):
        bd.save_custom_type("영수증", ["금액"])
    assert p.read_text(encoding="utf-8") == '{"위임장": ["위임인"'


def test_kv_txt_with_comma_in_first_value(tmp_path):
    p = tmp_path / "kv.txt"
    p.write_text("보증금: 100,000,000\n임대인: 홍길동\n임차인: 이영희\n", encoding="utf-8")
    assert bd.read_txt_table(p) == [["보증금", "임대인", "임차인"], ["100,000,000", "홍길동", "이영희"]]


def test_cp949_txt_and_csv_are_read(tmp_path):
    for name in ("a.txt", "a.csv"):
        p = tmp_path / name
        p.write_bytes("임대인,임차인\n김철수,이영희\n".encode("cp949"))
        assert bd.read_table(p) == [["임대인", "임차인"], ["김철수", "이영희"]]


def test_waiting_jobs_get_their_new_queue_position(svc, tmp_path):
    QCoreApplication.instance() or QCoreApplication([])
    a, b, c = _files(tmp_path, ["a.pdf", "b.pdf", "c.pdf"])
    jobs = [svc.start_read([f], tmp_path / "out") for f in (a, b, c)]
    class Screen(QObject):      # 화면처럼 메인 스레드의 객체가 받는다
        def __init__(self):
            super().__init__()
            self.seen = []

        @Slot(int)
        def on_queued(self, n):
            self.seen.append(n)
    screens = {i: Screen() for i in (1, 2)}
    for i in (1, 2):
        jobs[i].queued.connect(screens[i].on_queued)
    seen = {i: screens[i].seen for i in (1, 2)}
    done = []
    jobs[2].finished.connect(lambda *_: done.append(1))
    assert [j.queue_position for j in jobs] == [0, 1, 2]
    _pump(svc, lambda: done and len(seen[2]) == 2)
    assert seen[1] == [0] and seen[2] == [1, 0]
