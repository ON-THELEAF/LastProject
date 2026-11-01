import os
import socket
import subprocess
import sys
from pathlib import Path

from desktop import offline


def test_setup_sets_env(tmp_path, monkeypatch):
    for k in ("HF_HOME", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "PADDLE_PDX_CACHE_HOME"):
        monkeypatch.delenv(k, raising=False)
    models = offline.setup_offline(tmp_path)
    assert models == tmp_path / "models"
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
    assert os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] == "True"
    assert Path(os.environ["HF_HOME"]).is_relative_to(models)
    assert Path(os.environ["PADDLE_PDX_CACHE_HOME"]).is_relative_to(models)


def test_missing_models_lists_absent(tmp_path):
    missing = offline.missing_models(tmp_path)
    assert "Qwen3-VL-4B-Instruct" in missing
    assert "korean_PP-OCRv5_mobile_rec" in missing
    for rel in offline.required_model_dirs():
        (tmp_path / rel).mkdir(parents=True)
    assert offline.missing_models(tmp_path) == []


def test_korean_space_base(tmp_path):
    base = tmp_path / "바탕 화면" / "BridgeDocs"
    models = offline.setup_offline(base)
    assert models == base / "models"
    assert offline.missing_models(models)  # 폴더가 없어도 오류 없이 목록을 돌려준다


def test_no_network_on_import(tmp_path):
    # 새 프로세스에서 소켓 접속을 막고 엔진·백엔드를 불러온다(이미 불러온 모듈 영향 없이)
    code = (
        "import socket, sys\n"
        "def deny(*a, **k): raise RuntimeError('network attempt')\n"
        "socket.socket.connect = deny\nsocket.create_connection = deny\n"
        f"sys.path.insert(0, {str(Path(offline.__file__).parents[1])!r})\n"
        "from desktop import offline\n"
        f"offline.setup_offline(__import__('pathlib').Path({str(tmp_path)!r}))\n"
        "import bridgedocs\nimport desktop.service\nprint('ok')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip().endswith("ok")


def test_korean_path_copies_ocr_models_to_ascii_place(tmp_path, monkeypatch):
    # PaddleOCR(C++)은 경로에 한글이 있으면 모델 파일을 못 연다(실측) → 영어 경로로 복사해 쓴다
    from desktop import offline
    base = tmp_path / "바탕 화면" / "BridgeDocs_정식판"
    det = base / "models" / "paddlex" / "official_models" / "PP-OCRv5_mobile_det"
    det.mkdir(parents=True)
    (det / "inference.json").write_text("{}", encoding="utf-8")
    import shutil
    import tempfile
    ascii_dir = Path(tempfile.mkdtemp(dir=tempfile.gettempdir()))      # pytest 임시 폴더는 사용자 이름(한글일 수 있음) 아래라 따로 만든다
    try:
        monkeypatch.setenv("PROGRAMDATA", str(ascii_dir / "pd"))
        offline.setup_offline(base)
        pdx = os.environ["PADDLE_PDX_CACHE_HOME"]
        assert pdx.isascii() and (Path(pdx) / "official_models" / "PP-OCRv5_mobile_det" / "inference.json").exists()
        offline.setup_offline(ascii_dir)          # 영어 경로면 그대로 쓴다
        assert os.environ["PADDLE_PDX_CACHE_HOME"] == str(ascii_dir / "models" / "paddlex")
    finally:
        shutil.rmtree(ascii_dir, ignore_errors=True)
