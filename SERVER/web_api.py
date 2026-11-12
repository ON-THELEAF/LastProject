"""웹 화면(Front/src/) ↔ DesktopService. 화면은 POST /api/<이름> 에 JSON을 보내고 {"ok": 값} 또는 {"error": 한국어 문구}를 받는다.

브라우저는 파일 경로를 모르므로 파일은 /api/upload 로 내용을 보내 이 PC의 작업 폴더에 둔 뒤 그 경로를 쓴다.
폴더·저장 위치 고르기는 이 PC의 윈도 대화 상자(tkinter)를 띄운다.
"""
import html
import os
import re
import shutil
import threading
import uuid
import zipfile
from pathlib import Path

from PySide6.QtCore import Qt

UPLOADS = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BridgeDocs" / "uploads"


def docx_paragraphs(path: Path) -> list[str]:
    """워드 문서 본문의 문단 글자들(표 안 문단 포함, 순서대로). 문서 관리 미리보기용.
    shortcut: 문단 글자만 보여 준다(표·그림 모양은 없음). 모양까지 필요하면 convert_made처럼 Word로 그림을 만든다."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    paras = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
    texts = ["".join(re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", p)) for p in paras]
    return [html.unescape(t) for t in texts if t.strip()]


class Api:
    def __init__(self, svc):
        self.svc = svc
        self.events: list[dict] = []          # 읽기 작업 소식(화면이 since로 이어서 가져감)
        self.jobs: list = []
        self._read_lock = threading.Lock()    # 읽기 작업은 한 번에 하나(뒤에 넣은 작업은 기다림)
        self._dialog_lock = threading.Lock()
        # shortcut: 켤 때마다 지난번 올린 파일을 지운다(지난 읽기 결과 JSON은 남지만 쪽 이미지는 못 봄), 다시 열어 볼 기능이 생기면 바꾼다
        shutil.rmtree(UPLOADS, ignore_errors=True)

    # ── 파일 받기·대화 상자 ──
    def save_upload(self, batch: str, name: str, data: bytes) -> str:
        batch = "".join(c for c in batch if c.isalnum())[:40] or uuid.uuid4().hex
        name = Path(name).name.strip() or "file"         # 경로 글자를 빼서 작업 폴더 밖에 못 쓰게
        folder = UPLOADS / batch
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        target.write_bytes(data)
        return str(target)

    def _dialog(self, fn: str, **kw):
        import tkinter as tk
        from tkinter import filedialog
        with self._dialog_lock:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)     # 앱 창 뒤에 숨지 않게
            try:
                return getattr(filedialog, fn)(parent=root, **kw) or None
            finally:
                root.destroy()

    def pick_folder(self, initial: str = "") -> str | None:
        return self._dialog("askdirectory", initialdir=initial or str(self.svc.default_out_dir()), title="저장 폴더 고르기")

    def open_folder(self, path: str) -> None:
        p = Path(path)
        if not p.is_dir():
            raise ValueError(f"폴더가 없어요 — {p}")
        os.startfile(p)

    # ── 상태 ──
    def status(self) -> dict:
        return {**self.svc.status(), "out_dir": str(self.svc.default_out_dir())}

    # ── 문서 읽기 ──
    def read_start(self, files: list[str]) -> int:
        ok, bad = self.svc._check_read(files)       # 읽을 파일이 없거나 모델이 없으면 ValueError
        job = self.svc._make_job(ok, self.svc.default_out_dir(), False)
        job.rejected = [(Path(f).name, r) for f, r in bad]
        add = self.events.append
        job.progress.connect(lambda t: add({"type": "progress", "text": t}), Qt.DirectConnection)
        job.doc_done.connect(lambda d: add({"type": "done", "doc": d}), Qt.DirectConnection)
        job.doc_failed.connect(lambda n, r: add({"type": "failed", "name": n, "reason": r}), Qt.DirectConnection)
        job.finished.connect(lambda a, b: add({"type": "finished", "ok": a, "fail": b}), Qt.DirectConnection)
        self.jobs.append(job)

        def run():
            with self._read_lock:
                job.run()
        threading.Thread(target=run, daemon=True).start()
        return len(job.groups) + len(job.rejected)

    def read_events(self, since: int = 0) -> dict:
        return {"events": self.events[since:], "next": len(self.events)}

    def cancel_all(self) -> None:
        for job in self.jobs:
            job.cancel()

    def page_images(self, doc: dict) -> list[dict]:
        return self.svc.page_images(doc)

    def decide(self, field: dict, value: str, status: str) -> dict:
        return self.svc.decide(field, value, status)

    def decide_all(self, doc: dict) -> dict:
        return self.svc.decide_all(doc)

    def undo(self, field: dict) -> dict:
        return self.svc.undo(field)

    def parse_value(self, kind: str, value: str) -> dict:
        clean, err = self.svc.parse_value(kind, value)
        return {"value": clean, "error": err}

    def save_doc(self, doc: dict) -> None:
        self.svc.save_doc(doc)

    def save_custom_type(self, name: str, labels: list[str]) -> dict:
        return self.svc.save_custom_type(name, labels)

    def open_json_folder(self, docs: list[dict]) -> str:
        folders = self.svc.json_folders(docs) or [str(self.svc.default_out_dir())]
        self.open_folder(folders[0])
        return folders[0]

    # ── 내보내기 ──
    def export(self, docs: list[dict], kinds: list[str], append: bool, split: bool, review_file: bool,
               force: bool) -> list[str] | None:
        """저장 위치를 물은 뒤 내보낸다. 취소하면 None. split: 문서 종류마다 파일 따로. review_file: 확인 필요 칸만 따로 한 파일 더."""
        if not docs:
            raise ValueError("내보낼 문서가 없어요 — 먼저 문서를 읽으세요")
        left = self.svc.review_left(docs)
        if left and not force:
            raise ValueError(f"남은 확인 {left}칸 — 다 끝내야 내보낼 수 있어요")
        types = [("엑셀·CSV·TXT", "*.xlsx *.csv *.txt")]
        out = str(self.svc.default_out_dir())
        if append:
            picked = self._dialog("askopenfilename", initialdir=out, title="이어 붙일 파일 고르기", filetypes=types)
        else:
            picked = self._dialog("asksaveasfilename", initialdir=out, initialfile="추출 결과", title="저장할 이름 정하기",
                                  filetypes=types)
        if not picked:
            return None
        stem = Path(picked).with_suffix("") if Path(picked).suffix.lower() in (".xlsx", ".csv", ".txt") else Path(picked)
        groups = {}
        for d in docs:
            key = (d.get("doc_name") or d.get("doc_type") or "문서") if split else ""
            groups.setdefault(key, []).append(d)
        made = []
        for kind_name, ds in groups.items():
            target = stem.with_name(f"{stem.name}_{kind_name}") if kind_name else stem
            made += self.svc.export(ds, kinds, target, append=append, sep="|", force=True)
        if review_file:
            review = [{**d, "fields": [f for f in d["fields"] if f["status"] == "review"]} for d in docs]
            review = [d for d in review if d["fields"]]
            if review:
                made += self.svc.export(review, kinds, stem.with_name(f"{stem.name}_확인필요"), sep="|", force=True)
        return [str(p) for p in made]

    # ── 문서 관리(양식) ──
    def list_templates(self) -> list[dict]:
        out = []
        for t in self.svc.list_templates():
            p = Path(t["path"])
            try:
                slots, paras = self.svc.template_slots(p), docx_paragraphs(p)
                ok = True
            except (ValueError, OSError, KeyError, zipfile.BadZipFile, UnicodeDecodeError):
                slots, paras, ok = [], [], False
            out.append({"id": t["id"], "name": t["name"], "path": str(p), "slots": slots, "paragraphs": paras, "ok": ok,
                        "modified": p.stat().st_mtime})
        return out

    def add_template(self, path: str) -> dict:
        return self.svc.add_template(Path(path))

    def remove_template(self, id: str) -> None:
        self.svc.remove_template(id)

    # ── 문서 만들기 ──
    def preview_data(self, path: str, n: int = 8) -> list[list[str]]:
        return self.svc.preview_data(Path(path), n)

    def prepare_make(self, data: str, template: str, header: bool) -> list[dict]:
        return self.svc.prepare_make(Path(data), Path(template), header)

    def make(self, data: str, header: bool, template: str, out_dir: str, name: str = "문서",
             values: dict | None = None, names: list[str] | None = None, kinds: list[str] | None = None) -> list[str]:
        """양식 하나로 데이터의 모든 줄을 만든다. values: {문서 이름: {칸: 값}}(화면에서 고친 값).
        names: 문서마다 사용자가 정한 파일 이름(확장자 없이). kinds: "docx"·"pdf"·"png"(docx를 안 고르면 변환 뒤 지움)."""
        docs = self.svc.prepare_make(Path(data), Path(template), header)
        posted = [{"name": d["name"], "make": True, "values": (values or {}).get(d["name"], {})} for d in docs]
        paths = [Path(r["path"]) for r in self.svc.submit_make(docs, posted, Path(out_dir), name)["results"]]
        if names:
            renamed = []
            for p, n in zip(paths, names):
                q = p.with_name(self.svc._made_name(n) + ".docx")
                os.replace(p, q)
                renamed.append(q)
            paths = renamed
        kinds = kinds or ["docx"]
        made = list(paths) if "docx" in kinds else []
        for k in ("pdf", "png"):
            if k in kinds:
                made += self.svc.convert_made(paths, k)
        if "docx" not in kinds:
            for p in paths:
                p.unlink(missing_ok=True)
        return [str(p) for p in made]

    def convert(self, paths: list[str], kind: str) -> list[str]:
        return [str(p) for p in self.svc.convert_made([Path(p) for p in paths], kind)]
