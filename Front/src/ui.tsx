import type { ReactNode } from "react";

export type Screen =
  | "home"
  | "reader"
  | "verify"
  | "export"
  | "documents"
  | "builder"
  | "data"
  | "template"
  | "generate";

export type Go = (s: Screen) => void;

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    upload: (
      <>
        <path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5" />
        <path d="M5 15v4h14v-4" />
      </>
    ),
    file: (
      <>
        <path d="M6 2h8l4 4v16H6z" />
        <path d="M14 2v5h5" />
        <path d="M9 12h6M9 16h6" />
      </>
    ),
    layers: (
      <>
        <path d="m12 3-9 5 9 5 9-5-9-5Z" />
        <path d="m3 12 9 5 9-5M3 16l9 5 9-5" />
      </>
    ),
    folder: <path d="M3 6h7l2 2h9v11H3z" />,
    eye: (
      <>
        <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" />
        <circle cx="12" cy="12" r="2.5" />
      </>
    ),
    check: <path d="m5 12 4 4L19 6" />,
    plus: <path d="M12 5v14M5 12h14" />,
    trash: (
      <>
        <path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6" />
      </>
    ),
    table: (
      <>
        <rect x="3" y="4" width="18" height="16" rx="1" />
        <path d="M3 9h18M8 9v11M15 9v11" />
      </>
    ),
    chevron: <path d="m9 6 6 6-6 6" />,
    info: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 11v6M12 7h.01" />
      </>
    ),
    download: (
      <>
        <path d="M12 3v13m0 0 5-5m-5 5-5-5" />
        <path d="M5 21h14" />
      </>
    ),
    edit: (
      <>
        <path d="m4 16-1 5 5-1L19 9l-4-4L4 16Z" />
        <path d="m13 7 4 4" />
      </>
    ),
    grip: (
      <>
        <circle cx="8" cy="7" r="1" />
        <circle cx="16" cy="7" r="1" />
        <circle cx="8" cy="12" r="1" />
        <circle cx="16" cy="12" r="1" />
        <circle cx="8" cy="17" r="1" />
        <circle cx="16" cy="17" r="1" />
      </>
    ),
    sidebar: (
      <>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M9 4v16" />
      </>
    ),
    settings: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path
          d="M19 13.5v-3l-2-.7-.7-1.7.9-1.9-2.1-2.1-1.9.9-1.7-.7L10.5 2h-3l-.7 2-1.7.7-1.9-.9-2.1 2.1.9 1.9-.7 1.7-2 .7v3l2 .7.7 1.7-.9 1.9 2.1 2.1 1.9-.9 1.7.7.7 2h3l.7-2 1.7-.7 1.9.9 2.1-2.1-.9-1.9.7-1.7 2-.7Z"
          transform="translate(2.5 0) scale(.8)"
        />
      </>
    ),
  };
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  );
}

export function Button({
  children,
  primary,
  danger,
  onClick,
  className = "",
}: {
  children: ReactNode;
  primary?: boolean;
  danger?: boolean;
  onClick?: () => void;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`btn ${primary ? "btn-primary" : danger ? "btn-danger" : ""} ${className}`}
    >
      {children}
    </button>
  );
}

export function Badge({
  children,
  type = "info",
}: {
  children: ReactNode;
  type?: "ok" | "warn" | "fail" | "info";
}) {
  return <span className={`badge badge-${type}`}>{children}</span>;
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`card ${className}`}>{children}</section>;
}

export function PageTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="page-title">
      <h1>{children}</h1>
      {right}
    </div>
  );
}

export function Stepper({ current }: { current: 1 | 2 | 3 }) {
  const labels = [
    "데이터 넣기",
    current === 2 ? "문서 양식" : "파일 옵션",
    current === 3 ? "만들기" : "문서 만들기",
  ];
  return (
    <div className="stepper">
      {labels.map((label, i) => (
        <div
          key={label}
          className={`step ${i + 1 === current ? "current" : ""} ${i + 1 < current ? "done" : ""}`}
        >
          <span>{i + 1 < current ? <Icon name="check" size={14} /> : i + 1}</span>
          <b>{label}</b>
          {i < 2 && <em />}
        </div>
      ))}
    </div>
  );
}

export function StageHeader({ n, title, desc }: { n: string; title: string; desc: string }) {
  return (
    <div className="stage-head">
      <span>{n}</span>
      <div>
        <h3>
          {n}단계 · {title}
        </h3>
        <p>{desc}</p>
      </div>
    </div>
  );
}
