"""화면(frontend/)이 부르는 유일한 입구. 화면은 bridgedocs.py를 직접 부르지 않는다.

시작 순서(화면 쪽):
    from desktop import offline
    models = offline.setup_offline(offline.app_base())   # torch·paddle을 불러오기 전에
    from desktop.service import DesktopService             # 개발 중에는 desktop.fake.FakeService
    svc = DesktopService(models)

오류는 화면에 그대로 보여 줄 수 있는 한국어 문구의 ValueError로 알린다.
"""
import threading
import zipfile
from pathlib import Path

from desktop import offline

offline.ensure_offline()   # torch·transformers·paddle을 불러오기 전에(이미 설정했으면 그대로 둠)

from PySide6.QtCore import QThread, QTimer, Qt  # noqa: E402

import bridgedocs as bd  # noqa: E402
from desktop import export  # noqa: E402
from desktop.worker import Engine, ReadJob  # noqa: E402


class DesktopService:
    def __init__(self, models_dir: Path | None = None, engine=None, process=None):
        self.models_dir = Path(models_dir) if models_dir else offline.ensure_offline()
        self.engine = engine or Engine()
        self.process = process
        self._running = []     # (QThread, ReadJob, launch): 끝나기 전에 사라지지 않게 잡아 둔다
        self._launch_lock = threading.Lock()

    # ── 상태·표시용 이름 ──
    def status(self) -> dict:
        missing = offline.missing_models(self.models_dir)
        if missing:
            msg = (f"모델 파일이 없습니다: {', '.join(missing)} ({self.models_dir}). "
                   "문서 읽기는 쓸 수 없고, 문서 만들기는 모델 없이 쓸 수 있어요.")
        else:
            msg = "준비됨"
        return {"models_ok": not missing, "missing": missing, "message": msg}

    def labels(self) -> dict:
        """화면에 보일 한국어 이름들(복사본: 바꿔도 엔진에는 영향 없음)."""
        return {k: dict(v) for k, v in {"status": bd.STATUS_LABEL, "doc_type": bd.DOC_TYPE_LABEL, "field": bd.FIELD_LABEL,
                                         "route": bd.ROUTE_LABEL, "kind_hint": bd.KIND_HINT}.items()}

    def supported_suffixes(self) -> list[str]:
        return list(bd.SUPPORTED)

    # ── 문서 읽기 ──
    def preview_groups(self, files: list[Path]) -> dict:
        """읽기 전에 보여 줄 묶음. 'X_p1.png, X_p2.png'는 한 문서로 묶는다. 화면은 이걸 보여 주고 확인받은 뒤 start_read를 부른다.
        -> {"groups": [{"name", "files": [Path]}], "rejected": [{"file": Path, "reason"}]}"""
        ok, rejected = self._split(files)
        return {"groups": [{"name": ReadJob._stem(g), "files": g} for g in bd.group_documents(ok)],
                "rejected": [{"file": f, "reason": r} for f, r in rejected]}

    def start_read(self, files: list[Path], out_dir: Path, deskew: bool = False) -> ReadJob:
        """작업을 만들어 돌려준다. 스레드는 화면의 이벤트 루프로 돌아간 뒤에 시작하므로,
        돌려받은 직후에 신호(progress·doc_done·doc_failed·finished)를 연결하면 하나도 놓치지 않는다.
        읽는 중에 또 부르면 거절하지 않고 대기열 뒤에 붙인다(job.queue_position: 앞에 남은 작업 수).
        지원하지 않거나 없는 파일은 그 파일만 doc_failed(파일 이름, 사유)로 돌려보내고 나머지는 읽는다.
        취소: job.cancel() — 지금 읽는 문서도 바로 멈춘다(끝난 문서의 JSON은 남음)."""
        ok, rejected = self._check_read(files)
        job = self._make_job(ok, Path(out_dir), deskew)
        job.rejected = [(Path(f).name, r) for f, r in rejected]
        return self._start(job)

    @staticmethod
    def _split(files) -> tuple[list[Path], list[tuple[Path, str]]]:
        ok, bad = [], []
        for f in map(Path, files):
            if not f.exists():
                bad.append((f, "파일이 없음"))
            elif f.suffix.lower() not in bd.SUPPORTED:
                bad.append((f, f"지원하지 않는 파일 (지원: {', '.join(bd.SUPPORTED)})"))
            else:
                ok.append(f)
        return ok, bad

    def _check_read(self, files) -> tuple[list[Path], list[tuple[Path, str]]]:
        if not files:
            raise ValueError("읽을 파일을 고르세요")
        ok, bad = self._split(files)
        if not ok:
            raise ValueError("없거나 지원하지 않는 파일: " + ", ".join(str(f) for f, _ in bad) + f" (지원: {', '.join(bd.SUPPORTED)})")
        st = self.status()
        if not st["models_ok"]:     # 모델이 빠지면 PaddleX가 내려받기를 시도한다 → 시작하지 않는다
            raise ValueError(st["message"])
        return ok, bad

    def _make_job(self, files: list[Path], out_dir: Path, deskew: bool) -> ReadJob:
        return ReadJob(files, out_dir, deskew, self.engine, process=self.process)

    def busy(self) -> bool:
        return any(not t.isFinished() for t, _, _ in self._running)

    def _start(self, job: ReadJob) -> ReadJob:
        thread = QThread()
        job.moveToThread(thread)
        thread.started.connect(job.run)
        # 바로 연결: 메인 스레드가 wait_all()로 기다리는 중에도 스레드가 끝나도록(대기열로 보내면 서로 막힘)
        job.finished.connect(thread.quit, Qt.DirectConnection)
        def launch():
            with self._launch_lock:     # 앞 작업 스레드와 wait_all()이 동시에 불러도 한 번만 시작
                if job.started:
                    return
                job.started = True
                thread.start()
                moved = job.queue_position > 0      # 앞 작업이 끝나서 시작: 뒤에서 기다리는 작업도 하나씩 당긴다
                waiting = [job] + [j for _, j, _ in self._running if not j.started] if moved else []
            for j in waiting:
                j.queue_position = max(0, j.queue_position - 1) if j is not job else 0
                j.queued.emit(j.queue_position)
        job.started = False
        ahead = [t for t, _, _ in self._running if not t.isFinished()]
        job.queue_position = len(ahead)
        self._running.append((thread, job, launch))
        if ahead:      # 대기열: 바로 앞 작업이 끝나면 시작(이미 끝났으면 아래 타이머가 시작)
            ahead[-1].finished.connect(launch, Qt.DirectConnection)
            if ahead[-1].isFinished():
                QTimer.singleShot(0, launch)
        else:
            QTimer.singleShot(0, launch)   # 화면이 신호를 연결할 틈을 준다
        return job

    def wait_all(self, ms: int = 600000) -> None:
        """창을 닫을 때: 진행 중·대기 중인 작업을 모두 취소하고 끝날 때까지 기다린다."""
        for thread, job, launch in list(self._running):
            job.cancel()
            launch()        # 아직 시작 전이면 바로 시작(취소 상태라 곧 끝남)
            thread.wait(ms)
        self._running = [r for r in self._running if not r[0].isFinished()]

    # ── 새 문서 종류(AI가 처음 보는 문서) ──
    def custom_types(self) -> dict:
        """사용자가 확정한 새 문서 종류: {종류 이름: [칸 이름]}."""
        return bd.custom_types()

    def save_custom_type(self, name: str, labels: list[str]) -> dict:
        """읽기 결과의 new_type이 True인 문서: 사용자가 칸 목록을 고치고 확정하면 저장한다. 다음에 같은 종류는 이 칸들을 읽는다."""
        try:
            return bd.save_custom_type(name, labels)
        except OSError as err:
            raise ValueError(f"새 문서 종류를 저장할 수 없음 — {bd.custom_path()} ({err.strerror or err})") from err

    def page_images(self, doc: dict, width: int = 900) -> list[dict]:
        """쪽 이미지: [{"src": base64 JPEG(가로 width), "w": 원래 가로, "h": 원래 세로}]. 칸의 line_box는 원래 크기 좌표.
        원본 파일이 없거나 docx면 []."""
        return bd.page_images(doc, width)

    # ── 판별·내보내기 ──
    parse_value = staticmethod(export.parse_value)
    decide = staticmethod(export.decide)
    decide_all = staticmethod(export.decide_all)
    undo = staticmethod(export.undo)
    review_left = staticmethod(export.review_left)

    json_folders = staticmethod(export.json_folders)
    read_rows = staticmethod(export.read_rows)

    def export(self, docs: list[dict], kinds: list[str], path_stem: Path, append: bool = False) -> list[Path]:
        """kinds: "xlsx"·"csv"·"txt" 중에서. append=True면 같은 이름의 기존 파일 뒤에 이어 붙인다(같은 문서 종류만).
        새 파일로 할지 이어 붙일지는 화면이 저장할 때마다 사용자에게 묻는다. JSON을 고르면 저장된 폴더를 알려 주는 ValueError."""
        return export.export_files(docs, kinds, path_stem, append)

    # ── 문서 만들기(모델 필요 없음) ──
    def list_templates(self) -> list[dict]:
        """문서 만들기에서 고를 양식. 원래 있던 양식(기본 양식, 쓸 수 있는 것만) + 사용자가 넣은 워드 양식.
        -> [{"id", "name", "kind": "builtin"|"docx", "doc_type"(기본 양식만), "path"(워드 양식만)}]"""
        out = [{"id": f"builtin:{t}", "name": f"{bd.DOC_TYPE_LABEL.get(t, t)} (기본 양식)", "kind": "builtin", "doc_type": t, "path": None}
               for t in bd.MAKE_TYPES if t not in bd.NEEDS_FORM or bd.form_file(t)]
        folder = bd.forms_dir()
        docx = sorted(folder.glob("*.docx")) if folder and folder.is_dir() else []
        return out + [{"id": str(p), "name": p.name, "kind": "docx", "doc_type": None, "path": p} for p in docx if not p.name.startswith("~$")]

    def add_template(self, path: Path) -> dict:
        """새 워드 양식을 프로그램의 양식 폴더로 복사해 둔다(다음에 켜도 남음). 같은 이름이 있으면 ' (2)'를 붙인다."""
        path = Path(path)
        if path.suffix.lower() != ".docx" or not path.is_file():
            raise ValueError(f"워드 양식(.docx) 파일을 고르세요 — {path.name}")
        folder = bd.forms_dir()
        target, n = folder / path.name, 1
        while target.exists():
            n += 1
            target = folder / f"{path.stem} ({n}).docx"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
        except OSError as err:
            raise ValueError(f"양식을 저장할 수 없음 — {folder} ({err.strerror or err})") from err
        return next(t for t in self.list_templates() if t["id"] == str(target))

    def remove_template(self, template_id: str) -> None:
        if template_id.startswith("builtin:"):
            raise ValueError("기본 양식은 지울 수 없어요")
        p = Path(template_id)
        if p.parent != bd.forms_dir() or p.suffix.lower() != ".docx":    # 양식 폴더 밖의 파일은 지우지 않는다
            raise ValueError(f"양식 폴더의 워드 양식만 지울 수 있어요 — {p.name}")
        p.unlink(missing_ok=True)

    def template_keys(self, doc_type: str) -> list[str]:
        """워드 양식 안에 {{이름}}으로 쓸 수 있는 이름들(칸 표기와 키 둘 다, 그리고 파일·문서 종류·오늘)."""
        dtype = bd.resolve_type(doc_type)
        if not dtype:
            raise ValueError(f"모르는 문서 종류 '{doc_type}' (가능: {', '.join(bd.DOC_FIELDS)})")
        keys = [k for k in bd.DOC_FIELDS[dtype] if k not in bd.COMPUTED]
        return ["파일", "문서 종류", "오늘"] + [n for k in keys for n in (bd.FIELD_LABEL.get(k, k), k)]

    def prepare_make(self, data_path: Path, template: Path | None = None, doc_type: str | None = None, seed: int = 7) -> list[dict]:
        """데이터 → 만들 문서 목록(아직 파일은 안 씀). 화면은 이걸 보여 주고 사용자가 확인·수정하게 한다."""
        data_path = Path(data_path)
        if not data_path.exists():
            raise ValueError(f"데이터 파일이 없음 — {data_path}")
        if template and (not Path(template).exists() or Path(template).suffix.lower() != ".docx"):
            raise ValueError(f"양식은 있는 .docx 파일이어야 함 — {template}")
        dtype = bd.resolve_type(doc_type) if doc_type else None
        if doc_type and not dtype:
            raise ValueError(f"모르는 문서 종류 '{doc_type}' (가능: {', '.join(bd.DOC_FIELDS)})")
        try:
            recs = bd.load_records(data_path, dtype)
        except (OSError, ValueError, IndexError, KeyError, zipfile.BadZipFile, UnicodeDecodeError) as e:
            raise ValueError(f"데이터 파일을 읽을 수 없음 — {data_path.name} ({type(e).__name__}: {e})") from e
        if data_path.suffix.lower() == ".txt" and not any(r["values"] for r in recs):      # 표·'칸: 값' 형식이 아닌 보통 글
            types = list(bd.DOC_FIELDS) if template else bd.MAKE_TYPES
            recs = [bd.text_record(bd.read_text_any(data_path), self._ask_text, dtype, bd.safe_name(data_path.stem, 1), types)]
        if not recs:
            raise ValueError("데이터가 비어 있음")
        if dtype:      # 기본 양식을 골랐으면 그 종류로 만든다. 데이터가 다른 종류면 값 칸이 맞지 않으니 막는다
            other = {r["doc_type"] for r in recs if r["doc_type"] and r["doc_type"] != dtype}
            if other:
                raise ValueError(f"데이터는 {', '.join(bd.DOC_TYPE_LABEL.get(t, t) for t in other)}인데 "
                                 f"고른 양식은 {bd.DOC_TYPE_LABEL.get(dtype, dtype)}이에요 — 맞는 양식을 고르세요")
            recs = [{**r, "doc_type": dtype} for r in recs]
        tp = str(template) if template else None     # submit_make가 같은 양식을 쓰도록 문서에 기억
        return [{**d, "template_path": tp} for d in bd.prepare_docs(recs, template, seed)]

    def _ask_text(self, prompt: str) -> str:
        """글만 주고 AI에게 묻기(보통 글로 쓴 txt 데이터용). 처음이면 모델을 불러와 수십 초 걸린다.
        shortcut: 화면 스레드에서 돈다(그동안 창이 멈춤) — 자주 쓰게 되면 ReadJob처럼 작업 스레드로."""
        if self.busy():
            raise ValueError("문서를 읽는 중이에요 — AI를 같이 써서, 읽기가 끝난 뒤에 다시 불러오세요")
        if not self.status()["models_ok"]:
            raise ValueError("보통 문장으로 쓴 txt는 AI가 읽어야 하는데 모델이 없어요 — '칸: 값' 줄 형식(예: 임대인: 김철수)으로 써 주세요")
        model, proc, _ = self.engine.get()
        return bd.ask_text(model, proc, prompt, max_new=400)

    def preview_make(self, doc: dict, values: dict, width: int = 900) -> dict:
        """만들어질 문서 미리보기와 고칠 곳 표시. 비어 있거나 형식이 틀린 칸마다 마크(문서 위 상자 위치)를 준다.
        -> {"pages": [{"src", "w", "h"}], "marks": [{"key", "label", "reason", "page", "box": [x0, y0, x1, y1](원래 크기 px) 또는 None,
                                                    "boxes": [{"page", "box"}](그 값에서 따라 나온 글자 자리까지 모두)}]}
        워드 양식으로 만드는 문서는 그림을 그릴 수 없어 pages가 []이고 마크의 page·box는 None."""
        dtype = doc["doc_type"]
        known = bd.DOC_FIELDS.get(dtype)
        if known is None:
            raise ValueError("문서 종류를 알 수 없어 미리 볼 수 없어요")
        vals = {k: str(v) for k, v in values.items() if k in known}
        reasons = {}
        for k in known:
            if k in bd.COMPUTED:
                continue
            if not vals.get(k, "").strip():
                reasons[k] = "비어 있음 — 빈칸으로 만들어져요"
                continue
            _, probs = bd.check_record({"name": doc["name"], "doc_type": dtype, "values": {k: vals[k]}, "unknown": []})
            if probs:
                reasons[k] = probs[0]
        if doc.get("template_path") or doc.get("template") or dtype not in bd.MAKE_TYPES:
            return {"pages": [], "marks": [{"key": k, "label": bd.FIELD_LABEL.get(k, k), "reason": r, "page": None, "box": None}
                                           for k, r in reasons.items()]}
        draw = {k: v for k, v in vals.items() if k not in reasons}      # 비었거나 틀린 칸은 빈칸으로 그리고 상자로 표시
        try:
            pages, spots, _ = bd.render_doc(dtype, draw)
        except (OSError, ValueError, KeyError, IndexError, TypeError) as err:
            raise ValueError(f"미리보기를 만들 수 없음 — {err}") from err
        marks = []
        for k, r in reasons.items():
            where = spots.get(k) or [(None, None)]
            marks.append({"key": k, "label": bd.FIELD_LABEL.get(k, k), "reason": r, "page": where[0][0], "box": where[0][1],
                          "boxes": [{"page": pg, "box": bx} for pg, bx in where if bx]})
        return {"pages": [bd.image_payload(p, width) for p in pages], "marks": marks}

    def submit_make(self, docs: list[dict], posted: list[dict], out_dir: Path, seed: int = 7) -> dict:
        """확인·수정한 값으로 문서를 만든다. posted: [{"name", "make": bool, "values": {칸 키: 값}}].
        양식은 prepare_make에서 고른 것(문서의 template_path)을 쓴다.
        -> {"problems": {이름: [문구]}, "results": [{"name", "path"(Path), "message"("")}]}.
        형식이 틀린 칸이 하나라도 있으면 아무 파일도 만들지 않고 problems로 알린다(화면이 preview_make로 자리를 보여 줌).
        빈칸은 빈칸으로 만든다. 파일을 만들다 실패하면 ValueError."""
        if not any(p.get("make") for p in posted):
            raise ValueError("만들 문서를 하나 이상 고르세요")
        tps = {d.get("template_path") for d in docs}
        if len(tps) > 1:
            raise ValueError("서로 다른 양식으로 준비한 문서가 섞여 있어요 — 데이터를 다시 불러오세요")
        tp = tps.pop() if tps else None
        todo = bd.apply_edits(docs, posted)
        bad = {d["name"]: d["problems"] for d in todo if d["problems"]}
        if bad:
            return {"problems": bad, "results": []}
        try:
            res = bd.write_docs(todo, Path(tp) if tp else None, Path(out_dir), seed)
        except OSError as err:
            raise ValueError(f"결과 폴더를 만들 수 없음 — {out_dir} ({err.strerror or err})") from err
        return {"problems": {}, "results": [{"name": n, "path": p, "message": m} for n, p, m in res]}
