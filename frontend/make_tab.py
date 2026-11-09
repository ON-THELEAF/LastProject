"""문서 만들기 탭: 데이터 넣기 → 양식 고르기(사용자가 넣은 워드 양식) → 값 확인·수정 → 만들기.

화면은 DesktopService만 부른다. 오류는 서비스가 주는 한국어 문구를 그대로 보여 준다.
"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

import theme
from home import DropZone

DATA_FILTER = "데이터 (*.json *.csv *.xlsx *.txt)"
FILLED_NOTE = "비어 있어 임의 값으로 채움 — 확인하세요"


def _step(n: int, title: str, sub: str, content) -> QWidget:
    """번호 동그라미 · 단계 제목 · 내용이 한 줄인 단계 카드."""
    f, lay = theme.card()
    lay.setContentsMargins(22, 20, 22, 20)
    row = QHBoxLayout()
    row.setSpacing(18)
    num = theme.label(str(n), "stepNum")
    num.setFixedSize(30, 30)
    num.setAlignment(Qt.AlignCenter)
    head = QVBoxLayout()
    head.setSpacing(2)
    head.addWidget(num)
    head.addWidget(theme.label(f"{n}단계", "eyebrow"))
    head.addWidget(theme.label(title, "sectionTitle"))
    head.addWidget(theme.label(sub, "muted", wrap=True))
    head.addStretch(1)
    box = QWidget()
    box.setFixedWidth(170)
    box.setLayout(head)
    row.addWidget(box)
    row.addLayout(content, 1)
    lay.addLayout(row)
    return f


class MakeTab(QWidget):
    def __init__(self, svc, parent=None):
        super().__init__(parent)
        self.svc, self.data_path, self.out_dir, self.docs = svc, None, None, []

        self.data_label = theme.label("데이터 파일을 고르세요", "sectionTitle")
        self.data_sub = theme.label("CSV · TXT · XLSX · JSON  |  한 줄이 문서 하나예요", "muted")
        self.header = False      # 첫 줄은 제목(데이터를 고를 때 뜨는 창에서 정함)
        self.name_box = QLineEdit("문서")      # 만들 파일 이름: 문서1.docx, 문서2.docx …
        self.name_box.setAccessibleName("파일 이름")
        self.name_box.setFixedWidth(140)
        self.made_base = "문서"    # 마지막으로 만들 때 쓴 파일 이름
        self.template_box = QComboBox()
        self.template_box.setAccessibleName("양식")
        self.template_box.setPlaceholderText("양식을 추가하세요")
        self.hint = theme.label("", "muted", wrap=True)
        self.out_label = theme.label("저장 폴더를 고르세요", "muted")
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["문서 / 빈칸", "값", "알림"])
        self.tree.setColumnWidth(0, 220)
        self.tree.setColumnWidth(1, 300)
        self.tree.setMinimumHeight(260)
        self.message = theme.label("", "muted", wrap=True)      # 안내 한 줄(3단계 카드 안)
        self.message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.made = []          # 마지막으로 만든 문서 파일들(PDF·그림 내보내기 대상)

        buttons = {name: theme.button(name) for name in ("데이터 고르기", "새 양식 추가…", "양식 지우기", "저장 폴더 고르기")}
        buttons.update({name: theme.button(name, "primary") for name in ("불러오기", "만들기")})
        buttons["데이터 고르기"].clicked.connect(self._pick_data)
        buttons["새 양식 추가…"].clicked.connect(self._pick_template)
        buttons["양식 지우기"].clicked.connect(self._remove_template)
        buttons["불러오기"].clicked.connect(self.load)
        buttons["저장 폴더 고르기"].clicked.connect(self._pick_out)
        buttons["만들기"].clicked.connect(lambda: self.make())
        self.template_box.currentIndexChanged.connect(self._show_hint)

        # 1단계 데이터
        s1 = QVBoxLayout()
        file_box, fl = theme.card()
        frow = QHBoxLayout()
        ic = theme.label("▤", "iconBoxGreen")
        ic.setFixedSize(40, 40)
        ic.setAlignment(Qt.AlignCenter)
        frow.addWidget(ic)
        fc = QVBoxLayout()
        fc.setSpacing(2)
        fc.addWidget(self.data_label)
        fc.addWidget(self.data_sub)
        frow.addLayout(fc, 1)
        frow.addWidget(buttons["데이터 고르기"])
        fl.addLayout(frow)
        s1.addWidget(file_box)
        s1.addWidget(DropZone(lambda ps: self.set_data(ps[0]), "데이터 파일을 끌어다 놓아도 돼요", "CSV · TXT · XLSX · JSON",
                              button_text="", file_filter=DATA_FILTER))
        # 2단계 양식과 내용
        s2 = QVBoxLayout()
        trow = QHBoxLayout()
        trow.addWidget(theme.label("문서 양식", "sectionTitle"))
        trow.addWidget(self.template_box, 1)
        trow.addWidget(buttons["새 양식 추가…"])
        trow.addWidget(buttons["양식 지우기"])
        trow.addWidget(buttons["불러오기"])
        s2.addLayout(trow)
        s2.addWidget(self.hint)
        s2.addWidget(self.tree, 1)
        # 3단계 만들기
        s3 = QVBoxLayout()
        r3 = QHBoxLayout()
        r3.addWidget(self.out_label, 1)
        r3.addWidget(theme.label("파일 이름", "muted"))
        r3.addWidget(self.name_box)
        r3.addWidget(buttons["저장 폴더 고르기"])
        r3.addWidget(buttons["만들기"])
        s3.addLayout(r3)
        r4 = QHBoxLayout()
        r4.addWidget(theme.label("만든 문서를 다른 형식으로도 저장", "muted"), 1)
        for text, kind in (("PDF로 내보내기", "pdf"), ("이미지로 내보내기", "png")):
            b = theme.button(text)
            b.clicked.connect(lambda _=False, k=kind: self.convert(k))
            r4.addWidget(b)
        s3.addLayout(r4)
        s3.addWidget(self.message)

        steps = QVBoxLayout()
        steps.setSpacing(14)
        steps.addWidget(_step(1, "데이터 불러오기", "CSV, TXT, XLSX 파일을 쓸 수 있어요.", s1))
        steps.addWidget(_step(2, "양식과 내용 확인", "값을 두 번 눌러 직접 고칠 수 있어요.", s2), 1)
        steps.addWidget(_step(3, "문서 만들기", "저장 폴더를 고르고 만드세요.", s3))

        guide, gl = theme.card()
        gi = theme.label("i", "iconBox")
        gi.setFixedSize(40, 40)
        gi.setAlignment(Qt.AlignCenter)
        gl.addWidget(gi)
        gl.addWidget(theme.label("사용 안내", "cardTitle"))
        gl.addWidget(theme.label("내가 넣은 워드 양식(.docx)의 빈칸 {{…}}에 데이터 값을 왼쪽부터 순서대로 넣어 문서를 만들어요.", "muted", wrap=True))
        for t in ("없는 값은 빈칸으로 만들어요", "표 한 줄 = 문서 하나", "첫 줄이 제목이면 체크하세요"):
            gl.addWidget(theme.label("✓  " + t))
        tip, tl = theme.card("guide")
        tl.addWidget(theme.label("양식 만드는 법", "sectionTitle"))
        tl.addWidget(theme.label("워드에서 값이 들어갈 자리에 {{이름}}처럼 적어 저장한 뒤 '새 양식 추가…'로 넣으세요.", "muted", wrap=True))
        gl.addWidget(tip)
        gl.addStretch(1)
        guide.setFixedWidth(300)

        body = QHBoxLayout()
        body.setSpacing(18)
        body.addLayout(steps, 1)
        body.addWidget(guide, 0, Qt.AlignTop)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 18)
        lay.setSpacing(14)
        lay.addLayout(theme.page_head("문서 만들기", "문서 만들기", "데이터를 불러와 내용을 확인하고 새 문서를 만드세요."))
        lay.addLayout(body, 1)
        self.refresh_templates()

    # ── 양식 ──
    def refresh_templates(self, select: str | None = None) -> None:
        self.template_box.blockSignals(True)
        self.template_box.clear()
        for t in self.svc.list_templates():
            self.template_box.addItem(t["name"], t)
        self.template_box.blockSignals(False)
        if select:
            self.select_template(select)
        self._show_hint()

    def select_template(self, template_id: str) -> None:
        i = next(i for i in range(self.template_box.count()) if self.template_box.itemData(i)["id"] == template_id)
        self.template_box.setCurrentIndex(i)

    def template(self) -> dict | None:
        return self.template_box.currentData()

    def add_template(self, path: Path) -> None:
        try:
            t = self.svc.add_template(path)
        except ValueError as err:
            return self._say(str(err))
        self.refresh_templates(t["id"])
        self._say(f"양식을 추가했어요: {t['name']}")

    def _show_hint(self) -> None:
        t = self.template()
        if not t:
            self.hint.setText("양식이 없어요. '새 양식 추가…'로 워드 양식(.docx)을 넣으세요. 양식 안에 {{칸 이름}}을 적어 두면 그 자리에 값이 들어가요.")
        else:
            try:
                slots = self.svc.template_slots(t["path"])
            except ValueError as err:
                return self.hint.setText(str(err))
            order = "  ".join(f"{i}.{{{{{s}}}}}" for i, s in enumerate(slots, 1)) or "없음 — 양식에 {{칸 이름}}을 적어 두세요"
            self.hint.setText("데이터 한 줄이 문서 하나예요. 값을 왼쪽부터 순서대로 양식의 빈칸에 넣어 docx로 만들어요"
                              "(첫 줄이 제목이면 '첫 줄은 제목'을 체크하세요).\n이 양식의 빈칸 순서: " + order)

    # ── 불러오기·확인 ──
    def set_data(self, path: Path, ask: bool = True) -> None:
        """데이터 파일을 고르면 앞부분을 보여 주는 창을 띄워 '첫 줄은 제목'을 정하고 값을 확인받는다(엑셀의 텍스트 불러오기처럼)."""
        path = Path(path)
        if ask:
            header = self._data_dialog(path)
            if header is None:
                return
            self.header = header
        self.data_path = path
        self.data_label.setText(path.name + ("  (첫 줄은 제목)" if self.header else ""))
        self.data_sub.setText(str(path.parent))

    def _data_dialog(self, path: Path) -> bool | None:
        """-> 첫 줄을 제목으로 볼지(그만두면 None)."""
        try:
            rows = self.svc.preview_data(path)
        except ValueError as err:
            self._say(str(err))
            return None
        dlg = QDialog(self)
        dlg.setWindowTitle("데이터 확인")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"{path.name} — 앞부분 {len(rows)}줄이에요. 한 줄이 문서 하나가 돼요."))
        width = max((len(r) for r in rows), default=0)
        table = QTableWidget(len(rows), width)
        for i, r in enumerate(rows):
            for j, v in enumerate(r):
                table.setItem(i, j, QTableWidgetItem(v))
        lay.addWidget(table, 1)
        box = QCheckBox("첫 줄은 제목(건너뛰기)")
        box.setChecked(self.header)
        json_file = path.suffix.lower() == ".json"
        box.setEnabled(not json_file)      # JSON(읽기 결과)에는 제목 줄이 없다

        def mark():
            for j in range(width):
                if table.item(0, j):
                    table.item(0, j).setBackground(QColor(theme.LINE) if box.isChecked() else QColor(theme.CARD))
        box.toggled.connect(mark)
        mark()
        lay.addWidget(box)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.resize(640, 360)
        if dlg.exec() != QDialog.Accepted:
            return None
        return box.isChecked() and not json_file

    def load(self) -> None:
        t = self.template()
        if not self.data_path:
            return self._say("데이터 파일을 먼저 고르세요")
        if not t:
            return self._say("양식을 고르세요 — '새 양식 추가…'로 워드 양식(.docx)을 넣을 수 있어요")
        self._say("불러오는 중…")
        QApplication.processEvents()
        try:
            self.docs = self.svc.prepare_make(self.data_path, template=t["path"], header=self.header)
        except ValueError as err:
            self.docs = []
            self._fill_tree()
            return self._say(str(err))
        self._fill_tree()
        self._show_hint()
        notes = [f"· {d['name']}: {n}" for d in self.docs for n in d.get("notes", [])]
        self._say(f"{len(self.docs)}건을 불러왔어요. 값을 확인하고 고친 뒤 만들기를 누르세요." + ("\n" + "\n".join(notes) if notes else ""))

    def _fill_tree(self, problems: dict | None = None) -> None:
        self.tree.clear()
        labels = self.svc.labels()
        for d in self.docs:
            probs = (problems or {}).get(d["name"], d["problems"])
            top = QTreeWidgetItem([d["name"], f"값 {sum(bool(v) for v in d['values'].values())}개", " / ".join(probs + d.get("notes", []))])
            top.setFlags(top.flags() | Qt.ItemIsUserCheckable)
            top.setCheckState(0, Qt.Unchecked if d["problems"] else Qt.Checked)
            top.setData(0, Qt.UserRole, d["name"])
            for k, v in d["values"].items():
                child = QTreeWidgetItem([labels["field"].get(k, k), str(v), FILLED_NOTE if k in d.get("filled", []) else ""])
                child.setFlags(child.flags() | Qt.ItemIsEditable)
                child.setData(0, Qt.UserRole, k)
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)

    def posted(self) -> list[dict]:
        """화면에서 고친 값 → submit_make에 넘길 형식."""
        out = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            vals = {top.child(j).data(0, Qt.UserRole): top.child(j).text(1) for j in range(top.childCount())}
            out.append({"name": top.data(0, Qt.UserRole), "make": top.checkState(0) == Qt.Checked, "values": vals})
        return out

    # ── 만들기 ──
    def make(self, out_dir: Path | None = None) -> dict | None:
        out_dir = Path(out_dir) if out_dir else self.out_dir
        if not self.docs:
            return self._say("데이터를 먼저 불러오세요")
        if not out_dir:
            return self._say("저장 폴더를 고르세요")
        name = self.name_box.text().strip() or "문서"
        posted = self.posted()
        exist = [p for p in self.svc.made_paths(self.docs, posted, out_dir, name) if p.exists()]
        if exist and QMessageBox.question(self, "덮어쓰기", f"같은 이름의 문서가 {len(exist)}개 있어요({exist[0].name} 등). 덮어쓸까요?") != QMessageBox.Yes:
            return self._say("만들지 않았어요 — 파일 이름을 바꾸거나 다른 폴더를 고르세요")
        try:
            res = self.svc.submit_make(self.docs, posted, out_dir, name=name)
        except ValueError as err:
            return self._say(str(err))
        if res["problems"]:
            self._fill_tree(res["problems"])
            self._say("고칠 칸이 있어 만들지 않았어요: " + ", ".join(res["problems"]))
            return res
        self.made = [r["path"] for r in res["results"] if r["path"]]
        self.made_base = self.svc._made_name(name)
        self._say(f"{len(self.made)}건을 만들었어요 (워드 .docx) — {out_dir}\nPDF나 그림이 필요하면 아래 버튼을 누르세요.")
        return res

    def convert(self, kind: str) -> list[Path] | None:
        """마지막으로 만든 문서들을 같은 폴더에 PDF 또는 그림(PNG)으로도 저장."""
        name = "PDF" if kind == "pdf" else "그림"
        if not self.made:
            return self._say("먼저 [만들기]를 누르세요")
        # 저장하기 전에 이름을 고칠 수 있다. 여러 건이면 그 이름 뒤에 워드 파일과 같은 번호가 붙는다
        one = len(self.made) == 1
        default = Path(self.made[0]).stem if one else self.made_base
        base, ok = QInputDialog.getText(self, f"{name}로 내보내기", "파일 이름" + ("" if one else "(뒤에 1, 2 … 번호가 붙어요)") + ":",
                                        text=default)
        if not ok:
            return None
        base = base.strip() or default
        names = [base] if one else [base + Path(p).stem[len(self.made_base):] for p in self.made]
        self._say(f"{name}로 바꾸는 중… (Word를 화면 없이 써서 몇 초 걸려요)")
        QApplication.processEvents()
        try:
            out = self.svc.convert_made(self.made, kind, names=names)
        except ValueError as err:
            return self._say(str(err))
        self._say(f"{name} {len(out)}개를 저장했어요 — {out[0].parent}")
        return out

    # ── 파일 고르기 창 ──
    def _pick_data(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "데이터 고르기", "", DATA_FILTER)
        if p:
            self.set_data(Path(p))

    def _pick_template(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "새 양식 고르기", "", "워드 양식 (*.docx)")
        if p:
            self.add_template(Path(p))

    def _remove_template(self) -> None:
        t = self.template()
        if not t:
            return
        if QMessageBox.question(self, "양식 지우기", f"'{t['name']}' 양식을 지울까요? 양식 폴더에서 파일이 삭제돼요.") != QMessageBox.Yes:
            return
        try:
            self.svc.remove_template(t["id"])
        except ValueError as err:
            return self._say(str(err))
        self.refresh_templates()
        self._say(f"양식을 지웠어요: {t['name']}")

    def _pick_out(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "저장 폴더 고르기")
        if p:
            self.out_dir = Path(p)
            self.out_label.setText(p)

    def _say(self, text: str) -> None:
        self.message.setText(text)
