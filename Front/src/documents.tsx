import { useState, type DragEvent } from "react";
import { call, pickFiles, report, upload, type Template } from "./api";
import { Badge, Button, Icon } from "./ui";

// 문단 글자에서 빈칸 {{…}}만 파랗게
function Paragraph({ text }: { text: string }) {
  const parts = text.split(/(\{\{[^}]*\}\})/);
  return <p className="managed-copy">{parts.map((p, i) => (/^\{\{/.test(p) ? <b key={i}>{p}</b> : p))}</p>;
}

export function DocumentManager({ templates, refresh }: { templates: Template[]; refresh: () => Promise<Template[]> }) {
  const [selectedId, setSelectedId] = useState("");
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [justAdded, setJustAdded] = useState("");
  const current = templates.find((t) => t.id === selectedId) ?? templates[0];

  const addFiles = async (files: File[]) => {
    const docx = files.filter((f) => /\.docx$/i.test(f.name));
    if (!docx.length) return report("워드 양식(.docx) 파일을 고르세요");
    try {
      let last = "";
      for (const up of await upload(docx)) last = (await call<{ id: string }>("add_template", { path: up.path })).id;
      await refresh();
      setSelectedId(last);
      setJustAdded(last);
      setAdding(false);
    } catch (err) {
      report(err);
    }
  };

  const handleDrop = (event: DragEvent) => {
    event.preventDefault();
    addFiles(Array.from(event.dataTransfer.files));
  };

  const deleteTemplate = async () => {
    if (!current) return;
    try {
      await call("remove_template", { id: current.id });
      await refresh();
      setSelectedId("");
    } catch (err) {
      report(err);
    }
    setDeleting(false);
  };

  return (
    <main className="document-manager" onDragOver={(event) => event.preventDefault()} onDrop={handleDrop}>
      <div className="manager-toolbar">
        <div>
          <h1>문서 관리</h1>
          <p>여기에 등록된 문서 양식만 문서 만들기에서 사용할 수 있어요.</p>
        </div>
        <div>
          <Button onClick={() => (current ? setDeleting(true) : report("지울 양식을 고르세요"))} danger>
            <Icon name="trash" size={15} /> 선택 양식 삭제
          </Button>
          <Button primary onClick={() => setAdding(true)}>
            <Icon name="plus" size={15} /> 문서 양식 추가
          </Button>
        </div>
      </div>
      <div className="manager-shell">
        <aside className="manager-rail">
          <div className="rail-icon active">
            <Icon name="file" size={20} />
          </div>
          <div className="rail-icon">
            <Icon name="settings" size={20} />
          </div>
        </aside>
        <aside className="template-explorer">
          <div className="explorer-title">
            <span>문서 양식</span>
            <Badge type="info">{templates.length}개</Badge>
          </div>
          <div className="folder-row">
            <span>⌄</span>
            <Icon name="folder" size={14} />
            <b>BRIDGEDOCS 양식</b>
          </div>
          {templates.map((t) => (
            <button key={t.id} className={current?.id === t.id ? "active" : ""} onClick={() => setSelectedId(t.id)}>
              <Icon name="file" size={15} />
              <span>
                {t.name}
                {justAdded === t.id && " · 방금 추가"}
              </span>
            </button>
          ))}
          <div className="explorer-help">
            <Icon name="info" size={14} />
            DOCX 파일의 {"{{빈칸}}"}을 자동으로 찾아요.
          </div>
        </aside>
        <section className="template-editor">
          <div className="editor-tabs">
            <div className="active">
              <Icon name="file" size={14} />
              {current?.name ?? "양식이 없어요"}
            </div>
          </div>
          <div className="editor-breadcrumb">
            문서 양식 <span>›</span> {current?.name}
          </div>
          <div className="template-canvas">
            <div className="managed-paper">
              <div className="doc-watermark">B</div>
              {!current && <p className="managed-copy">[문서 양식 추가]로 워드 양식(.docx)을 넣어 주세요.</p>}
              {current && !current.ok && <p className="managed-copy">이 워드 파일을 읽을 수 없어요.</p>}
              {current?.paragraphs.map((text, i) => <Paragraph key={i} text={text} />)}
            </div>
          </div>
        </section>
        <aside className="template-inspector">
          <h3>양식 정보</h3>
          <div className="inspector-file">
            <Icon name="file" size={27} />
            <p>
              <b>{current?.name ?? "—"}</b>
              <span>워드 양식 · 빈칸 {current?.slots.length ?? 0}개</span>
            </p>
          </div>
          <dl>
            <div>
              <dt>상태</dt>
              <dd>
                <Badge type={current?.ok ? "ok" : "warn"}>{current?.ok ? "사용 가능" : "읽을 수 없음"}</Badge>
              </dd>
            </div>
            <div>
              <dt>최근 수정</dt>
              <dd>{current ? new Date(current.modified * 1000).toLocaleDateString("ko-KR") : "—"}</dd>
            </div>
            <div>
              <dt>파일 형식</dt>
              <dd>Word 문서 (.docx)</dd>
            </div>
          </dl>
          <h3>인식된 빈칸</h3>
          <div className="placeholder-list">
            {current?.slots.map((item, index) => (
              <div key={index}>
                <span>{index + 1}</span>
                <b>{item || "(이름 없는 빈칸)"}</b>
              </div>
            ))}
          </div>
        </aside>
      </div>
      {adding && (
        <div className="modal-backdrop">
          <div className="modal small-modal">
            <div className="modal-title">
              <div>
                <h2>문서 양식 추가</h2>
                <p>추가한 양식은 문서 만들기에서 바로 사용할 수 있어요.</p>
              </div>
              <button onClick={() => setAdding(false)}>×</button>
            </div>
            <button
              className="dropzone modal-drop"
              onClick={async () => addFiles(await pickFiles(".docx"))}
              onDragOver={(event) => event.preventDefault()}
              onDrop={handleDrop}
            >
              <span className="upload-icon">
                <Icon name="file" />
              </span>
              <strong>워드 양식 파일을 끌어다 놓으세요</strong>
              <small>지원 형식: .docx · 빈칸 표기: {"{{이름}}"}</small>
            </button>
            <div className="modal-actions">
              <Button onClick={() => setAdding(false)}>취소</Button>
              <Button primary onClick={async () => addFiles(await pickFiles(".docx"))}>
                파일 선택 및 추가
              </Button>
            </div>
          </div>
        </div>
      )}
      {deleting && current && (
        <div className="modal-backdrop">
          <div className="modal delete-modal">
            <div className="danger-icon">
              <Icon name="trash" size={22} />
            </div>
            <h2>문서 양식 삭제</h2>
            <p>
              <b>‘{current.name}’ 양식을 삭제할까요?</b>
            </p>
            <small>삭제하면 문서 만들기에서 더 이상 선택할 수 없어요.</small>
            <div className="modal-actions">
              <Button onClick={() => setDeleting(false)}>아니요</Button>
              <Button danger onClick={deleteTemplate}>
                삭제
              </Button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
