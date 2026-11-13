"""처음 한 번: 프로그램이 쓰는 AI 모델(약 9GB)을 인터넷에서 받아 models\\에 넣는다(BridgeDocs 설치.bat이 부름).
받은 뒤에는 offline.py가 models\\에서만 찾으므로 인터넷 없이 돈다. 이미 받은 파일은 건너뛴다."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import offline  # noqa: E402

models = offline.app_base() / "models"
os.environ["HF_HOME"] = str(models / "hf")
os.environ["PADDLE_PDX_CACHE_HOME"] = str(models / "paddlex")

from huggingface_hub import snapshot_download  # noqa: E402

print(f"모델 받는 곳: {models}")
print(f"1/2 {offline.QWEN_REPO} (약 9GB, 시간이 걸립니다)")
snapshot_download(offline.QWEN_REPO)

print("2/2 PaddleOCR 한국어 모델")
try:
    from paddleocr import PaddleOCR
    PaddleOCR(lang="korean", text_detection_model_name=offline._ocr_det(),
              text_recognition_model_name=offline.OCR_REC[0],
              use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)
except Exception as e:  # 한글 경로면 받은 뒤 '여는' 단계에서 실패할 수 있다: 파일만 있으면 실행 때 offline.py가 영어 경로로 복사해 쓴다
    print(f"  (OCR 모델 열기 실패, 파일만 확인합니다: {e})")

missing = offline.missing_models(models)
if missing:
    sys.exit(f"받지 못한 모델: {', '.join(missing)} — 인터넷 연결을 확인하고 다시 실행하세요.")
print("모델 준비 완료")
