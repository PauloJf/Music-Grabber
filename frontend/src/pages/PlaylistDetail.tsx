import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Empty, Flash } from "../components/bits";
import { errorText, get } from "../lib/api";
import type { Playlist, PlaylistTrack } from "../lib/types";

type Filter = "" | "have" | "missing" | "queued" | "removed";

export default function PlaylistDetail() {
  const { id } = useParams();
  const [filter, setFilter] = useState<Filter>("");
  const q = useQuery({
    queryKey: ["playlist", id, filter],
    queryFn: () => get<{ playlist: Playlist; tracks: PlaylistTrack[] }>(`/api/playlists/${id}${filter ? `?status=${filter}` : ""}`),
    refetchInterval: 8000,
  });
  if (q.isError) return <Flash kind="error">{errorText(q.error)}</Flash>;
  const pl = q.data?.playlist;
  const tracks = q.data?.tracks ?? [];

  return (
    <>
      <div className="page-head">
        <Link to="/playlists" className="muted">← Playlists</Link>
        <h1>{pl?.name ?? "…"}</h1>
        {pl && <span className="row" style={{ gap: 6 }}>
          <span className="chip chip--have">{pl.have_count} have</span>
          <span className="chip chip--missing">{pl.missing_count} missing</span>
          <span className="chip chip--queued">{pl.queued_count} queued</span>
        </span>}
      </div>
      <div className="segmented" role="tablist" style={{ marginBottom: 14 }}>
        {(["", "have", "missing", "queued", "removed"] as Filter[]).map((f) => (
          <button key={f || "all"} role="tab" aria-selected={filter === f} className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>{f || "All"}</button>
        ))}
      </div>
      {q.isSuccess && tracks.length === 0 ? <Empty>No tracks here.</Empty> : (
        <div className="card table-wrap" style={{ padding: 8 }}>
          <table className="stack">
            <thead><tr><th>#</th><th>Status</th><th>Artist</th><th>Title</th><th>Album</th></tr></thead>
            <tbody>
              {tracks.map((t) => (
                <tr key={t.id}>
                  <td className="mono muted">{t.position + 1}</td>
                  <td><span className={`chip chip--${t.derived_status === "removed" ? "neutral" : t.derived_status}`}>{t.derived_status}</span></td>
                  <td>{t.artist}</td><td>{t.title}</td><td className="muted">{t.album}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
