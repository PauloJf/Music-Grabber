import type { ReactNode } from "react";

export function Flash({ kind, children }: { kind: "error" | "success"; children: ReactNode }) {
  return <div className={`flash ${kind}`} role={kind === "error" ? "alert" : "status"}>{children}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export const statusBadge = (s: string) => <span className={`badge badge--${s}`}>{s.replace("_", " ")}</span>;

export function Score({ value }: { value: number | null }) {
  if (value == null) return <span className="muted">–</span>;
  const cls = value >= 0.85 ? "hi" : value >= 0.55 ? "mid" : "lo";
  return <span className={`score score--${cls}`}>{value.toFixed(2)}</span>;
}

export const duration = (ms: number | null | undefined) => {
  if (!ms) return "";
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

export function Progress({ value }: { value: number | null }) {
  const pct = Math.max(0, Math.min(1, value ?? 0)) * 100;
  return <div className="bar" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${pct}%` }} /></div>;
}
