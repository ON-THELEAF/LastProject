"""화면 없이 동작 확인: python -m desktop.selfcheck [--out 폴더]

순서: 오프라인 설정 → 모델 확인 → 상가 임대차계약서 1건 만들기 → 그 계약서와 합성 등기부·건축물대장 읽기 → 확인 필요 칸 확정 → xlsx 내보내기.
결과를 한국어로 요약하고, 모두 성공하면 종료 코드 0. exe에서도 같은 순서로 쓴다(모델·오프라인 확인용).
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from desktop import offline

offline.ensure_offline()

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

from desktop.service import DesktopService  # noqa: E402

FAKE_DIR = Path(__file__).parent / "fake_data"
SAMPLE = {"st_lessor": "김철수", "st_lessee": "이영희", "st_deposit": "50,000,000", "st_rent": "1,000,000", "st_mgmt_fee": "100,000",
          "st_start": "2025-03-01", "st_end": "2027-02-28"}
READ_LIMIT_MS = 30 * 60 * 1000     # 읽기가 이보다 오래 걸리면 멈춘 것으로 본다


def make_sample(svc, out: Path) -> Path:
    data = out / "selfcheck_data.json"
    data.write_text(json.dumps([{"source": ["selfcheck_store.pdf"], "doc_type": "store_lease",
                                 "fields": [{"key": k, "value": v} for k, v in SAMPLE.items()]}], ensure_ascii=False), encoding="utf-8")
    docs = svc.prepare_make(data)
    res = svc.submit_make(docs, [{"name": d["name"], "make": True, "values": d["values"]} for d in docs], out / "만든 문서")
    if res["problems"] or not res["results"][0]["path"]:
        raise ValueError(f"문서 만들기 실패 — {res['problems'] or res['results'][0]['message']}")
    return res["results"][0]["path"]


def read_all(svc, files: list[Path], out: Path) -> tuple[list[dict], list[tuple[str, str]]]:
    app = QCoreApplication.instance() or QCoreApplication([])
    done, failed = [], []
    job = svc.start_read(files, out)
    job.progress.connect(lambda s: print("  ·", s, flush=True))
    job.doc_done.connect(done.append)
    job.doc_failed.connect(lambda name, why: failed.append((name, why)))
    job.finished.connect(lambda *_: app.quit())
    QTimer.singleShot(READ_LIMIT_MS, app.quit)
    app.exec()
    svc.wait_all()
    return done, failed


def confirm_all(svc, docs: list[dict]) -> list[dict]:
    """사람이 확인한 셈 치고 확인 필요 칸을 읽은 값 그대로 확정한다(내보내기 확인용)."""
    return [{**d, "fields": [svc.decide(f, f["value"], "confirmed") if f["status"] == "review" else f for f in d["fields"]]} for d in docs]


def run(svc, out: Path) -> int:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    st = svc.status()
    print("모델:", st["message"])
    if not st["models_ok"]:
        return 1
    t0 = time.time()
    try:
        made = make_sample(svc, out)
    except ValueError as err:
        print(err)
        return 1
    print(f"문서 만들기: {made.name} ({time.time() - t0:.1f}초)")
    files = [made, *sorted(FAKE_DIR.glob("clean_s01_*_p*.png"))]
    t1 = time.time()
    docs, failed = read_all(svc, files, out / "읽은 결과")
    secs = time.time() - t1
    print(f"문서 읽기: 성공 {len(docs)}건 · 실패 {len(failed)}건 ({secs:.1f}초)")
    for d in docs:
        s = d["summary"]
        print(f"  - {', '.join(Path(f).name for f in d['source'])}: {d['doc_type']} · 자동 확정 {s['auto']}칸 · 확인 필요 {s['review']}칸")
    for name, why in failed:
        print(f"  - {name}: 실패 — {why}")
    lessor = next((f["value"] for d in docs if d["doc_type"] == "store_lease" for f in d["fields"] if f["key"] == "st_lessor"), None)
    if lessor is not None:
        print(f"  만든 계약서 다시 읽기: 임대인 '{lessor}' ({'일치' if lessor == SAMPLE['st_lessor'] else '불일치 — 확인 필요'})")
    if not docs:
        return 1
    try:
        paths = svc.export(confirm_all(svc, docs), ["xlsx"], out / "확정")
    except ValueError as err:
        print("내보내기 실패:", err)
        return 1
    print("내보내기:", paths[0])
    print(f"전체 {time.time() - t0:.1f}초 · 결과 폴더 {out}")
    return 0 if not failed else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="BridgeDocs 동작 확인(화면 없이)")
    ap.add_argument("--out", type=Path, default=None, help="결과 폴더(기본: 임시 폴더)")
    args = ap.parse_args(argv)
    return run(DesktopService(), args.out or Path(tempfile.mkdtemp(prefix="bridgedocs_selfcheck_")))


if __name__ == "__main__":
    sys.exit(main())
