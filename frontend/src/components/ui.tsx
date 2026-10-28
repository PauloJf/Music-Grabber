import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { errorText, get, post } from "../lib/api";
import { fmtDuration, useRefreshJobs } from "../lib/hooks";
import { usePlayer } from "../lib/player";
import { useQueueing } from "../lib/queueing";
import { useToast } from "../lib/toast";
import type { CatAlbum, CatTrack, TrackState } from "../lib/types";
import { Check, Explicit, Pause, Play, Plus, Retry, X } from "./icons";

export function Cover({ src, name, size = "", round = false }: { src?: string; name: string; size?: "lg" | "xl" | ""; round?: boolean }) {
  const [bad, setBad] = useState(false);
  const cls = `cover ${size} ${round ? "round" : ""}`;
  if (!src || bad) return <div className={`${cls} ph`} aria-hidden>{(name || "?").trim().charAt(0).toUpperCase()}</div>;
  return <img className={cls} src={src} alt="" loading="lazy" onError={() => setBad(true)} />;
}

export function Banner({ kind, children, action }: { kind: "err" | "warn" | "ok"; children: ReactNode; action?: ReactNode }) {
  return <div className={`banner ${kind}`} role={kind === "err" ? "alert" : "status"}><div className="row">{<span className="grow">{children}</span>}{action}</div></div>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><h3>{title}</h3>{children && <div>{children}</div>}</div>;
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  return <Banner kind="err" action={retry && <button className="btn sm" onClick={retry}>Try again</button>}>{errorText(error)}</Banner>;
}

export function Skeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="item" style={{ gridTemplateColumns: "52px 1fr 70px" }}>
          <div className="skel" style={{ width: 52, height: 52 }} /><div><div className="skel" style={{ height: 14, width: `${50 + (i * 13) % 35}%`, marginBottom: 7 }} /><div className="skel" style={{ height: 11, width: "35%" }} /></div><div className="skel" style={{ height: 32, width: 70 }} />
        </div>
      ))}
    </div>
  );
}

export function Pill({ kind, children }: { kind: "ok" | "warn" | "err" | "signal" | "mute"; children: ReactNode }) {
  return <span className={`pill ${kind}`}>{children}</span>;
}

const STATUS_WORD: Record<string, string> = { searching: "Searching", downloading: "Downloading", transcoding: "Converting", tagging: "Tagging", placing: "Filing" };

/** One control that tells the whole story of a track: queue it, or see where it is. */
export function StateControl({ track, busy }: { track: CatTrack; busy: boolean }) {
  const { queueTracks } = useQueueing();
  const refresh = useRefreshJobs();
  const toast = useToast();
  const s: TrackState = track.state;
  if (busy) return <div className="state"><div className="ring spin" role="status" aria-label="Queuing" /></div>;
  if (s.kind === "library") return <div className="state"><Pill kind="ok"><Check width={13} height={13} />In library</Pill></div>;
  if (s.kind === "queued") return <div className="state"><Pill kind="signal">Queued</Pill></div>;
  if (s.kind === "active") {
    const pct = Math.round((s.progress || 0) * 100);
    return (
      <div className="state row tight" title={STATUS_WORD[s.status] ?? s.status}>
        <span className="muted" style={{ fontSize: 12 }}>{STATUS_WORD[s.status] ?? s.status}</span>
        <div className="ring" style={{ ["--p" as string]: pct }} role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><span>{pct}</span></div>
      </div>
    );
  }
  if (s.kind === "review") return <div className="state"><Link className="pill warn" to={`/inbox/${s.item_id}`}>Needs you</Link></div>;
  if (s.kind === "failed") {
    return (
      <div className="state row tight">
        <span title={s.error}><Pill kind="err">Failed</Pill></span>
        <button className="btn sm icon" aria-label={`Retry ${track.title}`} onClick={() => post(`/api/queue/${s.item_id}/retry`).then(() => { toast.push({ kind: "info", text: "Trying again" }); refresh(); }).catch((e) => toast.push({ kind: "err", text: errorText(e) }))}><Retry width={16} height={16} /></button>
      </div>
    );
  }
  return (
    <div className="state">
      <button className="btn signal sm" aria-label={`Queue ${track.title}`} onClick={() => queueTracks([track], track.title)}><Plus width={16} height={16} />Queue</button>
    </div>
  );
}

export function PreviewButton({ track }: { track: CatTrack }) {
  const { playing, progress, toggle } = usePlayer();
  const on = playing === track.dz_id;
  return (
    <div className="preview">
      <Cover src={track.cover_url} name={track.album || track.title} />
      {track.preview_url && (
        <button className={`pbtn ${on ? "on" : ""}`} aria-label={`${on ? "Pause" : "Preview"} ${track.title}`} aria-pressed={on} onClick={() => toggle(track.dz_id, track.preview_url)}>
          {on ? <Pause /> : <Play />}
        </button>
      )}
      {on && <div className="pbar" style={{ width: `${progress * 100}%` }} />}
    </div>
  );
}

export function TrackRow({ track, ord, selected, onSelect, hideAlbum = false, hideArtist = false }: {
  track: CatTrack; ord?: number; selected?: boolean; onSelect?: (on: boolean) => void; hideAlbum?: boolean; hideArtist?: boolean;
}) {
  const { pending } = useQueueing();
  const own = track.state.kind === "library";
  return (
    <li className={`item ${own ? "dim" : ""}`} style={onSelect ? { gridTemplateColumns: "auto auto 1fr auto" } : undefined}>
      {onSelect && <input type="checkbox" aria-label={`Select ${track.title}`} checked={!!selected} disabled={own} onChange={(e) => onSelect(e.target.checked)} />}
      {ord != null ? <div className="row tight"><span className="ord">{ord}</span></div> : null}
      <div className="row" style={{ flexWrap: "nowrap", minWidth: 0, gap: 12 }}>
        {ord == null && <PreviewButton track={track} />}
        <div style={{ minWidth: 0 }}>
          <div className="t clamp">{track.title}{track.explicit && <> <Explicit width={13} height={13} style={{ verticalAlign: "-2px", color: "var(--ink-3)" }} /></>}</div>
          <div className="s clamp">
            {[
              !hideArtist && track.artist ? (track.artist_id ? <Link key="ar" to={`/artist/${track.artist_id}`}>{track.artist}</Link> : track.artist) : null,
              !hideAlbum && track.album ? (track.album_id ? <Link key="al" to={`/album/${track.album_id}`}>{track.album}</Link> : track.album) : null,
              track.duration_ms ? <span key="d" className="mono">{fmtDuration(track.duration_ms)}</span> : null,
            ].filter(Boolean).flatMap((x, i) => (i ? [" · ", x] : [x]))}
          </div>
        </div>
      </div>
      <StateControl track={track} busy={pending.has(track.dz_id)} />
    </li>
  );
}

export function Tile({ to, cover, title, sub, round }: { to: string; cover?: string; title: string; sub?: string; round?: boolean }) {
  return (
    <Link className="tile" to={to}>
      <Cover src={cover} name={title} size="xl" round={round} />
      <div><div className="tn">{title}</div>{sub && <div className="ts">{sub}</div>}</div>
    </Link>
  );
}

/** A 30-second preview for a tile: an artist plays its most popular track, an album its first. The track list is
 * fetched when you press play (and cached under the same keys the artist and album pages use). */
export function TilePreview({ kind, id }: { kind: "artist" | "album"; id: string }) {
  const { playing, toggle } = usePlayer();
  const qc = useQueryClient();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const key = `tile:${kind}:${id}`;
  const on = playing === key;
  const go = async () => {
    if (on) { toggle(key, ""); return; }
    setBusy(true);
    try {
      const opts = { queryKey: ["browse", kind, id], staleTime: 5 * 60_000 };
      const tracks: CatTrack[] = kind === "artist"
        ? (await qc.fetchQuery({ ...opts, queryFn: () => get<{ top: CatTrack[] }>(`/api/browse/artist/${id}`) })).top
        : (await qc.fetchQuery({ ...opts, queryFn: () => get<{ album: CatAlbum; tracks: CatTrack[] }>(`/api/browse/album/${id}`) })).tracks;
      const t = tracks.find((x) => x.preview_url);
      if (t) toggle(key, t.preview_url); else toast.push({ kind: "info", text: "No preview available for this one" });
    } catch (e) {
      toast.push({ kind: "err", text: errorText(e) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <button className="btn quiet sm" style={{ paddingLeft: 0 }} aria-pressed={on} aria-label={`${on ? "Pause" : "Preview"} ${kind}`} disabled={busy} onClick={go}>
      {on ? <Pause width={16} height={16} /> : <Play width={16} height={16} />}{on ? "Pause" : "Preview"}
    </button>
  );
}

/** A tile with a row of actions under it. The tile stretches, so the actions sit on one line across a grid row
 * whatever the text above them; they start at the tile's left edge (a quiet button's own padding is removed). */
export function TileCard({ children, ...tile }: Parameters<typeof Tile>[0] & { children: ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
      <div style={{ flex: 1 }}><Tile {...tile} /></div>
      <div className="row tight" style={{ marginTop: 6, gap: 4 }}>{children}</div>
    </div>
  );
}

export function CloseButton({ onClick, label = "Close" }: { onClick: () => void; label?: string }) {
  return <button className="btn quiet icon sm" aria-label={label} onClick={onClick}><X width={18} height={18} /></button>;
}
