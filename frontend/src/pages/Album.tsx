import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Plus } from "../components/icons";
import { Cover, ErrorState, Skeleton, TrackRow } from "../components/ui";
import { get } from "../lib/api";
import { fmtDuration, hasLive } from "../lib/hooks";
import { useQueueing } from "../lib/queueing";
import type { CatAlbum, CatTrack } from "../lib/types";
import { BackLink } from "./Artist";

export default function Album() {
  const { id } = useParams();
  const { queueTracks, pending } = useQueueing();
  const [sel, setSel] = useState<Set<string>>(new Set());
  const q = useQuery({ queryKey: ["browse", "album", id], queryFn: () => get<{ album: CatAlbum; tracks: CatTrack[] }>(`/api/browse/album/${id}`),
    refetchInterval: (query) => (hasLive(query.state.data?.tracks) ? 2000 : false) });
  if (q.isLoading) return <><BackLink /><Skeleton /></>;
  if (q.isError) return <><BackLink /><ErrorState error={q.error} retry={() => q.refetch()} /></>;
  const { album, tracks } = q.data!;
  const missing = tracks.filter((t) => t.state.kind === "none");
  const owned = tracks.filter((t) => t.state.kind === "library").length;
  const total = tracks.reduce((n, t) => n + (t.duration_ms ?? 0), 0);
  const chosen = tracks.filter((t) => sel.has(t.dz_id));
  const toggle = (id: string, on: boolean) => setSel((cur) => { const n = new Set(cur); on ? n.add(id) : n.delete(id); return n; });
  const busy = tracks.some((t) => pending.has(t.dz_id)) || pending.has(`album:${album.dz_id}`);

  return (
    <>
      <BackLink />
      <header className="row" style={{ flexWrap: "nowrap", gap: 16, margin: "6px 0 14px", alignItems: "flex-end" }}>
        <Cover src={album.cover_url} name={album.title} size="lg" />
        <div style={{ minWidth: 0 }}>
          <div className="eyebrow">{album.kind === "album" ? "Album" : album.kind}</div>
          <h1>{album.title}</h1>
          <div className="ink2">{album.artist_id ? <Link to={`/artist/${album.artist_id}`}>{album.artist}</Link> : album.artist}</div>
          <div className="muted mono">{[album.year, `${tracks.length} tracks`, fmtDuration(total)].filter(Boolean).join(" · ")}{owned ? ` · ${owned} in library` : ""}</div>
        </div>
      </header>
      <div className="row">
        <button className="btn signal" disabled={missing.length === 0 || busy} onClick={() => queueTracks(missing, album.title).then(() => setSel(new Set()))}>
          <Plus width={18} height={18} />{missing.length === 0 ? "Everything queued or owned" : missing.length === tracks.length ? "Queue album" : `Queue ${missing.length} missing`}
        </button>
        {chosen.length > 0 && <button className="btn quiet" onClick={() => setSel(new Set())}>Clear selection</button>}
      </div>
      <ul className="list" style={{ marginTop: 16 }}>
        {tracks.map((t, i) => <TrackRow key={t.dz_id} track={t} ord={t.track_no ?? i + 1} hideAlbum hideArtist={t.artist === album.artist}
          selected={sel.has(t.dz_id)} onSelect={(on) => toggle(t.dz_id, on)} />)}
      </ul>
      {chosen.length > 0 && (
        <div className="selbar" role="region" aria-label="Selection">
          <span className="grow">{chosen.length} selected</span>
          <button className="btn signal sm" onClick={() => queueTracks(chosen, `${chosen.length} tracks`).then(() => setSel(new Set()))}>Queue selected</button>
        </div>
      )}
    </>
  );
}
