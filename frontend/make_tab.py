"""문서 만들기 탭: 데이터 넣기 → 양식 고르기(원래 있던 양식 또는 새 워드 양식) → 값 확인·수정 → 만들기.

화면은 DesktopService만 부른다. 오류는 서비스가 주는 한국어 문구를 그대로 보여 준다.
"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

DATA_FILTER = "데이터 (*.json *.csv *.xlsx *.txt)"
FILLED_NOTE = "비어 있어 임의 값으로 채움 — 확인하세요"


class MakeTab(QWidget):
    def __init__(self, svc, parent=None):
        super().__init__(parent)
        self.svc, self.data_path, self.out_dir, self.docs = svc, None, None, []

        self.data_label = QLabel("데이터 파일을 고르세요")
        self.template_box = QComboBox()
        self.template_box.setAccessibleName("양식")
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.out_label = QLabel("저장 폴더를 고르세요")
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["문서 / 칸", "값", "알림"])
        self.tree.setColumnWidth(0, 220)
        self.tree.setColumnWidth(1, 260)
        self.message = QPlainTextEdit(readOnly=True)
        self.message.setMaximumHeight(110)

        buttons = {name: QPushButton(name) for name in ("데이터 고르기", "새 양식 추가…", "양식 지우기", "불러오기", "저장 폴더 고르기", "만들기")}
        buttons["데이터 고르기"].clicked.connect(self._pick_data)
        buttons["새 양식 추가…"].clicked.connect(self._pick_template)
        buttons["양식 지우기"].clicked.connect(self._remove_template)
        buttons["불러오기"].clicked.connect(self.load)
        buttons["저장 폴더 고르기"].clicked.connect(self._pick_out)
        buttons["만들기"].clicked.connect(lambda: self.make())
        self.template_box.currentIndexChanged.connect(self._show_hint)

        grid = QGridLayout()
        grid.addWidget(QLabel("1. 데이터"), 0, 0)
        grid.addWidget(self.data_label, 0, 1)
        grid.addWidget(buttons["데이터 고르기"], 0, 2)
        grid.addWidget(QLabel("2. 양식"), 1, 0)
        grid.addWidget(self.template_box, 1, 1)
        row = QHBoxLayout()
        row.addWidget(buttons["새 양식 추가…"])
        row.addWidget(buttons["양식 지우기"])
        grid.addLayout(row, 1, 2)
        grid.addWidget(self.hint, 2, 1, 1, 2)
        grid.addWidget(buttons["불러오기"], 3, 2)
        grid.setColumnStretch(1, 1)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("3. 저장 폴더"))
        bottom.addWidget(self.out_label, 1)
        bottom.addWidget(buttons["저장 폴더 고르기"])
        bottom.addWidget(buttons["만들기"])

        lay = QVBoxLayout(self)
        lay.addLayout(grid)
        lay.addWidget(self.tree, 1)
        lay.addLayout(bottom)
        lay.addWidget(self.message)
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
            self.hint.setText("")
        elif t["kind"] == "builtin":
            self.hint.setText("프로그램에 들어 있는 양식으로 PDF를 만들어요. 데이터에 없는 칸은 빈칸으로 둬요.")
        else:
            dtype = next((d["doc_type"] for d in self.docs if d.get("doc_type")), None)
            keys = "  ".join("{{" + k + "}}" for k in self.svc.template_keys(dtype)) if dtype else ""
            self.hint.setText("워드 양식 안의 {{칸 이름}} 자리에 값을 넣어 docx로 만들어요." + (f"\n쓸 수 있는 이름: {keys}" if keys else ""))

    # ── 불러오기·확인 ──
    def set_data(self, path: Path) -> None:
        self.data_path = Path(path)
        self.data_label.setText(self.data_path.name)

    def load(self) -> None:
        t = self.template()
        if not self.data_path:
            return self._say("데이터 파일을 먼저 고르세요")
        if not t:
            return self._say("양식을 고르세요")
        self._say("불러오는 중… (보통 문장으로 쓴 txt면 AI가 글을 읽어서 수십 초 걸려요)")
        QApplication.processEvents()
        try:
            self.docs = (self.svc.prepare_make(self.data_path, doc_type=t["doc_type"]) if t["kind"] == "builtin"
                         else self.svc.prepare_make(self.data_path, template=t["path"]))
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
            top = QTreeWidgetItem([d["name"], labels["doc_type"].get(d["doc_type"], d["doc_type"] or "알 수 없음"), " / ".join(probs)])
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
        try:
            res = self.svc.submit_make(self.docs, self.posted(), out_dir)
        except ValueError as err:
            return self._say(str(err))
        if res["problems"]:
            self._fill_tree(res["problems"])
            self._say("고칠 칸이 있어 만들지 않았어요: " + ", ".join(res["problems"]))
            return res
        lines = [f"{r['name']}: {r['path'] or '실패'}" + (f" ({r['message']})" if r["message"] else "") for r in res["results"]]
        self._say(f"{sum(bool(r['path']) for r in res['results'])}건을 만들었어요\n" + "\n".join(lines))
        return res

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
        self.message.setPlainText(text)
