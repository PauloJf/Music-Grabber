import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Empty, Flash } from "../components/bits";
import { del, errorText, get, patch, post } from "../lib/api";
import type { Playlist } from "../lib/types";

export default function Playlists() {
  const qc = useQueryClient();
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const q = useQuery({ queryKey: ["playlists"], queryFn: () => get<{ playlists: Playlist[] }>("/api/playlists"), refetchInterval: 8000 });

  const done = (msg = "") => () => { setError(""); setNote(msg); qc.invalidateQueries({ queryKey: ["playlists"] }); qc.invalidateQueries({ queryKey: ["status"] }); };
  const fail = (e: unknown) => { setNote(""); setError(errorText(e)); };
  const add = useMutation({ mutationFn: (v: string) => post<{ connected: boolean }>("/api/playlists", { value: v }),
    onSuccess: (r) => { setValue(""); done(r.connected ? "" : "Added. Connect Spotify to sync it.")(); }, onError: fail });
  const sync = useMutation({ mutationFn: (id: number | null) => post(id == null ? "/api/sync" : `/api/playlists/${id}/sync`), onSuccess: done("Synced."), onError: fail });
  const toggle = useMutation({ mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) => patch(`/api/playlists/${id}`, { enabled }), onSuccess: done(), onError: fail });
  const queueMissing = useMutation({ mutationFn: (id: number) => post<{ enqueued: number }>(`/api/playlists/${id}/enqueue-missing`),
    onSuccess: (r) => done(`Queued ${r.enqueued} missing track(s).`)(), onError: fail });
  const remove = useMutation({ mutationFn: (id: number) => del(`/api/playlists/${id}`), onSuccess: done("Removed."), onError: fail });

  const submit = (e: FormEvent) => { e.preventDefault(); if (value.trim()) add.mutate(value.trim()); };
  const lists = q.data?.playlists ?? [];

  return (
    <>
      <div className="page-head">
        <h1>Playlists</h1>
        <span className="muted">Spotify playlists to watch. Missing tracks are queued automatically.</span>
        <span className="spacer" />
        <button className="btn btn-sm" disabled={sync.isPending || lists.length === 0} onClick={() => sync.mutate(null)}>{sync.isPending ? "Syncing…" : "Sync all"}</button>
      </div>
      {error && <Flash kind="error">{error}</Flash>}
      {note && <Flash kind="success">{note}</Flash>}
      <form className="card row" onSubmit={submit}>
        <input aria-label="Spotify playlist URL" placeholder="https://open.spotify.com/playlist/…" value={value}
          onChange={(e) => setValue(e.target.value)} style={{ flex: 1, minWidth: 220 }} />
        <button className="btn btn-primary" disabled={add.isPending || !value.trim()}>Watch playlist</button>
      </form>
      {q.isSuccess && lists.length === 0 && <Empty>No playlists yet. Paste a Spotify playlist URL above.</Empty>}
      {lists.length > 0 && (
        <div className="card table-wrap" style={{ padding: 8 }}>
          <table className="stack">
            <thead><tr><th>Watch</th><th>Playlist</th><th>Coverage</th><th /></tr></thead>
            <tbody>
              {lists.map((p) => (
                <tr key={p.id}>
                  <td><input type="checkbox" aria-label={`Watch ${p.name}`} checked={!!p.enabled} onChange={(e) => toggle.mutate({ id: p.id, enabled: e.target.checked })} /></td>
                  <td><Link to={`/playlists/${p.id}`}>{p.name}</Link>{p.new_count > 0 && <span className="chip chip--queued" style={{ marginLeft: 8 }}>{p.new_count} new</span>}</td>
                  <td className="row" style={{ gap: 6 }} data-label="Coverage">
                    <span className="chip chip--have">{p.have_count} have</span>
                    <span className="chip chip--missing">{p.missing_count} missing</span>
                    <span className="chip chip--queued">{p.queued_count} queued</span>
                  </td>
                  <td>
                    <span className="row" style={{ justifyContent: "flex-end" }}>
                      <button className="btn btn-sm" disabled={sync.isPending} onClick={() => sync.mutate(p.id)}>Sync</button>
                      <button className="btn btn-sm" disabled={p.missing_count === 0 || queueMissing.isPending} onClick={() => queueMissing.mutate(p.id)}>Queue missing</button>
                      <button className="btn btn-sm btn-danger" onClick={() => { if (confirm(`Stop watching “${p.name}”? Downloaded files are not touched.`)) remove.mutate(p.id); }}>Remove</button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
