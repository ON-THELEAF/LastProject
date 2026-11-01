"""화면 개발용 가짜 백엔드. GPU·모델 없이 DesktopService와 똑같은 메서드를 쓴다.

- 문서 읽기: 고른 파일과 상관없이 fake_data\\의 합성 결과를 차례로 돌려준다.
  순서: 등기부(2쪽, 모두 자동 확정) → 일부러 실패(GPU 메모리 부족) → 건축물대장(확인 필요 1칸).
  문서마다 delay초 간격(진행 표시를 볼 수 있게).
- 판별·내보내기·문서 만들기: 진짜와 같은 코드(모델이 필요 없음).
"""
import json
import time
from pathlib import Path

from desktop.service import DesktopService
from desktop.worker import ReadJob

FAKE_DIR = Path(__file__).parent / "fake_data"
FAIL_NAME = "깨진문서"


class _NoEngine:
    def get(self):
        return None, None, None


class FakeService(DesktopService):
    def __init__(self, delay: float = 0.3):
        super().__init__(models_dir=FAKE_DIR, engine=_NoEngine(), process=self._process)
        self.delay = delay

    def status(self) -> dict:
        return {"models_ok": True, "missing": [], "message": "연습용 가짜 백엔드 — 실제로 읽지 않고 미리 만든 결과를 보여 줘요"}

    def _ask_text(self, prompt):
        raise ValueError("연습판에서는 보통 문장으로 쓴 txt를 AI로 읽을 수 없어요 — '칸: 값' 줄 형식(예: 임대인: 김철수)으로 써 보세요")

    def _process(self, paths, model, proc, ocr, deskew=False, stop=None):
        time.sleep(self.delay)
        stem = Path(paths[0]).stem.rsplit("_p", 1)[0]
        if stem == FAIL_NAME:
            raise RuntimeError("CUDA out of memory (연습용 실패)")
        doc = json.loads((FAKE_DIR / f"{stem}.json").read_text(encoding="utf-8"))
        return {**doc, "files": [str(FAKE_DIR / n) for n in doc["files"]]}

    def _make_job(self, files, out_dir, deskew):
        # 입력 검사(빈 목록·지원 안 하는 파일·작업 중)는 진짜와 같고, 읽는 내용만 미리 만든 결과로 바꾼다
        reg = sorted(FAKE_DIR.glob("clean_s01_registry_p*.png"))
        fake_files = reg + [FAKE_DIR / f"{FAIL_NAME}.pdf"] + sorted(FAKE_DIR.glob("clean_s01_ledger_p*.png"))
        return ReadJob(fake_files, out_dir, deskew, self.engine, process=self.process)
