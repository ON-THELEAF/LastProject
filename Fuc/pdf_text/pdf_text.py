"""PDF 글자 레이어 읽기(믿을 수 있는 쪽은 OCR 없이)  (bridgedocs.py에서 나눔)"""
import unicodedata


import pymupdf  # noqa: E402


# ============================================================================
# PDF 글자 레이어 경로  (이전: text_layer.py)
# ============================================================================
# PDF 글자 레이어 → path_a.ocr_items 와 같은 형식의 items 로 바꾸고, 그 글자 레이어를 믿어도 되는지 쪽 단위로 진단한다.
# (OCR·VLM 없이 PDF 안의 글자와 좌표를 바로 쓰는 경로. 스캔·사진·글자가 깨진 PDF 는 usable=False 로 걸러 OCR 경로로 넘긴다.)
#
# 핵심 함수
#   route_pdf(path)               : 문서 판정(text/mixed/ocr)과 쪽별 진단·items, 등기부면 필드까지 — 파이프라인이 부르는 입구
#   extract_text_layer(path, dpi) : 쪽별 {items, diag, usable, reasons}
#   diagnose_page / usable / warnings : 쪽 진단 지표, 사용 가능 판정, 경고(말소선·외곽선 글자·PUA 등)
#   text_items(page, dpi)         : 좌표 기반으로 줄을 다시 묶은 items — path_a.lines_xy 에 그대로 넣을 수 있다(path_b.check_field 도 동작)
#   render_crosscheck(...)        : 글자 레이어 값을 쪽 렌더링→OCR 로 다시 읽어 맞는지 확인(글자 매핑이 그럴듯하게 틀린 PDF 는 이것만 잡는다)
# 실행: python bridgedocs.py layer <pdf>
# 임계값 TH 는 샘플 3개와 합성 사례로 정했다 — 실제 발급본·스캔본으로 다시 맞춰야 한다.

PUA = [(0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD)]


def is_hangul(c):
    o = ord(c)
    return 0xAC00 <= o <= 0xD7A3 or 0x3131 <= o <= 0x318E or 0x1100 <= o <= 0x11FF


def is_pua(c):
    o = ord(c)
    return any(a <= o <= b for a, b in PUA)


def is_bad(c):
    """깨진 매핑 신호: U+FFFD, 비정상 제어문자(탭·개행 제외), 미할당/비문자(Cn)"""
    o = ord(c)
    return c == "�" or (o < 32 and c not in "\t\n\r") or 0x7F <= o <= 0x9F or unicodedata.category(c) == "Cn"


def chars_of(page, strike=True, flat=True):
    """texttrace 를 글자 단위로 푼다. 공백은 버린다. 반환: 글자 dict 목록(콘텐츠 스트림 순서 seq 보존)."""
    out, seq = [], 0
    pr = page.rect
    for s in page.get_texttrace():
        for uni, gid, org, bb in s["chars"]:
            c = chr(uni) if uni and uni > 0 else "�"   # 매핑 없는 글리프(ToUnicode 없음) = 0 → 깨짐으로 센다
            seq += 1
            if c.isspace():
                continue
            x0, y0, x1, y1 = bb
            off = x1 <= pr.x0 or x0 >= pr.x1 or y1 <= pr.y0 or y0 >= pr.y1
            out.append({"c": c, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "base": org[1], "size": s["size"], "type": s["type"],
                        "color": s["color"], "opacity": s["opacity"], "font": s["font"], "seq": seq, "off": off, "struck": False, "wm": False, "flat": False})
    out = _dedupe(out)
    _mark_watermark(out)
    if flat and out:
        _mark_flat(page, out)
    if strike and out:
        _mark_struck(page, out)
    return out


def _dedupe(chars):
    """같은 글자가 같은 자리에 2번 그려진 것(한글/HWP 의 굵게 흉내, 발급본 제목 등)을 1개로. MuPDF get_text 는 이걸 알아서 지우지만 texttrace 는 안 지운다."""
    seen, out = {}, []
    for c in chars:
        k = (c["c"], round(c["x0"]), round(c["base"]))
        hit = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for o in seen.get((c["c"], k[1] + dx, k[2] + dy), ()):
                    if abs(o["x0"] - c["x0"]) < 0.2 * c["size"] and abs(o["base"] - c["base"]) < 0.2 * c["size"]:
                        hit = True
        if not hit:
            seen.setdefault(k, []).append(c)
            out.append(c)
    return out


def _mark_watermark(chars):
    """'열람용' 같은 큰 연회색 글자(쪽 가운데 대각/가로 워터마크). 값 추출에서 빼야 한다."""
    for c in chars:
        col = c["color"]
        c["wm"] = c["size"] >= 30 and min(col) >= 0.6 and max(col) - min(col) < 0.05


def _mark_struck(page, chars):
    """글자 가운데를 가로지르는 얇은 가로선(말소선)이 있는 글자에 struck=True. 등기부의 '실선으로 그어진 부분은 말소사항'."""
    lines = []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                if abs(a.y - b.y) < 0.6 and abs(a.x - b.x) > 2:
                    lines.append((min(a.x, b.x), max(a.x, b.x), a.y))
            elif it[0] == "re":
                r = it[1]
                if r.height < 1.2 and r.width > 2:
                    lines.append((r.x0, r.x1, (r.y0 + r.y1) / 2))
    if not lines:
        return
    pm = page.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
    k, W, H, buf = 150 / 72, pm.width, pm.height, pm.samples

    def line_visible(ch, y):
        """그 높이의 화면 픽셀이 글자 폭 전체에서 이어진 잉크인가(획이 아니라 선이 실제로 보이는지; 흰 상자로 덮인 선은 제외).
        가는 선은 안티앨리어싱으로 2행에 나뉘므로 열마다 y±1 행의 최솟값을 본다."""
        yy = int(y * k)
        xs = range(max(0, int(ch["x0"] * k)), min(W, int(ch["x1"] * k) + 1))
        if not xs or not (1 <= yy < H - 1):
            return False
        dark = sum(min(buf[(yy + dy) * W + x] for dy in (-1, 0, 1)) < 210 for x in xs)
        return dark / len(xs) >= 0.9

    for ch in chars:
        if ch["wm"]:
            continue
        h = ch["y1"] - ch["y0"]
        cy = (ch["y0"] + ch["y1"]) / 2
        cx = (ch["x0"] + ch["x1"]) / 2
        # 글자 중간 띠(±22% 높이)를 지나고 글자 중심 x 를 덮는 선
        if any(a - 0.5 <= cx <= b + 0.5 and b - a >= 1.5 * (ch["x1"] - ch["x0"]) and abs(y - cy) <= 0.22 * h and line_visible(ch, y) for a, b, y in lines):
            ch["struck"] = True


def _union_area(rects, W, H, step=4):
    """이미지 bbox 합집합 면적 비율(격자 근사)"""
    if not rects:
        return 0.0
    gx, gy = int(W / step) + 1, int(H / step) + 1
    cell = bytearray(gx * gy)
    for x0, y0, x1, y1 in rects:
        for j in range(max(0, int(y0 / step)), min(gy, int(y1 / step) + 1)):
            base = j * gx
            for i in range(max(0, int(x0 / step)), min(gx, int(x1 / step) + 1)):
                cell[base + i] = 1
    return sum(cell) / (gx * gy)


def _mark_flat(page, chars, dpi=100):
    """렌더링한 화면에서 글자 상자 안이 '평평'(잉크 없음)한 글자에 flat=True — 흰 글씨/가려진 글씨/렌더 안 되는 글씨.
    정부 임대차 표준서식의 흰색 안내문('이름 또는 법인명 기재')이 실제로 이 경우다."""
    pm = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    k = dpi / 72
    W, H, buf = pm.width, pm.height, pm.samples
    for ch in chars:
        ch["flat"] = False
        if ch["off"]:
            continue
        x0, x1 = max(0, int(ch["x0"] * k)), min(W, int(ch["x1"] * k) + 1)
        y0, y1 = max(0, int(ch["y0"] * k)), min(H, int(ch["y1"] * k) + 1)
        if x1 <= x0 or y1 <= y0:
            continue
        vals = [buf[y * W + x] for y in range(y0, y1) for x in range(x0, x1)]
        white = min(ch["color"]) > 0.9                       # 흰 글씨는 '밝은 배경 위'면 숨은 글자(어두운 띠 위의 흰 제목은 정상)
        med = sorted(vals)[len(vals) // 2]
        ch["flat"] = max(vals) - min(vals) < 24 or (white and med > 200)


def diagnose_page(page, chars=None, render_check=True):
    chars = chars_of(page) if chars is None else chars
    n = len(chars)
    W, H = page.rect.width, page.rect.height
    txt = [c["c"] for c in chars]
    hang = sum(is_hangul(c) for c in txt)
    letters = sum(c.isalnum() for c in txt)
    imgs = []
    for im in page.get_image_info():
        r = pymupdf.Rect(im["bbox"]) & page.rect
        if not r.is_empty:
            imgs.append((r.x0, r.y0, r.x1, r.y1))
    fonts = {f[3].split("+")[-1]: f[2] for f in page.get_fonts()}
    type3_names = {k for k, v in fonts.items() if v == "Type3"}
    n_t3 = sum(1 for c in chars if c["font"].split("+")[-1] in type3_names)
    white = sum(1 for c in chars if all(v > 0.95 for v in c["color"]))
    draws = page.get_drawings()
    # 글자를 도형(외곽선)으로 바꾼 흔적: 작은 채움 곡선 경로 수. 이 샘플에서 HWP 내보내기 매매계약서는 라벨·제목 일부가 이렇게 나온다
    glyph_paths = sum(1 for x in draws if x["type"] in ("f", "fs") and any(it[0] == "c" for it in x["items"])
                      and 1 < x["rect"].width < 30 and x["rect"].height < 30)
    d = {
        "n_chars": n,
        "hangul_ratio": hang / n if n else 0.0,
        "alnum_ratio": letters / n if n else 0.0,
        "pua_ratio": sum(is_pua(c) for c in txt) / n if n else 0.0,
        "bad_ratio": sum(is_bad(c) for c in txt) / n if n else 0.0,
        "mode3_ratio": sum(1 for c in chars if c["type"] == 3) / n if n else 0.0,
        "transparent_ratio": sum(1 for c in chars if c["opacity"] < 0.05) / n if n else 0.0,
        "white_ratio": white / n if n else 0.0,
        "offpage_ratio": sum(c["off"] for c in chars) / n if n else 0.0,
        "flat_ratio": (sum(c["flat"] for c in chars) / n if n else 0.0) if render_check else None,
        "image_cover": _union_area(imgs, W, H),
        "n_images": len(imgs),
        "type3_char_ratio": n_t3 / n if n else 0.0,
        "n_drawings": len(draws),
        "glyph_paths": glyph_paths,
        "outlined_ratio": glyph_paths / (n + glyph_paths) if (n + glyph_paths) else 0.0,
        "struck_ratio": sum(c["struck"] for c in chars) / n if n else 0.0,
        "fonts": fonts,
    }
    # 글자 밀도: 쪽 면적(100pt^2)당 글자 수. 외곽선(글자→도형) PDF 는 n_chars≈0 인데 드로잉이 많다
    d["chars_per_100pt2"] = n / (W * H / 100)
    return d


# ponytail: 임계값은 이 3개 샘플 + 합성 실험(make_layer_fixtures.py)에서 정했다. 실제 발급본·스캔본 표본으로 다시 맞춰야 한다.
TH = {"min_chars": 40, "min_hangul": 0.30, "max_pua": 0.10, "max_bad": 0.01, "max_mode3": 0.02, "max_flat": 0.10,
      "max_off": 0.02, "max_img_cover": 0.60, "max_white": 0.30, "max_outlined": 0.30}


def usable(d, th=TH):
    """쪽 단위 판정 -> (사용 가능 bool, 사유 목록). 하나라도 걸리면 OCR 로 넘긴다."""
    why = []
    if d["n_chars"] < th["min_chars"]:
        if d["image_cover"] < 0.05 and d["glyph_paths"] < 5:
            return True, []      # 정말 빈 쪽(여백 쪽)은 OCR 로 보낼 이유가 없다
        why.append(f"글자 {d['n_chars']}개 < {th['min_chars']}")
    if d["outlined_ratio"] > th["max_outlined"]:
        why.append(f"글자가 외곽선 도형으로 바뀐 쪽(도형 글자 {d['glyph_paths']}개 / 글자 {d['n_chars']}개)")
    elif d["hangul_ratio"] < th["min_hangul"] and d["alnum_ratio"] < 0.5:      # 한글 서식인데 한글이 거의 없으면 매핑 의심
        why.append(f"한글 {d['hangul_ratio']:.0%} / 영숫자 {d['alnum_ratio']:.0%}")
    if d["pua_ratio"] > th["max_pua"]:
        why.append(f"PUA {d['pua_ratio']:.1%}")
    if d["bad_ratio"] > th["max_bad"]:
        why.append(f"깨진 글자 {d['bad_ratio']:.1%}")
    if d["mode3_ratio"] > th["max_mode3"] or d["transparent_ratio"] > th["max_mode3"]:
        why.append(f"보이지 않는 글자(mode3 {d['mode3_ratio']:.0%}/투명 {d['transparent_ratio']:.0%}) = 스캔 OCR 층 또는 숨김")
    if d["flat_ratio"] is not None and d["flat_ratio"] > th["max_flat"]:
        why.append(f"화면에 안 그려지는 글자 {d['flat_ratio']:.0%}")
    if d["offpage_ratio"] > th["max_off"]:
        why.append(f"쪽 밖 글자 {d['offpage_ratio']:.0%}")
    if d["image_cover"] > th["max_img_cover"]:
        why.append(f"이미지가 쪽의 {d['image_cover']:.0%} 덮음")
    return (not why), why


# ───────── 줄 묶기 → path_a.ocr_items 형식 ─────────
def text_items(page, dpi=150, chars=None, row_tol=0.45, gap_em=0.9, skip_hidden=True, drop_struck=False, drop_wm=True):
    """글자 → (1) 기준선 y 로 행 묶기 (2) 행 안에서 x 순 정렬 (3) 간격이 gap_em×글자크기 이상이면 조각(셀) 분리.
    반환 items 는 path_a.ocr_items 와 같은 키(text,x0,x1,y0,y1,cx,cy)+ struck/size. 좌표는 dpi 기준 픽셀.
    HWP 내보내기처럼 글자가 뒤섞인 스트림도 좌표로 정렬하므로 순서가 복원된다(행 묶기 오차만 없으면)."""
    chars = chars_of(page) if chars is None else chars
    if skip_hidden:
        chars = [c for c in chars if c["type"] != 3 and c["opacity"] >= 0.05 and not c["off"] and not c["flat"]]
    if drop_struck:
        chars = [c for c in chars if not c["struck"]]
    if drop_wm:
        chars = [c for c in chars if not c["wm"]]
    # 행 묶기: 기준선 오름차순으로 훑으며, 현재 행의 기준선 평균과 (row_tol × 글자크기) 이내면 같은 행
    rows = []
    for ch in sorted(chars, key=lambda c: (c["base"], c["x0"])):
        for r in rows[-3:][::-1]:            # 최근 행 몇 개만 본다(위에서 아래로 정렬돼 있으므로)
            if abs(ch["base"] - r["base"]) <= row_tol * max(ch["size"], r["size"]):
                r["ch"].append(ch)
                r["base"] = (r["base"] * (len(r["ch"]) - 1) + ch["base"]) / len(r["ch"])
                r["size"] = max(r["size"], ch["size"])
                break
        else:
            rows.append({"base": ch["base"], "size": ch["size"], "ch": [ch]})
    k = dpi / 72
    items = []
    for r in rows:
        cs = sorted(r["ch"], key=lambda c: c["x0"])
        seg = [cs[0]]
        segs = []
        for c in cs[1:]:
            gap = c["x0"] - seg[-1]["x1"]
            if gap > gap_em * max(c["size"], seg[-1]["size"]):
                segs.append(seg)
                seg = [c]
            else:
                seg.append(c)
        segs.append(seg)
        for sg in segs:
            x0, x1 = min(c["x0"] for c in sg), max(c["x1"] for c in sg)
            y0, y1 = min(c["y0"] for c in sg), max(c["y1"] for c in sg)
            # 같은 행의 조각이 lines_xy 의 cy 허용오차(10px)로 한 줄이 되도록 행 공통 cy(기준선 기반)를 쓴다
            cy = (r["base"] - 0.3 * r["size"]) * k
            items.append({"text": _join(sg), "x0": x0 * k, "x1": x1 * k, "y0": y0 * k, "y1": y1 * k,
                          "cx": (x0 + x1) / 2 * k, "cy": cy, "size": r["size"], "struck": sum(c["struck"] for c in sg) > len(sg) / 2})
    return items


def _join(seg):
    """조각 안 글자를 이어 붙이되, 글자 사이 간격이 큰 곳(공백 문자였던 곳)에는 공백 1개를 넣는다."""
    s = seg[0]["c"]
    for a, b in zip(seg, seg[1:]):
        if b["x0"] - a["x1"] > 0.25 * max(a["size"], b["size"]):
            s += " "
        s += b["c"]
    return s


def extract_text_layer(path, dpi=150, render_check=True):
    doc = pymupdf.open(path)
    if doc.needs_pass:
        return [{"page": 0, "error": "password"}]
    res = []
    for p in doc:
        ch = chars_of(p)
        d = diagnose_page(p, ch, render_check)
        ok, why = usable(d)
        res.append({"page": p.number + 1, "diag": d, "usable": ok, "reasons": why,
                    "items": text_items(p, dpi, ch) if ok else [], "scale": dpi / 72})
    return res
