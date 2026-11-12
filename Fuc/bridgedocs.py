"""BridgeDocs 읽기·검증 엔진.

사용: python bridgedocs.py <명령> [옵션]
  python bridgedocs.py run    # 문서 입력 → 분석 → 환각검증 → 판별 화면(브라우저)에서 확인·데이터 추출
  python bridgedocs.py make   # 데이터(JSON·CSV·엑셀) → 문서(기본 양식 PDF 또는 워드 양식 채우기)

프로그램(윈도우 화면)은 SERVER/service.py를 거쳐 이 엔진을 부른다.
명령마다 옵션은 `python bridgedocs.py <명령> --help` 로 본다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # 프로젝트 맨 위(Fuc·DB를 찾게)

import argparse
import base64
import difflib
import io
import json
import os
import random
import re
import sys
import tempfile
import time
import unicodedata
import zipfile
from datetime import date, timedelta
from pathlib import Path

# ponytail: Colab(리눅스 x86)에서 oneDNN 가속 경로가 "ConvertPirAttribute2RuntimeAttribute not support" 오류를 낸다(실측).
# 맥(ARM)에는 없는 경로라 맥에서는 안 났다. 속도는 조금 느려질 수 있지만 가속을 꺼서 우회한다.
os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
os.environ.setdefault("FLAGS_use_mkldnn", "0")

# Windows: torch와 paddle이 이름이 같은 CUDA DLL(cuDNN·cuBLAS)을 따로 갖고 있어, paddle을 먼저 불러오면 torch가 깨진다(실측).
# torch를 먼저 불러오면 둘 다 동작한다(cuDNN은 torch와 같은 9.10으로 맞춤, 설치는 README 참고).
if sys.platform == "win32":
    try:
        import torch  # noqa: F401
    except ImportError:
        pass  # torch가 없는 환경(A경로만 쓸 때)은 순서 문제가 없다

import pymupdf  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

# 기능별 폴더(Fuc/…, DB/)의 이름을 모두 여기로 모은다: 다른 코드는 계속 `import bridgedocs as bd`로 쓴다.
# 불러오는 순서가 중요하다: 기능끼리 서로 부르므로, 모듈 맨 위에서 다른 기능을 쓰는 hallucination_check·make_doc보다
# 그 대상(scoring·read_pipeline)을 먼저 불러온다. 다른 코드는 이 파일(import bridgedocs)로만 엔진을 부른다.
import Fuc.scoring.scoring  # noqa: E402,F401
import Fuc.read_pipeline.read_pipeline  # noqa: E402,F401
from DB.storage import ROOT, custom_path, custom_types, delete_custom_type, forms_dir, save_custom_type, save_result  # noqa: E402,F401
from Fuc.docx_read.docx_read import DOCX_KEEP, DOCX_MAX_CHARS, WNS, _para_text, _run_text, _w, ask_text, docx_lines  # noqa: E402,F401
from Fuc.hallucination_check.hallucination_check import ALIASES, DATE_ANY, DATE_DECOY, DATE_FORMS, EMPTY_OK, EN_MON, FIRST_DATE, FLAG_WORD, GRADE_RANGE, KO_PARTICLE, LAST_OWNER, NAME_PREFIX, NAME_SUFFIX, NEED_ON_PAGE, OCR_DET, ROLE_ORDER, SECOND_READ_ANY, VOCAB, ZOOM_NUMBER, ZOOM_Q, _CHO, _JONG, _JUNG, _MON, alnum_key, any_dates, arbitrate, best_window, check_field, compute_checks, cross_pairs, dates_in, edit_distance, format_ok, governing, jamo, lang_field, occurrences, role_lines, second_read_issue, share_sum_ok, spaced, squash, standalone, zoom_box, zoom_number, zoom_raw  # noqa: E402,F401
from Fuc.make_doc.make_doc import FREE_FIELDS_Q, FREE_TYPE_Q, KIND_HINT, KV_LINE, SLOT, SLOT_PART, TEXT_LIMIT, TXT_SEPS, WIN_RESERVED, _xml_text, apply_edits, check_record, confirm_html, fill_docx, fill_xml, load_records, main_make, prepare_docs, read_table, read_text_any, read_txt_table, read_xlsx, resolve_key, resolve_type, safe_name, serve_confirm, template_slots, template_values, text_record, write_docs  # noqa: E402,F401
from Fuc.path_a_ocr.path_a_ocr import HERE, lines_xy, make_ocr, ocr_items  # noqa: E402,F401
from Fuc.path_b_vlm.path_b_vlm import HERE, HINT, OCR_NOTE, PRE, PROMPTS, V1, V2, V2Q, V3, ask, garbled, load, with_ocr  # noqa: E402,F401
from Fuc.pdf_text.pdf_text import PUA, TH, _dedupe, _join, _mark_flat, _mark_struck, _mark_watermark, _union_area, chars_of, diagnose_page, extract_text_layer, is_bad, is_hangul, is_pua, text_items, usable  # noqa: E402,F401
from Fuc.read_pipeline.preprocess import deskew_angle, preprocess, render  # noqa: E402,F401
from Fuc.read_pipeline.read_pipeline import COMPUTED, Cancelled, DOC_FIELDS, DOC_TYPE_LABEL, DOC_TYPE_WORDS, EXPORT_COLUMNS, FIELD_LABEL, FIELD_PAGE_WORDS, JOINT_PRE, LANG_CODE, LANG_LABEL, NEW_TYPE_Q, ROUTE_LABEL, STATUS_LABEL, SUPPORTED, TYPE_PRE, _field_page, ask_doc_type, custom_fields, detect_doc_type, docx_body, group_documents, joint_answers, joint_question, load_pages, main_run, parse_joint, parse_json_obj, process_document, read_pages, resolve_ai_type, text_question, type_question  # noqa: E402,F401
from Fuc.scoring.scoring import CHOICES, CHOICE_WORDS, GRADE_DROP, NUM_RE, REFUSAL, SEAL_TAIL, SHARE_RE, _digits, clean, grade_key, norm, owners_text, parse_owners, person_key, strip_seal  # noqa: E402,F401
from Fuc.view.view import VIEW_HTML, image_payload, page_images, render_view  # noqa: E402,F401


# 명령 선택
COMMANDS = {
    "run": main_run,
    "make": main_make,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv.pop(1)
    sys.argv[0] = f"bridgedocs.py {cmd}"
    COMMANDS[cmd]()


if __name__ == "__main__":
    main()
