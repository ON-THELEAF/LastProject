"""문서 읽기 작업 스레드. 모델은 한 번만 읽어 두고, 문서마다 바로 JSON을 저장한다(도중에 닫아도 끝난 문서는 남음).

화면은 신호만 받는다: progress(문구), doc_done(결과 dict), doc_failed(이름, 사유), finished(성공 수, 실패 수).
취소하면 지금 읽는 문서도 바로 멈추고 그 문서 결과는 버린다.
"""
import json
import re
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

import bridgedocs as bd

QWEN = "Qwen/Qwen3-VL-4B-Instruct"


class Engine:
    """Qwen 모델과 OCR. 처음 get()에서만 읽어 들인다(수십 초·GPU 메모리 약 10GB)."""

    def __init__(self):
        self._parts = None
        self._lock = threading.Lock()

    def get(self):
        with self._lock:
            if self._parts is None:
                model, proc = bd.load(QWEN, "bfloat16")
                self._parts = (model, proc, bd.make_ocr())
            return self._parts


def _reason(err: Exception) -> str:
    text = str(err)
    if "out of memory" in text.lower() or type(err).__name__ == "OutOfMemoryError":
        return "GPU 메모리 부족 — 다른 프로그램(게임·영상 편집 등)을 닫고 다시 시도하세요"
    return f"처리 중 오류: {text}" if text else f"처리 중 오류: {type(err).__name__}"


def _free_gpu() -> None:
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


class ReadJob(QObject):
    progress = Signal(str)
    doc_done = Signal(dict)
    doc_failed = Signal(str, str)
    finished = Signal(int, int)
    queued = Signal(int)       # 대기 순번이 바뀔 때(0이면 이제 시작)

    def __init__(self, files: list[Path], out_dir: Path, deskew: bool, engine, process=None, rejected=()):
        super().__init__()
        self.groups = bd.group_documents(files)
        self.rejected = list(rejected)     # [(파일 이름, 사유)]: 읽지 않고 실패로 알린다
        self.queue_position = 0            # 0이면 바로 시작, n이면 앞에 작업 n개가 끝나야 시작
        self.out_dir = Path(out_dir)
        self.deskew = deskew
        self.engine = engine
        self.process = process or bd.process_document
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """지금 읽는 문서도 바로 멈춘다(그 문서 결과는 버림). 이미 끝난 문서의 JSON은 남는다."""
        self._cancel.set()

    @staticmethod
    def _stem(paths) -> str:
        return re.sub(r"_p\d+$", "", Path(paths[0]).stem)

    def run(self) -> None:
        self._counts = [0, 0]
        try:
            self._run()
        except Exception as err:  # 예상 못 한 오류: 남은 문서는 처리하지 않고 알린다(아래 finally가 finished를 보냄)
            self.progress.emit(f"작업이 중단됨 — {err}")
        finally:
            self.finished.emit(*self._counts)   # 화면이 '읽는 중'에 멈추지 않도록 항상 보낸다

    def _fail(self, stem: str, reason: str) -> None:
        self._counts[1] += 1
        self.doc_failed.emit(stem, reason)

    def _run(self) -> None:
        for name, reason in self.rejected:
            self._fail(name, reason)
        if self._cancel.is_set() or not self.groups:
            return
        try:
            self.progress.emit("모델 불러오는 중… (처음 한 번, 수십 초)")
            model, proc, ocr = self.engine.get()
        except Exception as err:  # 모델이 없거나 깨짐: 모든 문서를 실패로 알린다
            for paths in self.groups:
                self._fail(self._stem(paths), f"모델을 불러오지 못함 — {err}")
            return
        used = set()
        for i, paths in enumerate(self.groups, 1):
            if self._cancel.is_set():
                return
            stem = self._stem(paths)
            self.progress.emit(f"{i}/{len(self.groups)} 읽는 중: {stem}")
            try:
                r = self.process(paths, model, proc, ocr, deskew=self.deskew, stop=self._cancel.is_set)
            except bd.Cancelled:
                return
            except ValueError as err:
                self._fail(stem, str(err))
                continue
            except Exception as err:  # GPU 메모리 부족·손상 파일 등: 이 문서만 실패, 다음 문서는 계속
                _free_gpu()
                self._fail(stem, _reason(err))
                continue
            if self._cancel.is_set():     # 읽는 도중 취소: 이 문서 결과는 버린다
                return
            name, n = stem, 1
            while name in used:   # 다른 폴더의 같은 이름 파일이 결과를 덮어쓰지 않게
                n += 1
                name = f"{stem} ({n})"
            used.add(name)
            target = self.out_dir / f"{name}.json"
            try:
                self.out_dir.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError as err:  # 디스크 가득 참·읽기 전용 폴더·긴 경로 등
                self._fail(stem, f"결과를 저장할 수 없음 — {self.out_dir} ({err.strerror or err})")
                continue
            if r.get("error"):
                self._fail(stem, r["error"])
                continue
            self._counts[0] += 1
            self.doc_done.emit({**r, "json_path": str(target)})    # 화면이 'JSON은 여기 있어요'로 알려 줄 수 있게
