"""쪽 이미지 만들기와 스캔 전처리(기울기 보정·대비 늘리기). 문서를 읽을 때와 결과 보기에서 같은 보정을 쓴다."""
from PIL import Image


def render(page, dpi=150):
    pix = page.get_pixmap(dpi=dpi)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples), pix.width / page.rect.width


def deskew_angle(img, max_deg=3.0, step=0.1):
    """글자 줄이 가장 또렷하게 가로로 늘어서는 각도(도). 가로 투영(행별 검은 점 수)의 분산이 가장 큰 각도를 고른다."""
    import numpy as np
    g = img.convert("L")
    g = g.resize((g.width // 2, g.height // 2))
    ink = g.point(lambda x: 255 if x < 160 else 0)     # 먼저 흑백으로 바꾼 뒤 모든 각도를 같은 방식(NEAREST)으로 돌린다.
    best, best_v = 0.0, -1.0                            # (돌리지 않은 0도만 선명해서 늘 0도가 뽑히던 문제를 막음)
    for k in range(int(-max_deg / step), int(max_deg / step) + 1):
        a = k * step
        r = np.asarray(ink.rotate(a, resample=Image.NEAREST, fillcolor=0)) > 0
        v = float(np.var(r.sum(axis=1)))
        if v > best_v:
            best, best_v = a, v
    return best


def preprocess(img):
    """스캔 전처리: 기울기 보정 + 대비 늘리기(기획서 1-4). -> (보정된 이미지, 보정 각도)"""
    from PIL import ImageOps
    a = deskew_angle(img)
    out = img.rotate(a, resample=Image.BICUBIC, fillcolor="white") if abs(a) >= 0.05 else img
    return ImageOps.autocontrast(out.convert("L"), cutoff=1).convert("RGB"), a
