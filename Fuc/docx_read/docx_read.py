"""docx 직접 읽기(이미지로 바꾸지 않고 글자를 꺼냄)  (bridgedocs.py에서 나눔)"""
import re


# ============================================================================
# docx 직접 읽기: 이미지로 바꾸지 않고 docx 안의 글자를 꺼낸다(본문 문단·표의 한 행을 한 줄로, 취소선 글자는 뺌)
# ============================================================================

WNS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
       "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"}


# 긴 문서(임대차 표준계약서 약 5,800토큰)는 4,400토큰을 넘으면서 GPU 메모리(12GB, 화면이 약 2GB 사용)를 넘쳐
# 질문 하나에 55초가 걸렸다(2026-10-23). 값이 없는 긴 조항 줄을 빼고 줄을 200자로 잘라 약 2,000토큰으로 줄인다.
DOCX_MAX_CHARS = 4000
DOCX_KEEP = re.compile(r"\d{2,}|성\s*명|임대인|임차인|이름|소유자|매도인|매수인|근저당|압류|집합건물")


def _w(tag):
    p, t = tag.split(":")
    return "{%s}%s" % (WNS[p], t)


def _run_text(r):
    rp = r.find(_w("w:rPr"))
    if rp is not None:
        for s in (rp.find(_w("w:strike")), rp.find(_w("w:dstrike"))):
            if s is not None and s.get(_w("w:val")) not in ("0", "false"):
                return ""      # 취소선 = 말소된 글자
    return "".join((t.text or "") if t.tag == _w("w:t") else " " for t in r if t.tag in (_w("w:t"), _w("w:tab")))


def _para_text(p):
    """문단의 글자. 글상자 안 글자는 넣지 않는다(직접 자식 런만 본다)."""
    runs = [c for c in p if c.tag == _w("w:r")] + [r for c in p if c.tag in (_w("w:hyperlink"), _w("w:ins")) for r in c if r.tag == _w("w:r")]
    return re.sub(r"\s+", " ", "".join(_run_text(r) for r in runs)).strip()


def docx_lines(path):
    """-> OCR 줄과 같은 모양의 줄 목록 [{text, x0, x1, y0, y1, cy}]. 좌표는 순서만 뜻하는 가짜 값이다."""
    import xml.etree.ElementTree as ET
    import zipfile
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    xml = re.sub(r"<mc:Fallback>.*?</mc:Fallback>", "", xml, flags=re.S)     # 같은 글상자의 예전 형식 사본
    root = ET.fromstring(xml)
    texts = []

    def walk(el):
        for ch in el:
            if ch.tag == _w("w:p"):
                texts.append(_para_text(ch))
            elif ch.tag == _w("w:tbl"):
                for tr in ch.findall(_w("w:tr")):
                    cells = [" ".join(t for t in (_para_text(p) for p in tc.iter(_w("w:p"))) if t) for tc in tr.findall(_w("w:tc"))]
                    texts.append(" ".join(c for c in cells if c))
            elif ch.tag == _w("w:sdt"):
                walk(ch.find(_w("w:sdtContent")) if ch.find(_w("w:sdtContent")) is not None else ch)
    walk(root.find(_w("w:body")))
    boxes = []
    for a in root.iter(_w("wp:anchor")):
        ph, pv = a.find(_w("wp:positionH")), a.find(_w("wp:positionV"))
        t = " ".join(x for x in (_para_text(p) for p in a.iter(_w("w:p"))) if x)
        if t and ph is not None and pv is not None and ph.find(_w("wp:posOffset")) is not None and pv.find(_w("wp:posOffset")) is not None:
            boxes.append((int(pv.find(_w("wp:posOffset")).text), int(ph.find(_w("wp:posOffset")).text), t))
    rows = []
    for y, x, t in sorted(boxes):
        if rows and abs(rows[-1][0] - y) < 60000:      # 약 1.7mm 안이면 같은 줄
            rows[-1][1].append((x, t))
        else:
            rows.append((y, [(x, t)]))
    if rows:
        texts.append("[글상자에 채워진 값 — 위에서 아래 순서]")
        texts += [" ".join(t for _, t in sorted(r)) for _, r in rows]
    texts = [t for t in texts if t]
    return [{"text": t, "x0": 0, "x1": 1000, "y0": i * 20, "y1": i * 20 + 10, "cy": i * 20 + 5} for i, t in enumerate(texts)]


def ask_text(model, proc, prompt, max_new=64):
    import torch
    msgs = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
    text = proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    inputs = proc(text=[text], return_tensors="pt").to(model.device)
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=max_new, do_sample=False)
    return proc.batch_decode(out[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0].strip()
