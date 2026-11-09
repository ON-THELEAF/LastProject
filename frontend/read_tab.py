"""문서 읽기 화면(피그마 디자인): 파일 넣기 → 묶음 확인 → 읽기(대기열·취소) → 문서별 확인 → 내보내기.

화면은 DesktopService만 부른다. 오류는 서비스가 주는 한국어 문구를 그대로 보여 준다.
shortcut: 확대·축소·쪽 넘기기 버튼은 없다(칸을 누르면 그 쪽으로 감).
칸을 누르면 쪽 이미지 위에 그 칸 자리를 상자로 표시한다: 확인 필요 = 빨강, 그 밖 = 노랑.
"""
import base64
from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout,
                               QInputDialog, QLabel, QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
                               QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

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
        self.out_dir = svc.default_out_dir() if hasattr(svc, "default_out_dir") else DEFAULT_OUT   # 설치할 때 정한 저장 폴더
        self.labels = svc.labels()
        self.want: list[str] = []      # 사용자가 고른 칸 이름(비면 AI가 정함)
        self.group = True              # 쪽 번호 이미지를 한 문서로 묶을지
        self.broken: set[int] = set()  # 쪽 이미지를 못 불러온 문서 번호
        self._shown = -1               # 지금 보이는 문서(다른 문서로 갈 때 자동 저장)

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

        self.sep = QComboBox()       # TXT 구분 기호
        self.sep.setAccessibleName("TXT 구분 기호")
        for name in ("탭", "쉼표", "세미콜론", "세로줄"):
            self.sep.addItem(f"TXT: {name}", {"탭": "\t", "쉼표": ",", "세미콜론": ";", "세로줄": "|"}[name])
        b = {n: theme.button(n) for n in ("저장 폴더", "취소", "이 칸 확정", "비교 값으로", "되돌리기", "JSON 위치",
                                          "읽을 칸 고르기", "목록 숨기기", "새 종류로 확정", "문서 종류 관리")}
        b["읽을 칸 고르기"].clicked.connect(self._pick_fields)
        b["되돌리기"].clicked.connect(lambda: self.undo(self.table.currentRow()))
        b["새 종류로 확정"].clicked.connect(self._save_type)
        b["문서 종류 관리"].clicked.connect(self._manage_types)
        self.toggle_list = b["목록 숨기기"]
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
        for n in ("읽을 칸 고르기", "문서 종류 관리", "저장 폴더", "취소", "읽기 시작"):
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
        for n in ("이 칸 확정", "비교 값으로", "되돌리기", "한 번에 확정"):
            row.addWidget(b[n], 1)
        rl.addLayout(row)
        self.type_row = QWidget()      # 처음 보는 문서 종류일 때만 보임
        tr = QHBoxLayout(self.type_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.addWidget(theme.label("처음 보는 문서 종류예요. 칸 이름을 확정하면 다음부터 그 칸들로 읽어요.", "muted", wrap=True), 1)
        tr.addWidget(b["새 종류로 확정"])
        self.type_row.hide()
        rl.addWidget(self.type_row)
        ml.insertWidget(0, b["목록 숨기기"], 0, Qt.AlignLeft)
        self.lcard = lcard
        b["목록 숨기기"].clicked.connect(self._toggle_list)
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
        erow.addWidget(self.sep)
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
    def add_files(self, paths: list[Path], ask: bool = True) -> dict:
        """묶음을 보여 주고 확인받는다(읽기 시작을 눌러야 읽음). 묶음을 거절하면 이미지를 따로따로 읽는다."""
        self.files = [Path(p) for p in paths]
        g = self.svc.preview_groups(self.files)
        self.group = True
        if ask and any(len(x["files"]) > 1 for x in g["groups"]):
            listing = "\n".join(f"· {x['name']}: {', '.join(Path(f).name for f in x['files'])}" for x in g["groups"] if len(x["files"]) > 1)
            box = QMessageBox(self)
            box.setWindowTitle("쪽 묶음 확인")
            box.setText("쪽 번호가 붙은 이미지를 한 문서로 묶었어요. 이대로 읽을까요?\n\n" + listing)
            keep = box.addButton("묶어서 읽기", QMessageBox.AcceptRole)
            box.addButton("따로따로 읽기", QMessageBox.RejectRole)
            box.exec()
            self.group = box.clickedButton() is keep
        n = len(g["groups"]) if self.group else sum(len(x["files"]) for x in g["groups"])
        lines = [f"· {x['name']}: {', '.join(Path(f).name for f in x['files'])}" for x in g["groups"]] if self.group else []
        lines += [f"✕ {Path(x['file']).name}: {x['reason']}" for x in g["rejected"]]
        self._say(f"문서 {n}건을 읽을게요. 맞으면 [읽기 시작]을 누르세요.\n" + "\n".join(lines))
        return g

    def _pick_fields(self) -> None:
        text, ok = QInputDialog.getMultiLineText(self, "읽을 칸 고르기",
                                                 "읽을 칸 이름을 한 줄에 하나씩 적으세요.\n비워 두면 AI가 문서에 맞는 칸을 정해요.",
                                                 "\n".join(self.want))
        if ok:
            self.want = [x.strip() for x in text.splitlines() if x.strip()]
            self._say("이 칸들만 읽어요: " + ", ".join(self.want) if self.want else "AI가 칸을 정해서 읽어요")

    def start(self) -> None:
        if not self.files:
            return self._say("파일을 먼저 넣으세요")
        try:
            job = self.svc.start_read(self.files, self.out_dir, want=self.want, group=self.group)
        except ValueError as err:
            return self._say(str(err))
        self.files = []
        self.jobs.append(job)
        job.progress.connect(self.on_progress)
        job.queued.connect(self.on_queued)
        job.doc_done.connect(self.on_done)
        job.doc_failed.connect(self.on_failed)
        job.finished.connect(self.on_finished)
        job.finished.connect(lambda *_, j=job: self.jobs.remove(j) if j in self.jobs else None)
        if job.queue_position:
            self.on_queued(job.queue_position)

    def cancel(self) -> None:
        """지금 읽는 문서는 바로 멈춘다. 대기 중인 작업이 있으면 그것까지 취소할지 묻는다."""
        if not self.jobs:
            return self._say("읽는 중인 작업이 없어요")
        waiting = [j for j in self.jobs if j.queue_position > 0]
        targets = list(self.jobs)
        if waiting:
            box = QMessageBox(self)
            box.setWindowTitle("취소")
            box.setText(f"대기 중인 작업이 {len(waiting)}개 있어요. 무엇을 취소할까요?")
            now = box.addButton("지금 읽는 것만", QMessageBox.AcceptRole)
            every = box.addButton("대기 중인 것까지 모두", QMessageBox.DestructiveRole)
            box.addButton("그만두기", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is now:
                targets = [j for j in self.jobs if j not in waiting]
            elif box.clickedButton() is not every:
                return
        for j in targets:
            j.cancel()
        self.status.setText("취소했어요" + (" — 대기 중인 작업은 계속해요" if len(targets) < len(self.jobs) else ""))

    @Slot(str)
    def on_progress(self, text):
        self.status.setText(text)

    @Slot(int)
    def on_queued(self, n):
        self.status.setText(f"대기 중 — 앞에 작업 {n}개 (대기 순번 {n})" if n else "곧 시작해요")

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

    def _autosave(self) -> None:
        """보던 문서의 확정·수정 내용을 JSON에 저장(다른 문서로 넘어갈 때)."""
        if 0 <= self._shown < len(self.docs):
            try:
                self.svc.save_doc(self.docs[self._shown])
            except ValueError as err:
                self._say(str(err))

    def _show_doc(self, i: int) -> None:
        if i < 0 or i >= len(self.docs):
            return
        if i != self._shown:
            self._autosave()
            self._shown = i
        doc = self.docs[i]
        left = self.svc.review_left([doc])
        self.doc_title.setText(f"선택한 문서: {self._name(doc)} · {self._kind(doc)} · "
                               + (f"확인할 칸 {left}개" if left else "확인 필요 항목 없음"))
        self.type_row.setVisible(bool(doc.get("new_type")))
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
        elif not left:
            self._say("이 문서는 확인 필요 항목이 없어요")

    def _pages(self, i: int) -> list[tuple[QPixmap, int]]:
        doc = self.docs[i]
        key = tuple(doc.get("files") or doc.get("source") or [])
        if key not in self._images:
            pages = []
            try:
                for img in self.svc.page_images(doc):
                    pm = QPixmap()
                    if not pm.loadFromData(base64.b64decode(img["src"])):
                        raise ValueError("그림이 깨짐")
                    pages.append((pm, img["w"]))
            except Exception:  # 손상된 이미지: 그 문서는 오류 표시하고 확인 순서에서 건너뛴다
                pages = None
                self.broken.add(i)
                self.doc_list.item(i).setText(self._doc_title(doc))
            self._images[key] = pages
        return self._images[key]

    def mark(self, r: int) -> None:
        """r번째 칸이 있는 쪽을 보이고 그 칸에 상자를 그린다. 그 쪽의 확인 필요 칸은 늘 빨간 상자."""
        i = self.current()
        if i < 0 or i >= len(self.docs):
            return
        fields = self.docs[i]["fields"]
        pages = self._pages(i)
        if not pages:
            self.page.show_pixmap(QPixmap())
            return self.page.setText("문서 이미지를 불러오지 못했어요 — 오른쪽 표는 그대로 쓸 수 있어요" if pages is None
                                     else "쪽 이미지 없음")
        sel = fields[r] if 0 <= r < len(fields) else None
        n = min(max((sel or {}).get("page") or 1, 1), len(pages))
        pm, w = pages[n - 1]
        boxes = [(f["line_box"], RED, 3) for f in fields
                 if f["status"] == "review" and f.get("line_box") and (f.get("page") or 1) == n and f is not sel]
        if sel and sel.get("line_box"):
            boxes.append((sel["line_box"], RED if sel["status"] == "review" else YELLOW, 6))
        self.page.show_pixmap(draw_boxes(pm, pm.width() / w, boxes))

    def _set_field(self, r: int, value: str, status: str, advance: bool = False) -> None:
        i = self.current()
        doc = self.docs[i]
        fields = list(doc["fields"])
        fields[r] = self.svc.decide(fields[r], value, status)
        self.docs[i] = {**doc, "fields": fields}
        self.doc_list.item(i).setText(self._doc_title(self.docs[i]))
        if advance:
            return self._next_review(i, r)
        self._show_doc(i)

    def _next_review(self, i: int, r: int) -> None:
        """다음 '확인 필요' 칸으로. 이 문서에 없으면 다음 문서로(손상된 문서는 건너뜀). 다 끝나면 안내하고 그 자리에 머문다."""
        fields = self.docs[i]["fields"]
        nxt = next((k for k in list(range(r + 1, len(fields))) + list(range(0, r + 1)) if fields[k]["status"] == "review"), None)
        if nxt is not None:
            self._show_doc(i)
            return self.table.setCurrentCell(nxt, 1)
        order = list(range(i + 1, len(self.docs))) + list(range(0, i))
        j = next((k for k in order if k not in self.broken and self.svc.review_left([self.docs[k]])), None)
        if j is None:
            self._show_doc(i)
            self.status.setText("모든 확인을 마쳤어요 — 내보내기를 누르세요")
            return self._say("모든 확인 필요 칸을 처리했어요")
        self.doc_list.setCurrentRow(j)
        first = next(k for k, f in enumerate(self.docs[j]["fields"]) if f["status"] == "review")
        self.table.setCurrentCell(first, 1)

    def confirm(self, r: int, use_compare: bool = False) -> None:
        """[이 칸 확정] = 사용자 확인. [비교 값으로] = 값을 비교 값으로 바꾸므로 사용자 수정(처음 읽은 값은 남음)."""
        i = self.current()
        if i < 0 or r < 0:
            return self._say("칸을 고르세요")
        f = self.docs[i]["fields"][r]
        if use_compare and not f.get("compare"):
            return self._say("비교 값이 없는 칸이에요")
        self._set_field(r, f["compare"] if use_compare else f["value"], "edited" if use_compare else "confirmed", advance=True)

    def undo(self, r: int) -> None:
        i = self.current()
        if i < 0 or r < 0:
            return self._say("칸을 고르세요")
        f = self.docs[i]["fields"][r]
        if "orig" not in f:
            return self._say("되돌릴 내용이 없어요")
        fields = list(self.docs[i]["fields"])
        fields[r] = self.svc.undo(f)
        self.docs[i] = {**self.docs[i], "fields": fields}
        self.doc_list.item(i).setText(self._doc_title(self.docs[i]))
        self._show_doc(i)
        self._say(f"{f['label']}: 고치기 전 값으로 되돌렸어요")

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
        """현재 문서의 확인 필요 칸만 보이는 값 그대로 확정."""
        i = self.current()
        if i >= 0:
            self.docs[i] = self.svc.decide_all(self.docs[i])
            self.doc_list.item(i).setText(self._doc_title(self.docs[i]))
            self._next_review(i, -1)

    # ── 새 문서 종류 ──
    def _save_type(self) -> None:
        i = self.current()
        if i < 0 or not self.docs[i].get("new_type"):
            return
        doc = self.docs[i]
        name, ok = QInputDialog.getText(self, "새 문서 종류 확정", "문서 종류 이름:", text=doc.get("doc_name") or "")
        if not ok:
            return
        text, ok = QInputDialog.getMultiLineText(self, "새 문서 종류 확정", "읽을 칸 이름(한 줄에 하나, 고치거나 지워도 돼요):",
                                                 "\n".join(f["label"] for f in doc["fields"]))
        if not ok:
            return
        try:
            self.svc.save_custom_type(name, text.splitlines())
        except ValueError as err:
            return self._say(str(err))
        self.docs[i] = {**doc, "new_type": False, "doc_name": name.strip()}
        self.doc_list.item(i).setText(self._doc_title(self.docs[i]))
        self._show_doc(i)
        self._say(f"'{name.strip()}' 종류를 저장했어요. 다음부터 이 칸들로 읽어요.")

    def _manage_types(self) -> None:
        """확정해 둔 새 문서 종류의 칸 목록을 고치거나 지운다."""
        types = self.svc.custom_types()
        if not types:
            return self._say("확정해 둔 새 문서 종류가 없어요")
        name, ok = QInputDialog.getItem(self, "문서 종류 관리", "고치거나 지울 문서 종류:", list(types), 0, False)
        if not ok:
            return
        box = QMessageBox(self)
        box.setWindowTitle("문서 종류 관리")
        box.setText(f"'{name}' 칸: " + ", ".join(types[name]))
        fix = box.addButton("칸 고치기", QMessageBox.AcceptRole)
        rm = box.addButton("지우기", QMessageBox.DestructiveRole)
        box.addButton("그만두기", QMessageBox.RejectRole)
        box.exec()
        try:
            if box.clickedButton() is fix:
                text, ok = QInputDialog.getMultiLineText(self, "칸 고치기", "칸 이름(한 줄에 하나):", "\n".join(types[name]))
                if ok:
                    self.svc.save_custom_type(name, text.splitlines())
                    self._say(f"'{name}' 칸을 고쳤어요")
            elif box.clickedButton() is rm:
                if QMessageBox.question(self, "지우기", f"'{name}' 종류를 지울까요? 다음부터는 처음 보는 종류로 읽어요.") == QMessageBox.Yes:
                    self.svc.delete_custom_type(name)
                    self._say(f"'{name}' 종류를 지웠어요")
        except ValueError as err:
            self._say(str(err))

    # ── 내보내기 ──
    def export_to(self, path_stem: Path, append: bool = False, force: bool = False, only: list[str] | None = None) -> list[Path] | None:
        kinds = [k for k, c in self.kinds.items() if c.isChecked()]
        try:
            out = self.svc.export(self.docs, kinds, Path(path_stem), append=append, sep=self.sep.currentData(), force=force, only=only)
        except ValueError as err:
            return self._say(str(err))
        self._say("저장했어요:\n" + "\n".join(map(str, out)))
        return out

    def _choose_fields(self) -> list[str] | None:
        """두 건 이상을 한 파일로 묶을 때 내보낼 칸을 고르는 창. 그만두면 None."""
        labels = self.svc.field_labels(self.docs)
        dlg = QDialog(self)
        dlg.setWindowTitle("한 번에 내보내기")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"읽은 문서 {len(self.docs)}건을 한 파일로 묶어요. 내보낼 칸을 고르세요."))
        inner = QWidget()
        il = QVBoxLayout(inner)
        boxes = [QCheckBox(lab) for lab in labels]
        for c in boxes:
            c.setChecked(True)
            il.addWidget(c)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.resize(380, 420)
        if dlg.exec() != QDialog.Accepted:
            return None
        return [c.text() for c in boxes if c.isChecked()]

    def _export_dialog(self) -> None:
        if not self.docs:
            return self._say("내보낼 문서가 없어요 — 먼저 문서를 읽으세요")
        self._autosave()
        force = False
        left = self.svc.review_left(self.docs)
        if left:
            if QMessageBox.question(self, "남은 확인", f"남은 확인 {left}칸이 있어요. 그래도 내보낼까요?") != QMessageBox.Yes:
                return self._say(f"남은 확인 {left}칸")
            force = True
        only = None
        if len(self.docs) >= 2:
            only = self._choose_fields()
            if only is None:
                return
        box = QMessageBox(self)
        box.setText("어떻게 저장할까요?")
        new = box.addButton("새 파일로", QMessageBox.AcceptRole)
        add = box.addButton("기존 파일에 이어 붙이기", QMessageBox.ActionRole)
        box.addButton("그만두기", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is new:
            p, _ = QFileDialog.getSaveFileName(self, "새 파일 이름", str(self.out_dir / "확정"))
            if p:
                self.export_to(Path(p), force=force, only=only)
        elif box.clickedButton() is add:
            while True:      # 이어 붙일 수 없는 파일이면 이유를 보여 주고 다시 고르게 한다
                p, _ = QFileDialog.getOpenFileName(self, "이어 붙일 파일", str(self.out_dir), "내보낸 파일 (*.xlsx *.csv *.txt)")
                if not p or self.export_to(Path(p), append=True, force=force, only=only) is not None:
                    return
                QMessageBox.warning(self, "이어 붙일 수 없어요", self.message.toPlainText() + "\n\n다른 파일을 고르세요.")

    # ── 기타 ──
    def _toggle_list(self) -> None:
        self.lcard.setVisible(not self.lcard.isVisible())
        self.toggle_list.setText("목록 숨기기" if self.lcard.isVisible() else "목록 보이기")

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
        bad = any(self.docs[k] is doc for k in self.broken if k < len(self.docs))
        return f"{'✕ ' if bad else '⚠ ' if left else '✓ '}{self._name(doc)} ({self._kind(doc)})"

    def _say(self, text: str) -> None:
        self.message.setPlainText(text)
