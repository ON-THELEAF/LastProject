"""임시 문서 읽기 탭: 가짜 백엔드로 넣기 → 읽기 → 확인 → 내보내기 흐름."""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "frontend"), str(ROOT / "pipeline")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop.fake import FakeService  # noqa: E402
from read_tab import ReadTab  # noqa: E402

app = QApplication.instance() or QApplication([])


def test_read_review_and_export(tmp_path):
    svc = FakeService(delay=0)
    tab = ReadTab(svc)
    tab.out_dir = tmp_path / "결과"
    for n in ("a.pdf", "x_p1.png", "x_p2.png", "메모.zip"):
        (tmp_path / n).write_bytes(b"x")
    g = tab.add_files([tmp_path / n for n in ("a.pdf", "x_p1.png", "x_p2.png", "메모.zip")])
    assert len(g["groups"]) == 2 and len(g["rejected"]) == 1
    tab.start()
    end = time.time() + 20
    while "끝났어요" not in tab.status.text() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    svc.wait_all()
    assert tab.docs and tab.doc_list.count() == len(tab.docs)
    tab.doc_list.setCurrentRow(len(tab.docs) - 1)
    assert tab._name(tab.docs[-1]) in tab.doc_title.text()

    assert tab.export_to(tmp_path / "확정") is None and "남은 확인" in tab.message.toPlainText()
    for i in range(len(tab.docs)):
        tab.doc_list.setCurrentRow(i)
        tab.confirm_all()
    assert svc.review_left(tab.docs) == 0
    out = tab.export_to(tmp_path / "확정")
    assert [p.name for p in out] == ["확정.xlsx"]


def test_edit_cell_checks_format(tmp_path):
    svc = FakeService(delay=0)
    tab = ReadTab(svc)
    doc = {"source": ["a.pdf"], "doc_type": "lease", "fields": [
        {"key": "deposit_amount", "label": "보증금", "kind": "amount", "value": "1,000", "status": "review",
         "reason": "r", "candidates": [], "compare": "2,000", "page": 1, "line_box": None}]}
    tab.on_done(doc)
    tab.table.item(0, 1).setText("일억")
    assert "금액은 숫자로" in tab.message.toPlainText() and tab.docs[0]["fields"][0]["status"] == "review"
    tab.confirm(0, use_compare=True)
    f = tab.docs[0]["fields"][0]
    assert f["value"] == "2,000" and f["status"] == "confirmed"


def test_page_image_fits_whole_page():
    from PySide6.QtGui import QPixmap
    from read_tab import FitImage
    img = FitImage()
    img.show()
    pm = QPixmap(600, 1200)
    img.show_pixmap(pm)
    for w, h in ((300, 400), (900, 500)):
        img.resize(w, h)
        app.processEvents()
        got = img.pixmap()
        assert got.width() <= w and got.height() <= h and (got.width() == w or got.height() == h)
        assert abs(got.width() / got.height() - 0.5) < 0.02


def test_clicked_field_gets_box_on_its_page():
    from PySide6.QtGui import QPixmap
    from read_tab import RED, YELLOW, ReadTab

    class Svc(FakeService):
        def page_images(self, doc, width=900):
            import base64
            from PySide6.QtCore import QBuffer
            out = []
            for _ in range(2):
                pm = QPixmap(200, 300)
                pm.fill()
                buf = QBuffer()
                buf.open(QBuffer.WriteOnly)
                pm.save(buf, "PNG")
                out.append({"src": base64.b64encode(bytes(buf.data())).decode(), "w": 400, "h": 600})
            return out
    tab = ReadTab(Svc(delay=0))
    f = {"label": "칸", "kind": "text", "reason": "", "candidates": [], "compare": ""}
    tab.on_done({"source": ["a.pdf"], "doc_type": "lease", "fields": [
        {**f, "key": "a", "value": "x", "status": "auto", "page": 2, "line_box": [100, 100, 200, 140]},
        {**f, "key": "b", "value": "y", "status": "review", "page": 2, "line_box": [100, 300, 200, 340]},
        {**f, "key": "c", "value": "z", "status": "auto", "page": 1, "line_box": None}]})
    tab.table.setCurrentCell(0, 0)
    img = tab.page.source.toImage()            # 원래 폭 400 → 보이는 폭 200: 좌표 절반
    assert img.pixelColor(47, 60) == YELLOW     # 고른 칸(노랑, 2쪽)
    assert img.pixelColor(47, 160) == RED       # 같은 쪽 확인 필요 칸(빨강)
    tab.table.setCurrentCell(2, 0)
    assert "위치를 찾지 못한" in tab.message.toPlainText()
