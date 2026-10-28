import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Back, Plus } from "../components/icons";
import { Cover, ErrorState, Skeleton, Tile, TrackRow } from "../components/ui";
import { get, put } from "../lib/api";
import { fmtCount, hasLive } from "../lib/hooks";
import { useQueueing } from "../lib/queueing";
import type { ArtistPref, CatAlbum, CatArtist, CatTrack } from "../lib/types";

interface Page { artist: CatArtist; top: CatTrack[]; albums: CatAlbum[]; related: CatArtist[] }

function ArtistPrefs({ id, name }: { id: string; name: string }) {
  const qc = useQueryClient();
  const prefs = useQuery({ queryKey: ["artist-prefs"], queryFn: () => get<{ prefs: ArtistPref[] }>("/api/artist-prefs") });
  const set = useMutation({ mutationFn: (changes: object) => put(`/api/artist-prefs/${id}`, { name, ...changes }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["artist-prefs"] }); qc.invalidateQueries({ queryKey: ["releases"] }); qc.invalidateQueries({ queryKey: ["suggest"] }); } });
  if (!prefs.data) return null;
  const mine = prefs.data.prefs.find((p) => p.dz_artist_id === id);
  return (
    <div className="row tight" style={{ margin: "6px 0 14px" }}>
      <button className="btn sm" aria-pressed={!!mine?.follow} onClick={() => set.mutate({ follow: !mine?.follow })}>{mine?.follow ? "Following" : "Follow new releases"}</button>
      <button className="btn quiet sm" aria-pressed={!!mine?.hide_releases} onClick={() => set.mutate({ hide_releases: !mine?.hide_releases })}>{mine?.hide_releases ? "Hidden from new releases" : "Hide from new releases"}</button>
      <button className="btn quiet sm" aria-pressed={!!mine?.hide_suggestions} onClick={() => set.mutate({ hide_suggestions: !mine?.hide_suggestions })}>{mine?.hide_suggestions ? "Hidden from suggestions" : "Hide from suggestions"}</button>
    </div>
  );
}

export default function Artist() {
  const { id } = useParams();
  const { queueTracks, pending } = useQueueing();
  const [kind, setKind] = useState<"album" | "single">("album");
  const q = useQuery({ queryKey: ["browse", "artist", id], queryFn: () => get<Page>(`/api/browse/artist/${id}`),
    refetchInterval: (query) => (hasLive(query.state.data?.top) ? 2000 : false) });
  if (q.isLoading) return <><BackLink /><Skeleton /></>;
  if (q.isError) return <><BackLink /><ErrorState error={q.error} retry={() => q.refetch()} /></>;
  const { artist, top, albums, related } = q.data!;
  const missing = top.filter((t) => t.state.kind === "none");
  const albumsOnly = albums.filter((a) => a.kind === "album" || a.kind === "compile");
  const singles = albums.filter((a) => a.kind !== "album" && a.kind !== "compile");
  const shown = (kind === "album" ? albumsOnly : singles);
  const busy = missing.some((t) => pending.has(t.dz_id));

  return (
    <>
      <BackLink />
      <header className="row" style={{ flexWrap: "nowrap", gap: 16, margin: "6px 0 8px", alignItems: "flex-end" }}>
        <Cover src={artist.image_url} name={artist.name} size="lg" round />
        <div style={{ minWidth: 0 }}>
          <div className="eyebrow">Artist</div>
          <h1>{artist.name}</h1>
          <div className="muted mono">{[artist.fans ? `${fmtCount(artist.fans)} fans` : "", artist.album_count ? `${artist.album_count} releases` : ""].filter(Boolean).join(" · ")}</div>
        </div>
      </header>

      <ArtistPrefs id={artist.dz_id} name={artist.name} />

      <section>
        <div className="sectionhead">
          <h2>Popular</h2>
          <button className="btn signal sm" disabled={missing.length === 0 || busy} onClick={() => queueTracks(missing, `${artist.name}'s top tracks`)}>
            <Plus width={16} height={16} />{missing.length === 0 ? "All queued or owned" : `Queue ${missing.length} missing`}
          </button>
        </div>
        {top.length ? <ul className="list">{top.map((t) => <TrackRow key={t.dz_id} track={t} hideArtist />)}</ul> : <p className="muted">No popular tracks listed.</p>}
      </section>

      <section>
        <div className="sectionhead">
          <h2>Releases</h2>
          <div className="seg" role="tablist">
            <button role="tab" aria-selected={kind === "album"} onClick={() => setKind("album")}>Albums ({albumsOnly.length})</button>
            <button role="tab" aria-selected={kind === "single"} onClick={() => setKind("single")}>Singles &amp; EPs ({singles.length})</button>
          </div>
        </div>
        {shown.length ? <div className="grid">{shown.map((a) => <Tile key={a.dz_id} to={`/album/${a.dz_id}`} cover={a.cover_url} title={a.title} sub={[a.year, a.track_count ? `${a.track_count} tracks` : ""].filter(Boolean).join(" · ")} />)}</div> : <p className="muted">Nothing here.</p>}
      </section>

      {related.length > 0 && (
        <section>
          <div className="sectionhead"><h2>Fans also like</h2></div>
          <div className="chips">{related.map((r) => <Link key={r.dz_id} className="chip" to={`/artist/${r.dz_id}`}><Cover src={r.image_url} name={r.name} round />{r.name}</Link>)}</div>
        </section>
      )}
    </>
  );
}

function BackLink() {
  return <Link to="/" className="btn quiet sm" style={{ marginTop: 10, paddingLeft: 0 }} onClick={(e) => { if (window.history.length > 1) { e.preventDefault(); window.history.back(); } }}><Back width={16} height={16} />Back</Link>;
}
export { BackLink };
