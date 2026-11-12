"""결과 보기: 결과 JSON을 판별 화면 HTML 한 장으로  (bridgedocs.py에서 나눔)"""
import base64
import io
import json
from pathlib import Path


# ============================================================================
# 결과 보기: 결과·확정 JSON → 인터넷 없이 여는 HTML 한 장(쪽 이미지 + 읽은 위치 + 칸 표 + 클릭 판별·내려받기)
# ============================================================================

VIEW_HTML = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BridgeDocs 결과 보기</title><style>
:root{--bg:#f6f8fa;--card:#fff;--fg:#1f2328;--muted:#656d76;--line:#d0d7de;--hl:#fff8c5}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--fg:#e6edf3;--muted:#8d96a0;--line:#30363d;--hl:#3b2e00}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 "Malgun Gothic",sans-serif}
main{max-width:1200px;margin:auto;padding:16px}h1{font-size:22px;margin:8px 0}.muted{color:var(--muted)}
.flow{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 12px}.flow span{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:4px 10px}
.bar{position:sticky;top:0;z-index:5;background:var(--bg);padding:8px 0;display:flex;flex-wrap:wrap;gap:10px;align-items:center;border-bottom:1px solid var(--line);margin-bottom:14px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:4px 12px}.stat b{font-size:18px;margin-left:6px}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;padding:4px 10px;cursor:pointer}
button:hover{border-color:#0969da}button:disabled{opacity:.45;cursor:not-allowed}button.main{background:#0969da;color:#fff;border-color:#0969da}
.doc{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:18px}
.doc h2{font-size:17px;margin:0 0 10px}.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.1fr);gap:14px}
@media (max-width:800px){.grid{grid-template-columns:1fr}}
.page{position:relative;margin-bottom:8px}.page img{width:100%;display:block;border:1px solid var(--line);border-radius:6px}
.box{position:absolute;border:3px solid;border-radius:3px;cursor:pointer}.box.on{box-shadow:0 0 0 4px rgba(255,200,0,.8)}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}
tr.f{cursor:pointer}tr.f.on{background:var(--hl)}td:first-child,th{white-space:nowrap}
.badge{color:#fff;border-radius:10px;padding:1px 8px;font-size:12px;white-space:nowrap}
.old{text-decoration:line-through;color:var(--muted);margin-left:6px}
.judge{margin-top:6px;display:flex;flex-wrap:wrap;gap:6px;align-items:center}.judge input{font:inherit;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);min-width:0;width:11em}
.err{color:#cf222e;font-size:13px;width:100%}
dialog{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:10px;padding:18px 20px;width:min(420px,calc(100vw - 32px));box-sizing:border-box}
dialog::backdrop{background:rgba(0,0,0,.45)}dialog h3{margin:0 0 4px}dialog label{display:block;margin:8px 0}
dialog label.name input{font:inherit;margin-left:6px;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);width:12em}
dialog .acts{display:flex;justify-content:flex-end;gap:8px;margin-top:12px}
</style></head><body><main>
<h1>BridgeDocs 결과 보기</h1>
<p class="muted">표의 줄이나 그림의 상자를 누르면 서로 위치를 짚어 줍니다. 빨간 "확인 필요" 칸은 후보를 누르거나 직접 입력해 확정하세요. 인터넷 없이 동작합니다.</p>
<div class="flow"><span>① 문서 입력 (쪽 이미지 · 기울기 보정 · 종류 판별)</span><span>② 문서 분석 (PDF 안 글자 · OCR · AI · docx)</span><span>③ 환각검증 및 사용자 판별</span><span>④ 데이터 추출 (엑셀 · CSV · JSON)</span></div>
<div class="bar" id="bar"></div><div id="docs"></div></main>
<dialog id="exp"><h3>내보내기</h3><p class="muted">만들 파일 형식을 고르세요. 고른 것마다 파일이 하나씩 내려받아집니다.</p>
<label><input type="checkbox" value="xlsx" checked> 엑셀 (.xlsx)</label>
<label><input type="checkbox" value="csv"> CSV (.csv) — 엑셀·한글에서 열림</label>
<label><input type="checkbox" value="json"> JSON (.json) — 다른 프로그램 연결용(위치·사유 등 전체 정보)</label>
<label class="name">파일 이름 <input id="expname" value="확정"></label>
<div class="err" id="experr"></div>
<div class="acts"><button id="expno">취소</button><button class="main" id="expgo">내보내기</button></div></dialog>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById("data").textContent);
const DOCS = DATA.docs, ST = DATA.status, COLOR = DATA.color;
const el = (tag, { dataset, ...props } = {}, ...kids) => {   // dataset은 읽기 전용이라 따로 넣는다
  const e = Object.assign(document.createElement(tag), props);
  Object.assign(e.dataset, dataset || {});
  e.append(...kids.filter(k => k != null));
  return e;
};

// 사용자가 직접 입력한 값 검사(없는 날짜, "1억" 같은 금액, 한 글자 이름 등)
function parseValue(kind, s) {
  s = s.trim();
  if (!s) return [null, "빈 값"];
  if (kind === "date") {
    const m = s.match(/(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})/);
    if (!m) return [null, "날짜는 2025-06-13처럼 연-월-일로 입력"];
    const d = new Date(+m[1], m[2] - 1, +m[3]);
    if (d.getMonth() !== m[2] - 1 || d.getDate() !== +m[3]) return [null, "없는 날짜"];
    return [`${m[1]}-${m[2].padStart(2, "0")}-${m[3].padStart(2, "0")}`, ""];
  }
  if (kind === "amount") return /^[\d,]+\s*원?$/.test(s) ? [s.match(/[\d,]+/)[0], ""] : [null, "금액은 숫자로 입력(쉼표 가능, 예: 100,000,000)"];
  if (kind === "flag") return s.includes("있") ? ["있음", ""] : s.includes("없") ? ["없음", ""] : [null, "'있음' 또는 '없음'으로 입력"];
  if (kind === "name") return (s.match(/[가-힣]/g) || []).length >= 2 ? [s, ""] : [null, "이름은 한글 2자 이상"];
  if (kind === "number") return /^\d+(\.\d+)?\s*(㎡|점)?$/.test(s) ? [s.match(/[\d.]+/)[0], ""] : [null, "숫자로 입력(예: 84.97)"];
  return [s, ""];
}

function decide(f, value, status) {
  if (!("read_value" in f)) f.read_value = f.value;
  if (!("orig" in f)) f.orig = { value: f.value, status: f.status };
  Object.assign(f, { value, status });
  render();
}
function undo(f) { Object.assign(f, f.orig); delete f.orig; delete f.read_value; render(); }

function focus(di, fi) {
  document.querySelectorAll(".on").forEach(n => n.classList.remove("on"));
  document.querySelectorAll(`[data-k="${di}-${fi}"]`).forEach(n => { n.classList.add("on"); });
  const box = document.querySelector(`.box[data-k="${di}-${fi}"]`);
  if (box) box.scrollIntoView({ block: "center", behavior: "smooth" });
}

function judgeControls(f) {
  const cands = f.candidates && f.candidates.length ? f.candidates : (f.value ? [f.value] : []);
  const err = el("div", { className: "err" });
  const input = el("input", { placeholder: "직접 입력", onclick: e => e.stopPropagation() });
  const typed = () => { const [v, e] = parseValue(f.kind, input.value); if (e) { err.textContent = e; return; } decide(f, v, cands.includes(v) ? "confirmed" : "edited"); };
  input.onkeydown = e => { if (e.key === "Enter") typed(); };
  return el("div", { className: "judge" },
    ...cands.map((c, i) => el("button", { textContent: `${i + 1}) ${c}`, title: "이 값으로 확정", onclick: e => { e.stopPropagation(); decide(f, c, "confirmed"); } })),
    input, el("button", { textContent: "입력", onclick: e => { e.stopPropagation(); typed(); } }), err);
}

function render() {
  const all = DOCS.flatMap(d => d.fields), cnt = s => all.filter(f => f.status === s).length, left = cnt("review");
  const bar = document.getElementById("bar");
  bar.replaceChildren(el("span", { className: "stat" }, "문서", el("b", { textContent: DOCS.length })),
    ...Object.keys(ST).filter(s => cnt(s)).map(s => el("span", { className: "stat", style: `border-color:${COLOR[s]}` }, ST[s], el("b", { textContent: cnt(s), style: `color:${COLOR[s]}` }))),
    el("span", { className: "muted", textContent: left ? `남은 확인 ${left}칸 — 다 끝내면 내보낼 수 있어요` : "모든 칸 확정 완료" }),
    el("button", { className: "main", textContent: "내보내기", disabled: !!left, onclick: () => { document.getElementById("experr").textContent = ""; document.getElementById("exp").showModal(); } }));
  const root = document.getElementById("docs");
  root.replaceChildren(...DOCS.map((d, di) => {
    const pages = (d.images || []).map((im, pi) => el("div", { className: "page" }, el("img", { src: "data:image/jpeg;base64," + im.src, alt: `${d.source[0]} ${pi + 1}쪽` }),
      ...d.fields.map((f, fi) => [f, fi]).filter(([f]) => f.page === pi + 1 && f.line_box).map(([f, fi]) => {
        const [x0, y0, x1, y1] = f.line_box;
        return el("div", { className: "box", title: `${f.label}: ${f.value}`, onclick: () => focus(di, fi), dataset: { k: `${di}-${fi}` },
          style: `left:${x0 / im.w * 100}%;top:${y0 / im.h * 100}%;width:${(x1 - x0) / im.w * 100}%;height:${(y1 - y0) / im.h * 100}%;border-color:${COLOR[f.status]}` });
      })));
    const rows = d.fields.map((f, fi) => {
      const val = el("td", {}, f.value ? f.value : el("span", { className: "muted", textContent: "(읽지 못함)" }),
        f.read_value != null && f.read_value !== f.value ? el("span", { className: "old", textContent: f.read_value }) : null);
      const note = el("td", {}, f.reason || "", f.status === "review" ? judgeControls(f) : null,
        f.orig ? el("div", {}, el("button", { textContent: "되돌리기", onclick: e => { e.stopPropagation(); undo(f); } })) : null);
      return el("tr", { className: "f", onclick: () => focus(di, fi), dataset: { k: `${di}-${fi}` } }, el("td", { textContent: f.label }), val,
        el("td", {}, el("span", { className: "badge", textContent: ST[f.status], style: `background:${COLOR[f.status]}` })), note, el("td", { textContent: f.page }));
    });
    const route = [...new Set((d.route || []).map(r => DATA.routeLabel[r] || r))].join(" · ");
    const isDocx = (d.route || []).join() === "docx";
    return el("section", { className: "doc" }, el("h2", {}, (DATA.docLabel[d.doc_type] || d.doc_type) + " ",
        el("span", { className: "muted", textContent: "· " + (d.source || []).join(", ") + (route ? ` · 읽은 길: ${route}` : "") })),
      el("div", { className: "grid" }, el("div", {}, ...(pages.length ? pages : [el("p", { className: "muted",
        textContent: isDocx ? "docx는 쪽 이미지 없이 문서 안의 글자로 읽었어요" : "원본 이미지를 찾을 수 없음" })])),
        el("div", {}, el("table", {}, el("tr", {}, ...["항목", "값", "상태", "사유 · 판별", "쪽"].map(t => el("th", { textContent: t }))), ...rows))));
  }));
}

const cleanDocs = () => DOCS.map(({ images, ...d }) => ({ ...d, fields: d.fields.map(({ orig, ...f }) => f),
  summary: Object.fromEntries(["auto", "confirmed", "edited"].map(s => [s, d.fields.filter(f => f.status === s).length])) }));
const toRows = () => [DATA.columns, ...DOCS.flatMap(d => d.fields.map(f => [(d.source || []).join(", "), DATA.docLabel[d.doc_type] || d.doc_type,
  f.label, f.value, ST[f.status], f.read_value ?? f.value, f.reason, f.page]))];
function toCsv(rows) {
  const q = v => /[",\r\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v);
  return rows.map(r => r.map(q).join(",")).join("\r\n");
}
// --- xlsx start: 라이브러리 없이(인터넷 불필요) xlsx = XML 5개를 무압축 zip으로 묶는다
const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
const crc32 = b => { let c = ~0; for (const x of b) c = CRC[(c ^ x) & 255] ^ (c >>> 8); return ~c >>> 0; };
function zipStore(files) {
  const enc = new TextEncoder(), local = [], central = [];
  let off = 0;
  for (const [name, text] of files) {
    const n = enc.encode(name), d = enc.encode(text), c = crc32(d);
    const h = new DataView(new ArrayBuffer(30)), e = new DataView(new ArrayBuffer(46));
    [[0, 0x04034b50, 4], [4, 20, 2], [6, 0x0800, 2], [12, 33, 2], [14, c, 4], [18, d.length, 4], [22, d.length, 4], [26, n.length, 2]]
      .forEach(([o, v, s]) => s === 4 ? h.setUint32(o, v, true) : h.setUint16(o, v, true));
    [[0, 0x02014b50, 4], [4, 20, 2], [6, 20, 2], [8, 0x0800, 2], [14, 33, 2], [16, c, 4], [20, d.length, 4], [24, d.length, 4], [28, n.length, 2], [42, off, 4]]
      .forEach(([o, v, s]) => s === 4 ? e.setUint32(o, v, true) : e.setUint16(o, v, true));
    local.push(new Uint8Array(h.buffer), n, d);
    central.push(new Uint8Array(e.buffer), n);
    off += 30 + n.length + d.length;
  }
  const size = central.reduce((s, a) => s + a.length, 0), end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
  end.setUint32(12, size, true); end.setUint32(16, off, true);
  return new Blob([...local, ...central, new Uint8Array(end.buffer)], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
}
function toXlsx(rows, sheet = "추출 결과") {
  const x = s => String(s).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const col = i => String.fromCharCode(65 + i);   // 열은 8개뿐(A~H)
  const cell = (v, i, r) => typeof v === "number" ? `<c r="${col(i)}${r}"><v>${v}</v></c>`
    : `<c r="${col(i)}${r}" t="inlineStr"><is><t xml:space="preserve">${x(v ?? "")}</t></is></c>`;
  const body = rows.map((row, ri) => `<row r="${ri + 1}">${row.map((v, i) => cell(v, i, ri + 1)).join("")}</row>`).join("");
  const X = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', M = "http://schemas.openxmlformats.org/", R = M + "officeDocument/2006/relationships";
  return zipStore([
    ["[Content_Types].xml", `${X}<Types xmlns="${M}package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>`],
    ["_rels/.rels", `${X}<Relationships xmlns="${M}package/2006/relationships"><Relationship Id="rId1" Type="${R}/officeDocument" Target="xl/workbook.xml"/></Relationships>`],
    ["xl/workbook.xml", `${X}<workbook xmlns="${M}spreadsheetml/2006/main" xmlns:r="${R}"><sheets><sheet name="${x(sheet)}" sheetId="1" r:id="rId1"/></sheets></workbook>`],
    ["xl/_rels/workbook.xml.rels", `${X}<Relationships xmlns="${M}package/2006/relationships"><Relationship Id="rId1" Type="${R}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>`],
    ["xl/worksheets/sheet1.xml", `${X}<worksheet xmlns="${M}spreadsheetml/2006/main"><sheetData>${body}</sheetData></worksheet>`]]);
}
// --- xlsx end
const EXPORTERS = {
  json: () => new Blob([JSON.stringify(cleanDocs(), null, 1)], { type: "application/json" }),
  csv: () => new Blob(["﻿" + toCsv(toRows())], { type: "text/csv" }),   // BOM: 엑셀에서 열어도 한글이 안 깨짐
  xlsx: () => toXlsx(toRows()),
};
function save(name, blob) { const a = el("a", { href: URL.createObjectURL(blob), download: name }); document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000); }
function doExport() {
  const picked = [...document.querySelectorAll("#exp input[type=checkbox]:checked")].map(c => c.value);
  if (!picked.length) { document.getElementById("experr").textContent = "형식을 하나 이상 고르세요"; return; }
  const base = document.getElementById("expname").value.trim().replace(/[\\/:*?"<>|]/g, "") || "확정";
  picked.forEach((fmt, i) => setTimeout(() => save(`${base}.${fmt}`, EXPORTERS[fmt]()), i * 300));   // 여러 개를 한꺼번에 받으면 브라우저가 막을 수 있어 간격을 둔다
  document.getElementById("exp").close();
}
document.getElementById("expgo").onclick = doExport;
document.getElementById("expno").onclick = () => document.getElementById("exp").close();
render();
</script></body></html>"""


def image_payload(img, width=900):
    """쪽 이미지 → {"src": base64 JPEG(가로 width), "w", "h"(원래 크기)}."""
    buf = io.BytesIO()
    img.convert("RGB").resize((width, round(img.height * width / img.width))).save(buf, "JPEG", quality=80)
    return {"src": base64.b64encode(buf.getvalue()).decode(), "w": img.width, "h": img.height}


def page_images(doc, width=900):
    """쪽 이미지(읽을 때와 같은 보정)를 base64 JPEG로. 상자는 브라우저가 원래 크기 비율로 그린다. 원본이 없으면 빈 목록."""
    files = doc.get("files") or []
    if not files or not all(Path(p).exists() for p in files):
        return []
    out = []
    if doc.get("route") == ["docx"]:
        return []
    routes = doc.get("route") or []
    for i, pg in enumerate(load_pages(files)):
        if doc.get("deskew") and (routes[i] if i < len(routes) else "ocr") == "ocr":     # PDF 안 글자로 읽은 쪽은 보정하지 않았다
            pg = preprocess(pg)[0]
        out.append(image_payload(pg, width))
    return out


def render_view(docs):
    """결과(또는 확정) 문서들 → 판별까지 할 수 있는 HTML 한 장. 값은 JSON으로 넣고 화면은 브라우저가 textContent로 그린다(문서 글자가 HTML로 해석되지 않음)."""
    payload = {"docs": [{**d, "images": page_images(d)} for d in docs], "status": STATUS_LABEL,
               "color": {"auto": "#1a7f37", "confirmed": "#0969da", "edited": "#bc4c00", "review": "#cf222e"},
               "docLabel": DOC_TYPE_LABEL, "routeLabel": ROUTE_LABEL, "columns": EXPORT_COLUMNS}
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")     # 값 속 "</script>"가 스크립트를 끝내지 않도록
    return VIEW_HTML.replace("__DATA__", data)


# 함수 안에서만 쓰는 다른 기능의 이름: 맨 아래에서 불러온다(서로 부르는 기능끼리 순환해도 됨)
from Fuc.read_pipeline.read_pipeline import DOC_TYPE_LABEL, EXPORT_COLUMNS, ROUTE_LABEL, STATUS_LABEL, load_pages  # noqa: E402,F401
from Fuc.read_pipeline.preprocess import preprocess  # noqa: E402,F401
