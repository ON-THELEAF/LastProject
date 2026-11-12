import { useEffect, useState, type DragEvent } from "react";
import { call, DATA_EXT, pickFiles, report, stem, upload, type MakeDoc, type Template, type Uploaded } from "./api";
import { Badge, Button, Card, Icon, PageTitle, StageHeader, Stepper, type Go } from "./ui";

const KINDS: [string, string][] = [
  ["docx", "워드 문서(.docx)"],
  ["pdf", "PDF로 변환"],
  ["png", "이미지(.png)로 변환"],
];

// 문서 만들기 화면들(한 장짜리 화면과 1~3단계 화면)이 같이 쓰는 값. 화면을 옮겨도 남게 App이 들고 있다.
export function useMakeStore(templates: Template[]) {
  const [data, setDataFile] = useState<Uploaded | null>(null);
  const [rows, setRows] = useState<string[][]>([]);
  const [header, setHeader] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [prepared, setPrepared] = useState<Record<string, MakeDoc[] | string>>({});
  const [edits, setEdits] = useState<Record<string, Record<string, Record<string, string>>>>({});
  const [outDir, setOutDir] = useState("");
  const [name, setName] = useState("문서");
  const [kinds, setKinds] = useState<string[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  const [made, setMade] = useState<string[]>([]);

  useEffect(() => {
    call<{ out_dir: string }>("status")
      .then((s) => setOutDir((d) => d || s.out_dir))
      .catch(report);
  }, []);

  const setData = async (files: File[]) => {
    const file = files.find((f) => DATA_EXT.test(f.name));
    if (!file) return report("데이터 파일(CSV·TXT·XLSX·JSON)을 고르세요");
    try {
      const [up] = await upload([file]);
      setRows(await call<string[][]>("preview_data", { path: up.path, n: 100000 }));
      setDataFile(up);
      setHeader(false);
      setPrepared({});
      setEdits({});
    } catch (err) {
      report(err);
    }
  };

  const prepare = async (ids: string[], head = header) => {
    if (!data) return report("데이터 파일을 먼저 고르세요");
    const out: Record<string, MakeDoc[] | string> = {};
    for (const id of ids) {
      const t = templates.find((x) => x.id === id);
      if (!t) continue;
      try {
        out[id] = await call<MakeDoc[]>("prepare_make", { data: data.path, template: t.path, header: head });
      } catch (err) {
        out[id] = err instanceof Error ? err.message : String(err);
      }
    }
    setPrepared((p) => ({ ...p, ...out }));
    return out;
  };

  const docsOf = (id: string) => (Array.isArray(prepared[id]) ? (prepared[id] as MakeDoc[]) : []);
  const valueOf = (id: string, doc: MakeDoc, key: string) => edits[id]?.[doc.name]?.[key] ?? doc.values[key] ?? "";
  const edit = (id: string, doc: string, key: string, value: string) =>
    setEdits((e) => ({ ...e, [id]: { ...e[id], [doc]: { ...e[id]?.[doc], [key]: value } } }));

  const items = selected.flatMap((id) => {
    const t = templates.find((x) => x.id === id);
    return docsOf(id).map((d) => {
      const key = `${id}|${d.name}`;
      return { id, key, doc: d.name, template: t ? stem(t.name) : "", name: names[key] ?? `${t ? stem(t.name) : ""}_${d.name}` };
    });
  });

  const pickFolder = async () => {
    try {
      const dir = await call<string | null>("pick_folder", { initial: outDir });
      if (dir) setOutDir(dir);
    } catch (err) {
      report(err);
    }
  };

  const run = async () => {
    if (!data) throw new Error("데이터 파일을 먼저 고르세요");
    const all: string[] = [];
    for (const id of selected) {
      const t = templates.find((x) => x.id === id)!;
      all.push(
        ...(await call<string[]>("make", {
          data: data.path,
          header,
          template: t.path,
          out_dir: outDir,
          values: edits[id] ?? {},
          names: items.filter((x) => x.id === id).map((x) => x.name),
          kinds,
        })),
      );
    }
    setMade(all.filter((p) => p.toLowerCase().endsWith(".docx")));
    return all;
  };

  return {
    data, rows, header, setHeader, selected, setSelected, prepared, prepare, docsOf, valueOf, edit,
    outDir, pickFolder, name, setName, kinds, setKinds, items, setNames, made, setMade, setData, run,
  };
}

export type MakeStore = ReturnType<typeof useMakeStore>;

const slotName = (key: string) => key.replace(/^\d+\.\s*/, "") || "(이름 없는 빈칸)";

export function Builder({ go, store, templates }: { go: Go; store: MakeStore; templates: Template[] }) {
  const [editing, setEditing] = useState<string | null>(null);
  const current = store.selected[0] ?? templates[0]?.id ?? "";
  const template = templates.find((t) => t.id === current);
  const docs = store.docsOf(current);
  const problem = typeof store.prepared[current] === "string" ? (store.prepared[current] as string) : "";

  const handleDrop = (event: DragEvent) => {
    event.preventDefault();
    store.setData(Array.from(event.dataTransfer.files)).then(() => go("data"));
  };

  const convert = (kind: string) => async () => {
    if (!store.made.length) return report("내보낼 문서가 없어요 — 먼저 [만들기]를 누르세요");
    try {
      const paths = await call<string[]>("convert", { paths: store.made, kind });
      alert(`저장했어요\n${paths.join("\n")}`);
    } catch (err) {
      report(err);
    }
  };

  return (
    <main className="workspace builder-page">
      <PageTitle>
        문서 만들기 <span>— 데이터를 불러와 내용을 확인하고 새 문서를 만드세요.</span>
      </PageTitle>
      <div className="builder-grid">
        <div className="stage-cards">
          <Card className="stage">
            <StageHeader n="1" title="데이터 불러오기" desc="CSV, TXT, XLSX 파일을 쓸 수 있어요." />
            <div className="stage-body">
              <div className="file-line">
                <Icon name="table" />
                <p>
                  <b>{store.data ? store.data.name : "데이터 파일을 고르세요"}</b>
                  <span>
                    {store.data
                      ? `${store.rows.length - (store.header ? 1 : 0)}줄 · 한 줄이 문서 하나예요`
                      : "CSV · TXT · XLSX · JSON | 한 줄이 문서 하나예요"}
                  </span>
                </p>
                <Button primary onClick={() => go("data")}>
                  데이터 고르기
                </Button>
              </div>
              <button
                className="slim-drop"
                onClick={() => go("data")}
                onDragOver={(event) => event.preventDefault()}
                onDrop={handleDrop}
              >
                데이터 파일을 끌어다 놓아도 돼요
              </button>
            </div>
          </Card>
          <Card className="stage">
            <StageHeader n="2" title="양식과 내용 확인" desc="값을 두 번 눌러 직접 고칠 수 있어요." />
            <div className="stage-body">
              <div className="template-bar">
                <label>
                  문서 양식{" "}
                  <select value={current} onChange={(event) => store.setSelected([event.target.value])}>
                    {!templates.length && <option value="">등록된 양식이 없어요</option>}
                    {templates.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </label>
                <Button onClick={() => go("documents")}>문서 관리</Button>
                <Button
                  primary
                  onClick={() => (current ? store.prepare([current]) : report("문서 관리에서 양식을 먼저 추가하세요"))}
                >
                  불러오기
                </Button>
              </div>
              <p className="mapping-help">
                데이터 한 줄이 문서 하나예요. 값을 왼쪽부터 순서대로 양식의 빈칸에 넣어 docx로
                만들어요.
                <br />
                <b>
                  이 양식의 빈칸 순서:{" "}
                  {template?.slots.map((s, i) => `${i + 1}.{{${s}}}`).join(" ") || "—"}
                </b>
              </p>
              {problem && <p className="mapping-help">{problem}</p>}
              <table className="compact-table">
                <thead>
                  <tr>
                    <th>문서</th>
                    <th>빈칸</th>
                    <th>값</th>
                    <th>알림</th>
                  </tr>
                </thead>
                <tbody>
                  {docs.map((d) =>
                    Object.keys(d.values).map((key, i) => {
                      const value = store.valueOf(current, d, key);
                      const id = `${d.name}|${key}`;
                      return (
                        <tr key={id}>
                          {i === 0 && <td rowSpan={Object.keys(d.values).length}>{d.name}</td>}
                          <td>{slotName(key)}</td>
                          <td
                            className={value ? "" : "muted"}
                            onDoubleClick={() => setEditing(id)}
                          >
                            {editing === id ? (
                              <input
                                autoFocus
                                defaultValue={value}
                                onBlur={(event) => {
                                  store.edit(current, d.name, key, event.target.value);
                                  setEditing(null);
                                }}
                              />
                            ) : (
                              value || "(비어 있음)"
                            )}
                          </td>
                          <td>
                            <Badge type={value ? "ok" : "warn"}>{value ? "확정" : "값 없음"}</Badge>
                          </td>
                        </tr>
                      );
                    }),
                  )}
                </tbody>
              </table>
            </div>
          </Card>
          <Card className="stage">
            <StageHeader n="3" title="문서 만들기" desc="저장 폴더를 고르고 만드세요." />
            <div className="stage-body">
              <div className="create-row">
                <label>
                  저장 폴더를 고르세요
                  <input value={store.outDir} readOnly />
                </label>
                <label>
                  파일 이름
                  <input value={store.name} onChange={(event) => store.setName(event.target.value)} />
                </label>
                <small>
                  기본: {store.name || "문서"} → {store.name || "문서"}1.docx, {store.name || "문서"}2.docx …
                </small>
                <Button onClick={store.pickFolder}>저장 폴더 고르기</Button>
                <Button
                  primary
                  onClick={() => {
                    if (!current) return report("문서 관리에서 양식을 먼저 추가하세요");
                    store.setSelected([current]);
                    store.setNames(
                      Object.fromEntries(docs.map((d) => [`${current}|${d.name}`, `${store.name || "문서"}${d.name.replace(/\D/g, "")}`])),
                    );
                    go("generate");
                  }}
                >
                  만들기
                </Button>
              </div>
              <div className="secondary-export">
                <p>
                  <b>만든 문서를 다른 형식으로도 저장</b>
                  <span>저장 전에 이름을 고칠 수 있어요</span>
                </p>
                <Button onClick={convert("pdf")}>PDF로 내보내기</Button>
                <Button onClick={convert("png")}>이미지로 내보내기</Button>
                {store.made.length > 0 && (
                  <Badge type="ok">{store.made.length}건을 만들었어요 (워드 .docx)</Badge>
                )}
              </div>
            </div>
          </Card>
        </div>
        <Card className="guide">
          <h3>
            <Icon name="info" /> 사용 안내
          </h3>
          <p>
            내가 넣은 워드 양식(.docx)의 빈칸 <b>{"{{…}}"}</b>에 데이터 값을 왼쪽부터 순서대로 넣어
            문서를 만들어요.
          </p>
          {["없는 값은 빈칸으로 만들어요", "표 한 줄 = 문서 하나", "첫 줄이 제목이면 체크하세요"].map((x) => (
            <div key={x}>
              <span>✓</span>
              {x}
            </div>
          ))}
          <div className="guide-ill">
            <Icon name="table" size={44} />
            <i>→</i>
            <Icon name="file" size={44} />
          </div>
        </Card>
      </div>
    </main>
  );
}

export function DataPage({ go, store, openOptions }: { go: Go; store: MakeStore; openOptions: () => void }) {
  const pick = async () => store.setData(await pickFiles(".csv,.txt,.xlsx,.json", false));
  const head = store.header ? store.rows[0] : undefined;
  const width = Math.max(0, ...store.rows.slice(0, 6).map((r) => r.length));
  return (
    <main className="workspace flow-page">
      <Stepper current={1} />
      <div className="flow-title">
        <h1>데이터 파일 선택</h1>
        <p>문서를 만들 때 사용할 데이터를 불러오세요.</p>
      </div>
      <button
        className="dropzone data-drop"
        onClick={pick}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          store.setData(Array.from(event.dataTransfer.files));
        }}
      >
        <span className="upload-icon">
          <Icon name="upload" size={25} />
        </span>
        <strong>파일을 이곳에 끌어다 놓거나 눌러서 고르세요</strong>
        <small>지원 형식: CSV · XLSX · TXT · JSON</small>
        <span className="btn btn-primary">파일 선택</span>
      </button>
      {store.data && (
        <Card className="data-preview">
          <div className="panel-heading">
            <div>
              <h3>미리보기</h3>
              <p>{store.data.name}</p>
            </div>
            <Badge type="info">총 {store.rows.length - (store.header ? 1 : 0)}건</Badge>
          </div>
          <table>
            <thead>
              <tr>
                {Array.from({ length: width }, (_, i) => (
                  <th key={i}>{head?.[i] ?? `${i + 1}열`}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {store.rows.slice(store.header ? 1 : 0, (store.header ? 1 : 0) + 5).map((r, i) => (
                <tr key={i}>
                  {Array.from({ length: width }, (_, j) => (
                    <td key={j}>{r[j] || "—"}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <div className="flow-actions">
        <Button onClick={() => go("builder")}>이전</Button>
        <Button primary onClick={() => (store.data ? openOptions() : report("데이터 파일을 먼저 고르세요"))}>
          다음: 파일 옵션 설정 <Icon name="chevron" size={16} />
        </Button>
      </div>
    </main>
  );
}

export function OptionsModal({ close, confirm, store }: { close: () => void; confirm: () => void; store: MakeStore }) {
  const isJson = /\.json$/i.test(store.data?.name ?? "");
  const [header, setHeader] = useState(isJson ? false : store.header);
  const rows = store.rows.slice(0, 8);
  const width = Math.max(0, ...rows.map((r) => r.length));
  return (
    <div className="modal-backdrop">
      <div className="modal options-modal">
        <div className="modal-title">
          <div>
            <h2>데이터 확인</h2>
            <p>{store.data?.name} — 앞부분 {rows.length}줄이에요. 한 줄이 문서 하나가 돼요.</p>
          </div>
          <button onClick={close}>×</button>
        </div>
        <table>
          <thead>
            <tr>
              <th></th>
              {Array.from({ length: width }, (_, i) => (
                <th key={i}>{i + 1} 열</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className={i === 0 && header ? "skipped" : ""}>
                <td>{i + 1}</td>
                {Array.from({ length: width }, (_, j) => (
                  <td key={j}>{r[j] ?? ""}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <label className="toggle-row">
          <input type="checkbox" checked={header} disabled={isJson} onChange={() => setHeader(!header)} />
          <span className="toggle" />
          <b>첫 줄은 제목(건너뛰기)</b>
        </label>
        <p className="modal-help">
          켜면 표의 첫 줄이 회색으로 바뀌고 건너뛰어요. JSON(읽기 결과)일 때는 꺼져 있어요.
          <br />
          구분 기호는 자동으로 판단해요.
        </p>
        <div className="modal-actions">
          <Button onClick={close}>취소</Button>
          <Button
            primary
            onClick={() => {
              store.setHeader(header);
              confirm();
            }}
          >
            확인
          </Button>
        </div>
      </div>
    </div>
  );
}

export function TemplatePage({ go, store, templates }: { go: Go; store: MakeStore; templates: Template[] }) {
  const [previewId, setPreviewId] = useState(store.selected[0] ?? templates[0]?.id ?? "");
  useEffect(() => {
    store.prepare(templates.map((t) => t.id));
  }, [templates, store.header, store.data?.path]);

  const fits = (id: string) => {
    const d = store.docsOf(id)[0];
    return !!d && Object.values(d.values).every((v) => String(v).trim());
  };
  const toggle = (id: string) => {
    setPreviewId(id);
    store.setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  };
  const preview = templates.find((t) => t.id === previewId);
  const first = store.docsOf(previewId)[0];
  const problem = store.prepared[previewId];
  const count = Math.max(0, ...store.selected.map((id) => store.docsOf(id).length));

  return (
    <main className="workspace flow-page template-page">
      <Stepper current={2} />
      <PageTitle
        right={
          <Button onClick={() => go("documents")}>
            <Icon name="settings" size={16} /> 문서 관리
          </Button>
        }
      >
        2단계: 문서 양식 선택 <span>— 같은 데이터로 여러 양식을 동시에 만들 수 있어요.</span>
      </PageTitle>
      <div className="template-grid">
        <Card className="template-list">
          <div className="template-list-title">
            <div>
              <h3>문서 관리에 등록된 양식</h3>
              <p>필요한 양식을 여러 개 선택하세요.</p>
            </div>
            <Badge type="info">{store.selected.length}개 선택</Badge>
          </div>
          {!templates.length && <p className="empty-view">등록된 양식이 없어요. 문서 관리에서 추가하세요.</p>}
          {templates.map((t) => (
            <button className={store.selected.includes(t.id) ? "selected" : ""} key={t.id} onClick={() => toggle(t.id)}>
              <span className="file-icon">
                <Icon name="file" />
              </span>
              <p>
                <b>{t.name}</b>
                <small>빈칸 {t.slots.length}개</small>
              </p>
              {fits(t.id) && <Badge type="ok">적합</Badge>}
              <span className={`template-check ${store.selected.includes(t.id) ? "checked" : ""}`}>
                {store.selected.includes(t.id) && "✓"}
              </span>
            </button>
          ))}
          <button className="manage-template-link" onClick={() => go("documents")}>
            <Icon name="settings" size={14} /> 문서 관리에서 양식 추가·삭제
          </button>
        </Card>
        <Card className="field-preview">
          <div className="panel-heading">
            <div>
              <h3>빈칸 순서 미리보기</h3>
              <p>{preview?.name ?? "—"}</p>
            </div>
            {preview && (
              <Badge type={fits(previewId) ? "ok" : "warn"}>
                {fits(previewId) ? "데이터와 적합" : "일부 값 확인 필요"}
              </Badge>
            )}
          </div>
          {typeof problem === "string" && <p className="mapping-help">{problem}</p>}
          <table>
            <thead>
              <tr>
                {["순서", "칸 이름", "미리보기 값", "상태"].map((x) => (
                  <th key={x}>{x}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {first &&
                Object.keys(first.values).map((key, i) => {
                  const value = store.valueOf(previewId, first, key);
                  return (
                    <tr key={key}>
                      <td>{i + 1}</td>
                      <td>{slotName(key)}</td>
                      <td className={value ? "" : "muted"}>{value || "(빈칸)"}</td>
                      <td>
                        <Badge type={value ? "ok" : "warn"}>{value ? "자동 확정" : "확인 필요"}</Badge>
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
          <div className="template-next">
            <p>
              <b>{store.selected.length}개 양식</b>으로 데이터 {count}건을 만들 예정이에요.
            </p>
            <Button
              primary
              onClick={() => (store.selected.length ? go("generate") : report("양식을 하나 이상 고르세요"))}
            >
              3단계: 만들기 <Icon name="chevron" size={16} />
            </Button>
          </div>
        </Card>
      </div>
    </main>
  );
}

export function GeneratePage({ go, store, openConfirm }: { go: Go; store: MakeStore; openConfirm: () => void }) {
  const [formatOpen, setFormatOpen] = useState(true);
  const [showAll, setShowAll] = useState(false);
  const toggleFormat = (k: string) => {
    store.setKinds((current) => (current.includes(k) ? current.filter((x) => x !== k) : [...current, k]));
    setFormatOpen(false);
  };
  const id = store.selected[0] ?? "";
  const first = store.docsOf(id)[0];
  const keys = first ? Object.keys(first.values) : [];
  const filled = first ? keys.filter((k) => store.valueOf(id, first, k)).length : 0;
  const rowsCount = Math.max(0, ...store.selected.map((x) => store.docsOf(x).length));
  const label = (k: string) => KINDS.find((x) => x[0] === k)?.[1] ?? k;
  const shown = showAll ? store.items : store.items.slice(0, 3);

  return (
    <main className="workspace flow-page">
      <Stepper current={3} />
      <PageTitle>3단계: 만들기</PageTitle>
      <div className="generate-grid">
        <Card>
          <div className="panel-heading">
            <div>
              <h3>채우기 미리보기</h3>
              <p>값 목록 (순서대로 채우기)</p>
            </div>
            <Badge type="info">문서 {rowsCount}건</Badge>
          </div>
          <table>
            <thead>
              <tr>
                {["순서", "값", "상태"].map((x) => (
                  <th key={x}>{x}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {first &&
                keys.map((k, i) => {
                  const v = store.valueOf(id, first, k);
                  return (
                    <tr key={k}>
                      <td>{i + 1}</td>
                      <td className={v ? "" : "muted"}>{v || "(빈칸)"}</td>
                      <td>
                        <Badge type={v ? "ok" : "warn"}>{v ? "채울 수 있음" : "빈칸으로 만듦"}</Badge>
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
          <div className="fill-summary">
            <h4>채우기 상태 요약</h4>
            {[
              ["총 빈칸", `${keys.length}개`],
              ["채울 값", `${filled}개`],
              ["빈칸 부족", `${keys.length - filled}개`],
            ].map((x) => (
              <div key={x[0]}>
                <span>{x[0]}</span>
                <b>{x[1]}</b>
              </div>
            ))}
          </div>
        </Card>
        <Card className="generate-settings">
          <h3>저장 폴더 설정</h3>
          <div className="folder-input">
            <input value={store.outDir} readOnly />
            <Button onClick={store.pickFolder}>
              <Icon name="folder" size={15} /> 폴더 선택
            </Button>
          </div>
          <hr />
          <button className="collapsible-heading" onClick={() => setFormatOpen(!formatOpen)}>
            <span>
              <b>내보내기 형식</b>
              <small>{store.kinds.length ? store.kinds.map(label).join(" · ") : "아직 선택하지 않았어요"}</small>
            </span>
            <em>{formatOpen ? "접기 ∧" : "다시 선택 ∨"}</em>
          </button>
          {formatOpen && (
            <div className="format-options">
              {KINDS.map(([k, text]) => (
                <label className="check-option inline" key={k}>
                  <input type="checkbox" checked={store.kinds.includes(k)} onChange={() => toggleFormat(k)} />
                  <span>
                    <b>{text}</b>
                  </span>
                </label>
              ))}
            </div>
          )}
          {!formatOpen && (
            <button className="chosen-format" onClick={() => setFormatOpen(true)}>
              {store.kinds.length ? (
                store.kinds.map((k) => (
                  <Badge type="info" key={k}>
                    {label(k)}
                  </Badge>
                ))
              ) : (
                <span>형식을 선택해야 문서를 만들 수 있어요.</span>
              )}
              <b>수정</b>
            </button>
          )}
          <hr />
          <div className="name-editor-heading">
            <div>
              <h3>문서 이름 정하기</h3>
              <p>각 문서의 이름을 수정하세요.</p>
            </div>
            <Badge type="info">{store.items.length}개 파일</Badge>
          </div>
          <div className="document-name-list">
            {shown.map((item, index) => (
              <div className="document-name-item" key={item.key}>
                <span className="drag-handle">
                  <Icon name="grip" size={16} />
                </span>
                <span className="name-order">{index + 1}</span>
                <input
                  value={item.name}
                  onChange={(event) => store.setNames((n) => ({ ...n, [item.key]: event.target.value }))}
                  aria-label={`${index + 1}번 문서 이름`}
                />
                <small>.docx</small>
                <em>{item.template}</em>
              </div>
            ))}
            {store.items.length > 3 && (
              <button className="show-more-names" onClick={() => setShowAll(!showAll)}>
                {showAll ? "접기" : `나머지 ${store.items.length - 3}개 문서 이름 보기`}
              </button>
            )}
          </div>
          <hr />
          <h3>만들기 요약</h3>
          <div className="make-summary">
            <p>
              <span>데이터 행 수</span>
              <b>{rowsCount}건</b>
            </p>
            <p>
              <span>선택 양식</span>
              <b>{store.selected.length}개</b>
            </p>
            <p>
              <span>예상 파일 수</span>
              <b>{store.items.length}개</b>
            </p>
          </div>
          <div className="generate-actions">
            <Button onClick={() => go("template")}>이전 단계로</Button>
            <Button
              primary
              onClick={() =>
                !store.kinds.length
                  ? report("내보내기 형식을 하나 이상 고르세요")
                  : !store.items.length
                    ? report("만들 문서가 없어요 — 데이터와 양식을 확인하세요")
                    : openConfirm()
              }
              className={!store.kinds.length ? "soft-disabled" : ""}
            >
              문서 만들기 실행
            </Button>
          </div>
        </Card>
      </div>
    </main>
  );
}

export function ConfirmGenerate({ close, store }: { close: () => void; store: MakeStore }) {
  const [busy, setBusy] = useState(false);
  const start = async () => {
    setBusy(true);
    try {
      const paths = await store.run();
      alert(`${paths.length}개 파일을 만들었어요\n${store.outDir}`);
      close();
    } catch (err) {
      report(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="modal-backdrop">
      <div className="modal confirm-modal">
        <div className="modal-title">
          <div>
            <h2>문서 만들기 실행 확인</h2>
          </div>
          <button onClick={close}>×</button>
        </div>
        <div className="confirm-ill">
          <Icon name="layers" size={32} />
        </div>
        <p>
          <b>
            {store.selected.length}개 양식으로 {store.items.length}개의 문서를 만듭니다.
          </b>
          <br />
          저장 폴더에 기존 파일이 있으면 덮어씁니다.
        </p>
        <div className="path-box">
          <Icon name="folder" />
          <span>
            저장 위치: <b>{store.outDir}</b>
          </span>
        </div>
        <div className="modal-actions">
          <Button onClick={close}>취소</Button>
          <Button primary onClick={busy ? undefined : start}>
            {busy ? "만드는 중…" : "만들기 시작"}
          </Button>
        </div>
      </div>
    </div>
  );
}
