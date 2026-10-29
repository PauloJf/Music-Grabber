import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Cover, Empty, ErrorState, Skeleton, Tile } from "../components/ui";
import { get } from "../lib/api";
import { fmtDuration } from "../lib/hooks";
import type { LibAlbum, LibAlbumPage, LibArtist, LibArtistPage } from "../lib/types";
import { BackLink } from "./Artist";
import { LibraryTabs } from "./Owned";

const NO_ALBUM = "(no album)";
const STEP = 200;
const enc = encodeURIComponent;
export const artistPath = (name: string) => `/library/artists/${enc(name)}`;
export const albumPath = (artist: string, album: string) => `/library/albums/${enc(artist)}/${enc(album)}`;

function useDebounced(text: string) {
  const [q, setQ] = useState("");
  useEffect(() => { const id = window.setTimeout(() => setQ(text.trim()), 250); return () => window.clearTimeout(id); }, [text]);
  return q;
}

function Filter({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div className="search" style={{ margin: "14px 0 6px" }}>
      <input type="search" aria-label={label} placeholder={label} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function Head({ title, children }: { title: string; children: string }) {
  return (
    <>
      <section className="hero" style={{ paddingBottom: 0 }}>
        <div className="eyebrow">Library</div><h1>{title}</h1>
        <p>{children}</p>
      </section>
      <LibraryTabs />
    </>
  );
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const albumSub = (a: LibAlbum, withArtist: boolean) =>
  [withArtist ? a.artist : "", a.year ? String(a.year) : "", plural(a.tracks, "track")].filter(Boolean).join(" · ");

function AlbumTile({ a, withArtist }: { a: LibAlbum; withArtist: boolean }) {
  return <Tile to={albumPath(a.artist, a.title)} cover={a.cover_url} title={a.title} sub={albumSub(a, withArtist)} />;
}

export function LibraryArtists() {
  const [text, setText] = useState("");
  const q = useDebounced(text);
  const res = useQuery({ queryKey: ["lib-artists", q], queryFn: () => get<{ artists: LibArtist[]; total: number }>(`/api/library/artists${q ? `?q=${enc(q)}` : ""}`) });
  return (
    <>
      <Head title="Artists you own">Everyone with a track in your library, with the albums you have of theirs.</Head>
      <Filter label="Filter artists" value={text} onChange={setText} />
      {res.isLoading && <Skeleton rows={6} />}
      {res.isError && <ErrorState error={res.error} retry={() => res.refetch()} />}
      {res.isSuccess && res.data.total === 0 && <Empty title={q ? "No artist matches" : "The library index is empty"}>{q ? "Try fewer letters." : "Check that the music folder is mounted, then refresh the library."}</Empty>}
      {res.isSuccess && res.data.total > 0 && (
        <>
          <p className="muted mono" style={{ margin: "8px 0" }}>{plural(res.data.total, "artist")}{q ? ` matching “${q}”` : ""}</p>
          <div className="grid">{res.data.artists.map((a) => (
            <Tile key={a.key} to={artistPath(a.name)} cover={a.image_url} title={a.name} sub={`${plural(a.albums, "album")} · ${plural(a.tracks, "track")}`} round />))}</div>
        </>
      )}
    </>
  );
}

export function LibraryArtist() {
  const { name = "" } = useParams();
  const res = useQuery({ queryKey: ["lib-artist", name], queryFn: () => get<LibArtistPage>(`/api/library/artist?name=${enc(name)}`) });
  if (res.isLoading) return <><BackLink /><Skeleton /></>;
  if (res.isError) return <><BackLink /><ErrorState error={res.error} retry={() => res.refetch()} /></>;
  const a = res.data!;
  return (
    <>
      <BackLink />
      <header className="row" style={{ flexWrap: "nowrap", gap: 16, margin: "6px 0 8px", alignItems: "flex-end" }}>
        <Cover src={a.image_url} name={a.name} size="lg" round />
        <div style={{ minWidth: 0 }}>
          <div className="eyebrow">In your library</div>
          <h1>{a.name}</h1>
          <div className="muted mono">{plural(a.tracks, "track")}</div>
        </div>
      </header>
      {a.dz_artist_id && <div style={{ margin: "6px 0 14px" }}><Link className="btn sm" to={`/artist/${a.dz_artist_id}`}>See everything on Deezer</Link></div>}
      {a.albums.length > 0 && (
        <section>
          <div className="sectionhead"><h2>Albums</h2></div>
          <div className="grid">{a.albums.map((x) => <AlbumTile key={x.title} a={x} withArtist={false} />)}</div>
        </section>
      )}
      {a.appears_on.length > 0 && (
        <section>
          <div className="sectionhead"><h2>Appears on</h2></div>
          <div className="grid">{a.appears_on.map((x) => <AlbumTile key={`${x.artist}/${x.title}`} a={x} withArtist />)}</div>
        </section>
      )}
    </>
  );
}

export function LibraryAlbums() {
  const [text, setText] = useState("");
  const [shown, setShown] = useState(STEP);
  const q = useDebounced(text);
  useEffect(() => setShown(STEP), [q]);
  const res = useQuery({ queryKey: ["lib-albums", q], queryFn: () => get<{ albums: LibAlbum[]; total: number }>(`/api/library/albums${q ? `?q=${enc(q)}` : ""}`) });
  return (
    <>
      <Head title="Albums you own">Every album with at least one track in your library, by title.</Head>
      <Filter label="Filter albums" value={text} onChange={setText} />
      {res.isLoading && <Skeleton rows={6} />}
      {res.isError && <ErrorState error={res.error} retry={() => res.refetch()} />}
      {res.isSuccess && res.data.total === 0 && <Empty title={q ? "No album matches" : "The library index is empty"}>{q ? "Try fewer letters." : "Check that the music folder is mounted, then refresh the library."}</Empty>}
      {res.isSuccess && res.data.total > 0 && (
        <>
          <p className="muted mono" style={{ margin: "8px 0" }}>{Math.min(shown, res.data.total)} of {res.data.total}{q ? ` matching “${q}”` : ""}</p>
          <div className="grid">{res.data.albums.slice(0, shown).map((a) => <AlbumTile key={`${a.artist_key}/${a.album_key}`} a={a} withArtist />)}</div>
          {shown < res.data.total && (
            <div className="row" style={{ justifyContent: "center", margin: "14px 0" }}>
              <button className="btn" onClick={() => setShown(shown + STEP)}>Show {Math.min(STEP, res.data.total - shown)} more</button>
            </div>
          )}
        </>
      )}
    </>
  );
}

export function LibraryAlbum() {
  const { artist = "", album = "" } = useParams();
  const res = useQuery({ queryKey: ["lib-album", artist, album], queryFn: () => get<LibAlbumPage>(`/api/library/album?artist=${enc(artist)}&album=${enc(album)}`) });
  if (res.isLoading) return <><BackLink /><Skeleton /></>;
  if (res.isError) return <><BackLink /><ErrorState error={res.error} retry={() => res.refetch()} /></>;
  const a = res.data!;
  const total = a.tracks_list.reduce((n, t) => n + (t.duration_ms ?? 0), 0);
  return (
    <>
      <BackLink />
      <header className="row" style={{ flexWrap: "nowrap", gap: 16, margin: "6px 0 8px", alignItems: "flex-end" }}>
        <Cover src={a.cover_url} name={a.title} size="lg" />
        <div style={{ minWidth: 0 }}>
          <div className="eyebrow">Album in your library</div>
          <h1>{a.title === NO_ALBUM ? "Tracks without an album" : a.title}</h1>
          <div><Link to={artistPath(a.artist)}>{a.artist}</Link></div>
          <div className="muted mono">{[a.year, plural(a.tracks, "track"), total ? fmtDuration(total) : "", a.discs > 1 ? plural(a.discs, "disc") : ""].filter(Boolean).join(" · ")}</div>
        </div>
      </header>
      {a.dz_album_id && <div style={{ margin: "6px 0 14px" }}><Link className="btn sm" to={`/album/${a.dz_album_id}`}>See on Deezer and queue what's missing</Link></div>}
      <ul className="list">
        {a.tracks_list.map((t) => (
          <li key={t.path} className="item" style={{ gridTemplateColumns: "auto 1fr auto" }}>
            <span className="ord">{t.track_no ?? ""}</span>
            <div style={{ minWidth: 0 }}>
              <div className="t wrap">{t.title}</div>
              {t.artist && t.artist !== a.artist && <div className="s wrap">{t.artist}</div>}
            </div>
            <span className="muted mono">{fmtDuration(t.duration_ms)}</span>
          </li>
        ))}
      </ul>
    </>
  );
}
