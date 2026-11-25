"""화면(web_api.py)이 부르는 유일한 입구. 화면은 bridgedocs.py를 직접 부르지 않는다.

시작 순서(화면 쪽):
    import offline
    models = offline.setup_offline(offline.app_base())   # torch·paddle을 불러오기 전에
    from service import DesktopService
    svc = DesktopService(models)

오류는 화면에 그대로 보여 줄 수 있는 한국어 문구의 ValueError로 알린다.
"""
import threading
import zipfile
from pathlib import Path

import offline

offline.ensure_offline()   # torch·transformers·paddle을 불러오기 전에(이미 설정했으면 그대로 둠)

from PySide6.QtCore import QThread, QTimer, Qt  # noqa: E402

import bridgedocs as bd  # noqa: E402
from DB import storage  # noqa: E402
from Fuc.export import export  # noqa: E402
from worker import Engine, ReadJob  # noqa: E402


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

    @staticmethod
    def default_out_dir() -> Path:
        """설치할 때 정한 저장 폴더(설정.json의 '저장 폴더'). 읽기 결과 JSON과 내보내기 파일의 기본 자리."""
        import os
        return Path(os.environ.get("BD_OUT") or Path.home() / "Documents" / "BridgeDocs 결과")

    def start_read(self, files: list[Path], out_dir: Path, deskew: bool = False, want: list[str] | None = None,
                   group: bool = True) -> ReadJob:
        """작업을 만들어 돌려준다. 스레드는 화면의 이벤트 루프로 돌아간 뒤에 시작하므로,
        돌려받은 직후에 신호(progress·doc_done·doc_failed·finished)를 연결하면 하나도 놓치지 않는다.
        읽는 중에 또 부르면 거절하지 않고 대기열 뒤에 붙인다(job.queue_position: 앞에 남은 작업 수).
        지원하지 않거나 없는 파일은 그 파일만 doc_failed(파일 이름, 사유)로 돌려보내고 나머지는 읽는다.
        취소: job.cancel() — 지금 읽는 문서도 바로 멈춘다(끝난 문서의 JSON은 남음).
        want: 사용자가 고른 칸 이름들(비면 AI가 정함). group=False: 쪽 번호 묶음을 거절 → 이미지마다 따로 읽는다."""
        ok, rejected = self._check_read(files)
        job = self._make_job(ok, Path(out_dir), deskew)
        job.want = [w.strip() for w in (want or []) if w.strip()]
        if not group:
            job.groups = [[f] for f in ok]
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

    def save_custom_type(self, name: str, labels: list[str], kinds: dict | None = None) -> dict:
        """읽기 결과의 new_type이 True인 문서: 사용자가 칸 목록을 고치고 확정하면 저장한다. 다음에 같은 종류는 이 칸들을 읽는다."""
        try:
            return bd.save_custom_type(name, labels, kinds)
        except OSError as err:
            raise ValueError(f"새 문서 종류를 저장할 수 없음 — {bd.custom_path()} ({err.strerror or err})") from err

    def delete_custom_type(self, name: str) -> dict:
        """확정해 둔 새 문서 종류를 지운다. 고치려면 save_custom_type을 같은 이름으로 다시 부른다(덮어씀)."""
        try:
            return bd.delete_custom_type(name)
        except OSError as err:
            raise ValueError(f"새 문서 종류를 지울 수 없음 — {bd.custom_path()} ({err.strerror or err})") from err

    @staticmethod
    def save_doc(doc: dict) -> None:
        """확인 중인 문서(사용자가 확정·고친 값 포함)를 읽을 때 만든 JSON에 다시 저장한다(다른 문서로 넘어갈 때 자동 저장)."""
        storage.save_result(doc)

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

    def export(self, docs: list[dict], kinds: list[str], path_stem: Path, append: bool = False, sep: str = "\t",
               force: bool = False, only: list[str] | None = None) -> list[Path]:
        """kinds: "xlsx"·"csv"·"txt" 중에서. append=True면 같은 이름의 기존 파일 뒤에 이어 붙인다(같은 문서 종류만).
        새 파일로 할지 이어 붙일지는 화면이 저장할 때마다 사용자에게 묻는다. JSON을 고르면 저장된 폴더를 알려 주는 ValueError.
        sep: TXT 구분 기호. force=True: 확인 필요 칸이 남아도 사용자가 승인했으니 내보낸다.
        only: 이 칸 이름들만 내보낸다(여러 문서를 한 파일로 묶을 때 사용자가 고른 칸)."""
        return export.export_files(docs, kinds, path_stem, append, sep=sep, force=force, only=only)

    @staticmethod
    def field_labels(docs: list[dict]) -> list[str]:
        """여러 문서의 칸 이름(처음 나온 순서, 중복 없이): 한 번에 내보내기에서 고를 목록."""
        return list(dict.fromkeys(f["label"] for d in docs for f in d["fields"]))

    # ── 문서 관리(워드 양식, 모델 필요 없음) ──
    def list_templates(self) -> list[dict]:
        """문서 관리 화면의 양식: 사용자가 넣은 워드 양식만(고정 기본 양식은 없음, 2026-11-06 결정).
        -> [{"id", "name", "kind": "docx", "doc_type": None, "path"}]"""
        folder = bd.forms_dir()
        docx = sorted(folder.glob("*.docx")) if folder and folder.is_dir() else []
        return [{"id": str(p), "name": p.name, "kind": "docx", "doc_type": None, "path": p} for p in docx if not p.name.startswith("~$")]

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
        p = Path(template_id)
        if p.parent != bd.forms_dir() or p.suffix.lower() != ".docx":    # 양식 폴더 밖의 파일은 지우지 않는다
            raise ValueError(f"양식 폴더의 워드 양식만 지울 수 있어요 — {p.name}")
        p.unlink(missing_ok=True)

    def template_slots(self, template: Path) -> list[str]:
        """양식 안 빈칸({{…}}) 이름들, 문서에 나오는 순서대로. 데이터 값이 이 순서로 들어간다."""
        try:
            return bd.template_slots(Path(template))
        except (OSError, KeyError, zipfile.BadZipFile, UnicodeDecodeError) as err:
            raise ValueError(f"워드 양식을 읽을 수 없음 — {Path(template).name} ({err})") from err

