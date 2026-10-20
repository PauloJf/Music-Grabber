import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Retry, X } from "../components/icons";
import { Empty, ErrorState, Pill } from "../components/ui";
import { TERMINAL, del, errorText, get, post } from "../lib/api";
import { ago, useRefreshJobs } from "../lib/hooks";
import { useToast } from "../lib/toast";
import type { Candidate, QueueEvent, QueueItem } from "../lib/types";

const WORD: Record<string, string> = { pending: "Waiting", searching: "Searching", downloading: "Downloading", transcoding: "Converting", tagging: "Tagging", placing: "Filing" };

function Details({ id }: { id: number }) {
  const q = useQuery({ queryKey: ["queue-item", id], queryFn: () => get<{ item: QueueItem; candidates: Candidate[]; events: QueueEvent[] }>(`/api/queue/${id}`) });
  if (q.isLoading) return <p className="muted">Loading…</p>;
  if (q.isError) return <ErrorState error={q.error} />;
  const { item, events } = q.data!;
  return (
    <div style={{ padding: "4px 0 12px 64px" }}>
      {item.error && <div className="banner err">{item.error}</div>}
      {item.final_path && <p className="mono muted" style={{ margin: "4px 0", wordBreak: "break-all" }}>{item.final_path}</p>}
      <ul className="mono muted" style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
        {events.map((e) => <li key={e.id}>{e.created_at?.slice(11, 19)} {e.event}{e.detail ? ` · ${e.detail}` : ""}</li>)}
      </ul>
    </div>
  );
}

export default function Queue() {
  const qc = useQueryClient();
  const toast = useToast();
  const refresh = useRefreshJobs();
  const [open, setOpen] = useState<number | null>(null);
  const all = useQuery({ queryKey: ["queue", "all"], queryFn: () => get<{ items: QueueItem[] }>("/api/queue"), refetchInterval: 2000 });
  const hist = useQuery({ queryKey: ["queue", "history"], queryFn: () => get<{ items: QueueItem[] }>("/api/queue/history"), refetchInterval: 6000 });

  const act = useMutation({
    mutationFn: ({ method, path, body }: { method: "post" | "del"; path: string; body?: unknown; ok?: string }) => (method === "post" ? post(path, body ?? {}) : del(path)),
    onSuccess: (_d, v) => { if (v.ok) toast.push({ kind: "ok", text: v.ok }); refresh(); qc.invalidateQueries({ queryKey: ["queue"] }); },
    onError: (e) => toast.push({ kind: "err", text: errorText(e) }),
  });
  const run = (method: "post" | "del", path: string, ok?: string, body?: unknown) => act.mutate({ method, path, ok, body });

  if (all.isError) return <ErrorState error={all.error} retry={() => all.refetch()} />;
  const items = all.data?.items ?? [];
  const now = items.filter((i) => ["searching", "downloading", "transcoding", "tagging", "placing"].includes(i.status));
  const next = items.filter((i) => i.status === "pending");
  const review = items.filter((i) => i.status === "awaiting_user");
  const failed = items.filter((i) => i.status === "failed");
  const done = (hist.data?.items ?? []).filter((i) => i.status !== "failed" || !failed.some((f) => f.id === i.id)).slice(0, 25);

  const row = (it: QueueItem, actions: ReactNode, meta?: ReactNode) => (
    <Fragment key={it.id}>
      <li className="item" style={{ gridTemplateColumns: "1fr auto" }}>
        <div style={{ minWidth: 0 }}>
          <button className="btn quiet" style={{ padding: 0, minHeight: 0, textAlign: "left", fontWeight: 600 }} aria-expanded={open === it.id} onClick={() => setOpen(open === it.id ? null : it.id)}>{it.title}</button>
          <div className="s clamp">{it.artist}{it.album ? ` · ${it.album}` : ""}</div>
          {meta}
          {it.note && <div className="s">{it.note}</div>}
        </div>
        <div className="row tight">{actions}</div>
      </li>
      {open === it.id && <li><Details id={it.id} /></li>}
    </Fragment>
  );
  const section = (title: string, n: number, body: ReactNode, extra?: ReactNode) => n === 0 ? null : (
    <section><div className="sectionhead"><h2>{title} <span className="muted mono" style={{ fontSize: 14 }}>{n}</span></h2>{extra}</div><ul className="list">{body}</ul></section>
  );

  return (
    <>
      <section className="hero" style={{ paddingBottom: 0 }}><div className="eyebrow">Queue</div><h1>What's happening</h1></section>
      {all.isSuccess && items.length === 0 && (hist.data?.items.length ?? 0) === 0 && (
        <Empty title="The queue is empty"><Link className="btn signal" to="/" style={{ marginTop: 10 }}>Find something to queue</Link></Empty>
      )}
      {review.length > 0 && (
        <div className="banner warn row" role="status"><span className="grow"><b>{review.length}</b> track{review.length > 1 ? "s" : ""} need{review.length > 1 ? "" : "s"} your choice.</span><Link className="btn sm" to="/inbox">Open inbox</Link></div>
      )}
      {section("Now", now.length, now.map((it) => row(it,
        <button className="btn sm danger" onClick={() => run("post", `/api/queue/${it.id}/cancel`, "Cancelled")}>Cancel</button>,
        <div style={{ marginTop: 6, maxWidth: 360 }}><div className="row tight mono" style={{ fontSize: 12, justifyContent: "space-between" }}><span>{WORD[it.status] ?? it.status}{it.provider ? ` · ${it.provider}` : ""}</span><span>{Math.round((it.progress ?? 0) * 100)}%</span></div><div className="bar"><i style={{ width: `${Math.round((it.progress ?? 0) * 100)}%` }} /></div></div>)))}
      {section("Up next", next.length, next.map((it) => row(it, <>
        <button className="btn sm" title="Move to the front" aria-label={`Move ${it.title} to the front`} onClick={() => run("post", `/api/queue/${it.id}/priority`, "Moved to the front", { priority: (it.priority ?? 0) + 1 })}>↑ First</button>
        <button className="btn sm quiet" aria-label={`Cancel ${it.title}`} onClick={() => run("post", `/api/queue/${it.id}/cancel`, "Cancelled")}><X width={16} height={16} /></button></>)))}
      {section("Failed", failed.length, failed.map((it) => row(it, <>
        <button className="btn sm" onClick={() => run("post", `/api/queue/${it.id}/retry`, "Trying again")}><Retry width={15} height={15} />Retry</button>
        <button className="btn sm quiet" aria-label={`Remove ${it.title}`} onClick={() => run("del", `/api/queue/${it.id}`)}><X width={16} height={16} /></button></>,
        <div className="s" style={{ color: "var(--err)" }}>{it.error || "Failed"}</div>)),
        <button className="btn sm" onClick={() => run("post", "/api/queue/retry-failed", "Retrying all failed")}>Retry all</button>)}
      {section("Finished", done.length, done.map((it) => row(it,
        <><Pill kind={it.status === "done" ? "ok" : "mute"}>{it.status}</Pill><span className="muted" style={{ fontSize: 12 }}>{ago(it.updated_at)}</span>
          {TERMINAL.includes(it.status) && <button className="btn sm quiet" aria-label={`Remove ${it.title}`} onClick={() => run("del", `/api/queue/${it.id}`)}><X width={16} height={16} /></button>}</>)),
        <button className="btn sm" onClick={() => run("post", "/api/queue/clear-completed", "Cleared")}>Clear completed</button>)}
    </>
  );
}
