// 화면 ↔ 이 PC의 BridgeDocs 엔진(web_api.py). 서버는 127.0.0.1에서만 열린다.

export type Status = "auto" | "review" | "confirmed" | "edited";

export type Field = {
  key: string;
  label: string;
  kind: string;
  value: string;
  status: Status;
  reason: string;
  compare: string;
  page: number | null;
  line_box: number[] | null;
  read_value?: string;
};

export type Doc = {
  source: string[];
  files: string[];
  doc_type: string;
  doc_name: string;
  new_type: boolean;
  json_path: string;
  pages: number;
  fields: Field[];
};

export type PageImage = { src: string; w: number; h: number };

export type Template = {
  id: string;
  name: string;
  path: string;
  slots: string[];
  paragraphs: string[];
  ok: boolean;
  modified: number;
};

export type MakeDoc = { name: string; values: Record<string, string>; notes?: unknown };

export type Uploaded = { name: string; path: string };

export async function call<T>(name: string, body: object = {}): Promise<T> {
  const res = await fetch(`/api/${name}`, {
    method: "POST",
    headers: { "X-BD": "1", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const json = await res.json();
  if ("error" in json) throw new Error(json.error);
  return json.ok as T;
}

// 브라우저는 파일 경로를 모르므로 내용을 이 PC의 작업 폴더로 보내고 그 경로를 받는다.
export async function upload(files: File[]): Promise<Uploaded[]> {
  const batch = Math.random().toString(36).slice(2) + Date.now().toString(36);
  return Promise.all(
    files.map(async (file) => {
      const res = await fetch("/api/upload", {
        method: "POST",
        headers: { "X-BD": "1", "X-Batch": batch, "X-Name": encodeURIComponent(file.name) },
        body: file,
      });
      const json = await res.json();
      if ("error" in json) throw new Error(json.error);
      return { name: file.name, path: json.ok as string };
    }),
  );
}

export function pickFiles(accept: string, multiple = true): Promise<File[]> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.multiple = multiple;
    input.onchange = () => resolve(Array.from(input.files ?? []));
    input.click();
  });
}

export function report(err: unknown) {
  alert(err instanceof Error ? err.message : String(err));
}

export const STATUS_LABEL: Record<Status, string> = {
  auto: "자동 확정",
  review: "확인 필요",
  confirmed: "사용자 확인",
  edited: "사용자 수정",
};

export const READ_EXT = /\.(pdf|png|jpe?g|docx)$/i;
export const DATA_EXT = /\.(csv|txt|xlsx|json)$/i;
export const stem = (path: string) => path.split(/[\\/]/).pop()!.replace(/\.[^.]+$/, "");
export const reviewCount = (doc: Doc) => doc.fields.filter((f) => f.status === "review").length;
