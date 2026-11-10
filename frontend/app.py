"""BridgeDocs 프로그램 창. 실행: python frontend/app.py

위쪽 메뉴: 홈(home.py) · 문서 읽기(read_tab.py) · 문서 만들기(make_tab.py) · 문서 관리(docs_tab.py). 모양은 theme.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

from desktop import offline  # noqa: E402

offline.ensure_offline()     # torch·paddle을 불러오기 전에

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow,  # noqa: E402
                               QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

import theme  # noqa: E402
from desktop.service import DesktopService  # noqa: E402
from docs_tab import DocsTab  # noqa: E402
from home import HomePage  # noqa: E402
from make_tab import MakeTab  # noqa: E402
from read_tab import ReadTab  # noqa: E402

MAKE_SUFFIXES = {".csv", ".txt", ".xlsx", ".json"}


class MainWindow(QMainWindow):
    def __init__(self, svc):
        super().__init__()
        self.setWindowTitle("BridgeDocs")
        self.read, self.make = ReadTab(svc), MakeTab(svc, open_page=self.open)
        self.docs = DocsTab(svc, on_change=self.make.refresh_templates)
        self.home = HomePage(open_page=self.open, drop=self.route_files)
        self.stack = QStackedWidget()
        self.pages = {"홈": self.home, "문서 읽기": self.read, "문서 만들기": self.make, "문서 관리": self.docs}
        for w in self.pages.values():
            self.stack.addWidget(w)

        header = QFrame()
        header.setObjectName("header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(24, 0, 24, 0)
        mark = QLabel("B")
        mark.setObjectName("logoMark")
        mark.setFixedSize(34, 34)
        mark.setAlignment(Qt.AlignCenter)
        hl.addWidget(mark)
        hl.addWidget(theme.label("BridgeDocs", "logo"))
        hl.addSpacing(36)
        self.nav = QButtonGroup(self)
        for name in self.pages:
            b = QPushButton(name)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, n=name: self.open(n))
            self.nav.addButton(b)
            hl.addWidget(b)
        hl.addStretch(1)
        hl.addWidget(theme.label(f'<span style="color:{theme.FAIL}">●</span>&nbsp; 오프라인', "online"))

        footer = QFrame()
        footer.setObjectName("footer")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(24, 10, 24, 10)
        fl.addWidget(theme.label("BridgeDocs", "muted"))
        fl.addStretch(1)
        fl.addWidget(theme.label("인터넷 연결 없이 안전하게 사용할 수 있어요", "muted"))

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(header)
        lay.addWidget(self.stack, 1)
        lay.addWidget(footer)
        self.setCentralWidget(body)
        self.open("홈")

    def open(self, name: str) -> None:
        self.stack.setCurrentWidget(self.pages[name])
        for b in self.nav.buttons():
            b.setChecked(b.text() == name)

    def route_files(self, paths: list[Path]) -> None:
        """홈에 놓은 파일: 데이터(CSV·TXT·XLSX·JSON)는 문서 만들기로, 나머지(이미지·PDF·docx)는 문서 읽기로."""
        data = [p for p in paths if p.suffix.lower() in MAKE_SUFFIXES]
        docs = [p for p in paths if p not in data]
        if data and docs:
            QMessageBox.information(self, "파일을 다시 골라 주세요",
                                    "읽을 문서(PDF·이미지·워드)와 데이터 파일(CSV·TXT·XLSX·JSON)이 섞여 있어요.\n한 종류만 골라 다시 넣어 주세요.")
            return
        if docs:
            self.read.add_files(docs)
            self.open("문서 읽기")
        if data:
            self.open("문서 만들기")
            self.make.set_data(data[0])


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication.instance() or QApplication(argv)
    theme.apply(app)
    svc = DesktopService()
    win = MainWindow(svc)
    win.resize(1280, 820)
    win.show()
    app.aboutToQuit.connect(svc.wait_all)     # 창을 닫으면 읽던 작업을 멈추고 기다린다
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
