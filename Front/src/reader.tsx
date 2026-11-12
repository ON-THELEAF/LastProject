import { useEffect, useRef, useState, type DragEvent } from "react";
import {
  call,
  READ_EXT,
  report,
  reviewCount,
  STATUS_LABEL,
  upload,
  type Doc,
  type Field,
  type PageImage,
} from "./api";
import { Badge, Button, Card, Icon, PageTitle, type Go } from "./ui";

type Waiting = { name: string; path: string; reading: boolean };
type Failed = { name: string; reason: string };
type Summary = { ok: number; fail: number; fresh: number };

const baseName = (name: string) => name.replace(/\.[^.]+$/, "").replace(/_p\d+$/, "");

// 읽기·확인·내보내기가 같은 문서를 본다(화면을 옮겨도 남게 App이 들고 있음).
export function useReaderStore() {
  const [waiting, setWaiting] = useState<Waiting[]>([]);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [failed, setFailed] = useState<Failed[]>([]);
  const [jobs, setJobs] = useState(0);
  const [progress, setProgress] = useState("");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [images, setImages] = useState<Record<string, PageImage[] | "loading">>({});
  const [formats, setFormats] = useState<string[]>(["xlsx"]);
  const since = useRef(0);

  useEffect(() => {
    if (!jobs) return;
    const timer = setInterval(async () => {
      try {
        const res = await call<{ events: ReaderEvent[]; next: number }>("read_events", {
          since: since.current,
        });
        since.current = res.next;
        res.events.forEach(apply);
      } catch (err) {
        setProgress(err instanceof Error ? err.message : String(err));
      }
    }, 800);
    return () => clearInterval(timer);
  }, [jobs]);

  type ReaderEvent =
    | { type: "progress"; text: string }
    | { type: "done"; doc: Doc }
    | { type: "failed"; name: string; reason: string }
    | { type: "finished"; ok: number; fail: number };

  const dropWaiting = (name: string) =>
    setWaiting((w) => w.filter((x) => baseName(x.name) !== baseName(name) && x.name !== name));

  function apply(e: ReaderEvent) {
    if (e.type === "progress") setProgress(e.text);
    if (e.type === "done") {
      setDocs((d) => [...d, e.doc]);
      dropWaiting(e.doc.source[0] ?? "");
    }
    if (e.type === "failed") {
      setFailed((f) => [...f, { name: e.name, reason: e.reason }]);
      dropWaiting(e.name);
    }
    if (e.type === "finished") {
      setJobs((n) => n - 1);
      setProgress("");
      setWaiting((w) => w.map((x) => ({ ...x, reading: false })));
      setSummary((s) => ({
        ok: (s?.ok ?? 0) + e.ok,
        fail: (s?.fail ?? 0) + e.fail,
        fresh: 0,
      }));
    }
  }

  const addFiles = async (files: File[]) => {
    const ok = files.filter((f) => READ_EXT.test(f.name));
    if (!ok.length) return;
    try {
      const up = await upload(ok);
      setWaiting((w) => [...w, ...up.map((u) => ({ ...u, reading: false }))]);
    } catch (err) {
      report(err);
    }
  };

  const start = async () => {
    const todo = waiting.filter((w) => !w.reading);
    if (!todo.length) return report("읽을 파일을 먼저 문서 목록에 놓아 주세요");
    try {
      await call("read_start", { files: todo.map((w) => w.path) });
      setWaiting((w) => w.map((x) => (todo.includes(x) ? { ...x, reading: true } : x)));
      setSummary(null);
      setJobs((n) => n + 1);
    } catch (err) {
      report(err);
    }
  };

  const loadImages = async (doc: Doc) => {
    if (images[doc.json_path]) return;
    setImages((m) => ({ ...m, [doc.json_path]: "loading" }));
    try {
      const pages = await call<PageImage[]>("page_images", { doc });
      setImages((m) => ({ ...m, [doc.json_path]: pages }));
    } catch {
      setImages((m) => ({ ...m, [doc.json_path]: [] }));
    }
  };

  const replaceDoc = async (doc: Doc) => {
    setDocs((ds) => ds.map((d) => (d.json_path === doc.json_path ? doc : d)));
    try {
      await call("save_doc", { doc });
    } catch (err) {
      report(err);
    }
  };

  return {
    waiting,
    docs,
    failed,
    reading: jobs > 0,
    progress,
    summary: summary && { ...summary, fresh: docs.filter((d) => d.new_type).length },
    images,
    formats,
    setFormats,
    addFiles,
    start,
    loadImages,
    replaceDoc,
  };
}

export type ReaderStore = ReturnType<typeof useReaderStore>;

// 한 칸 판정 뒤 다음 확인 필요 칸(없으면 null)
const nextReview = (doc: Doc, from: number) => {
  const order = [...doc.fields.keys()].map((i) => (from + 1 + i) % doc.fields.length);
  return order.find((i) => doc.fields[i].status === "review") ?? null;
};

async function applyValue(store: ReaderStore, doc: Doc, i: number, input: string) {
  const field = doc.fields[i];
  const original = field.read_value ?? field.value;
  if (!input.trim()) throw new Error("비교 값을 입력하세요");
  const parsed = await call<{ value: string | null; error: string }>("parse_value", {
    kind: field.kind,
    value: input,
  });
  if (parsed.error) throw new Error(`${field.label}: ${parsed.error}`);
  const status = parsed.value === original ? "confirmed" : "edited";
  const decided = await call<Field>("decide", { field, value: parsed.value, status });
  const next = { ...doc, fields: doc.fields.map((f, j) => (j === i ? decided : f)) };
  await store.replaceDoc(next);
  return next;
}

function PageView({
  doc,
  images,
  selected,
  onSelect,
  only,
}: {
  doc: Doc;
  images: PageImage[] | "loading" | undefined;
  selected: number | null;
  onSelect: (i: number) => void;
  only?: number;
}) {
  if (images === "loading" || images === undefined)
    return <p className="empty-view">쪽 이미지를 불러오는 중…</p>;
  if (!images.length)
    return <p className="empty-view">이 문서는 쪽 이미지를 보여 줄 수 없어요. 오른쪽 표로 확인하세요.</p>;
  return (
    <div className="page-shots">
      {images.map((img, p) =>
        only !== undefined && only !== p ? null : (
          <div className="page-shot" key={p}>
            <img src={`data:image/jpeg;base64,${img.src}`} alt={`${p + 1}쪽`} />
            {doc.fields.map((f, i) =>
              f.line_box && (f.page ?? 1) === p + 1 ? (
                <button
                  key={f.key}
                  title={`${f.label}: ${f.value}`}
                  className={`shot-box ${f.status === "review" ? "review" : "ok"} ${selected === i ? "on" : ""}`}
                  style={{
                    left: `${(f.line_box[0] / img.w) * 100}%`,
                    top: `${(f.line_box[1] / img.h) * 100}%`,
                    width: `${((f.line_box[2] - f.line_box[0]) / img.w) * 100}%`,
                    height: `${((f.line_box[3] - f.line_box[1]) / img.h) * 100}%`,
                  }}
                  onClick={() => onSelect(i)}
                />
              ) : null,
            )}
          </div>
        ),
      )}
    </div>
  );
}

type Row =
  | { kind: "doc"; doc: Doc; index: number }
  | { kind: "failed"; item: Failed }
  | { kind: "waiting"; item: Waiting };

export function Reader({ go, store }: { go: Go; store: ReaderStore }) {
  const [selectedField, setSelectedField] = useState<number | null>(null);
  const [selectedRow, setSelectedRow] = useState(0);
  const [listHidden, setListHidden] = useState(false);
  const [registerPrompt, setRegisterPrompt] = useState(false);
  const [compares, setCompares] = useState<Record<string, string>>({});

  const rows: Row[] = [
    ...store.docs.map((doc, index) => ({ kind: "doc" as const, doc, index })),
    ...store.failed.map((item) => ({ kind: "failed" as const, item })),
    ...store.waiting.map((item) => ({ kind: "waiting" as const, item })),
  ];
  const row = rows[selectedRow] ?? rows[0];
  const doc = row?.kind === "doc" ? row.doc : null;

  useEffect(() => {
    if (doc) store.loadImages(doc);
  }, [doc?.json_path]);

  const handleDrop = (event: DragEvent) => {
    event.preventDefault();
    store.addFiles(Array.from(event.dataTransfer.files));
  };

  const compareOf = (d: Doc, i: number) =>
    compares[`${d.json_path}|${d.fields[i].key}`] ?? d.fields[i].compare ?? "";

  const moveOn = (next: Doc, from: number) => {
    const n = nextReview(next, from);
    if (n !== null) return setSelectedField(n);
    const other = rows.findIndex((r) => r.kind === "doc" && r.doc !== doc && reviewCount(r.doc) > 0);
    setSelectedField(null);
    if (other >= 0) setSelectedRow(other);
  };

  const target = () => {
    if (!doc) throw new Error("먼저 읽은 문서를 고르세요");
    const i = selectedField ?? doc.fields.findIndex((f) => f.status === "review");
    if (i < 0) throw new Error("확인할 칸이 없어요");
    return i;
  };

  const run = (fn: () => Promise<void>) => () => fn().catch(report);

  const confirmField = run(async () => {
    const i = target();
    const f = doc!.fields[i];
    const decided = await call<Field>("decide", { field: f, value: f.value, status: "confirmed" });
    const next = { ...doc!, fields: doc!.fields.map((x, j) => (j === i ? decided : x)) };
    await store.replaceDoc(next);
    moveOn(next, i);
  });

  const applyCompare = run(async () => {
    const i = target();
    moveOn(await applyValue(store, doc!, i, compareOf(doc!, i)), i);
  });

  const undoField = run(async () => {
    const i = target();
    const back = await call<Field>("undo", { field: doc!.fields[i] });
    await store.replaceDoc({ ...doc!, fields: doc!.fields.map((x, j) => (j === i ? back : x)) });
  });

  const confirmAll = run(async () => {
    if (!doc) throw new Error("먼저 읽은 문서를 고르세요");
    const next = await call<Doc>("decide_all", { doc });
    await store.replaceDoc(next);
    moveOn(next, 0);
  });

  const renameField = (i: number, label: string) => {
    if (!doc) return;
    store.replaceDoc({ ...doc, fields: doc.fields.map((f, j) => (j === i ? { ...f, label } : f)) });
  };

  const newDocs = store.docs.filter((d) => d.new_type);
  const startExport = () => {
    if (!store.docs.length) return report("내보낼 문서가 없어요 — 먼저 문서를 읽으세요");
    if (newDocs.length) setRegisterPrompt(true);
    else go("export");
  };
  const register = run(async () => {
    for (const d of newDocs)
      await call("save_custom_type", { name: d.doc_name, labels: d.fields.map((f) => f.label) });
    go("export");
  });

  const waitingCount = store.waiting.filter((w) => !w.reading).length;
  const reviewLeft = doc ? reviewCount(doc) : 0;

  return (
    <main
      className="workspace reader-page"
      onDragOver={(event) => event.preventDefault()}
      onDrop={handleDrop}
    >
      <PageTitle>
        문서 읽기 <span>— 읽은 값을 확인하고 필요한 부분을 고쳐 주세요.</span>
      </PageTitle>
      <div className="action-strip">
        <p>
          <b>여러 파일을 문서 목록에 모은 뒤 [읽기 시작]을 누르세요</b>
          <span>파일을 한 번에 처리해 더 빠르고 안정적이에요.</span>
        </p>
        <div>
          <Button primary onClick={store.start}>
            읽기 시작 {waitingCount > 0 && `(${waitingCount}개)`}
          </Button>
        </div>
      </div>
      {store.reading ? (
        <div className="notice notice-warn">
          <Icon name="upload" size={17} />
          <b>문서를 읽는 중이에요</b>
          <span>{store.progress || "잠시만 기다려 주세요"}</span>
          <em>창을 닫지 마세요</em>
        </div>
      ) : waitingCount > 0 ? (
        <div className="notice notice-warn">
          <Icon name="upload" size={17} />
          <b>새 파일이 추가됐어요</b>
          <span>{waitingCount}개 파일이 읽기 대기 중이에요</span>
          <em>읽기 시작을 눌러 주세요</em>
        </div>
      ) : store.summary ? (
        <button className="notice notice-fail" onClick={() => go("verify")}>
          <Icon name="info" size={17} />
          <b>모든 문서의 읽기가 끝났어요</b>
          <span>
            성공 {store.summary.ok}개 · 새 문서 인식 {store.summary.fresh}개 · 오류{" "}
            {store.summary.fail}개
          </span>
          <em>오류 문서 확인하기 →</em>
        </button>
      ) : null}
      <div className={`reader-grid ${listHidden ? "list-hidden" : ""}`}>
        {!listHidden && (
          <Card className="doc-list">
            <div className="doc-list-head">
              <div>
                <h3>문서 목록</h3>
                <p>여러 파일을 올린 뒤 한 번에 읽어요</p>
              </div>
              <button
                className="collapse-list"
                onClick={() => setListHidden(true)}
                title="목록 숨기기"
              >
                <Icon name="sidebar" size={15} />
              </button>
            </div>
            {rows.map((r, index) => {
              const kind =
                r.kind === "failed"
                  ? "fail"
                  : r.kind === "waiting"
                    ? "waiting"
                    : r.doc.new_type
                      ? "new"
                      : reviewCount(r.doc)
                        ? "warn"
                        : "ok";
              const title = r.kind === "doc" ? r.doc.source.join(", ") : r.item.name;
              const sub =
                r.kind === "doc"
                  ? r.doc.doc_name
                  : r.kind === "failed"
                    ? "오류"
                    : r.item.reading
                      ? "읽는 중"
                      : "읽기 대기";
              return (
                <button
                  className={row === r ? "selected" : ""}
                  key={`${title}-${index}`}
                  onClick={() => {
                    setSelectedRow(index);
                    setSelectedField(null);
                  }}
                >
                  <i className={kind}>
                    {kind === "ok" ? "✓" : kind === "fail" ? "×" : kind === "new" ? "N" : "!"}
                  </i>
                  <span>
                    <b>{title}</b>
                    <small>{sub}</small>
                  </span>
                  {kind === "warn" && <Badge type="warn">확인 필요</Badge>}
                  {kind === "new" && <Badge type="info">새 문서</Badge>}
                </button>
              );
            })}
            <div className="mini-drop">
              <Icon name="plus" size={18} />
              <b>파일을 추가로 놓아주세요</b>
              <small>PDF · PNG · JPG · DOCX</small>
            </div>
          </Card>
        )}
        <Card className="doc-view">
          <div className="panel-top">
            {listHidden && (
              <button className="open-list" onClick={() => setListHidden(false)}>
                <Icon name="sidebar" size={14} /> 목록 열기
              </button>
            )}
            <p>
              <b>
                선택한 문서:{" "}
                {!row ? "없음" : row.kind === "doc" ? row.doc.source.join(", ") : row.item.name}
              </b>
              {doc && (
                <span>
                  {" "}
                  · {doc.doc_name} · {doc.new_type ? "칸 이름 수정 가능" : `확인할 칸 ${reviewLeft}개`}
                </span>
              )}
            </p>
          </div>
          {doc ? (
            <PageView
              doc={doc}
              images={store.images[doc.json_path]}
              selected={selectedField}
              onSelect={setSelectedField}
            />
          ) : (
            <p className="empty-view">
              {!row
                ? "파일을 이 화면에 끌어다 놓으세요."
                : row.kind === "failed"
                  ? `읽지 못했어요 — ${row.item.reason}`
                  : row.kind === "waiting" && row.item.reading
                    ? "읽는 중이에요."
                    : "[읽기 시작]을 누르면 읽어요."}
            </p>
          )}
        </Card>
        <Card className="recognized">
          <div className="recognized-head">
            <div>
              <h3>인식된 정보</h3>
              <p>원본 값은 그대로 두고 비교 값만 수정해 판독 정확도를 높여요.</p>
            </div>
            {doc?.new_type && <Badge type="warn">새 문서 · 칸 이름 수정 가능</Badge>}
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {["칸", "원본 값", "상태", "비교 값", "사유"].map((x) => (
                    <th key={x}>{x}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {doc?.fields.map((f, i) => (
                  <tr
                    key={f.key}
                    className={`${f.status === "review" ? "row-needs-check" : "row-confirmed"} ${selectedField === i ? "row-selected" : ""}`}
                    onClick={() => setSelectedField(i)}
                  >
                    <td>
                      {doc.new_type ? (
                        <input
                          className="field-name-input"
                          defaultValue={f.label}
                          onClick={(event) => event.stopPropagation()}
                          onBlur={(event) =>
                            event.target.value !== f.label && renameField(i, event.target.value)
                          }
                          aria-label={`${f.label} 칸 이름`}
                        />
                      ) : (
                        f.label
                      )}
                    </td>
                    <td>{(f.read_value ?? f.value) || "—"}</td>
                    <td>
                      <Badge type={f.status === "review" ? "warn" : "ok"}>
                        {STATUS_LABEL[f.status]}
                      </Badge>
                    </td>
                    <td>
                      <input
                        className="comparison-input"
                        value={compareOf(doc, i)}
                        placeholder="비교 값 입력"
                        onClick={(event) => event.stopPropagation()}
                        onFocus={() => setSelectedField(i)}
                        onChange={(event) =>
                          setCompares((c) => ({
                            ...c,
                            [`${doc.json_path}|${f.key}`]: event.target.value,
                          }))
                        }
                        aria-label={`${f.label} 비교 값`}
                      />
                    </td>
                    <td>{f.reason || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="button-row">
            {selectedField !== null && <Button onClick={confirmField}>이 칸 확정</Button>}
            <Button onClick={applyCompare}>비교 값 적용</Button>
            <Button onClick={undoField}>되돌리기</Button>
            <Button primary onClick={confirmAll}>
              한 번에 확정
            </Button>
          </div>
          {doc?.new_type && (
            <div className="new-document-tip">
              <Icon name="info" size={15} />
              새 문서의 칸 이름을 수정할 수 있어요. 내보낼 때 양식 등록 여부를 확인합니다.
            </div>
          )}
        </Card>
      </div>
      <div className="export-strip">
        <div>
          <Icon name="download" />
          <p>
            <b>확인이 끝났나요?</b>
            <span> 원하는 파일 형식을 고르고 결과를 저장하세요.</span>
          </p>
        </div>
        <div className="format-pills">
          {["xlsx", "csv", "txt"].map((k) => (
            <button
              key={k}
              className={store.formats.includes(k) ? "on" : ""}
              title={k === "txt" ? "기본 구분 기호: |" : undefined}
              onClick={() =>
                store.setFormats((f) => (f.includes(k) ? f.filter((x) => x !== k) : [...f, k]))
              }
            >
              {k.toUpperCase()}
            </button>
          ))}
        </div>
        <Button
          onClick={run(async () => {
            await call("open_json_folder", { docs: store.docs });
          })}
        >
          JSON 위치
        </Button>
        <Button primary onClick={startExport}>
          ↓ 내보내기
        </Button>
      </div>
      {registerPrompt && (
        <div className="modal-backdrop">
          <div className="modal register-modal">
            <div className="modal-title">
              <div>
                <h2>새 문서 양식을 등록할까요?</h2>
                <p>새로 인식한 문서의 칸 이름을 다음 읽기에도 사용할 수 있어요.</p>
              </div>
              <button onClick={() => setRegisterPrompt(false)}>×</button>
            </div>
            {newDocs.map((d) => (
              <div className="register-choice" key={d.json_path}>
                <Icon name="file" size={24} />
                <p>
                  <b>{d.source.join(", ")}</b>
                  <span>
                    {d.doc_name} · 새 칸 {d.fields.length}개
                  </span>
                </p>
                <Badge type="warn">새 문서</Badge>
              </div>
            ))}
            <div className="modal-actions">
              <Button onClick={() => go("export")}>등록하지 않고 내보내기</Button>
              <Button primary onClick={register}>
                양식 등록 후 내보내기
              </Button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

export function Verify({ go, store }: { go: Go; store: ReaderStore }) {
  const [current, setCurrent] = useState(0);
  const [page, setPage] = useState(0);
  const [listHidden, setListHidden] = useState(false);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const doc = store.docs[current] ?? store.docs[0];
  const images = doc ? store.images[doc.json_path] : undefined;
  const pageCount = Array.isArray(images) ? images.length : 0;

  useEffect(() => {
    if (doc) store.loadImages(doc);
    setPage(0);
  }, [doc?.json_path]);

  const total = store.docs.length;
  const done = store.docs.filter((d) => !reviewCount(d)).length;
  const left = store.docs.reduce((n, d) => n + reviewCount(d), 0);
  const need = doc ? doc.fields.map((f, i) => [f, i] as const).filter(([f]) => f.status === "review") : [];
  const autos = doc ? doc.fields.filter((f) => f.status !== "review") : [];

  return (
    <main className="workspace">
      <PageTitle
        right={
          <div className="title-actions">
            <b>
              확인 완료 {done} / 전체 {total}건
            </b>
            <Badge type={left ? "warn" : "ok"}>확인 필요 {left}개 항목</Badge>
            <Button primary onClick={() => go("export")}>
              내보내기
            </Button>
          </div>
        }
      >
        읽기 결과 확인
      </PageTitle>
      {store.failed.length > 0 && (
        <div className="notice notice-fail">
          <Icon name="info" size={17} />
          <b>읽지 못한 문서 {store.failed.length}개</b>
          <span>{store.failed.map((f) => `${f.name} — ${f.reason}`).join(" · ")}</span>
        </div>
      )}
      <div className="verify-grid">
        <Card className="verify-list">
          <div className="panel-heading">
            <h3>문서 목록</h3>
            <Button onClick={() => setListHidden(!listHidden)}>{listHidden ? "펼치기" : "접기"}</Button>
          </div>
          {!listHidden &&
            store.docs.map((d, i) => (
              <button
                className={d === doc ? "selected" : ""}
                key={d.json_path}
                onClick={() => setCurrent(i)}
              >
                <span className={reviewCount(d) ? "dot" : "dot done"} />
                <b>{d.source.join(", ")}</b>
                <Badge type={reviewCount(d) ? "warn" : "ok"}>{reviewCount(d) ? "확인 중" : "완료"}</Badge>
              </button>
            ))}
        </Card>
        <Card className="verify-paper">
          <div className="panel-heading">
            <div>
              <p>
                문서 종류: <b>{doc?.doc_name ?? "—"}</b>
              </p>
              <small>
                {pageCount ? page + 1 : 0}쪽 / 전체 {pageCount}쪽
              </small>
            </div>
          </div>
          {doc ? (
            <PageView doc={doc} images={images} selected={null} onSelect={() => {}} only={page} />
          ) : (
            <p className="empty-view">문서 읽기에서 문서를 먼저 읽으세요.</p>
          )}
          <div className="pager">
            <Button onClick={() => setPage(Math.max(0, page - 1))}>◀ 이전 쪽</Button>
            <b>
              {pageCount ? page + 1 : 0} / {pageCount}
            </b>
            <Button onClick={() => setPage(Math.min(pageCount - 1, page + 1))}>다음 쪽 ▶</Button>
          </div>
        </Card>
        <Card className="verify-info">
          <div className="panel-heading">
            <div>
              <h3>인식된 정보</h3>
              <p>
                확인 필요 {need.length} / 전체 {doc?.fields.length ?? 0}
              </p>
            </div>
            <Button
              primary
              onClick={() =>
                doc &&
                call<Doc>("decide_all", { doc }).then(store.replaceDoc).catch(report)
              }
            >
              이 문서 전체 확정
            </Button>
          </div>
          <h4 className="sub-title">확인이 필요한 항목</h4>
          {need.map(([f, i]) => {
            const key = `${doc!.json_path}|${f.key}`;
            return (
              <div className="check-card" key={f.key}>
                <div>
                  <b>{f.label}</b>
                  <span>
                    원문 값 <em>{f.value || "—"}</em>
                  </span>
                </div>
                <input
                  value={inputs[key] ?? (f.compare || f.value)}
                  onChange={(event) => setInputs((m) => ({ ...m, [key]: event.target.value }))}
                />
                <Button
                  onClick={() =>
                    applyValue(store, doc!, i, inputs[key] ?? (f.compare || f.value)).catch(report)
                  }
                >
                  확정
                </Button>
              </div>
            );
          })}
          <h4 className="sub-title auto">
            자동 확정된 항목 <Badge type="ok">{autos.length}개</Badge>
          </h4>
          {autos.map((f) => (
            <div className="auto-row" key={f.key}>
              <span>✓</span>
              <b>{f.label}</b>
              <p>{f.value || "—"}</p>
            </div>
          ))}
        </Card>
      </div>
    </main>
  );
}

export function ExportPage({ go, store }: { go: Go; store: ReaderStore }) {
  const [kinds, setKinds] = useState<string[]>(store.formats);
  const [append, setAppend] = useState(false);
  const [merge, setMerge] = useState(true);
  const [reviewFile, setReviewFile] = useState(true);
  const fields = store.docs.flatMap((d) => d.fields.map((f) => [d, f] as const));
  const reviewDocs = store.docs.filter((d) => reviewCount(d)).length;
  const left = fields.filter(([, f]) => f.status === "review").length;

  const exportAll = async () => {
    if (!kinds.length) return report("파일 형식을 하나 이상 고르세요");
    if (left && !confirm(`남은 확인 ${left}칸이 있어요. 그래도 내보낼까요?`)) return;
    try {
      const paths = await call<string[] | null>("export", {
        docs: store.docs,
        kinds,
        append,
        split: !merge,
        review_file: reviewFile,
        force: true,
      });
      if (paths) alert(`저장했어요\n${paths.join("\n")}`);
    } catch (err) {
      report(err);
    }
  };

  return (
    <main className="workspace">
      <PageTitle>내보내기</PageTitle>
      <div className="export-page-grid">
        <div>
          <Card className="summary">
            <h3>내보낼 결과 요약</h3>
            <div className="summary-grid">
              {[
                ["총 문서 수", `${store.docs.length}건`],
                ["확인 완료", `${store.docs.length - reviewDocs}건`],
                ["확인 필요", `${reviewDocs}건`],
                ["총 항목 수", `${fields.length}개`],
                ["자동 확정", `${fields.filter(([, f]) => f.status === "auto").length}개`],
                [
                  "수동 확인",
                  `${fields.filter(([, f]) => f.status === "confirmed" || f.status === "edited").length}개`,
                ],
              ].map((x, i) => (
                <div key={x[0]} className={i === 2 && reviewDocs ? "alert" : ""}>
                  <span>{x[0]}</span>
                  <b>{x[1]}</b>
                </div>
              ))}
            </div>
          </Card>
          <Card className="preview-card">
            <h3>저장 데이터 미리보기</h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {["파일", "문서 종류", "항목", "값", "상태"].map((x) => (
                      <th key={x}>{x}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {fields.map(([d, f], i) => (
                    <tr key={i}>
                      <td>{d.source.join(", ")}</td>
                      <td>{d.doc_name}</td>
                      <td>{f.label}</td>
                      <td>{f.value || "—"}</td>
                      <td>
                        <Badge type={f.status === "review" ? "warn" : "ok"}>
                          {STATUS_LABEL[f.status]}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
        <Card className="export-settings">
          <h3>파일 형식 선택</h3>
          <p>하나 이상 선택하세요</p>
          {[
            ["xlsx", "XLSX", "엑셀"],
            ["csv", "CSV", "쉼표 구분"],
            ["txt", "TXT", "텍스트(구분 기호 |)"],
          ].map((x) => (
            <label className="check-option" key={x[0]}>
              <input
                type="checkbox"
                checked={kinds.includes(x[0])}
                onChange={() =>
                  setKinds((k) => (k.includes(x[0]) ? k.filter((y) => y !== x[0]) : [...k, x[0]]))
                }
              />
              <span>
                <b>{x[1]}</b>
                <small>{x[2]}</small>
              </span>
            </label>
          ))}
          <hr />
          <h3>저장 방식</h3>
          <label className="radio">
            <input type="radio" name="save" checked={!append} onChange={() => setAppend(false)} /> 새
            파일로 저장
          </label>
          <label className="radio">
            <input type="radio" name="save" checked={append} onChange={() => setAppend(true)} /> 기존
            파일에 이어 붙이기
          </label>
          <p className="help">
            이어 붙이기 선택 시 기존 파일 끝에 행을 추가합니다. 같은 문서 종류 파일에만 이어 붙일 수
            있어요.
          </p>
          <hr />
          <h3>한 번에 내보내기 옵션</h3>
          <label className="radio">
            <input type="checkbox" checked={merge} onChange={() => setMerge(true)} /> 모든 문서를
            파일 하나로 통합
          </label>
          <label className="radio">
            <input type="checkbox" checked={!merge} onChange={() => setMerge(false)} /> 문서
            종류별로 파일 분리
          </label>
          <label className="radio">
            <input type="checkbox" checked={reviewFile} onChange={() => setReviewFile(!reviewFile)} />{" "}
            확인 필요 항목 별도 파일 생성
          </label>
          <div className="export-buttons">
            <Button primary onClick={exportAll}>
              <Icon name="download" size={16} /> 한 번에 내보내기
            </Button>
            <Button onClick={() => go("verify")}>확인(검증) 화면으로 돌아가기</Button>
          </div>
        </Card>
      </div>
    </main>
  );
}
