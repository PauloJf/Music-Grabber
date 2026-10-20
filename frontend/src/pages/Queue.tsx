import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Empty, Flash, Progress, statusBadge } from "../components/bits";
import { TERMINAL, del, errorText, get, post } from "../lib/api";
import type { Candidate, QueueEvent, QueueItem } from "../lib/types";

type Tab = "active" | "failed" | "history";

function Details({ id }: { id: number }) {
  const q = useQuery({
    queryKey: ["queue-item", id],
    queryFn: () => get<{ item: QueueItem; candidates: Candidate[]; events: QueueEvent[] }>(`/api/queue/${id}`),
  });
  if (q.isLoading) return <p className="muted">Loading…</p>;
  if (q.isError) return <Flash kind="error">{errorText(q.error)}</Flash>;
  const { item, events } = q.data!;
  return (
    <div style={{ padding: "6px 0 10px" }}>
      {item.error && <Flash kind="error">{item.error}</Flash>}
      {item.final_path && <p className="mono muted" style={{ margin: "4px 0" }}>{item.final_path}</p>}
      <ul className="mono muted" style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
        {events.map((e) => <li key={e.id}>{e.created_at?.slice(11, 19)} {e.event}{e.detail ? ` · ${e.detail}` : ""}</li>)}
      </ul>
    </div>
  );
}

export default function Queue() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("active");
  const [open, setOpen] = useState<number | null>(null);
  const [error, setError] = useState("");

  const queue = useQuery({ queryKey: ["queue", "all"], queryFn: () => get<{ items: QueueItem[] }>("/api/queue"), refetchInterval: 3000 });
  const history = useQuery({ queryKey: ["queue", "history"], queryFn: () => get<{ items: QueueItem[] }>("/api/queue/history"), refetchInterval: 8000 });

  const refresh = () => qc.invalidateQueries({ queryKey: ["queue"] });
  const act = useMutation({
    mutationFn: ({ method, path, body }: { method: "post" | "del"; path: string; body?: unknown }) =>
      method === "post" ? post(path, body ?? {}) : del(path),
    onSuccess: () => { setError(""); refresh(); qc.invalidateQueries({ queryKey: ["status"] }); },
    onError: (e) => setError(errorText(e)),
  });

  const all = queue.data?.items ?? [];
  const items: QueueItem[] =
    tab === "active" ? all.filter((i) => !TERMINAL.includes(i.status) && i.status !== "awaiting_user")
    : tab === "failed" ? all.filter((i) => i.status === "failed")
    : history.data?.items ?? [];
  const failedCount = all.filter((i) => i.status === "failed").length;

  return (
    <>
      <div className="page-head">
        <h1>Queue</h1>
        <div className="segmented" role="tablist">
          {(["active", "failed", "history"] as Tab[]).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
              {t[0].toUpperCase() + t.slice(1)}{t === "failed" && failedCount > 0 ? ` (${failedCount})` : ""}
            </button>
          ))}
        </div>
        <span className="spacer" />
        {tab === "failed" && failedCount > 0 && <button className="btn btn-sm" onClick={() => act.mutate({ method: "post", path: "/api/queue/retry-failed" })}>Retry all failed</button>}
        {tab === "history" && <button className="btn btn-sm" onClick={() => act.mutate({ method: "post", path: "/api/queue/clear-completed" })}>Clear completed</button>}
      </div>
      {error && <Flash kind="error">{error}</Flash>}
      {queue.isError && <Flash kind="error">{errorText(queue.error)}</Flash>}
      {items.length === 0 ? (
        <Empty>{tab === "active" ? "Nothing is downloading." : tab === "failed" ? "No failed items." : "No history yet."}</Empty>
      ) : (
        <div className="card table-wrap" style={{ padding: 8 }}>
          <table className="stack">
            <thead><tr><th>Status</th><th>Track</th><th>Provider</th><th style={{ width: 110 }}>Progress</th><th /></tr></thead>
            <tbody>
              {items.map((it) => (
                <Fragment key={it.id}>
                  <tr>
                    <td data-label="Status">{statusBadge(it.status)}</td>
                    <td>
                      <button className="btn btn-ghost btn-sm" style={{ border: 0, padding: 0, textAlign: "left" }}
                        aria-expanded={open === it.id} onClick={() => setOpen(open === it.id ? null : it.id)}>
                        {it.artist} – {it.title}
                      </button>
                      {it.note && <div className="muted" style={{ fontSize: 12 }}>{it.note}</div>}
                    </td>
                    <td className="mono" data-label={it.provider ? "Provider" : undefined}>{it.provider ?? ""}</td>
                    <td>{!TERMINAL.includes(it.status) && <Progress value={it.progress} />}</td>
                    <td>
                      <span className="row" style={{ justifyContent: "flex-end" }}>
                        {["failed", "skipped"].includes(it.status) && <button className="btn btn-sm" onClick={() => act.mutate({ method: "post", path: `/api/queue/${it.id}/retry` })}>Retry</button>}
                        {!TERMINAL.includes(it.status) && <>
                          <button className="btn btn-sm" title="Move to the front" onClick={() => act.mutate({ method: "post", path: `/api/queue/${it.id}/priority`, body: { priority: (it.priority ?? 0) + 1 } })}>↑</button>
                          <button className="btn btn-sm btn-danger" onClick={() => act.mutate({ method: "post", path: `/api/queue/${it.id}/cancel` })}>Cancel</button>
                        </>}
                        {TERMINAL.includes(it.status) && <button className="btn btn-sm btn-ghost" onClick={() => act.mutate({ method: "del", path: `/api/queue/${it.id}` })}>Remove</button>}
                      </span>
                    </td>
                  </tr>
                  {open === it.id && <tr><td colSpan={5}><Details id={it.id} /></td></tr>}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
