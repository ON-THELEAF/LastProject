"""문서 관리 화면(피그마 디자인): 문서 만들기에서 쓸 워드 양식을 추가·삭제하고, 양식 내용과 빈칸을 미리 본다.

화면은 DesktopService만 부른다. 오류는 서비스가 주는 한국어 문구를 그대로 보여 준다.
shortcut: 양식 미리보기는 워드 문단 글자만 보여 준다(표·그림 모양은 없음). 모양까지 필요하면 convert_made처럼 Word로 그림을 만든다.
"""
import html
import re
import zipfile
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QListWidget, QListWidgetItem, QMessageBox, QTextBrowser,
                               QVBoxLayout, QWidget)

import theme

SLOT = re.compile(r"(\{\{[^{}]*\}\})")


def docx_paragraphs(path: Path) -> list[str]:
    """워드 문서 본문의 문단 글자들(표 안 문단 포함, 순서대로)."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    paras = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
    texts = ["".join(re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", p)) for p in paras]
    return [html.unescape(t) for t in texts if t.strip()]


def preview_html(paras: list[str]) -> str:
    """문단 목록 → 빈칸 {{…}}만 파랗게 칠한 HTML."""
    def para(t: str) -> str:
        return SLOT.sub(lambda m: f'<span style="color:{theme.BLUE}; background:{theme.BLUE_SOFT};">{m.group(1)}</span>',
                        html.escape(t))
    return "".join(f"<p>{para(t)}</p>" for t in paras)


class DocsTab(QWidget):
    def __init__(self, svc, on_change=lambda: None, parent=None):
        super().__init__(parent)
        self.svc, self.on_change = svc, on_change      # on_change: 양식이 바뀌면(문서 만들기 목록 새로 고침)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        self.count = theme.badge("0개", "info")
        self.title = theme.label("양식을 고르세요", "sectionTitle")
        self.preview = QTextBrowser()
        self.preview.setStyleSheet(f"background: {theme.CARD}; border: 1px solid {theme.LINE}; border-radius: 10px; padding: 24px;")
        self.info_name = theme.label("", "sectionTitle", wrap=True)
        self.info_sub = theme.label("", "muted")
        self.info_rows = {k: theme.label("", "") for k in ("상태", "최근 수정", "파일 형식")}
        self.slots = QListWidget()

        delete = theme.button("선택 양식 삭제", "danger")
        delete.clicked.connect(self.remove_selected)
        add = theme.button("+  문서 양식 추가", "primary")
        add.clicked.connect(self._pick)

        # 왼쪽: 양식 목록
        lcard, ll = theme.card()
        lhead = QHBoxLayout()
        lhead.addWidget(theme.label("문서 양식", "sectionTitle"), 1)
        lhead.addWidget(self.count)
        ll.addLayout(lhead)
        ll.addWidget(theme.label("📁  BRIDGEDOCS 양식", "eyebrow"))
        ll.addWidget(self.list, 1)
        ll.addWidget(theme.label("DOCX 파일의 {{빈칸}}을 자동으로 찾아요.", "muted", wrap=True))
        lcard.setFixedWidth(280)
        # 가운데: 양식 미리보기
        mcard, ml = theme.card()
        ml.addWidget(self.title)
        ml.addWidget(self.preview, 1)
        # 오른쪽: 양식 정보 · 인식된 빈칸
        rcard, rl = theme.card()
        rl.addWidget(theme.label("양식 정보", "sectionTitle"))
        box, bl = theme.card("guide")
        bl.addWidget(self.info_name)
        bl.addWidget(self.info_sub)
        rl.addWidget(box)
        for k, v in self.info_rows.items():
            row = QHBoxLayout()
            row.addWidget(theme.label(k, "muted"), 1)
            row.addWidget(v)
            rl.addLayout(row)
        rl.addWidget(theme.label("인식된 빈칸", "sectionTitle"))
        rl.addWidget(self.slots, 1)
        rcard.setFixedWidth(280)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(lcard)
        body.addWidget(mcard, 1)
        body.addWidget(rcard)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 18)
        lay.setSpacing(14)
        lay.addLayout(theme.page_head("문서 관리", "여기에 등록된 문서 양식만 문서 만들기에서 사용할 수 있어요.", delete, add))
        lay.addLayout(body, 1)
        self.refresh()

    def refresh(self, select: str | None = None) -> None:
        self.templates = self.svc.list_templates()
        self.list.blockSignals(True)
        self.list.clear()
        for t in self.templates:
            self.list.addItem(QListWidgetItem("▤   " + t["name"]))
        self.list.blockSignals(False)
        self.count.setText(f"{len(self.templates)}개")
        ids = [t["id"] for t in self.templates]
        self.list.setCurrentRow(ids.index(select) if select in ids else 0 if ids else -1)
        if not ids:
            self._show(-1)

    def selected(self) -> dict | None:
        i = self.list.currentRow()
        return self.templates[i] if 0 <= i < len(self.templates) else None

    def _show(self, i: int) -> None:
        t = self.templates[i] if 0 <= i < len(self.templates) else None
        self.slots.clear()
        if not t:
            self.title.setText("양식이 없어요 — [문서 양식 추가]로 워드 양식(.docx)을 넣으세요")
            self.preview.setHtml("")
            self.info_name.setText("")
            self.info_sub.setText("")
            for v in self.info_rows.values():
                v.setText("")
            return
        path = Path(t["path"])
        self.title.setText(f"문서 양식  ›  {t['name']}")
        self.info_name.setText(t["name"])
        try:
            slots = self.svc.template_slots(path)
            self.preview.setHtml(preview_html(docx_paragraphs(path)))
            ok = True
        except (ValueError, OSError, KeyError, zipfile.BadZipFile) as err:
            slots, ok = [], False
            self.preview.setPlainText(f"양식을 열 수 없어요: {err}")
        self.info_sub.setText(f"빈칸 {len(slots)}개")
        self.info_rows["상태"].setText("사용 가능" if ok and slots else "빈칸 없음" if ok else "열 수 없음")
        self.info_rows["상태"].setProperty("badge", "ok" if ok and slots else "warn")
        theme.repolish(self.info_rows["상태"])
        self.info_rows["최근 수정"].setText(datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d") if path.exists() else "—")
        self.info_rows["파일 형식"].setText("Word 문서 (.docx)")
        for n, s in enumerate(slots, 1):
            self.slots.addItem(f"{n}    {s or '(이름 없는 빈칸)'}")

    def add(self, path: Path) -> None:
        try:
            t = self.svc.add_template(path)
        except ValueError as err:
            return QMessageBox.warning(self, "문서 양식 추가", str(err))
        self.refresh(t["id"])
        self.on_change()

    def remove_selected(self, ask: bool = True) -> None:
        t = self.selected()
        if not t:
            return
        if ask:
            box = QMessageBox(self)
            box.setWindowTitle("문서 양식 삭제")
            box.setText(f"‘{t['name']}’ 양식을 삭제할까요?")
            box.setInformativeText("삭제하면 문서 만들기에서 더 이상 선택할 수 없어요.")
            box.addButton("아니요", QMessageBox.RejectRole)
            yes = box.addButton("삭제", QMessageBox.DestructiveRole)
            box.exec()
            if box.clickedButton() is not yes:
                return
        try:
            self.svc.remove_template(t["id"])
        except ValueError as err:
            return QMessageBox.warning(self, "문서 양식 삭제", str(err))
        self.refresh()
        self.on_change()

    def _pick(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "문서 양식 추가", "", "워드 양식 (*.docx)")
        if p:
            self.add(Path(p))
