"""인터넷 없이 실행: 모델을 프로그램 폴더 안 models\\에서만 찾게 하고, 내려받기·원격 확인을 끈다.

반드시 `import bridgedocs`(torch·transformers·paddle)보다 먼저 setup_offline()을 부른다.
models\\ 구성(이 PC의 캐시 구조 그대로 복사):
  models\\hf\\hub\\models--Qwen--Qwen3-VL-4B-Instruct\\...      (← %USERPROFILE%\\.cache\\huggingface\\hub)
  models\\paddlex\\official_models\\<OCR 모델 이름>\\...         (← %USERPROFILE%\\.paddlex\\official_models)
"""
import os
import sys
from pathlib import Path

QWEN_REPO = "Qwen/Qwen3-VL-4B-Instruct"
# 엔진이 쓰는 OCR 인식 모델(bridgedocs.make_ocr, 한국어용만). 검출 모델은 BD_OCR=server면 서버용.
OCR_REC = ["korean_PP-OCRv5_mobile_rec"]


def app_base() -> Path:
    """묶은 exe면 exe가 있는 폴더, 소스 실행이면 저장소 루트."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


_models: Path | None = None   # 마지막으로 설정한 models 폴더


def ensure_offline() -> Path:
    """아직 설정하지 않았으면 프로그램 폴더 기준으로 설정한다(service가 import될 때 부름: 순서를 잊어도 온라인이 되지 않게)."""
    return _models if _models is not None else setup_offline(app_base())


def setup_offline(base: Path) -> Path:
    global _models
    models = _models = Path(base) / "models"
    os.environ["HF_HOME"] = str(models / "hf")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(_ascii_paddlex(models / "paddlex"))
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ.setdefault("BD_CUSTOM", str(Path(base) / "새 문서 종류.json"))   # 사용자가 확정한 새 문서 종류의 칸 목록
    _install_folders(Path(base))
    return models


SETTINGS = "설정.json"


def _install_folders(base: Path) -> None:
    """설치할 때 정한 폴더: 프로그램 옆 설정.json {"양식 폴더": "...", "저장 폴더": "..."}(없으면 기본값).
    양식 폴더 기본값은 프로그램 옆 양식\\, 저장 폴더(읽기 결과·내보내기) 기본값은 문서\\BridgeDocs 결과."""
    import json
    try:
        cfg = json.loads((base / SETTINGS).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        cfg = {}
    cfg = cfg if isinstance(cfg, dict) else {}
    if cfg.get("양식 폴더"):
        os.environ["BD_FORMS"] = str(cfg["양식 폴더"])
    os.environ.setdefault("BD_FORMS", str(base / "양식"))
    os.environ["BD_OUT"] = str(cfg.get("저장 폴더") or os.environ.get("BD_OUT") or Path.home() / "Documents" / "BridgeDocs 결과")


def _ascii_paddlex(src: Path) -> Path:
    """PaddleOCR(C++)은 경로에 한글이 있으면 모델 파일을 열지 못한다(실측: inference.json을 빈 파일로 읽음).
    프로그램 폴더 경로가 영어가 아니면 OCR 모델(약 100MB)만 C:\\ProgramData\\BridgeDocs\\paddlex로 한 번 복사해 쓴다."""
    if str(src).isascii() or not src.is_dir():
        return src
    import shutil
    dst = Path(os.environ.get("PROGRAMDATA") or r"C:\ProgramData") / "BridgeDocs" / "paddlex"
    try:
        for f in src.rglob("*"):
            out = dst / f.relative_to(src)
            if f.is_file() and (not out.exists() or out.stat().st_size != f.stat().st_size):
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, out)
    except OSError:
        return src      # 복사하지 못하면 원래 자리(영어 경로로 옮기라는 안내는 사용법에)
    return dst


def _ocr_det() -> str:
    return "PP-OCRv5_server_det" if os.environ.get("BD_OCR") == "server" else "PP-OCRv5_mobile_det"  # bridgedocs.OCR_DET과 같은 규칙


def required_model_dirs() -> list[Path]:
    """models\\ 기준 상대 경로."""
    qwen = Path("hf") / "hub" / ("models--" + QWEN_REPO.replace("/", "--"))
    return [qwen] + [Path("paddlex") / "official_models" / n for n in [_ocr_det(), *OCR_REC]]


def missing_models(models_dir: Path) -> list[str]:
    """없는 모델 이름 목록(다 있으면 [])."""
    out = []
    for rel in required_model_dirs():
        if not (Path(models_dir) / rel).is_dir():
            out.append(rel.name.split("--")[-1])
    return out
