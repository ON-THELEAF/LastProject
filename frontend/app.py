"""BridgeDocs 프로그램 창. 실행: python frontend/app.py  (--fake: GPU 없이 연습용 백엔드)

탭: 문서 읽기(임시, read_tab.py) · 문서 만들기(make_tab.py).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

from desktop import offline  # noqa: E402

offline.ensure_offline()     # torch·paddle을 불러오기 전에

from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget  # noqa: E402

from make_tab import MakeTab  # noqa: E402
from read_tab import ReadTab  # noqa: E402


def engine_installed() -> bool:
    """읽기 엔진(torch·paddle)이 설치돼 있는지. 연습판 패키지에는 없어서 읽기를 예시 결과로 보여 준다."""
    import importlib.util
    return all(importlib.util.find_spec(m) for m in ("torch", "transformers", "paddleocr"))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    practice = "--fake" in argv or not engine_installed()
    if practice:
        from desktop.fake import FakeService as Service
    else:
        from desktop.service import DesktopService as Service
    if "--selfcheck" in argv:      # 화면 없이 만들기→읽기→내보내기 한 번. 결과는 프로그램 폴더의 '점검 결과.txt'
        import tempfile
        from desktop import selfcheck
        log = offline.app_base() / "점검 결과.txt"
        with open(log, "w", encoding="utf-8") as f:
            sys.stdout = sys.stderr = f
            print("연습판(읽기는 예시 결과 — 실패 1건은 연습용으로 정상)" if practice else "정식판")
            return selfcheck.run(Service(), Path(tempfile.mkdtemp(prefix="bridgedocs_selfcheck_")))
    app = QApplication.instance() or QApplication(argv)
    win = QMainWindow()
    win.setWindowTitle("BridgeDocs" + (" — 연습판(문서 읽기는 예시 결과)" if practice else ""))
    tabs = QTabWidget()
    svc = Service()
    tabs.addTab(ReadTab(svc), "문서 읽기")
    tabs.addTab(MakeTab(svc), "문서 만들기")
    win.setCentralWidget(tabs)
    win.resize(1000, 720)
    win.show()
    app.aboutToQuit.connect(svc.wait_all)     # 창을 닫으면 읽던 작업을 멈추고 기다린다
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
