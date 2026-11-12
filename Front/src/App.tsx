import { useCallback, useEffect, useState, type DragEvent } from "react";
import { call, DATA_EXT, pickFiles, READ_EXT, report, type Template } from "./api";
import { DocumentManager } from "./documents";
import { Builder, ConfirmGenerate, DataPage, GeneratePage, OptionsModal, TemplatePage, useMakeStore } from "./make";
import { ExportPage, Reader, useReaderStore, Verify } from "./reader";
import { Icon, type Go, type Screen } from "./ui";

function Header({ screen, go }: { screen: Screen; go: Go }) {
  const section =
    screen === "home"
      ? "home"
      : ["reader", "verify", "export"].includes(screen)
        ? "reader"
        : screen === "documents"
          ? "documents"
          : "builder";
  return (
    <header className="topbar">
      <button className="brand" onClick={() => go("home")}>
        <span className="brandmark">B</span>
        <b>BridgeDocs</b>
      </button>
      <nav>
        <button className={section === "home" ? "active" : ""} onClick={() => go("home")}>
          홈
        </button>
        <button className={section === "reader" ? "active" : ""} onClick={() => go("reader")}>
          문서 읽기
        </button>
        <button className={section === "builder" ? "active" : ""} onClick={() => go("builder")}>
          문서 만들기
        </button>
        <button className={section === "documents" ? "active" : ""} onClick={() => go("documents")}>
          문서 관리
        </button>
      </nav>
      <div className="offline">
        <i /> 오프라인
      </div>
    </header>
  );
}

function Home({ go, route }: { go: Go; route: (files: File[]) => void }) {
  const handleDrop = (event: DragEvent) => {
    event.preventDefault();
    route(Array.from(event.dataTransfer.files));
  };
  return (
    <main className="home">
      <div className="hero">
        <p className="eyebrow">✦ 인터넷 없이 내 PC에서 하는 문서 자동화</p>
        <h1>
          복잡한 문서 작업을
          <br />
          <span>한 곳에서, 더 쉽게.</span>
        </h1>
        <p className="hero-copy">
          문서를 읽고 필요한 값을 확인하거나, 준비한 데이터로 새 문서를 만들어 보세요.
          <br />
          파일은 밖으로 보내지 않고 이 PC에서만 처리해요.
        </p>
      </div>
      <button
        className="dropzone home-drop"
        onClick={async () => route(await pickFiles(".pdf,.png,.jpg,.jpeg,.docx,.csv,.txt,.xlsx,.json"))}
        onDragOver={(event) => event.preventDefault()}
        onDrop={handleDrop}
      >
        <span className="upload-icon">
          <Icon name="upload" size={25} />
        </span>
        <strong>파일을 여기에 끌어다 놓으세요</strong>
        <small>이미지·PDF·워드는 문서 읽기로, CSV·TXT·XLSX는 문서 만들기로 자동 연결해요.</small>
        <em>두 종류를 섞어 넣으면 ‘파일을 다시 골라 주세요’ 안내 창이 떠요</em>
        <span className="btn btn-primary">파일 선택</span>
      </button>
      <div className="section-head">
        <span>작업 선택</span>
        <h2>무엇을 도와드릴까요?</h2>
      </div>
      <div className="choice-grid">
        <button className="choice reader-choice" onClick={() => go("reader")}>
          <div className="choice-icon">
            <Icon name="eye" size={24} />
          </div>
          <div>
            <label>DOCUMENT READER</label>
            <h3>문서 읽기</h3>
            <p>이미지나 PDF에서 필요한 정보를 읽고, 확인·수정한 뒤 원하는 형식으로 내보내세요.</p>
            <div className="chips">
              <span>PNG</span>
              <span>JPG</span>
              <span>PDF</span>
              <span>DOCX</span>
            </div>
            <b>
              문서 읽기 시작 <span>→</span>
            </b>
          </div>
        </button>
        <button className="choice builder-choice" onClick={() => go("builder")}>
          <div className="choice-icon">
            <Icon name="layers" size={24} />
          </div>
          <div>
            <label>DOCUMENT BUILDER</label>
            <h3>문서 만들기</h3>
            <p>데이터 파일을 불러와 내용을 확인하고, 내가 넣은 워드 양식으로 문서를 만드세요.</p>
            <div className="chips">
              <span>CSV</span>
              <span>TXT</span>
              <span>XLSX</span>
            </div>
            <b>
              문서 만들기 시작 <span>→</span>
            </b>
          </div>
        </button>
      </div>
      <div className="howto">
        {[
          ["01", "파일 넣기", "끌어놓거나 직접 선택"],
          ["02", "내용 확인 및 수정", "읽은 값을 바로 편집"],
          ["03", "내보내기", "원하는 형식으로 저장"],
        ].map((x, i) => (
          <div key={x[0]}>
            <span>{x[0]}</span>
            <Icon name={i === 0 ? "upload" : i === 1 ? "edit" : "download"} />
            <p>
              <b>{x[1]}</b>
              <small>{x[2]}</small>
            </p>
            {i < 2 && <i>→</i>}
          </div>
        ))}
      </div>
      <div className="safe-strip">
        <span className="brandmark mini">B</span> BridgeDocs · 인터넷 연결 없이 안전하게 사용할 수
        있어요
      </div>
    </main>
  );
}

export default function App() {
  const [screen, setScreen] = useState<Screen>("home");
  const [modal, setModal] = useState<"options" | "confirm" | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const reader = useReaderStore();
  const make = useMakeStore(templates);

  const refreshTemplates = useCallback(async () => {
    const list = await call<Template[]>("list_templates");
    setTemplates(list);
    return list;
  }, []);
  useEffect(() => {
    refreshTemplates().catch(report);
  }, [refreshTemplates]);

  const go = (s: Screen) => {
    setScreen(s);
    setModal(null);
    window.scrollTo(0, 0);
  };

  // 홈에 놓은 파일: 데이터는 문서 만들기로, 문서(이미지·PDF·워드)는 문서 읽기로
  const route = async (files: File[]) => {
    const data = files.filter((f) => DATA_EXT.test(f.name));
    const docs = files.filter((f) => READ_EXT.test(f.name));
    if (data.length && docs.length)
      return alert(
        "파일을 다시 골라 주세요\n읽을 문서(PDF·이미지·워드)와 데이터 파일(CSV·TXT·XLSX·JSON)이 섞여 있어요. 한 종류만 골라 다시 넣어 주세요.",
      );
    if (docs.length) {
      await reader.addFiles(docs);
      go("reader");
    } else if (data.length) {
      await make.setData(data);
      go("data");
    } else if (files.length) report("지원하지 않는 파일이에요");
  };

  return (
    <div className="app-shell">
      <Header screen={screen} go={go} />
      {screen === "home" && <Home go={go} route={route} />}
      {screen === "reader" && <Reader go={go} store={reader} />}
      {screen === "verify" && <Verify go={go} store={reader} />}
      {screen === "export" && <ExportPage go={go} store={reader} />}
      {screen === "documents" && <DocumentManager templates={templates} refresh={refreshTemplates} />}
      {screen === "builder" && <Builder go={go} store={make} templates={templates} />}
      {screen === "data" && <DataPage go={go} store={make} openOptions={() => setModal("options")} />}
      {screen === "template" && <TemplatePage go={go} store={make} templates={templates} />}
      {screen === "generate" && (
        <GeneratePage go={go} store={make} openConfirm={() => setModal("confirm")} />
      )}
      {modal === "options" && (
        <OptionsModal store={make} close={() => setModal(null)} confirm={() => go("template")} />
      )}
      {modal === "confirm" && <ConfirmGenerate store={make} close={() => setModal(null)} />}
    </div>
  );
}
