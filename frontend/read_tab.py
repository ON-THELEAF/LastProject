"""문서 읽기 화면(피그마 디자인): 파일 넣기 → 묶음 확인 → 읽기(대기열·취소) → 문서별 확인 → 내보내기.

화면은 DesktopService만 부른다. 오류는 서비스가 주는 한국어 문구를 그대로 보여 준다.
shortcut: 확대·축소·쪽 넘기기 버튼은 없다(칸을 누르면 그 쪽으로 감).
칸을 누르면 쪽 이미지 위에 그 칸 자리를 상자로 표시한다: 확인 필요 = 빨강, 그 밖 = 노랑.
"""
import base64
from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QMessageBox, QPlainTextEdit,
                               QPushButton, QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

import theme
from home import DropZone

COLS = ["칸", "값", "상태", "비교 값", "사유"]
DEFAULT_OUT = Path.home() / "Documents" / "BridgeDocs 읽기 결과"
RED, YELLOW = QColor(220, 30, 30), QColor(255, 190, 0)


def draw_boxes(pm: QPixmap, scale: float, boxes: list[tuple[list, QColor, int]]) -> QPixmap:
    """쪽 이미지 사본에 상자를 그린다. 상자 좌표는 원래 크기 기준이라 scale(보이는 폭 / 원래 폭)을 곱한다."""
    out = pm.copy()
    p = QPainter(out)
    for (x0, y0, x1, y1), color, width in boxes:
        p.setPen(QPen(color, width))
        p.drawRect(round(x0 * scale) - 3, round(y0 * scale) - 3, round((x1 - x0) * scale) + 6, round((y1 - y0) * scale) + 6)
    p.end()
    return out


class FitImage(QLabel):
    """쪽 이미지를 칸 크기에 맞춰 한 쪽이 통째로 보이게(비율 유지). 칸 크기가 바뀌면 다시 맞춘다."""
    def __init__(self):
        super().__init__("쪽 이미지")
        self.source = QPixmap()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(1, 1)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

    def show_pixmap(self, pm: QPixmap) -> None:
        self.source = pm
        self._fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        if not self.source.isNull():
            self.setPixmap(self.source.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))


class ReadTab(QWidget):
    def __init__(self, svc, parent=None):
        super().__init__(parent)
        self.svc, self.files, self.docs, self.jobs = svc, [], [], []
        self.out_dir = DEFAULT_OUT
        self.labels = svc.labels()

        self.status = theme.label("읽을 파일을 넣고 [읽기 시작]을 누르세요", "sectionTitle")
        self.out_label = theme.label(f"결과(JSON) 저장 폴더: {self.out_dir}", "muted")
        self.doc_list = QListWidget()
        self.doc_list.currentRowChanged.connect(self._show_doc)
        self.page = FitImage()
        self.page.setStyleSheet(f"background: #EEF1F6; border-radius: 10px; color: {theme.MUTED};")
        self.doc_title = theme.label("왼쪽 목록에서 문서를 고르세요", "sectionTitle", wrap=True)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._edited)
        self.table.currentCellChanged.connect(lambda r, *_: self.mark(r))
        self._images = {}       # 문서 파일들 → [(쪽 QPixmap, 원래 폭)]
        self.kinds = {}
        for k in ("xlsx", "csv", "txt"):     # 내보낼 형식(여러 개 고를 수 있음)
            self.kinds[k] = QPushButton(k.upper())
            self.kinds[k].setCheckable(True)
            self.kinds[k].setProperty("seg", True)
        self.kinds["xlsx"].setChecked(True)
        self.message = QPlainTextEdit(readOnly=True)
        self.message.setMaximumHeight(70)
        self.message.setPlaceholderText("진행 상황과 안내가 여기에 나와요")

        b = {n: theme.button(n) for n in ("저장 폴더", "취소", "이 칸 확정", "비교 값으로", "JSON 위치")}
        b.update({n: theme.button(n, "primary") for n in ("+  파일 추가", "읽기 시작", "한 번에 확정", "↓  내보내기")})
        b["+  파일 추가"].clicked.connect(self._pick_files)
        b["저장 폴더"].clicked.connect(self._pick_out)
        b["읽기 시작"].clicked.connect(self.start)
        b["취소"].clicked.connect(self.cancel)
        b["이 칸 확정"].clicked.connect(lambda: self.confirm(self.table.currentRow()))
        b["비교 값으로"].clicked.connect(lambda: self.confirm(self.table.currentRow(), use_compare=True))
        b["한 번에 확정"].clicked.connect(self.confirm_all)
        b["↓  내보내기"].clicked.connect(self._export_dialog)
        b["JSON 위치"].clicked.connect(lambda: self._say("JSON은 문서를 읽을 때 저장돼요: " +
                                                       (", ".join(self.svc.json_folders(self.docs)) or str(self.out_dir))))

        # 진행 상황 띠
        banner, bl = theme.card("banner")
        brow = QHBoxLayout()
        icon = theme.label("✓", "iconBox")
        icon.setFixedSize(42, 42)
        icon.setAlignment(Qt.AlignCenter)
        brow.addWidget(icon)
        bcol = QVBoxLayout()
        bcol.setSpacing(2)
        bcol.addWidget(self.status)
        bcol.addWidget(self.out_label)
        brow.addLayout(bcol, 1)
        for n in ("저장 폴더", "취소", "읽기 시작"):
            brow.addWidget(b[n])
        bl.addLayout(brow)
        bl.addWidget(self.message)

        # 세 칸: 문서 목록 · 쪽 이미지 · 읽은 값
        lcard, ll = theme.card()
        ll.addWidget(theme.label("문서 목록", "sectionTitle"))
        ll.addWidget(theme.label("읽은 문서를 누르면 확인할 수 있어요", "muted"))
        ll.addWidget(self.doc_list, 1)
        ll.addWidget(DropZone(self.add_files, "파일을 추가로 놓아주세요", "PDF · PNG · JPG · DOCX", button_text=""))
        mcard, ml = theme.card()
        ml.addWidget(self.doc_title)
        ml.addWidget(self.page, 1)
        rcard, rl = theme.card()
        head = QHBoxLayout()
        hc = QVBoxLayout()
        hc.setSpacing(2)
        hc.addWidget(theme.label("인식된 정보", "sectionTitle"))
        hc.addWidget(theme.label("값 칸을 두 번 눌러 고칠 수 있어요 · 칸을 누르면 문서에 위치가 보여요", "muted", wrap=True))
        head.addLayout(hc, 1)
        rl.addLayout(head)
        rl.addWidget(self.table, 1)
        row = QHBoxLayout()
        for n in ("이 칸 확정", "비교 값으로", "한 번에 확정"):
            row.addWidget(b[n], 1)
        rl.addLayout(row)
        split = QSplitter()
        split.setHandleWidth(14)
        for w in (lcard, mcard, rcard):
            split.addWidget(w)
        split.setSizes([260, 460, 520])

        # 내보내기 띠
        ecard, el = theme.card()
        erow = QHBoxLayout()
        eicon = theme.label("↓", "iconBoxGreen")
        eicon.setFixedSize(42, 42)
        eicon.setAlignment(Qt.AlignCenter)
        erow.addWidget(eicon)
        ecol = QVBoxLayout()
        ecol.setSpacing(2)
        ecol.addWidget(theme.label("확인이 끝났나요?", "sectionTitle"))
        ecol.addWidget(theme.label("원하는 파일 형식을 고르고 결과를 저장하세요.", "muted"))
        erow.addLayout(ecol, 1)
        seg = QFrame()
        seg.setObjectName("segBox")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(4, 4, 4, 4)
        sl.setSpacing(2)
        for c in self.kinds.values():
            sl.addWidget(c)
        erow.addWidget(seg)
        erow.addWidget(b["JSON 위치"])
        erow.addWidget(b["↓  내보내기"])
        el.addLayout(erow)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 18)
        lay.setSpacing(14)
        lay.addLayout(theme.page_head("문서 읽기", "문서 읽기", "읽은 값을 확인하고 필요한 부분을 고쳐 주세요.", b["+  파일 추가"]))
        lay.addWidget(banner)
        lay.addWidget(split, 1)
        lay.addWidget(ecard)

    # ── 파일 넣기·읽기 ──
    def add_files(self, paths: list[Path]) -> dict:
        """묶음을 보여 주고 확인받는다(읽기 시작을 눌러야 읽음)."""
        self.files = [Path(p) for p in paths]
        g = self.svc.preview_groups(self.files)
        lines = [f"· {x['name']}: {', '.join(Path(f).name for f in x['files'])}" for x in g["groups"]]
        lines += [f"✕ {Path(x['file']).name}: {x['reason']}" for x in g["rejected"]]
        self._say(f"문서 {len(g['groups'])}건으로 묶었어요. 맞으면 [읽기 시작]을 누르세요.\n" + "\n".join(lines))
        return g

    def start(self) -> None:
        if not self.files:
            return self._say("파일을 먼저 넣으세요")
        try:
            job = self.svc.start_read(self.files, self.out_dir)
        except ValueError as err:
            return self._say(str(err))
        self.files = []
        self.jobs.append(job)
        job.progress.connect(self.on_progress)
        job.queued.connect(self.on_queued)
        job.doc_done.connect(self.on_done)
        job.doc_failed.connect(self.on_failed)
        job.finished.connect(self.on_finished)
        if job.queue_position:
            self.on_queued(job.queue_position)

    def cancel(self) -> None:
        for j in self.jobs:
            j.cancel()
        self.status.setText("취소했어요")

    @Slot(str)
    def on_progress(self, text):
        self.status.setText(text)

    @Slot(int)
    def on_queued(self, n):
        self.status.setText(f"대기 중 — 앞에 작업 {n}개" if n else "곧 시작해요")

    @Slot(dict)
    def on_done(self, doc):
        self.docs.append(doc)
        s = doc.get("summary", {})
        self.doc_list.addItem(self._doc_title(doc))
        self._say(f"{self._name(doc)}: 자동 {s.get('auto', 0)}칸, 확인 필요 {s.get('review', 0)}칸")
        if self.doc_list.currentRow() < 0:
            self.doc_list.setCurrentRow(0)

    @Slot(str, str)
    def on_failed(self, name, reason):
        self.message.appendPlainText(f"실패 — {name}: {reason}")

    @Slot(int, int)
    def on_finished(self, ok, bad):
        self.status.setText(f"끝났어요: 성공 {ok}건, 실패 {bad}건")

    # ── 확인 ──
    def current(self) -> int:
        return self.doc_list.currentRow()

    def _show_doc(self, i: int) -> None:
        if i < 0 or i >= len(self.docs):
            return
        doc = self.docs[i]
        left = self.svc.review_left([doc])
        self.doc_title.setText(f"선택한 문서: {self._name(doc)} · {self._kind(doc)} · "
                               + (f"확인할 칸 {left}개" if left else "확인 끝"))
        row = self.table.currentRow()
        self.table.blockSignals(True)
        self.table.setRowCount(len(doc["fields"]))
        for r, f in enumerate(doc["fields"]):
            cells = [f["label"], str(f["value"] or ""), self.labels["status"].get(f["status"], f["status"]),
                     str(f.get("compare") or ""), f.get("reason") or ""]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c != 1:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if f["status"] == "review":
                    item.setBackground(QColor(theme.WARN_SOFT))
                self.table.setItem(r, c, item)
        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        if 0 <= row < len(doc["fields"]):
            self.table.setCurrentCell(row, 1)
        self.mark(row if 0 <= row < len(doc["fields"]) else -1)
        if error := doc.get("error"):
            self._say(error)

    def _pages(self, doc: dict) -> list[tuple[QPixmap, int]]:
        key = tuple(doc.get("files") or doc.get("source") or [])
        if key not in self._images:
            pages = []
            for img in self.svc.page_images(doc):
                pm = QPixmap()
                pm.loadFromData(base64.b64decode(img["src"]))
                pages.append((pm, img["w"]))
            self._images[key] = pages
        return self._images[key]

    def mark(self, r: int) -> None:
        """r번째 칸이 있는 쪽을 보이고 그 칸에 상자를 그린다. 그 쪽의 확인 필요 칸은 늘 빨간 상자."""
        i = self.current()
        if i < 0 or i >= len(self.docs):
            return
        fields = self.docs[i]["fields"]
        pages = self._pages(self.docs[i])
        if not pages:
            self.page.show_pixmap(QPixmap())
            return self.page.setText("쪽 이미지 없음")
        sel = fields[r] if 0 <= r < len(fields) else None
        n = min(max((sel or {}).get("page") or 1, 1), len(pages))
        pm, w = pages[n - 1]
        boxes = [(f["line_box"], RED, 3) for f in fields
                 if f["status"] == "review" and f.get("line_box") and (f.get("page") or 1) == n and f is not sel]
        if sel and sel.get("line_box"):
            boxes.append((sel["line_box"], RED if sel["status"] == "review" else YELLOW, 6))
        elif sel:
            self._say(f"{sel['label']}: 문서에서 위치를 찾지 못한 칸이에요(상자 없음)")
        self.page.show_pixmap(draw_boxes(pm, pm.width() / w, boxes))

    def _set_field(self, r: int, value: str, status: str) -> None:
        i = self.current()
        doc = self.docs[i]
        fields = list(doc["fields"])
        fields[r] = self.svc.decide(fields[r], value, status)
        self._replace(i, {**doc, "fields": fields})

    def _replace(self, i: int, doc: dict) -> None:
        self.docs[i] = doc
        self.doc_list.item(i).setText(self._doc_title(doc))
        left = self.svc.review_left([doc])
        if left == 0 and i + 1 < len(self.docs):      # 다 확인하면 다음 문서로
            self.doc_list.setCurrentRow(i + 1)
        else:
            self._show_doc(i)

    def confirm(self, r: int, use_compare: bool = False) -> None:
        i = self.current()
        if i < 0 or r < 0:
            return self._say("칸을 고르세요")
        f = self.docs[i]["fields"][r]
        if use_compare and not f.get("compare"):
            return self._say("비교 값이 없는 칸이에요")
        self._set_field(r, f["compare"] if use_compare else f["value"], "confirmed")

    def _edited(self, item: QTableWidgetItem) -> None:
        if item.column() != 1:
            return
        f = self.docs[self.current()]["fields"][item.row()]
        value, err = self.svc.parse_value(f["kind"], item.text())
        if err:
            self._say(f"{f['label']}: {err}")
            return self._show_doc(self.current())
        self._set_field(item.row(), value, "edited")

    def confirm_all(self) -> None:
        i = self.current()
        if i >= 0:
            self._replace(i, self.svc.decide_all(self.docs[i]))

    # ── 내보내기 ──
    def export_to(self, path_stem: Path, append: bool = False) -> list[Path] | None:
        kinds = [k for k, c in self.kinds.items() if c.isChecked()]
        try:
            out = self.svc.export(self.docs, kinds, Path(path_stem), append=append)
        except ValueError as err:
            return self._say(str(err))
        self._say("저장했어요:\n" + "\n".join(map(str, out)))
        return out

    def _export_dialog(self) -> None:
        box = QMessageBox(self)
        box.setText("어떻게 저장할까요?")
        new = box.addButton("새 파일로", QMessageBox.AcceptRole)
        add = box.addButton("기존 파일에 이어 붙이기", QMessageBox.ActionRole)
        box.addButton("그만두기", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is new:
            p, _ = QFileDialog.getSaveFileName(self, "새 파일 이름", str(self.out_dir / "확정"))
        elif box.clickedButton() is add:
            p, _ = QFileDialog.getOpenFileName(self, "이어 붙일 파일", str(self.out_dir), "내보낸 파일 (*.xlsx *.csv *.txt)")
        else:
            return
        if p:
            self.export_to(Path(p), append=box.clickedButton() is add)

    # ── 기타 ──
    def _pick_files(self) -> None:
        exts = " ".join("*" + s for s in self.svc.supported_suffixes())
        ps, _ = QFileDialog.getOpenFileNames(self, "읽을 문서", "", f"문서 ({exts})")
        if ps:
            self.add_files([Path(p) for p in ps])

    def _pick_out(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "결과(JSON) 저장 폴더", str(self.out_dir))
        if p:
            self.out_dir = Path(p)
            self.out_label.setText(f"결과(JSON) 저장 폴더: {p}")

    def _name(self, doc: dict) -> str:
        return ", ".join(doc.get("source") or []) or "문서"

    def _kind(self, doc: dict) -> str:
        return doc.get("doc_name") or self.labels["doc_type"].get(doc.get("doc_type"), doc.get("doc_type") or "알 수 없음")

    def _doc_title(self, doc: dict) -> str:
        left = self.svc.review_left([doc])
        return f"{'⚠ ' if left else '✓ '}{self._name(doc)} ({self._kind(doc)})"

    def _say(self, text: str) -> None:
        self.message.setPlainText(text)
