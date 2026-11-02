"""BridgeDocs 프로그램 창. 실행: python frontend/app.py

탭: 문서 읽기(임시, read_tab.py) · 문서 만들기(make_tab.py).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

from desktop import offline  # noqa: E402

offline.ensure_offline()     # torch·paddle을 불러오기 전에

from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget  # noqa: E402

from desktop.service import DesktopService  # noqa: E402
from make_tab import MakeTab  # noqa: E402
from read_tab import ReadTab  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication.instance() or QApplication(argv)
    win = QMainWindow()
    win.setWindowTitle("BridgeDocs")
    tabs = QTabWidget()
    svc = DesktopService()
    tabs.addTab(ReadTab(svc), "문서 읽기")
    tabs.addTab(MakeTab(svc), "문서 만들기")
    win.setCentralWidget(tabs)
    win.resize(1000, 720)
    win.show()
    app.aboutToQuit.connect(svc.wait_all)     # 창을 닫으면 읽던 작업을 멈추고 기다린다
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
