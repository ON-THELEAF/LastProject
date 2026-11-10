"""문서 읽기 화면(피그마 디자인): 파일 넣기 → 묶음 확인 → 읽기(대기열·취소) → 문서별 확인 → 내보내기.

화면은 DesktopService만 부른다. 오류는 서비스가 주는 한국어 문구를 그대로 보여 준다.
shortcut: 확대·축소·쪽 넘기기 버튼은 없다(칸을 누르면 그 쪽으로 감).
칸을 누르면 쪽 이미지 위에 그 칸 자리를 상자로 표시한다: 확인 필요 = 빨강, 그 밖 = 노랑.
"""
import base64
import html
import re
from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QListWidget, QMessageBox, QProgressDialog, QPushButton, QScrollArea, QSizePolicy,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

import theme
from home import DropZone

COLS = ["칸", "원본 값", "상태", "비교 값", "사유"]
COMPARE = 3     # 사용자가 고치는 열(비교 값). 원본 값은 그대로 둔다
DEFAULT_OUT = Path.home() / "Documents" / "BridgeDocs 읽기 결과"
RED, YELLOW = QColor(220, 30, 30), QColor(255, 190, 0)


def _stem(files: list) -> str:
    """문서 이름(쪽 번호 _p1 뺌). 엔진 작업(ReadJob._stem)과 같은 규칙이라 대기 줄과 결과를 짝짓는 데 쓴다."""
    return re.sub(r"_p\d+$", "", Path(files[0]).stem)


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
        self.group = True              # 쪽 번호 이미지를 한 문서로 묶을지
        self.broken: set[int] = set()  # 쪽 이미지를 못 불러온 문서 번호
        self.failures: list[str] = []  # 이번 읽기에서 실패한 파일: 사유
        self.pending: dict[tuple[int, int], str] = {}   # (문서, 칸) → 사용자가 적은 비교 값(아직 적용 전)
        self._shown = -1               # 지금 보이는 문서(다른 문서로 갈 때 자동 저장)
        self._last = ""                # 마지막 안내 문구

        self.status = theme.label("여러 파일을 문서 목록에 모은 뒤 [읽기 시작]을 누르세요", "sectionTitle")
        self.status.setStyleSheet(f"color: {theme.BLUE_DARK};")
        self.waiting: list[dict] = []  # 넣었지만 아직 다 읽지 않은 문서(목록에서 읽은 문서 뒤에 보임)
        self.loading = None            # 첫 문서가 끝날 때까지 띄우는 로딩 창
        self.doc_list = QListWidget()  # 줄 = 읽은 문서(self.docs) 다음에 대기 문서(self.waiting)
        self.doc_list.currentRowChanged.connect(self._show_doc)
        self.doc_list.setWordWrap(True)     # 문서 상자를 목록 폭에 맞춘다(옆으로 밀리지 않게)
        self.doc_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.doc_list.setTextElideMode(Qt.ElideMiddle)
        self.doc_list.setResizeMode(QListWidget.Adjust)
        self.page = FitImage()
        self.page.setStyleSheet(f"background: #EEF1F6; border-radius: 10px; color: {theme.MUTED};")
        self.doc_title = theme.label("왼쪽 목록에서 문서를 고르세요", "sectionTitle", wrap=True)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(True)        # 표가 칸 폭 안에 다 보이게(옆으로 끌지 않게)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        head = self.table.horizontalHeader()
        for c, mode in enumerate((QHeaderView.ResizeToContents, QHeaderView.Stretch, QHeaderView.ResizeToContents,
                                  QHeaderView.Stretch, QHeaderView.Stretch)):
            head.setSectionResizeMode(c, mode)
        self.table.itemChanged.connect(self._edited)
        self.table.currentCellChanged.connect(lambda r, *_: (self.mark(r), self.confirm_one.setVisible(r >= 0)))
        self._images = {}       # 문서 파일들 → [(쪽 QPixmap, 원래 폭)]
        self.kinds = {}
        for k in ("xlsx", "csv", "txt"):     # 내보낼 형식(여러 개 고를 수 있음)
            self.kinds[k] = QPushButton(k.upper())
            self.kinds[k].setCheckable(True)
            self.kinds[k].setProperty("seg", True)
        self.kinds["xlsx"].setChecked(True)

        self.kinds["txt"].setToolTip("기본 구분 기호: |")
        b = {n: theme.button(n) for n in ("이 칸 확정", "비교 값 적용", "되돌리기", "JSON 위치", "«")}
        b["이 칸 확정"].clicked.connect(lambda: self.confirm_value(self.table.currentRow()))
        self.confirm_one = b["이 칸 확정"]      # 칸을 골랐을 때만 보임
        self.confirm_one.hide()
        b["되돌리기"].clicked.connect(lambda: self.undo(self.table.currentRow()))
        b.update({n: theme.button(n, "primary") for n in ("읽기 시작", "한 번에 확정", "↓  내보내기")})
        b["읽기 시작"].clicked.connect(self.start)
        b["비교 값 적용"].clicked.connect(lambda: self.confirm(self.table.currentRow()))
        b["한 번에 확정"].clicked.connect(self.confirm_all)
        b["↓  내보내기"].clicked.connect(self._export_dialog)
        b["JSON 위치"].clicked.connect(lambda: self._say("JSON은 문서를 읽을 때 저장돼요: " +
                                                       (", ".join(self.svc.json_folders(self.docs)) or str(self.out_dir))))

        # 진행 상황 띠
        banner, bl = theme.card("banner")
        bl.setContentsMargins(16, 10, 12, 10)
        brow = QHBoxLayout()
        brow.addWidget(self.status)
        brow.addWidget(theme.label("파일을 한 번에 처리해 더 빠르고 안정적이에요.", "muted"), 1)
        brow.addWidget(b["읽기 시작"])
        bl.addLayout(brow)

        # 안내 띠(읽기 결과·오류 등). 평소엔 숨김
        self.notice = QFrame()
        self.notice.setObjectName("noticeInfo")
        nl = QHBoxLayout(self.notice)
        nl.setContentsMargins(14, 8, 14, 8)
        self.notice_text = QLabel()
        self.notice_text.setWordWrap(True)
        self.notice_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        nl.addWidget(self.notice_text, 1)
        self.fail_link = theme.button("오류 문서 확인하기 →", "link")
        self.fail_link.setStyleSheet(f"color: {theme.FAIL};")
        self.fail_link.clicked.connect(self._show_failures)
        nl.addWidget(self.fail_link)
        self.notice.hide()

        # 세 칸: 문서 목록 · 쪽 이미지 · 읽은 값
        lcard, ll = theme.card()
        lhead = QHBoxLayout()
        lcol = QVBoxLayout()
        lcol.setSpacing(2)
        lcol.addWidget(theme.label("문서 목록", "sectionTitle"))
        lcol.addWidget(theme.label("여러 파일을 올린 뒤 한 번에 읽어요", "muted"))
        lhead.addLayout(lcol, 1)
        b["«"].setToolTip("목록 숨기기")
        b["«"].setFixedWidth(40)
        lhead.addWidget(b["«"], 0, Qt.AlignTop)
        ll.addLayout(lhead)
        ll.addWidget(self.doc_list, 1)
        ll.addWidget(DropZone(self.add_files, "파일을 추가로 놓아주세요", "PDF · PNG · JPG · DOCX", button_text=""))
        mcard, ml = theme.card()
        ml.addWidget(self.doc_title)
        ml.addWidget(self.page, 1)
        rcard, rl = theme.card()
        rhead = QHBoxLayout()
        rcol = QVBoxLayout()
        rcol.setSpacing(2)
        rcol.addWidget(theme.label("인식된 정보", "sectionTitle"))
        rcol.addWidget(theme.label("원본 값은 그대로 두고 비교 값만 수정해 판독 정확도를 높여요.", "muted", wrap=True))
        rhead.addLayout(rcol, 1)
        self.new_badge = theme.badge("새 문서 · 칸 이름 수정 가능", "warn")    # 처음 보는 문서 종류일 때만
        rhead.addWidget(self.new_badge, 0, Qt.AlignTop)
        rl.addLayout(rhead)
        rl.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        for n in ("이 칸 확정", "비교 값 적용", "되돌리기", "한 번에 확정"):
            row.addWidget(b[n])
        rl.addLayout(row)
        self.new_note = theme.label("ⓘ  새 문서의 칸 이름을 수정할 수 있어요. 내보낼 때 양식 등록 여부를 확인합니다.", "hint", wrap=True)
        self.new_note.setContentsMargins(10, 6, 10, 6)
        rl.addWidget(self.new_note)
        self.new_badge.hide()
        self.new_note.hide()
        self.lcard = lcard
        self.show_list = theme.button("»")      # 목록을 숨겼을 때 가운데 칸에서 다시 열기
        self.show_list.setToolTip("목록 보이기")
        self.show_list.setFixedWidth(40)
        self.show_list.hide()
        self.show_list.clicked.connect(self._toggle_list)
        ml.insertWidget(0, self.show_list, 0, Qt.AlignLeft)
        b["«"].clicked.connect(self._toggle_list)
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
        lay.addLayout(theme.page_head("문서 읽기", "읽은 값을 확인하고 필요한 부분을 고쳐 주세요."))
        lay.addWidget(banner)
        lay.addWidget(self.notice)
        lay.addWidget(split, 1)
        lay.addWidget(ecard)

    # ── 파일 넣기·읽기 ──
    def add_files(self, paths: list[Path], ask: bool = True) -> dict:
        """넣은 파일을 바로 문서 목록에 '대기'로 보인다(읽기 시작을 눌러야 읽음). 쪽 묶음을 거절하면 이미지를 따로따로 읽는다."""
        paths = [Path(p) for p in paths]
        g = self.svc.preview_groups(paths)
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
        units = [x["files"] for x in g["groups"]] if self.group else [[f] for x in g["groups"] for f in x["files"]]
        first = len(self.docs) + len(self.waiting)
        for files in units:
            w = {"files": [str(f) for f in files], "source": [Path(f).name for f in files], "state": "대기"}
            self.waiting.append(w)
            self.doc_list.addItem(self._wait_title(w))
            self.files += [Path(f) for f in files]
        if units and self.doc_list.currentRow() < 0:
            self.doc_list.setCurrentRow(first)
        self._notice(f"<b>새 파일이 추가됐어요</b>&nbsp;&nbsp; {n}개 파일이 읽기 대기 중이에요 — 읽기 시작을 눌러 주세요"
                     + "".join("<br>" + html.escape(x) for x in lines))
        return g

    def start(self) -> None:
        if not self.files:
            return self._say("파일을 먼저 넣으세요")
        try:
            job = self.svc.start_read(self.files, self.out_dir, group=self.group)
        except ValueError as err:
            return self._say(str(err))
        self.files = []
        self.failures = []
        self._new_before = sum(bool(d.get("new_type")) for d in self.docs)
        for k, w in enumerate(self.waiting):
            if w["state"] == "대기":
                self.waiting[k] = {**w, "state": "읽는 중"}
                self.doc_list.item(len(self.docs) + k).setText(self._wait_title(self.waiting[k]))
        # 첫 문서가 끝날 때까지 기다리게 하는 로딩 창(그 뒤로는 확인하면서 나머지를 읽는다)
        self.loading = QProgressDialog("문서를 읽는 중이에요…\n첫 문서가 끝나면 바로 확인할 수 있어요.", None, 0, 0, self)
        self.loading.setWindowTitle("문서 읽기")
        self.loading.setCancelButton(None)
        self.loading.setWindowModality(Qt.WindowModal)
        self.loading.setMinimumDuration(0)
        self.loading.setAutoClose(False)
        self.loading.show()
        self.jobs.append(job)
        job.progress.connect(self.on_progress)
        job.queued.connect(self.on_queued)
        job.doc_done.connect(self.on_done)
        job.doc_failed.connect(self.on_failed)
        job.finished.connect(self.on_finished)
        job.finished.connect(lambda *_, j=job: self.jobs.remove(j) if j in self.jobs else None)
        if job.queue_position:
            self.on_queued(job.queue_position)

    @Slot(str)
    def on_progress(self, text):
        if self.loading:
            self.loading.setLabelText(f"{text}\n첫 문서가 끝나면 바로 확인할 수 있어요.")
            return self.status.setText(text)
        left = sum(w["state"] == "읽는 중" for w in self.waiting)
        self.status.setText(f"{text}  —  다른 문서 {left}건은 아직 읽는 중이에요. 끝난 문서부터 확인하세요" if left else text)

    def _close_loading(self) -> None:
        if self.loading:
            self.loading.close()
            self.loading = None

    @Slot(int)
    def on_queued(self, n):
        self.status.setText(f"대기 중 — 앞에 작업 {n}개 (대기 순번 {n})" if n else "곧 시작해요")

    @Slot(dict)
    def on_done(self, doc):
        """다 읽은 문서는 대기 줄을 빼고 읽은 문서 줄로 넣는다. 첫 문서면 로딩 창을 닫고 그 문서를 보여 준다."""
        prev, old = self.doc_list.currentRow(), len(self.docs)
        show_new = bool(self.loading) or prev < 0 or prev >= old    # 보던 읽은 문서가 없으면 새 문서를 보여 준다
        self.doc_list.blockSignals(True)
        k = self._waiting_index(_stem(doc.get("files") or doc.get("source") or ["?"]))
        if k is not None:
            self.waiting.pop(k)
            self.doc_list.takeItem(len(self.docs) + k)
        self.docs.append(doc)
        self.doc_list.insertItem(old, self._doc_title(doc))
        if show_new:
            self.doc_list.setCurrentRow(old)
        self.doc_list.blockSignals(False)
        if show_new:
            self._show_doc(old)
        s = doc.get("summary", {})
        self._say(f"{self._name(doc)}: 자동 {s.get('auto', 0)}칸, 확인 필요 {s.get('review', 0)}칸")
        self._close_loading()
        left = sum(w["state"] == "읽는 중" for w in self.waiting)
        if left:
            self.status.setText(f"다른 문서 {left}건은 아직 읽는 중이에요. 끝난 문서부터 확인하세요")

    def _waiting_index(self, stem: str) -> int | None:
        return next((k for k, w in enumerate(self.waiting) if w["state"] == "읽는 중" and _stem(w["files"]) == stem), None)

    @Slot(str, str)
    def on_failed(self, name, reason):
        self.failures.append(f"{name}: {reason}")
        k = self._waiting_index(name)
        if k is not None:      # 목록에 ✕ 오류로 남긴다
            self.waiting[k] = {**self.waiting[k], "state": "오류", "reason": reason}
            self.doc_list.item(len(self.docs) + k).setText(self._wait_title(self.waiting[k]))

    @Slot(int, int)
    def on_finished(self, ok, bad):
        self._close_loading()
        for k, w in enumerate(self.waiting):     # 중간에 멈춘 작업의 남은 문서는 다시 대기로
            if w["state"] == "읽는 중":
                self.waiting[k] = {**w, "state": "대기"}
                self.files += [Path(f) for f in w["files"]]
                self.doc_list.item(len(self.docs) + k).setText(self._wait_title(self.waiting[k]))
        new = sum(bool(d.get("new_type")) for d in self.docs) - getattr(self, "_new_before", 0)
        self.status.setText("여러 파일을 문서 목록에 모은 뒤 [읽기 시작]을 누르세요")
        self._notice(f"<b>ⓘ  모든 문서의 읽기가 끝났어요</b>&nbsp;&nbsp; 성공 {ok - new}개 · 새 문서 인식 {new}개 · 오류 {bad}개",
                     error=bool(bad))

    def _show_failures(self) -> None:
        QMessageBox.information(self, "오류 문서", "읽지 못한 문서예요.\n\n" + "\n".join(self.failures))

    # ── 확인 ──
    def current(self) -> int:
        """지금 고른 읽은 문서 번호(대기 문서를 골랐으면 -1)."""
        r = self.doc_list.currentRow()
        return r if r < len(self.docs) else -1

    def _autosave(self) -> None:
        """보던 문서의 확정·수정 내용을 JSON에 저장(다른 문서로 넘어갈 때)."""
        if 0 <= self._shown < len(self.docs):
            try:
                self.svc.save_doc(self.docs[self._shown])
            except ValueError as err:
                self._say(str(err))

    def _show_doc(self, i: int) -> None:
        if len(self.docs) <= i < len(self.docs) + len(self.waiting):
            return self._show_waiting(self.waiting[i - len(self.docs)])
        if i < 0 or i >= len(self.docs):
            return
        if i != self._shown:
            self._autosave()
            self._shown = i
        doc = self.docs[i]
        left = self.svc.review_left([doc])
        self.doc_title.setText(self._name(doc))      # 가운데 미리보기 위에는 파일 이름만
        new = bool(doc.get("new_type"))
        self.new_badge.setVisible(new)
        self.new_note.setVisible(new)
        row = self.table.currentRow()
        self.table.blockSignals(True)
        self.table.setRowCount(len(doc["fields"]))
        for r, f in enumerate(doc["fields"]):
            cells = [f["label"], str(f["value"] or ""), self.labels["status"].get(f["status"], f["status"]),
                     self.pending.get((i, r), str(f.get("compare") or "")), f.get("reason") or "—"]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c != COMPARE and not (c == 0 and new):     # 새 문서는 칸 이름도 고칠 수 있다
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if f["status"] == "review":
                    item.setBackground(QColor(theme.WARN_SOFT))
                if c == 2:
                    item.setForeground(QColor(theme.WARN if f["status"] == "review" else theme.GREEN))
                self.table.setItem(r, c, item)
        self.table.blockSignals(False)
        self.table.resizeRowsToContents()
        if 0 <= row < len(doc["fields"]):
            self.table.setCurrentCell(row, COMPARE)
        self.mark(row if 0 <= row < len(doc["fields"]) else -1)
        if error := doc.get("error"):
            self._say(error)
        elif not left:
            self._say("이 문서는 확인 필요 항목이 없어요")

    def _show_waiting(self, w: dict) -> None:
        """아직 안 읽은 문서: 가운데에 파일 첫 쪽만 보여 준다(칸 표는 비움)."""
        self.doc_title.setText(", ".join(w["source"]))
        self.new_badge.hide()
        self.new_note.hide()
        self.table.setRowCount(0)
        key = ("대기",) + tuple(w["files"])     # 읽은 뒤 쪽 그림(보정됨)과 섞이지 않게 따로 둔다
        if key not in self._images:
            try:
                img = (self.svc.page_images({"files": w["files"]}) or [None])[0]
                pm = QPixmap()
                self._images[key] = pm if img and pm.loadFromData(base64.b64decode(img["src"])) else None
            except Exception:  # 손상·지원 안 하는 파일: 미리보기만 못 보여 준다(읽기는 그대로)
                self._images[key] = None
        pm = self._images[key]
        self.page.show_pixmap(pm or QPixmap())
        if not pm:
            self.page.setText("미리보기가 없어요 (워드 문서이거나 열 수 없는 파일)")
        if w["state"] == "오류":
            self._say(f"이 문서는 읽지 못했어요 — {w.get('reason', '')}")

    def _wait_title(self, w: dict) -> str:
        mark = {"대기": "·", "읽는 중": "…", "오류": "✕"}[w["state"]]
        return f"{mark}   {', '.join(w['source'])}\n      {'읽기 대기' if w['state'] == '대기' else w['state']}"

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
            return self.table.setCurrentCell(nxt, COMPARE)
        order = list(range(i + 1, len(self.docs))) + list(range(0, i))
        j = next((k for k in order if k not in self.broken and self.svc.review_left([self.docs[k]])), None)
        if j is None:
            self._show_doc(i)
            self.status.setText("모든 확인을 마쳤어요 — 내보내기를 누르세요")
            return self._say("모든 확인 필요 칸을 처리했어요")
        self.doc_list.setCurrentRow(j)
        first = next(k for k, f in enumerate(self.docs[j]["fields"]) if f["status"] == "review")
        self.table.setCurrentCell(first, COMPARE)

    def confirm(self, r: int) -> None:
        """[비교 값 적용]: 비교 값 칸(사용자가 고친 값, 없으면 두 번째 판독 값)을 값으로 쓴다.
        원본 값과 같으면 사용자 확인, 다르면 사용자 수정(처음 읽은 값은 남음)."""
        i = self.current()
        if i < 0 or r < 0:
            return self._say("칸을 고르세요")
        f = self.docs[i]["fields"][r]
        text = self.pending.get((i, r), str(f.get("compare") or "")).strip()
        if not text:
            return self._say(f"{f['label']}: 비교 값 칸에 값을 적어 주세요")
        value, err = self.svc.parse_value(f["kind"], text)
        if err:
            return self._say(f"{f['label']}: {err}")
        self.pending.pop((i, r), None)
        self._set_field(r, value, "confirmed" if value == f["value"] else "edited", advance=True)

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
        """비교 값 칸을 고치면 적어 두기만 한다([비교 값 적용]을 눌러야 반영). 새 문서의 칸 이름은 바로 바꾼다."""
        i, r = self.current(), item.row()
        if item.column() == COMPARE:
            self.pending[(i, r)] = item.text()
        elif item.column() == 0 and item.text().strip():
            fields = list(self.docs[i]["fields"])
            fields[r] = {**fields[r], "label": item.text().strip()}
            self.docs[i] = {**self.docs[i], "fields": fields}

    def confirm_value(self, r: int) -> None:
        """[이 칸 확정]: 원본 값 그대로 사용자 확인."""
        i = self.current()
        if i < 0 or r < 0:
            return self._say("칸을 고르세요")
        self._set_field(r, self.docs[i]["fields"][r]["value"], "confirmed", advance=True)

    def confirm_all(self) -> None:
        """현재 문서의 확인 필요 칸만 보이는 값 그대로 확정."""
        i = self.current()
        if i >= 0:
            self.docs[i] = self.svc.decide_all(self.docs[i])
            self.doc_list.item(i).setText(self._doc_title(self.docs[i]))
            self._next_review(i, -1)

    # ── 내보내기 ──
    def export_to(self, path_stem: Path, append: bool = False, force: bool = False, only: list[str] | None = None) -> list[Path] | None:
        kinds = [k for k, c in self.kinds.items() if c.isChecked()]
        try:
            out = self.svc.export(self.docs, kinds, Path(path_stem), append=append, sep="|", force=force, only=only)
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

    def _ask_register(self) -> bool:
        """새 문서가 있으면 그 칸 이름을 문서 종류로 등록할지 묻는다. 창을 닫으면 내보내지 않음(False)."""
        new = [k for k, d in enumerate(self.docs) if d.get("new_type")]
        if not new:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("새 문서 양식 등록")
        names = ", ".join(self._kind(self.docs[k]) for k in new)
        box.setText(f"<b>새 문서 양식을 등록할까요?</b><br>새로 인식한 {names}의 칸 이름을 다음 읽기에도 사용할 수 있어요.")
        box.setInformativeText("\n".join(f"{self._name(self.docs[k])} — 새 칸 {len(self.docs[k]['fields'])}개" for k in new))
        skip = box.addButton("등록하지 않고 내보내기", QMessageBox.RejectRole)
        reg = box.addButton("양식 등록 후 내보내기", QMessageBox.AcceptRole)
        box.exec()
        if box.clickedButton() is reg:
            return self._register(new)
        return box.clickedButton() is skip

    def _register(self, new: list[int]) -> bool:
        """새 문서들의 (고친) 칸 이름을 문서 종류로 저장. 다음부터 그 칸들로 읽는다."""
        for k in new:
            doc = self.docs[k]
            name = self._kind(doc)
            try:
                self.svc.save_custom_type(name, [f["label"] for f in doc["fields"]])
            except ValueError as err:
                self._say(str(err))
                return False
            self.docs[k] = {**doc, "new_type": False, "doc_name": name}
            self.doc_list.item(k).setText(self._doc_title(self.docs[k]))
        self._show_doc(self.current())
        return True

    def _export_dialog(self) -> None:
        if not self.docs:
            return self._say("내보낼 문서가 없어요 — 먼저 문서를 읽으세요")
        self._autosave()
        if not self._ask_register():
            return
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
                QMessageBox.warning(self, "이어 붙일 수 없어요", self._last + "\n\n다른 파일을 고르세요.")

    # ── 기타 ──
    def _toggle_list(self) -> None:
        self.lcard.setVisible(not self.lcard.isVisible())
        self.show_list.setVisible(not self.lcard.isVisible())

    def _name(self, doc: dict) -> str:
        return ", ".join(doc.get("source") or []) or "문서"

    def _kind(self, doc: dict) -> str:
        return doc.get("doc_name") or self.labels["doc_type"].get(doc.get("doc_type"), doc.get("doc_type") or "알 수 없음")

    def _doc_title(self, doc: dict) -> str:
        left = self.svc.review_left([doc])
        bad = any(self.docs[k] is doc for k in self.broken if k < len(self.docs))
        mark = "✕" if bad else "!" if left else "N" if doc.get("new_type") else "✓"
        tag = "  ·  새 문서" if doc.get("new_type") else "  ·  확인 필요" if left else ""
        return f"{mark}   {self._name(doc)}\n      {self._kind(doc)}{tag}"

    def _notice(self, html: str, error: bool = False) -> None:
        """위쪽 안내 띠. error면 빨간 띠와 [오류 문서 확인하기]."""
        self.notice.setObjectName("notice" if error else "noticeInfo")
        theme.repolish(self.notice)
        self.notice_text.setText(html)
        self.fail_link.setVisible(error and bool(self.failures))
        self.notice.show()

    def _say(self, text: str) -> None:
        self._last = text
        self._notice(text.replace("\n", "<br>"))
