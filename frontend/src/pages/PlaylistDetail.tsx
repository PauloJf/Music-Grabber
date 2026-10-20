import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ErrorState, Empty, Pill, Skeleton } from "../components/ui";
import { get } from "../lib/api";
import type { Playlist, PlaylistTrack } from "../lib/types";
import { BackLink } from "./Artist";

type Filter = "" | "have" | "missing" | "queued" | "removed";
const KIND = { have: "ok", missing: "warn", queued: "signal", removed: "mute" } as const;

export default function PlaylistDetail() {
  const { id } = useParams();
  const [filter, setFilter] = useState<Filter>("");
  const q = useQuery({ queryKey: ["playlist", id, filter], queryFn: () => get<{ playlist: Playlist; tracks: PlaylistTrack[] }>(`/api/playlists/${id}${filter ? `?status=${filter}` : ""}`), refetchInterval: 5000 });
  if (q.isError) return <><BackLink /><ErrorState error={q.error} retry={() => q.refetch()} /></>;
  const pl = q.data?.playlist;
  const tracks = q.data?.tracks ?? [];
  return (
    <>
      <BackLink />
      <header style={{ margin: "6px 0 14px" }}>
        <div className="eyebrow">Playlist</div><h1>{pl?.name ?? "…"}</h1>
        {pl && <div className="row tight" style={{ marginTop: 8 }}><Pill kind="ok">{pl.have_count} have</Pill><Pill kind="warn">{pl.missing_count} missing</Pill><Pill kind="signal">{pl.queued_count} queued</Pill></div>}
      </header>
      <div className="seg" role="tablist" style={{ marginBottom: 14 }}>
        {(["", "have", "missing", "queued", "removed"] as Filter[]).map((f) => <button key={f || "all"} role="tab" aria-selected={filter === f} onClick={() => setFilter(f)}>{f || "All"}</button>)}
      </div>
      {q.isLoading && <Skeleton rows={5} />}
      {q.isSuccess && tracks.length === 0 && <Empty title="No tracks here" />}
      <ul className="list">
        {tracks.map((t) => (
          <li key={t.id} className="item" style={{ gridTemplateColumns: "34px 1fr auto" }}>
            <span className="ord">{t.position + 1}</span>
            <div style={{ minWidth: 0 }}><div className="t clamp">{t.title}</div><div className="s clamp">{t.artist} · {t.album}</div></div>
            <Pill kind={KIND[t.derived_status]}>{t.derived_status}</Pill>
          </li>
        ))}
      </ul>
      <p style={{ marginTop: 18 }}><Link to={`/?q=${encodeURIComponent(pl?.name ?? "")}`} className="muted">Search Deezer for something similar</Link></p>
    </>
  );
}
