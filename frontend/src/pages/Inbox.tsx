import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Empty, Flash, Score, duration } from "../components/bits";
import { errorText, get, post } from "../lib/api";
import type { InboxItem } from "../lib/types";

function useAction<V>(fn: (v: V) => Promise<unknown>, onDone: () => void, onError: (m: string) => void) {
  return useMutation({ mutationFn: fn, onSuccess: onDone, onError: (e) => onError(errorText(e)) });
}

function ItemCard({ item, onError }: { item: InboxItem; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const [query, setQuery] = useState("");
  const done = () => {
    onError("");
    qc.invalidateQueries({ queryKey: ["inbox"] });
    qc.invalidateQueries({ queryKey: ["queue"] });
  };
  const choose = useAction((cid: number) => post(`/api/inbox/${item.id}/choose`, { candidate_id: cid }), done, onError);
  const search = useAction((q: string) => post(`/api/inbox/${item.id}/search`, { query: q }), done, onError);
  const again = useAction<void>(() => post(`/api/inbox/${item.id}/research`), done, onError);
  const skip = useAction<void>(() => post(`/api/inbox/${item.id}/skip`), done, onError);
  const busy = choose.isPending || search.isPending || again.isPending || skip.isPending;

  return (
    <article className="card" aria-label={`${item.artist} – ${item.title}`}>
      <div className="row" style={{ alignItems: "baseline" }}>
        <h2 style={{ margin: 0 }}>{item.artist} – {item.title}</h2>
        <span className="muted">{item.album}</span>
        <span className="muted mono">{duration(item.duration_ms)}</span>
        {item.isrc && <span className="muted mono">{item.isrc}</span>}
      </div>
      {item.note && <p className="muted" style={{ margin: "6px 0 10px" }}>{item.note}</p>}
      {item.candidates.length === 0 ? (
        <p className="muted">No candidates were found. Try a different search below.</p>
      ) : (
        <div className="table-wrap">
          <table className="stack">
            <thead><tr><th>Score</th><th>Provider</th><th>Match</th><th>Quality</th><th>Length</th><th /></tr></thead>
            <tbody>
              {item.candidates.map((c) => (
                <tr key={c.id}>
                  <td data-label="Score"><Score value={c.score} /></td>
                  <td className="mono" data-label="Provider">{c.provider}</td>
                  <td data-label="Match">{c.artist} – {c.title}{c.album ? <span className="muted"> · {c.album}</span> : null}</td>
                  <td className="mono" data-label="Quality">{c.quality}</td>
                  <td className="mono" data-label="Length">{duration(c.duration_ms)}</td>
                  <td><button className="btn btn-primary btn-sm" disabled={busy} onClick={() => choose.mutate(c.id)}>Download this</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <form className="row" style={{ marginTop: 12 }} onSubmit={(e) => { e.preventDefault(); if (query.trim()) search.mutate(query.trim()); }}>
        <input aria-label={`Search again for ${item.title}`} placeholder="Search for something else…" value={query}
          onChange={(e) => setQuery(e.target.value)} style={{ flex: 1, minWidth: 200 }} />
        <button className="btn" disabled={busy || !query.trim()}>Search</button>
        <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => again.mutate()}>Search again</button>
        <button type="button" className="btn btn-danger" disabled={busy} onClick={() => skip.mutate()}>Skip</button>
      </form>
    </article>
  );
}

export default function Inbox() {
  const { id } = useParams();
  const [error, setError] = useState("");
  const q = useQuery({ queryKey: ["inbox"], queryFn: () => get<{ items: InboxItem[] }>("/api/inbox"), refetchInterval: 5000 });
  const qc = useQueryClient();
  const all = useMutation({
    mutationFn: () => post<{ requeued: number }>("/api/inbox/research-all"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["inbox"] }),
    onError: (e) => setError(errorText(e)),
  });
  const items = (q.data?.items ?? []).filter((i) => !id || String(i.id) === id);

  return (
    <>
      <div className="page-head">
        <h1>Inbox</h1>
        <span className="muted">Tracks where the best match wasn't confident enough to download on its own.</span>
        <span className="spacer" />
        {id && <Link to="/inbox" className="btn btn-sm">Show all</Link>}
        {!id && items.length > 1 && <button className="btn btn-sm" onClick={() => all.mutate()} disabled={all.isPending}>Search again for all</button>}
      </div>
      {error && <Flash kind="error">{error}</Flash>}
      {q.isError && <Flash kind="error">{errorText(q.error)}</Flash>}
      {q.isSuccess && items.length === 0 && <Empty>Nothing needs your attention.</Empty>}
      {items.map((it) => <ItemCard key={it.id} item={it} onError={setError} />)}
    </>
  );
}
