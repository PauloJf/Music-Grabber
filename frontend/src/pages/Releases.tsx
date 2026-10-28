import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Empty, ErrorState, Skeleton, TileCard } from "../components/ui";
import { del, errorText, get, post, put } from "../lib/api";
import { ago } from "../lib/hooks";
import { useToast } from "../lib/toast";
import type { ReleaseFeed, ReleasesHidden } from "../lib/types";

const KIND: Record<string, string> = { album: "Album", single: "Single", ep: "EP", compile: "Compilation" };

export default function Releases() {
  const qc = useQueryClient();
  const toast = useToast();
  const [showHidden, setShowHidden] = useState(false);
  const feed = useQuery({ queryKey: ["releases"], queryFn: () => get<ReleaseFeed>("/api/releases") });
  const hidden = useQuery({ queryKey: ["releases", "hidden"], queryFn: () => get<ReleasesHidden>("/api/releases/hidden"), enabled: showHidden });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["releases"] }); qc.invalidateQueries({ queryKey: ["artist-prefs"] }); };
  const fail = (e: unknown) => toast.push({ kind: "err", text: errorText(e) });
  const hideArtist = useMutation({ mutationFn: ({ id, name, on }: { id: string; name: string; on: boolean }) => put(`/api/artist-prefs/${id}`, { hide_releases: on, name }),
    onSuccess: (_d, v) => { toast.push({ kind: "ok", text: v.on ? `Hiding ${v.name}` : `Showing ${v.name} again` }); refresh(); }, onError: fail });
  const dismiss = useMutation({ mutationFn: ({ id, on }: { id: string; on: boolean }) => (on ? post(`/api/releases/${id}/dismiss`) : del(`/api/releases/${id}/dismiss`)),
    onSuccess: refresh, onError: fail });
  const scan = useMutation({ mutationFn: () => post("/api/releases/refresh"), onSuccess: () => toast.push({ kind: "ok", text: "Checking for new releases…" }), onError: fail });

  const d = feed.data;
  return (
    <>
      <section className="hero">
        <div className="eyebrow">New releases</div>
        <h1>What your artists<br />put out lately.</h1>
        <p>Albums, singles and EPs from artists you own a few tracks of, artists on your watched playlists, and ones you follow. <Link to="/">Suggestions for you</Link></p>
      </section>
      {feed.isLoading && <Skeleton />}
      {feed.isError && <ErrorState error={feed.error} retry={() => feed.refetch()} />}
      {d && !d.enabled && <Empty title="New releases are off">Set <span className="mono">RELEASES_ENABLED=true</span> and restart. It reads Deezer's public catalogue in the background, a few dozen artists at a time.</Empty>}
      {d?.enabled && (
        <>
          <div className="row" style={{ margin: "0 0 14px", justifyContent: "space-between" }}>
            <span className="muted mono">
              {d.status ? `${d.status.mapped} of ${d.status.artists} artists matched${d.status.last_checked ? ` · checked ${ago(d.status.last_checked)}` : " · not checked yet"}${d.status.more_to_do ? " · still working through your library" : ""}` : ""}
            </span>
            <button className="btn sm" onClick={() => scan.mutate()} disabled={scan.isPending}>Check now</button>
          </div>
          {d.status?.last_error && <p className="muted">Deezer said: {d.status.last_error}. What is shown may be out of date.</p>}
          {d.releases.length === 0
            ? <Empty title={`Nothing new in ${d.days} days`}>Releases show up here after the first pass has looked at your artists. Follow an artist from its page to include one you don't own.</Empty>
            : <div className="grid">{d.releases.map((r) => (
              <TileCard key={r.dz_album_id} to={`/album/${r.dz_album_id}`} cover={r.cover_url} title={r.title} sub={`${r.artist_name} · ${KIND[r.kind] ?? r.kind} · ${r.release_date}`}>
                  <button className="btn quiet sm" style={{ paddingLeft: 0 }} onClick={() => hideArtist.mutate({ id: r.dz_artist_id, name: r.artist_name, on: true })}>Hide artist</button>
                  <button className="btn quiet sm" style={{ paddingLeft: 0 }} onClick={() => dismiss.mutate({ id: r.dz_album_id, on: true })}>Not interested</button>
                </TileCard>))}</div>}
          <div style={{ margin: "26px 0 70px" }}>
            <button className="btn quiet sm" aria-expanded={showHidden} onClick={() => setShowHidden(!showHidden)}>{showHidden ? "Hide the hidden list" : "Hidden artists and releases"}</button>
            {showHidden && hidden.isLoading && <Skeleton rows={2} />}
            {showHidden && hidden.data && (
              <div className="stack" style={{ marginTop: 10, maxWidth: 560 }}>
                {hidden.data.artists.length === 0 && hidden.data.releases.length === 0 && <p className="muted">Nothing hidden.</p>}
                {hidden.data.artists.map((a) => (
                  <div key={a.dz_artist_id} className="row" style={{ justifyContent: "space-between" }}>
                    <Link to={`/artist/${a.dz_artist_id}`}>{a.name}</Link>
                    <button className="btn sm" onClick={() => hideArtist.mutate({ id: a.dz_artist_id, name: a.name, on: false })}>Show again</button>
                  </div>))}
                {hidden.data.releases.map((r) => (
                  <div key={r.dz_album_id} className="row" style={{ justifyContent: "space-between" }}>
                    <span>{r.title} <span className="muted">· {r.artist_name}</span></span>
                    <button className="btn sm" onClick={() => dismiss.mutate({ id: r.dz_album_id, on: false })}>Show again</button>
                  </div>))}
              </div>
            )}
          </div>
        </>
      )}
    </>
  );
}
