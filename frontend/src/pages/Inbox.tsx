import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Empty, ErrorState } from "../components/ui";
import { Pause, Play } from "../components/icons";
import { errorText, get, post } from "../lib/api";
import { fmtDuration, useRefreshJobs } from "../lib/hooks";
import { usePlayer } from "../lib/player";
import { useToast } from "../lib/toast";
import type { InboxItem, InboxPreview } from "../lib/types";

const grade = (s: number | null) => (s == null ? "" : s >= 0.85 ? "hi" : s >= 0.55 ? "mid" : "lo");

function useAction<V>(fn: (v: V) => Promise<unknown>, ok: string) {
  const toast = useToast();
  const refresh = useRefreshJobs();
  return useMutation({ mutationFn: fn, onSuccess: () => { toast.push({ kind: "ok", text: ok }); refresh(); }, onError: (e) => toast.push({ kind: "err", text: errorText(e) }) });
}

// ▶ for a candidate (or, with no candidate, the original track): fetches the preview on demand (one
// lookup per press, not one per row), then plays it in the shared player. A YouTube candidate's clip is
// cut on the server and takes a few seconds; if that fails the button becomes a link that opens the
// video in a new tab, so there is always a way to listen.
function PreviewButton({ itemId, candidateId, label, link }: { itemId: number; candidateId?: number; label: string; link?: string }) {
  const { playing, toggle, stop } = usePlayer();
  const toast = useToast();
  const [openUrl, setOpenUrl] = useState("");
  const key = `inbox-${itemId}-${candidateId ?? "source"}`;
  const on = playing === key;
  const load = useMutation({
    mutationFn: () => get<InboxPreview>(`/api/inbox/${itemId}/preview${candidateId ? `?candidate=${candidateId}` : ""}`),
    onSuccess: (r) => {
      if (r.url) { toggle(key, r.url); return; }
      toast.push({ kind: "info", text: r.reason || "No preview available" });
      const next = r.open_url || link || "";
      if (next.startsWith("https://")) setOpenUrl(next);
    },
    onError: (e) => { toast.push({ kind: "err", text: errorText(e) }); if (link?.startsWith("https://")) setOpenUrl(link); },
  });
  if (openUrl) return <a className="btn sm" href={openUrl} target="_blank" rel="noopener noreferrer" aria-label={`Open ${label} to listen`}>Listen ↗</a>;
  return (
    <button className="btn icon sm" aria-label={`${on ? "Pause" : "Preview"} ${label}`} aria-pressed={on} aria-busy={load.isPending} disabled={load.isPending}
      title={load.isPending ? "Getting a 30-second clip…" : undefined} onClick={() => (on ? stop() : load.mutate())}>
      {load.isPending ? <span aria-hidden>…</span> : on ? <Pause width={16} height={16} /> : <Play width={16} height={16} />}
    </button>
  );
}

function ItemCard({ item }: { item: InboxItem }) {
  const [query, setQuery] = useState("");
  const choose = useAction((cid: number) => post(`/api/inbox/${item.id}/choose`, { candidate_id: cid }), "Downloading your choice");
  const search = useAction((q: string) => post(`/api/inbox/${item.id}/search`, { query: q }), "Searching again");
  const again = useAction<void>(() => post(`/api/inbox/${item.id}/research`), "Searching again");
  const skip = useAction<void>(() => post(`/api/inbox/${item.id}/skip`), "Skipped");
  const busy = choose.isPending || search.isPending || again.isPending || skip.isPending;
  return (
    <article style={{ marginBottom: 30 }} aria-label={`${item.artist} – ${item.title}`}>
      <div className="eyebrow">We weren't sure which one</div>
      <div className="row" style={{ flexWrap: "nowrap", gap: 10 }}>
        <h2 style={{ marginTop: 4 }}>{item.artist} – {item.title}</h2>
        {item.isrc && <PreviewButton itemId={item.id} label={`the original ${item.title}`} />}
      </div>
      <div className="muted mono">{[item.album, fmtDuration(item.duration_ms), item.isrc].filter(Boolean).join(" · ")}</div>
      {item.note && <p className="ink2" style={{ margin: "6px 0 0" }}>{item.note}</p>}
      {item.candidates.length === 0 ? <p className="muted">No candidates were found. Try a different search below.</p> : (
        <ul className="list" style={{ marginTop: 12 }}>
          {item.candidates.map((c) => (
            <li key={c.id} className="item" style={{ gridTemplateColumns: "44px 1fr auto" }}>
              <span className={`score ${grade(c.score)}`} aria-label={`Match ${c.score?.toFixed(2)}`}>{c.score?.toFixed(2)}</span>
              <div style={{ minWidth: 0 }}>
                <div className="t wrap">{c.artist} – {c.title}</div>
                <div className="s wrap mono">{[c.provider, c.quality, c.album, fmtDuration(c.duration_ms)].filter(Boolean).join(" · ")}</div>
              </div>
              <div className="row tight" style={{ flexWrap: "nowrap" }}>
                {c.provider === "deezer" || c.provider === "ytdlp"
                  ? <PreviewButton itemId={item.id} candidateId={c.id} label={`${c.artist} – ${c.title}`} link={c.provider === "ytdlp" ? c.url ?? undefined : undefined} /> : null}
                <button className="btn signal sm" disabled={busy} onClick={() => choose.mutate(c.id)}>Download this</button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <form className="row" style={{ marginTop: 12 }} onSubmit={(e) => { e.preventDefault(); if (query.trim()) search.mutate(query.trim()); }}>
        <div className="grow" style={{ minWidth: 200 }}><input aria-label={`Search again for ${item.title}`} placeholder="Search for something else…" value={query} onChange={(e) => setQuery(e.target.value)} /></div>
        <button className="btn" disabled={busy || !query.trim()}>Search</button>
        <button type="button" className="btn quiet" disabled={busy} onClick={() => again.mutate()}>Search again</button>
        <button type="button" className="btn danger" disabled={busy} onClick={() => skip.mutate()}>Skip</button>
      </form>
    </article>
  );
}

export default function Inbox() {
  const { id } = useParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["inbox"], queryFn: () => get<{ items: InboxItem[] }>("/api/inbox"), refetchInterval: 4000 });
  const all = useAction<void>(() => post("/api/inbox/research-all"), "Searching again for all");
  const items = (q.data?.items ?? []).filter((i) => !id || String(i.id) === id);
  return (
    <>
      <section className="hero" style={{ paddingBottom: 0 }}>
        <div className="eyebrow">Inbox</div><h1>Needs your ear</h1>
        <p>Tracks where the best match wasn't confident enough to download on its own. Pick one, search again, or skip.</p>
      </section>
      <div className="row" style={{ margin: "12px 0 18px" }}>
        {id && <Link to="/inbox" className="btn sm">Show all</Link>}
        {!id && items.length > 1 && <button className="btn sm" onClick={() => all.mutate(undefined, { onSuccess: () => qc.invalidateQueries({ queryKey: ["inbox"] }) })}>Search again for all</button>}
      </div>
      {q.isError && <ErrorState error={q.error} retry={() => q.refetch()} />}
      {q.isSuccess && items.length === 0 && <Empty title="Nothing needs your attention">When a download isn't a confident match, it waits here.</Empty>}
      {items.map((it) => <ItemCard key={it.id} item={it} />)}
    </>
  );
}
