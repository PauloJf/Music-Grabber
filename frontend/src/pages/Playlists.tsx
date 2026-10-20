import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Empty, ErrorState, Pill, Skeleton } from "../components/ui";
import { del, errorText, get, patch, post } from "../lib/api";
import { ago, useRefreshJobs } from "../lib/hooks";
import { useToast } from "../lib/toast";
import type { Playlist } from "../lib/types";

export default function Playlists() {
  const qc = useQueryClient();
  const toast = useToast();
  const refresh = useRefreshJobs();
  const [value, setValue] = useState("");
  const q = useQuery({ queryKey: ["playlists"], queryFn: () => get<{ playlists: Playlist[] }>("/api/playlists"), refetchInterval: 6000 });
  const ok = (text: string) => () => { toast.push({ kind: "ok", text }); refresh(); qc.invalidateQueries({ queryKey: ["playlists"] }); };
  const fail = (e: unknown) => toast.push({ kind: "err", text: errorText(e) });
  const add = useMutation({ mutationFn: (v: string) => post<{ connected: boolean }>("/api/playlists", { value: v }), onError: fail,
    onSuccess: (r) => { setValue(""); ok(r.connected ? "Playlist added, syncing" : "Added. Connect Spotify to sync it")(); } });
  const sync = useMutation({ mutationFn: (id: number | null) => post(id == null ? "/api/sync" : `/api/playlists/${id}/sync`), onSuccess: ok("Synced"), onError: fail });
  const toggle = useMutation({ mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) => patch(`/api/playlists/${id}`, { enabled }), onSuccess: ok("Updated"), onError: fail });
  const queueMissing = useMutation({ mutationFn: (id: number) => post<{ enqueued: number }>(`/api/playlists/${id}/enqueue-missing`), onError: fail,
    onSuccess: (r) => ok(r.enqueued ? `Queued ${r.enqueued} missing track${r.enqueued > 1 ? "s" : ""}` : "Nothing missing")() });
  const remove = useMutation({ mutationFn: (id: number) => del(`/api/playlists/${id}`), onSuccess: ok("Removed"), onError: fail });
  const submit = (e: FormEvent) => { e.preventDefault(); if (value.trim()) add.mutate(value.trim()); };
  const lists = q.data?.playlists ?? [];

  return (
    <>
      <section className="hero" style={{ paddingBottom: 0 }}>
        <div className="eyebrow">Library</div><h1>Playlists you watch</h1>
        <p>Missing tracks from these Spotify playlists are queued automatically.</p>
      </section>
      <form className="row" onSubmit={submit} style={{ margin: "14px 0 6px" }}>
        <div className="grow" style={{ minWidth: 220 }}><input aria-label="Spotify playlist URL" placeholder="Paste a Spotify playlist link" value={value} onChange={(e) => setValue(e.target.value)} /></div>
        <button className="btn primary" disabled={add.isPending || !value.trim()}>{add.isPending ? "Adding…" : "Watch playlist"}</button>
        <button type="button" className="btn" disabled={sync.isPending || lists.length === 0} onClick={() => sync.mutate(null)}>{sync.isPending ? "Syncing…" : "Sync all"}</button>
      </form>
      {q.isLoading && <Skeleton rows={3} />}
      {q.isError && <ErrorState error={q.error} retry={() => q.refetch()} />}
      {q.isSuccess && lists.length === 0 && <Empty title="No playlists yet">Paste a Spotify playlist link above and its missing tracks will be queued.</Empty>}
      {lists.length > 0 && (
        <ul className="list" style={{ marginTop: 14 }}>
          {lists.map((p) => (
            <li key={p.id} className={`item ${p.enabled ? "" : "dim"}`} style={{ gridTemplateColumns: "auto 1fr", alignItems: "start" }}>
              <input type="checkbox" aria-label={`Watch ${p.name}`} checked={!!p.enabled} onChange={(e) => toggle.mutate({ id: p.id, enabled: e.target.checked })} />
              <div style={{ minWidth: 0 }}>
                <Link className="t" to={`/playlists/${p.id}`} style={{ fontSize: 17 }}>{p.name}</Link>
                <div className="row tight" style={{ margin: "5px 0 8px" }}>
                  <Pill kind="ok">{p.have_count} have</Pill><Pill kind="warn">{p.missing_count} missing</Pill><Pill kind="signal">{p.queued_count} queued</Pill>
                  {p.new_count > 0 && <Pill kind="mute">{p.new_count} new</Pill>}
                  <span className="muted" style={{ fontSize: 12 }}>{p.last_synced_at ? `synced ${ago(p.last_synced_at)}` : "never synced"}</span>
                </div>
                <div className="row tight">
                  <button className="btn sm" disabled={sync.isPending} onClick={() => sync.mutate(p.id)}>Sync</button>
                  <button className="btn sm signal" disabled={p.missing_count === 0 || queueMissing.isPending} onClick={() => queueMissing.mutate(p.id)}>Queue missing</button>
                  <button className="btn sm quiet danger" onClick={() => { if (confirm(`Stop watching “${p.name}”? Downloaded files are not touched.`)) remove.mutate(p.id); }}>Remove</button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
