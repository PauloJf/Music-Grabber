import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Search, X } from "../components/icons";
import { Empty, ErrorState, Skeleton, Tile, TrackRow } from "../components/ui";
import { ApiError, get } from "../lib/api";
import { fmtCount, hasLive } from "../lib/hooks";
import type { CatAlbum, CatArtist, CatTrack } from "../lib/types";

type Kind = "track" | "artist" | "album";
const KINDS: { k: Kind; label: string }[] = [{ k: "track", label: "Songs" }, { k: "artist", label: "Artists" }, { k: "album", label: "Albums" }];
const RECENT = "mg.recent";

function recent(): string[] {
  try { return JSON.parse(localStorage.getItem(RECENT) ?? "[]"); } catch { return []; }
}
function remember(q: string) {
  try { localStorage.setItem(RECENT, JSON.stringify([q, ...recent().filter((x) => x !== q)].slice(0, 8))); } catch { /* storage blocked */ }
}

export default function Discover() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const kind = (params.get("t") as Kind) || "track";
  const [text, setText] = useState(q);
  const [recents, setRecents] = useState(recent);
  const input = useRef<HTMLInputElement>(null);

  // Search as you type (debounced); the URL is the state, so Back returns to the same results.
  useEffect(() => {
    if (text.trim() === q) return;
    const id = window.setTimeout(() => {
      const next = new URLSearchParams(params);
      text.trim() ? next.set("q", text.trim()) : next.delete("q");
      setParams(next, { replace: true });
    }, 350);
    return () => window.clearTimeout(id);
  }, [text, q, params, setParams]);
  useEffect(() => setText(q), [q]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && document.activeElement?.tagName !== "INPUT") { e.preventDefault(); input.current?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const res = useQuery({
    queryKey: ["browse", "search", kind, q],
    queryFn: async () => {
      const r = await get<{ results: (CatTrack | CatArtist | CatAlbum)[] }>(`/api/browse/search?q=${encodeURIComponent(q)}&type=${kind}`);
      remember(q); setRecents(recent());
      return r.results;
    },
    enabled: q.length > 0,
    refetchInterval: (query) => (kind === "track" && hasLive(query.state.data as CatTrack[] | undefined) ? 2000 : false),
  });

  const setKind = (k: Kind) => { const n = new URLSearchParams(params); n.set("t", k); setParams(n, { replace: true }); };
  const code = res.error instanceof ApiError ? res.error.code : "";

  return (
    <>
      <section className="hero">
        <div className="eyebrow">Discover</div>
        <h1>Find it, then<br />queue it.</h1>
        <p>Search Deezer for songs, artists and albums. Preview 30 seconds, see what you already own, and queue the rest. <Link to="/releases">New releases from your artists</Link></p>
      </section>
      <form role="search" onSubmit={(e) => { e.preventDefault(); const n = new URLSearchParams(params); text.trim() ? n.set("q", text.trim()) : n.delete("q"); setParams(n); }}>
        <div className="search">
          <Search />
          <input ref={input} data-search type="search" inputMode="search" enterKeyHint="search" autoComplete="off" autoCapitalize="off" spellCheck={false}
            aria-label="Search Deezer" placeholder="Song, artist or album" value={text} onChange={(e) => setText(e.target.value)} />
          {text && <button type="button" className="btn quiet icon sm clear" aria-label="Clear search" onClick={() => { setText(""); input.current?.focus(); }}><X width={18} height={18} /></button>}
        </div>
      </form>
      <div className="row" style={{ margin: "14px 0 16px" }} role="tablist" aria-label="Search in">
        <div className="seg">{KINDS.map((x) => <button key={x.k} role="tab" aria-selected={kind === x.k} onClick={() => setKind(x.k)}>{x.label}</button>)}</div>
        {res.isFetching && q && <span className="muted mono" role="status">Searching…</span>}
      </div>

      {!q && (
        <>
          {recents.length > 0 ? (
            <section>
              <div className="sectionhead"><h3>Recent searches</h3><button className="btn quiet sm" onClick={() => { try { localStorage.removeItem(RECENT); } catch { /* ignore */ } setRecents([]); }}>Clear</button></div>
              <div className="chips" style={{ flexWrap: "wrap" }}>{recents.map((r) => <button key={r} className="btn sm" onClick={() => setText(r)}>{r}</button>)}</div>
            </section>
          ) : <Empty title="Start with a name">Try an artist you love, or a song stuck in your head. Press <span className="mono">/</span> to jump to search.</Empty>}
        </>
      )}

      {q && res.isLoading && <Skeleton />}
      {q && res.isError && <ErrorState error={res.error} retry={() => res.refetch()} />}
      {q && res.isSuccess && res.data.length === 0 && <Empty title={`Nothing for “${q}”`}>Check the spelling, or try the {kind === "track" ? "Artists" : "Songs"} tab.</Empty>}
      {res.isSuccess && res.data.length > 0 && kind === "track" && (
        <ul className="list">{(res.data as CatTrack[]).map((t) => <TrackRow key={t.dz_id} track={t} />)}</ul>
      )}
      {res.isSuccess && res.data.length > 0 && kind === "artist" && (
        <div className="grid">{(res.data as CatArtist[]).map((a) => <Tile key={a.dz_id} to={`/artist/${a.dz_id}`} cover={a.image_url} title={a.name} sub={a.fans ? `${fmtCount(a.fans)} fans` : ""} round />)}</div>
      )}
      {res.isSuccess && res.data.length > 0 && kind === "album" && (
        <div className="grid">{(res.data as CatAlbum[]).map((a) => <Tile key={a.dz_id} to={`/album/${a.dz_id}`} cover={a.cover_url} title={a.title} sub={`${a.artist}${a.year ? ` · ${a.year}` : ""}`} />)}</div>
      )}
      {code === "deezer_unreachable" && <p className="muted">Deezer's public catalogue is needed for browsing. Downloads that are already queued keep going.</p>}
    </>
  );
}
