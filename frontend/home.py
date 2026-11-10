"""홈 화면: 소개 문구 · 파일 끌어놓기 칸 · 작업 고르기 카드 2개 · 3단계 안내 (피그마 메인페이지)."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

import theme


class DropZone(QFrame):
    """파일을 끌어다 놓거나 [파일 선택]으로 고르는 칸. 고른 파일은 drop(경로 목록)으로 넘긴다."""
    def __init__(self, drop, title: str, sub: str, button_text: str = "파일 선택", file_filter: str = ""):
        super().__init__()
        self.setObjectName("drop")
        self.setAttribute(Qt.WA_Hover)      # 마우스를 올리면 강조(theme.QSS #drop:hover)
        self.setAcceptDrops(True)
        self.drop, self.file_filter = drop, file_filter
        lay = QHBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        icon = theme.label("↑", "iconBox")
        icon.setFixedSize(44, 44)
        icon.setAlignment(Qt.AlignCenter)
        lay.addWidget(icon)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(theme.label(title, "sectionTitle"))
        col.addWidget(theme.label(sub, "muted", wrap=True))
        lay.addLayout(col, 1)
        if button_text:
            b = theme.button(button_text)
            b.clicked.connect(self._pick)
            lay.addWidget(b)

    def _pick(self) -> None:
        ps, _ = QFileDialog.getOpenFileNames(self, "파일 고르기", "", self.file_filter)
        if ps:
            self.drop([Path(p) for p in ps])

    def mousePressEvent(self, e):
        self._pick()

    def _hover(self, on: bool) -> None:
        self.setProperty("hover", on)
        theme.repolish(self)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._hover(True)

    def dragLeaveEvent(self, e):
        self._hover(False)

    def dropEvent(self, e):
        self._hover(False)
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.drop(paths)


def _task_card(icon: str, green: bool, eyebrow: str, title: str, body: str, chips: list[str], action: str, on_click) -> QFrame:
    f, lay = theme.card("taskCard")      # 마우스를 올리면 강조(theme.QSS)
    f.setAttribute(Qt.WA_Hover)
    f.setCursor(Qt.PointingHandCursor)
    f.mousePressEvent = lambda e: on_click()
    lay.setContentsMargins(28, 26, 28, 24)
    ic = theme.label(icon, "iconBoxGreen" if green else "iconBox")
    ic.setFixedSize(50, 50)
    ic.setAlignment(Qt.AlignCenter)
    lay.addWidget(ic)
    lay.addSpacing(8)
    lay.addWidget(theme.label(eyebrow, "eyebrowGreen" if green else "eyebrow"))
    lay.addWidget(theme.label(title, "cardTitle"))
    lay.addWidget(theme.label(body, "muted", wrap=True))
    row = QHBoxLayout()
    for c in chips:
        row.addWidget(theme.label(c, "chip"))
    row.addStretch(1)
    b = theme.button(action + "  →", "linkGreen" if green else "link")
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(on_click)
    row.addWidget(b)
    lay.addStretch(1)
    lay.addLayout(row)
    return f


class HomePage(QScrollArea):
    def __init__(self, open_page, drop):
        super().__init__()
        self.setWidgetResizable(True)
        inner = QWidget()
        self.setWidget(inner)
        outer = QHBoxLayout(inner)
        outer.addStretch(1)
        col = QVBoxLayout()
        col.setSpacing(14)
        col.setContentsMargins(0, 44, 0, 36)
        outer.addLayout(col, 8)
        outer.addStretch(1)

        pill = theme.label("✦  인터넷 없이 내 PC에서 하는 문서 자동화", "pill")
        col.addWidget(pill, 0, Qt.AlignHCenter)
        for text, name in (("복잡한 문서 작업을", "heroTitle"), ("한 곳에서, 더 쉽게.", "heroBlue")):
            col.addWidget(theme.label(text, name), 0, Qt.AlignHCenter)
        sub = theme.label("문서를 읽고 필요한 값을 확인하거나, 준비한 데이터로 새 문서를 만들어 보세요.\n"
                          "파일은 밖으로 보내지 않고 이 PC에서만 처리해요.", "pageSub")
        sub.setAlignment(Qt.AlignCenter)
        col.addWidget(sub)
        col.addSpacing(10)
        col.addWidget(DropZone(drop, "파일을 여기에 끌어다 놓으세요",
                               "이미지·PDF·워드는 문서 읽기로, CSV·TXT·XLSX는 문서 만들기로 자동 연결해요.\n"
                               "두 종류를 섞어 넣으면 '파일을 다시 골라 주세요' 안내 창이 떠요"))
        col.addSpacing(26)
        col.addWidget(theme.label("작업 선택", "eyebrow"), 0, Qt.AlignHCenter)
        q = QLabel("무엇을 도와드릴까요?")
        q.setStyleSheet("font-size: 22px; font-weight: 500;")
        col.addWidget(q, 0, Qt.AlignHCenter)

        grid = QGridLayout()
        grid.setSpacing(18)
        grid.addWidget(_task_card("▤", False, "DOCUMENT READER", "문서 읽기",
                                  "이미지나 PDF에서 필요한 정보를 읽고, 확인·수정한 뒤 원하는 형식으로 내보내세요.",
                                  ["PNG", "JPG", "PDF", "DOCX"], "문서 읽기 시작", lambda: open_page("문서 읽기")), 0, 0)
        grid.addWidget(_task_card("✎", True, "DOCUMENT BUILDER", "문서 만들기",
                                  "데이터 파일을 불러와 내용을 확인하고, 내가 넣은 워드 양식으로 문서를 만드세요.",
                                  ["CSV", "TXT", "XLSX"], "문서 만들기 시작", lambda: open_page("문서 만들기")), 0, 1)
        col.addLayout(grid)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.LINE};")
        col.addSpacing(8)
        col.addWidget(line)
        steps = QHBoxLayout()
        steps.addStretch(1)
        for i, (t, s) in enumerate((("파일 넣기", "끌어놓거나 직접 선택"), ("내용 확인 및 수정", "읽은 값을 바로 편집"),
                                    ("내보내기", "원하는 형식으로 저장")), 1):
            if i > 1:
                steps.addWidget(theme.label("›", "muted"))
            n = theme.label(f"0{i}", "stepNo")
            n.setFixedSize(30, 30)
            n.setAlignment(Qt.AlignCenter)
            steps.addWidget(n)
            c = QVBoxLayout()
            c.setSpacing(0)
            c.addWidget(theme.label(t, "sectionTitle"))
            c.addWidget(theme.label(s, "muted"))
            steps.addLayout(c)
            steps.addSpacing(18)
        steps.addStretch(1)
        col.addLayout(steps)
        col.addStretch(1)
