import { useInfiniteQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { Empty, ErrorState, Skeleton } from "../components/ui";
import { get } from "../lib/api";
import { fmtDuration } from "../lib/hooks";
import type { OwnedPage } from "../lib/types";

const PAGE = 100;

export function LibraryTabs() {
  return (
    <nav className="seg" aria-label="Library" style={{ marginTop: 14 }}>
      <NavLink to="/playlists" end className={({ isActive }) => (isActive ? "on" : "")}>Playlists</NavLink>
      <NavLink to="/playlists/owned" className={({ isActive }) => (isActive ? "on" : "")}>Owned tracks</NavLink>
    </nav>
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
            {tracks.map((t) => (
              <li key={t.path} className="item" style={{ gridTemplateColumns: "1fr auto" }}>
                <div style={{ minWidth: 0 }}>
                  <div className="t wrap">{t.title || t.path.split("/").pop()}</div>
                  <div className="s wrap">{[t.artist || t.album_artist, t.album, t.year].filter(Boolean).join(" · ")}</div>
                </div>
                <span className="muted mono">{fmtDuration(t.duration_ms)}</span>
              </li>
            ))}
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
