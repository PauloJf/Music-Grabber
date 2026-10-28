import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Empty, ErrorState, Skeleton, Tile } from "../components/ui";
import { del, errorText, get, post, put } from "../lib/api";
import { fmtCount } from "../lib/hooks";
import { useToast } from "../lib/toast";
import type { SuggestHidden, Suggestions } from "../lib/types";

export default function ForYou() {
  const qc = useQueryClient();
  const toast = useToast();
  const [showHidden, setShowHidden] = useState(false);
  const q = useQuery({ queryKey: ["suggest"], queryFn: () => get<Suggestions>("/api/suggest") });
  const hidden = useQuery({ queryKey: ["suggest", "hidden"], queryFn: () => get<SuggestHidden>("/api/suggest/hidden"), enabled: showHidden });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["suggest"] }); qc.invalidateQueries({ queryKey: ["artist-prefs"] }); };
  const fail = (e: unknown) => toast.push({ kind: "err", text: errorText(e) });
  const dismiss = useMutation({ mutationFn: ({ kind, id, on }: { kind: "album" | "artist"; id: string; on: boolean }) =>
    (on ? post(`/api/suggest/${kind}/${id}/dismiss`) : del(`/api/suggest/${kind}/${id}/dismiss`)), onSuccess: refresh, onError: fail });
  const hideArtist = useMutation({ mutationFn: ({ id, name, on }: { id: string; name: string; on: boolean }) => put(`/api/artist-prefs/${id}`, { hide_suggestions: on, name }),
    onSuccess: (_d, v) => { toast.push({ kind: "ok", text: v.on ? `No more suggestions for ${v.name}` : `${v.name} is back` }); refresh(); }, onError: fail });

  const d = q.data;
  const nothing = d?.enabled && !d.gaps_enabled && !d.artists_enabled;
  return (
    <>
      <section className="hero">
        <div className="eyebrow">For you</div>
        <h1>Based on what<br />you already play.</h1>
        <p>Albums you're missing from artists you own, and artists your artists point to. <Link to="/releases">New releases</Link></p>
      </section>
      {q.isLoading && <Skeleton />}
      {q.isError && <ErrorState error={q.error} retry={() => q.refetch()} />}
      {d && !d.enabled && <Empty title="Suggestions are off">Turn on New releases and suggestions in Settings. They read Deezer's public catalogue in the background and your own library.</Empty>}
      {nothing && <Empty title="Every suggestion is switched off">Choose which ones you want under Settings, New releases and suggestions.</Empty>}

      {d?.artists_enabled && (
        <section>
          <div className="sectionhead"><h2>Artists you may like</h2></div>
          {d.artists.length === 0
            ? <Empty title="Nothing yet">It needs a pass over your top artists first{d.status?.more_to_do ? " (still working through them)" : ""}.</Empty>
            : <div className="grid">{d.artists.map((a) => (
              <div key={a.dz_id}>
                <Tile to={`/artist/${a.dz_id}`} cover={a.image_url} title={a.name} sub={`Because you have ${a.because.join(" and ")}${a.fans ? ` · ${fmtCount(a.fans)} fans` : ""}`} round />
                <div className="row tight" style={{ marginTop: 6 }}>
                  <button className="btn quiet sm" onClick={() => dismiss.mutate({ kind: "artist", id: a.dz_id, on: true })}>Not interested</button>
                </div>
              </div>))}</div>}
        </section>
      )}

      {d?.gaps_enabled && (
        <section>
          <div className="sectionhead"><h2>Albums you're missing</h2></div>
          {d.gaps.length === 0
            ? <Empty title="No gaps found">Albums by artists you own show up here once they have been read from Deezer.</Empty>
            : <div className="grid">{d.gaps.map((g) => (
              <div key={g.dz_album_id}>
                <Tile to={`/album/${g.dz_album_id}`} cover={g.cover_url} title={g.title}
                  sub={`${g.artist_name}${g.release_date ? ` · ${g.release_date.slice(0, 4)}` : ""} · ${g.state === "partial" ? `you have ${g.owned} of ${g.track_count}` : "none of it"}`} />
                <div className="row tight" style={{ marginTop: 6 }}>
                  <button className="btn quiet sm" onClick={() => dismiss.mutate({ kind: "album", id: g.dz_album_id, on: true })}>Not interested</button>
                  <button className="btn quiet sm" onClick={() => hideArtist.mutate({ id: g.dz_artist_id, name: g.artist_name, on: true })}>Hide artist</button>
                </div>
              </div>))}</div>}
        </section>
      )}

      {d?.enabled && (
        <div style={{ margin: "26px 0 70px" }}>
          <button className="btn quiet sm" aria-expanded={showHidden} onClick={() => setShowHidden(!showHidden)}>{showHidden ? "Hide the hidden list" : "Hidden suggestions"}</button>
          {showHidden && hidden.isLoading && <Skeleton rows={2} />}
          {showHidden && hidden.data && (
            <div className="stack" style={{ marginTop: 10, maxWidth: 560 }}>
              {hidden.data.artists.length === 0 && hidden.data.albums.length === 0 && <p className="muted">Nothing hidden.</p>}
              {hidden.data.artists.map((a) => (
                <div key={a.dz_artist_id} className="row" style={{ justifyContent: "space-between" }}>
                  <Link to={`/artist/${a.dz_artist_id}`}>{a.name}</Link>
                  <button className="btn sm" onClick={() => ("hide_suggestions" in a && a.hide_suggestions
                    ? hideArtist.mutate({ id: a.dz_artist_id, name: a.name, on: false })
                    : dismiss.mutate({ kind: "artist", id: a.dz_artist_id, on: false }))}>Show again</button>
                </div>))}
              {hidden.data.albums.map((r) => (
                <div key={r.dz_album_id} className="row" style={{ justifyContent: "space-between" }}>
                  <span>{r.title} <span className="muted">· {r.artist_name}</span></span>
                  <button className="btn sm" onClick={() => dismiss.mutate({ kind: "album", id: r.dz_album_id, on: false })}>Show again</button>
                </div>))}
            </div>
          )}
        </div>
      )}
    </>
  );
}
