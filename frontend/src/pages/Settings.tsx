import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { Banner, ErrorState, Pill, Skeleton } from "../components/ui";
import { errorText, get, post } from "../lib/api";
import { ago, useStatus } from "../lib/hooks";
import { useToast } from "../lib/toast";

interface Settings { settings: Record<string, string | number | boolean | null>; editable: Record<string, { kind: string; options: string[] | null }> }
const MASK = "********";
const PROVIDERS = [["deezer,ytdlp", "Deezer, then YouTube"], ["ytdlp", "YouTube only"], ["deezer", "Deezer only"]];

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return <section style={{ margin: "30px 0" }}><h2>{title}</h2>{hint && <p className="muted" style={{ margin: "2px 0 12px" }}>{hint}</p>}<div className="stack" style={{ marginTop: 12 }}>{children}</div></section>;
}
const Field = ({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) => (
  <label className="field"><span>{label}</span>{children}{hint && <small className="muted">{hint}</small>}</label>
);

export default function SettingsPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const status = useStatus();
  const q = useQuery({ queryKey: ["settings"], queryFn: () => get<Settings>("/api/settings") });
  const [draft, setDraft] = useState<Record<string, string | number | boolean | null>>({});
  useEffect(() => { if (q.data) setDraft(q.data.settings); }, [q.data]);

  const dirty = q.data ? Object.keys(draft).filter((k) => String(draft[k] ?? "") !== String(q.data!.settings[k] ?? "")) : [];
  const set = (k: string, v: string | number | boolean) => setDraft((d) => ({ ...d, [k]: v }));
  const save = useMutation({
    mutationFn: () => post<{ applied: string[]; restart_required: string[] }>("/api/settings", Object.fromEntries(dirty.map((k) => [k, draft[k]]))),
    onSuccess: (r) => { toast.push({ kind: "ok", text: r.restart_required.length ? `Saved. Restart to apply: ${r.restart_required.join(", ")}` : "Saved and applied" }); qc.invalidateQueries({ queryKey: ["settings"] }); qc.invalidateQueries({ queryKey: ["status"] }); },
    onError: (e) => toast.push({ kind: "err", text: errorText(e) }),
  });
  const test = useMutation({
    mutationFn: (which: "deezer" | "ntfy") => post<{ ok: boolean; message?: string; error?: string }>(`/api/settings/test-${which}`, which === "deezer" && draft.deezer_arl && draft.deezer_arl !== MASK ? { deezer_arl: draft.deezer_arl } : {}),
    onSuccess: (r) => toast.push({ kind: r.ok ? "ok" : "err", text: r.message ?? r.error ?? (r.ok ? "Works" : "Failed") }),
    onError: (e) => toast.push({ kind: "err", text: errorText(e) }),
  });
  const connect = useMutation({ mutationFn: () => get<{ url: string }>("/api/spotify/authorize-url"), onSuccess: ({ url }) => window.open(url, "_blank", "noopener"), onError: (e) => toast.push({ kind: "err", text: errorText(e) }) });
  const disconnect = useMutation({ mutationFn: () => post("/api/spotify/disconnect"), onSuccess: () => { toast.push({ kind: "info", text: "Spotify disconnected" }); qc.invalidateQueries({ queryKey: ["status"] }); } });

  if (q.isLoading) return <Skeleton rows={4} />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const opts = (k: string) => q.data!.editable[k]?.options ?? [];
  const sp = status.data?.spotify;
  const str = (k: string) => String(draft[k] ?? "");
  const example = str("path_template").replace("{AlbumArtist}", "Dua Lipa").replace("{Album}", "Future Nostalgia").replace("{TrackNo:02d}", "05").replace("{Title}", "Levitating").replace("{ext}", str("output_format").split("-")[0]);

  return (
    <>
      <section className="hero" style={{ paddingBottom: 0 }}><div className="eyebrow">Settings</div><h1>Set it up once</h1>{!dirty.length && <div style={{ marginTop: 8 }}><Pill kind="ok">All saved</Pill></div>}</section>

      <Section title="Connections">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div><b>Spotify</b> <span className="muted">(for watching playlists)</span><div className="muted" style={{ fontSize: 13 }}>{!sp ? "…" : !sp.configured ? "Client not configured: set SPOTIFY_CLIENT_ID, _SECRET and _REDIRECT_URI" : sp.connected ? "Connected" : "Not connected"}{sp?.last_error ? ` · ${sp.last_error}` : ""}</div></div>
          {sp?.configured && (sp.connected ? <button className="btn" onClick={() => disconnect.mutate()}>Disconnect</button> : <button className="btn signal" disabled={connect.isPending} onClick={() => connect.mutate()}>Connect Spotify</button>)}
        </div>
        <Field label="Deezer ARL" hint="Your own session cookie. Stored on this server, never shown again. A free account downloads at 128 kbps.">
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <input type="password" autoComplete="off" value={str("deezer_arl")} placeholder="Paste a new ARL" onFocus={(e) => { if (e.target.value === MASK) set("deezer_arl", ""); }} onChange={(e) => set("deezer_arl", e.target.value)} />
            <button className="btn" disabled={test.isPending} onClick={() => test.mutate("deezer")}>Test</button>
          </div>
        </Field>
        <Field label="Notifications (ntfy)" hint="Get a ping when a track needs you or a download fails.">
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <input value={str("ntfy_url")} placeholder="https://ntfy.sh" onChange={(e) => set("ntfy_url", e.target.value)} />
            <input type="password" autoComplete="off" value={str("ntfy_topic")} placeholder="topic" onFocus={(e) => { if (e.target.value === MASK) set("ntfy_topic", ""); }} onChange={(e) => set("ntfy_topic", e.target.value)} />
            <button className="btn" disabled={test.isPending} onClick={() => test.mutate("ntfy")}>Send test</button>
          </div>
        </Field>
        <label className="row"><input type="checkbox" checked={!!draft.ntfy_notify_done} onChange={(e) => set("ntfy_notify_done", e.target.checked)} /><span>Ping for every finished track <span className="muted">(off = only problems)</span></span></label>
      </Section>

      <Section title="Downloads">
        <Field label="Sources, in order"><select value={str("provider_order")} onChange={(e) => set("provider_order", e.target.value)}>
          {PROVIDERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}{!PROVIDERS.some(([v]) => v === str("provider_order")) && <option value={str("provider_order")}>{str("provider_order")}</option>}</select></Field>
        <div className="row" style={{ alignItems: "flex-start" }}>
          <div className="grow"><Field label="Output format"><select value={str("output_format")} onChange={(e) => set("output_format", e.target.value)}>{opts("output_format").map((o) => <option key={o}>{o}</option>)}</select></Field></div>
          <div className="grow"><Field label="Deezer quality" hint="320 and FLAC need a paid account"><select value={str("deezer_quality")} onChange={(e) => set("deezer_quality", e.target.value)}>{opts("deezer_quality").map((o) => <option key={o}>{o}</option>)}</select></Field></div>
        </div>
        <div className="row" style={{ alignItems: "flex-start" }}>
          <div className="grow"><Field label="Auto-download above" hint="A match at or above this score downloads without asking"><input type="number" step="0.01" min="0" max="1" value={str("auto_accept_threshold")} onChange={(e) => set("auto_accept_threshold", e.target.value)} /></Field></div>
          <div className="grow"><Field label="Ask below" hint="Below this there's nothing worth asking about"><input type="number" step="0.01" min="0" max="1" value={str("ask_threshold")} onChange={(e) => set("ask_threshold", e.target.value)} /></Field></div>
        </div>
        <label className="row"><input type="checkbox" checked={!!draft.grab_dry_run} onChange={(e) => set("grab_dry_run", e.target.checked)} /><span>Dry run <span className="muted">(search and score, never download)</span></span></label>
        <label className="row"><input type="checkbox" checked={!!draft.lyrics_enabled} onChange={(e) => set("lyrics_enabled", e.target.checked)} /><span>Embed lyrics when available</span></label>
      </Section>

      <Section title="Filing" hint="Where finished files go, relative to your music folder.">
        <Field label="Path template"><input value={str("path_template")} onChange={(e) => set("path_template", e.target.value)} /></Field>
        <div className="mono ink2" style={{ fontSize: 13 }}>→ {example}</div>
        {status.data && (
          <dl className="kv" style={{ margin: 0 }}>
            <dt>Library index</dt><dd>{status.data.library.index}, {status.data.library.tracks} tracks</dd>
            <dt>BPM analysis</dt><dd>{status.data.analysis === "none" ? "off" : status.data.analysis}</dd>
            <dt>After filing</dt><dd>{status.data.hooks.join(", ") || "nothing"}</dd>
            <dt>Version</dt><dd className="mono">{status.data.version} <span className="muted">· up {ago(new Date(Date.now() - status.data.uptime_seconds * 1000).toISOString()).replace(" ago", "")}</span></dd>
          </dl>
        )}
        <Banner kind="warn">These are set by environment variables and need a restart to change: music folder, library index, BPM analysis, hooks.</Banner>
      </Section>

      <Section title="New releases and suggestions" hint="Reads Deezer's public catalogue in the background and your own library. Each part can be switched off; what is already learned is kept.">
        <label className="row"><input type="checkbox" checked={!!draft.releases_enabled} onChange={(e) => set("releases_enabled", e.target.checked)} /><span>New releases and suggestions <span className="muted">(the master switch)</span></span></label>
        <label className="row"><input type="checkbox" disabled={!draft.releases_enabled} checked={!!draft.suggest_gaps} onChange={(e) => set("suggest_gaps", e.target.checked)} /><span>Albums you're missing from artists you own</span></label>
        <label className="row"><input type="checkbox" disabled={!draft.releases_enabled} checked={!!draft.suggest_artists} onChange={(e) => set("suggest_artists", e.target.checked)} /><span>Artists you may like</span></label>
        <label className="row"><input type="checkbox" disabled={!draft.releases_enabled} checked={!!draft.suggest_stars} onChange={(e) => set("suggest_stars", e.target.checked)} /><span>Use the songs you starred in BPM Tagger or Navidrome <span className="muted">(only with the OpenSubsonic library index)</span></span></label>
      </Section>

      <div style={{ height: 70 }} />
      {dirty.length > 0 && (
        <div className="selbar" role="region" aria-label="Save settings">
          <span className="grow">{dirty.length} unsaved change{dirty.length > 1 ? "s" : ""}</span>
          <button className="btn quiet sm" style={{ color: "inherit" }} onClick={() => q.data && setDraft(q.data.settings)}>Discard</button>
          <button className="btn signal sm" disabled={save.isPending} onClick={() => save.mutate()}>{save.isPending ? "Saving…" : "Save changes"}</button>
        </div>
      )}
    </>
  );
}
