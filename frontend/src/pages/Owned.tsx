import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { Cover, Empty, ErrorState, Skeleton } from "../components/ui";
import { get } from "../lib/api";
import { fmtDuration } from "../lib/hooks";
import type { OwnedPage, OwnedRelated, OwnedTrack } from "../lib/types";

const PAGE = 100;

export function LibraryTabs() {
  return (
    <nav className="seg" aria-label="Library" style={{ marginTop: 14 }}>
      <NavLink to="/playlists" end className={({ isActive }) => (isActive ? "on" : "")}>Playlists</NavLink>
      <NavLink to="/playlists/owned" className={({ isActive }) => (isActive ? "on" : "")}>Owned tracks</NavLink>
    </nav>
  );
}

/** What Deezer knows around one owned track: its artist, album and track pages, and related artists. Looked up
 * only when the row is opened. */
function Related({ track }: { track: OwnedTrack }) {
  const q = useQuery({ queryKey: ["owned-related", track.path], queryFn: () => get<OwnedRelated>(`/api/library/related?path=${encodeURIComponent(track.path)}`), staleTime: 10 * 60_000 });
  if (q.isLoading) return <Skeleton rows={2} />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  if (!d.found) return <p className="muted" style={{ margin: 0 }}>Can't find this artist on Deezer with certainty, so there is nothing to show.</p>;
  return (
    <div className="stack" style={{ gap: 10, minWidth: 0 }}>
      <div className="row tight">
        {d.artist && <Link className="btn sm" to={`/artist/${d.artist.dz_id}`}>Artist: {d.artist.name}</Link>}
        {d.album && <Link className="btn sm" to={`/album/${d.album.dz_id}`}>Album: {d.album.title}</Link>}
        {d.track && <a className="btn sm" href={`https://www.deezer.com/track/${d.track.dz_id}`} target="_blank" rel="noopener noreferrer">Track on Deezer</a>}
      </div>
      {!d.album && <span className="muted" style={{ fontSize: 13 }}>No ISRC on this file, so only the artist could be matched.</span>}
      {d.related.length > 0 ? (
        <div style={{ minWidth: 0 }}>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Fans also like</div>
          <div className="chips">{d.related.map((r) => <Link key={r.dz_id} className="chip" to={`/artist/${r.dz_id}`}><Cover src={r.image_url} name={r.name} round />{r.name}</Link>)}</div>
        </div>
      ) : <span className="muted" style={{ fontSize: 13 }}>No related artists listed.</span>}
    </div>
  );
}

function Row({ t }: { t: OwnedTrack }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="item" style={{ gridTemplateColumns: "minmax(0, 1fr) auto" }}>
      <div style={{ minWidth: 0 }}>
        <div className="t wrap">{t.title || t.path.split("/").pop()}</div>
        <div className="s wrap">{[t.artist || t.album_artist, t.album, t.year].filter(Boolean).join(" · ")}</div>
      </div>
      <div className="row tight">
        <span className="muted mono">{fmtDuration(t.duration_ms)}</span>
        <button className="btn quiet sm" aria-expanded={open} aria-label={`Related to ${t.title || t.path}`} onClick={() => setOpen(!open)}>Related</button>
      </div>
      {open && <div style={{ gridColumn: "1 / -1", paddingTop: 6, minWidth: 0 }}><Related track={t} /></div>}
    </li>
  );
}

export default function Owned() {
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => { const id = window.setTimeout(() => setQ(text.trim()), 300); return () => window.clearTimeout(id); }, [text]);
  const res = useInfiniteQuery({
    queryKey: ["owned", q],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => get<OwnedPage>(`/api/library?limit=${PAGE}&offset=${pageParam}${q ? `&q=${encodeURIComponent(q)}` : ""}`),
    getNextPageParam: (last) => (last.offset + last.tracks.length < last.total ? last.offset + last.tracks.length : undefined),
  });
  const pages = res.data?.pages ?? [];
  const tracks = pages.flatMap((p) => p.tracks);
  const total = pages[0]?.total ?? 0;

  return (
    <>
      <section className="hero" style={{ paddingBottom: 0 }}>
        <div className="eyebrow">Library</div><h1>Tracks you already own</h1>
        <p>Everything the library index knows: what Discover and your playlists count as owned.</p>
      </section>
      <LibraryTabs />
      <div className="search" style={{ margin: "14px 0 6px" }}>
        <input type="search" aria-label="Filter owned tracks" placeholder="Filter by title, artist, album or path" value={text} onChange={(e) => setText(e.target.value)} />
      </div>
      {res.isLoading && <Skeleton rows={6} />}
      {res.isError && <ErrorState error={res.error} retry={() => res.refetch()} />}
      {res.isSuccess && total === 0 && (
        <Empty title={q ? "No owned track matches" : "The library index is empty"}>
          {q ? "Try fewer words." : "Check that the music folder is mounted where LIBRARY_DIR points, then refresh the library."}
        </Empty>
      )}
      {tracks.length > 0 && (
        <>
          <p className="muted mono" style={{ margin: "8px 0" }}>{tracks.length} of {total}{q ? ` matching “${q}”` : ""}</p>
          <ul className="list">
            {tracks.map((t) => <Row key={t.path} t={t} />)}
          </ul>
          {res.hasNextPage && (
            <div className="row" style={{ justifyContent: "center", margin: "14px 0" }}>
              <button className="btn" disabled={res.isFetchingNextPage} onClick={() => res.fetchNextPage()}>
                {res.isFetchingNextPage ? "Loading…" : `Show ${Math.min(PAGE, total - tracks.length)} more`}
              </button>
            </div>
          )}
        </>
      )}
    </>
  );
}
