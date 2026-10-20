import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { AuthProvider } from "./lib/auth";
import { PlayerProvider } from "./lib/player";
import { QueueingProvider } from "./lib/queueing";
import { ToastProvider } from "./lib/toast";
import { tokenStore } from "./lib/api";

export interface Call { method: string; path: string; body: unknown; auth: string | null }

type Reply = unknown | { status: number; body: unknown } | ((call: Call) => unknown);
export type Routes = Record<string, Reply>;

/** Stub `fetch` with route handlers keyed "METHOD /path" (query string included when given). */
export function mockApi(routes: Routes): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init: RequestInit = {}) => {
    const method = (init.method ?? "GET").toUpperCase();
    const headers = (init.headers ?? {}) as Record<string, string>;
    const call: Call = { method, path, body: init.body ? JSON.parse(String(init.body)) : undefined, auth: headers.Authorization ?? null };
    calls.push(call);
    let reply: Reply = routes[`${method} ${path}`] ?? routes[`${method} ${path.split("?")[0]}`];
    if (reply === undefined) return new Response(JSON.stringify({ error: "not_found", message: `no mock for ${method} ${path}` }), { status: 404 });
    if (typeof reply === "function") reply = await (reply as (c: Call) => unknown)(call);
    const r = reply as { status?: number; body?: unknown };
    const status = r && typeof r === "object" && "status" in r ? (r.status as number) : 200;
    const body = r && typeof r === "object" && "status" in r ? r.body : reply;
    return new Response(JSON.stringify(body ?? {}), { status, headers: { "Content-Type": "application/json" } });
  }));
  return calls;
}

export function renderApp(ui: ReactElement, { route = "/", token = "t".repeat(20) }: { route?: string; token?: string } = {}) {
  if (token) tokenStore.set(token);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <MemoryRouter initialEntries={[route]}>
          <ToastProvider><PlayerProvider><QueueingProvider>{ui}</QueueingProvider></PlayerProvider></ToastProvider>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

export const STATUS = {
  version: "0.4.0", uptime_seconds: 5, spotify: { configured: true, connected: true, last_error: "", redirect_uri: "" },
  library: { index: "filesystem", tracks: 10 }, analysis: "none", hooks: [], providers: ["ytdlp"], queue: {},
  grabbed_total: 0, managed_files: 0, last_synced_epoch: null,
  playlists: { total: 0, watched: 0, have: 0, missing: 0, queued: 0 }, dry_run: false,
};

export const ACTIVITY = { active: [], inbox: 0, recent: [], counts: {} };
export const SETTINGS = {
  settings: { output_format: "mp3-128", deezer_quality: "MP3_128", provider_order: "deezer,ytdlp", deezer_arl: "", auto_accept_threshold: 0.85,
    ask_threshold: 0.55, grab_dry_run: false, lyrics_enabled: false, ntfy_url: "", ntfy_topic: "", ntfy_notify_done: true,
    path_template: "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}", spotify_sync_minutes: 30, grab_workers: 1, ui_public_url: "" },
  editable: { output_format: { kind: "enum", options: ["mp3-320", "mp3-128", "flac"] }, deezer_quality: { kind: "enum", options: ["MP3_128", "MP3_320", "FLAC"] } },
};
export const track = (id: number, title: string, state: object = { kind: "none" }) => ({
  dz_id: String(id), title, artist: "Dua Lipa", artist_id: "7", album: "Future Nostalgia", album_id: "55", duration_ms: 200000,
  preview_url: "https://cdnt-preview.dzcdn.net/x.mp3", cover_url: "", explicit: false, track_no: id, disc_no: 1, year: 2020, state,
});
