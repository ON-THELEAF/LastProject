"""A경로: PaddleOCR 규칙 OCR  (bridgedocs.py에서 나눔)"""
from pathlib import Path


# ============================================================================
# A경로: PaddleOCR 규칙 OCR  (이전: path_a.py)
# ============================================================================
# A경로(규칙 기반 OCR, PaddleOCR 한국어) — 이 파일 하나로 실행·재사용한다.
#
# - lease/sale(고정 서식): 양식에 '저장된 칸 좌표'(ground_truth의 region = 서식 칸)를 잘라서 읽는다.
#   이 PoC에서는 좌표를 make_samples.py에서 사람이 정해 두었다(제품 설계는 B경로 bbox 자동학습 + 관리자 승인).
#   ±3px 이동만 있고 실제 스캔의 기울기·확대가 없어 낙관적이다.
# - registry(가변 행): 전체 페이지 OCR 후 '갑구/을구' 앵커 기준으로 행을 재구성해 값을 뽑는다.
# - read_crop(): 임의의 bbox 영역만 잘라 OCR로 다시 읽는다 → path_b.py의 환각검증(VLM이 낸 위치를 OCR로 재확인)이 쓴다.
#
# 실행: python bridgedocs.py a [--samples samples] [--doc s03_registry --doc s06_lease]

HERE = Path(__file__).parent


def make_ocr():
    from paddleocr import PaddleOCR
    # 기본은 모바일 검출 모델(서버용은 CPU에서 쪽마다 2분 넘게 걸린다, 실측).
    # 주의: 모델 이름을 하나라도 직접 지정하면 lang='korean'이 무시돼 인식 모델이 한국어가 아니게 된다(실측) → rec도 명시.
    # BD_OCR=server 이면 서버용 검출 모델(GPU에서는 빠름). 인식 모델은 한국어 전용 모바일 그대로.
    return PaddleOCR(lang="korean", text_detection_model_name=OCR_DET,
                     text_recognition_model_name="korean_PP-OCRv5_mobile_rec",
                     use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
                     text_det_limit_type="max", text_det_limit_side_len=1280)


def ocr_items(ocr, path):
    r = ocr.predict(str(path))[0]
    boxes = r.get("rec_boxes")
    if boxes is None:
        boxes = r["dt_polys"]
    out = []
    scores = r.get("rec_scores")
    scores = [1.0] * len(r["rec_texts"]) if scores is None else scores
    for t, b, sc in zip(r["rec_texts"], boxes, scores):
        b = [float(v) for v in b] if len(b) == 4 and not hasattr(b[0], "__len__") else [min(p[0] for p in b), min(p[1] for p in b), max(p[0] for p in b), max(p[1] for p in b)]
        out.append({"text": t, "x0": b[0], "x1": b[2], "y0": b[1], "y1": b[3], "cx": (b[0] + b[2]) / 2, "cy": (b[1] + b[3]) / 2, "score": float(sc)})
    return out


def lines_xy(items, tol=10):
    """lines_of와 같지만 줄마다 위치(x0,x1,y0,y1,cy)도 돌려준다 — 라벨 근접·줄 잘라내기 검증이 쓴다."""
    rows = []
    for it in sorted(items, key=lambda i: i["cy"]):
        if rows and abs(rows[-1][0]["cy"] - it["cy"]) < tol:
            rows[-1].append(it)
        else:
            rows.append([it])
    out = []
    for r in rows:
        r = sorted(r, key=lambda i: i["x0"])
        out.append({"text": " ".join(i["text"] for i in r), "x0": r[0]["x0"], "x1": max(i["x1"] for i in r),
                    "y0": min(i["y0"] for i in r), "y1": max(i["y1"] for i in r), "cy": sum(i["cy"] for i in r) / len(r),
                    "parts": [{"text": i["text"], "x0": i["x0"], "y0": i["y0"], "x1": i["x1"], "y1": i["y1"], "score": i.get("score", 1.0)} for i in r]})   # 조각 상자: 이름 칸 확대 재판독용
    return out


# 함수 안에서만 쓰는 다른 기능의 이름: 맨 아래에서 불러온다(서로 부르는 기능끼리 순환해도 됨)
from Fuc.hallucination_check.hallucination_check import OCR_DET  # noqa: E402,F401
