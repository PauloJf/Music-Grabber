import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Inbox from "./Inbox";
import Owned from "./Owned";
import Playlists from "./Playlists";
import Queue from "./Queue";
import Settings from "./Settings";
import { ACTIVITY, SETTINGS, STATUS, mockApi, renderApp } from "../test-utils";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const base = { "GET /api/activity": ACTIVITY, "GET /api/status": STATUS };

const qi = (id: number, status: string, extra: object = {}) => ({ id, status, title: `Song ${id}`, artist: "Band", album: "LP", duration_ms: 1000, isrc: null,
  provider: "deezer", progress: 0.4, priority: 0, note: null, error: null, final_path: null, updated_at: new Date().toISOString(), ...extra });

describe("inbox", () => {
  const item = { ...qi(7, "awaiting_user", { title: "Houdini", note: "best 0.72 below auto-accept" }), candidates: [
    { id: 40, queue_item_id: 7, provider: "deezer", title: "Houdini", artist: "Band", album: "LP", duration_ms: 185000, quality: "MP3_128", score: 0.72, rank: 0 },
    { id: 41, queue_item_id: 7, provider: "ytdlp", title: "Houdini (Live)", artist: "Band", album: "", duration_ms: 240000, quality: "", score: 0.4, rank: 1 }] };

  it("lists candidates with scores and chooses one, confirming with a toast", async () => {
    const calls = mockApi({ ...base, "GET /api/inbox": { items: [item] }, "POST /api/inbox/7/choose": { ok: true } });
    renderApp(<Inbox />);
    await screen.findByRole("heading", { name: "Band – Houdini" });
    expect(screen.getByText("best 0.72 below auto-accept")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Download this" })[1]);
    await screen.findByText(/downloading your choice/i);
    expect(calls.find((c) => c.path === "/api/inbox/7/choose")?.body).toEqual({ candidate_id: 41 });
  });

  it("previews a Deezer candidate and the original before choosing", async () => {
    const play = vi.fn(() => Promise.resolve());
    vi.stubGlobal("Audio", class { src = ""; play = play; pause = vi.fn(); addEventListener = vi.fn(); });
    const calls = mockApi({ ...base, "GET /api/inbox": { items: [{ ...item, isrc: "ISRC1" }] },
      "GET /api/inbox/7/preview": { url: "https://cdn.dzcdn.net/p.mp3", open_url: "", reason: "" } });
    renderApp(<Inbox />);
    await screen.findByRole("heading", { name: "Band – Houdini" });
    expect(calls.some((c) => c.path.includes("/preview"))).toBe(false);                    // nothing fetched until you press play
    fireEvent.click(screen.getByRole("button", { name: "Preview Band – Houdini" }));
    await waitFor(() => expect(play).toHaveBeenCalled());
    expect(calls.find((c) => c.path.includes("/preview"))?.path).toBe("/api/inbox/7/preview?candidate=40");
    await screen.findByRole("button", { name: "Pause Band – Houdini" });
    fireEvent.click(screen.getByRole("button", { name: /preview the original houdini/i }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/inbox/7/preview")).toBe(true));
  });

  const yt = { ...item, candidates: [item.candidates[0], { ...item.candidates[1], url: "https://music.youtube.com/watch?v=x" }] };

  it("plays the 30-second clip of a YouTube candidate", async () => {
    const play = vi.fn(() => Promise.resolve());
    let src = "";
    vi.stubGlobal("Audio", class { play = play; pause = vi.fn(); addEventListener = vi.fn(); set src(v: string) { src = v; } });
    mockApi({ ...base, "GET /api/inbox": { items: [yt] },
      "GET /api/inbox/7/preview": { url: "/api/clips/0123456789abcdef.mp3?exp=1&sig=s", clip: "0123456789abcdef.mp3", open_url: "https://music.youtube.com/watch?v=x", reason: "" } });
    renderApp(<Inbox />);
    await screen.findByRole("heading", { name: "Band – Houdini" });
    expect(screen.queryByRole("link", { name: /listen/i })).toBeNull();               // a ▶ first, not a link
    fireEvent.click(screen.getByRole("button", { name: "Preview Band – Houdini (Live)" }));
    await waitFor(() => expect(play).toHaveBeenCalled());
    expect(src).toBe("/api/clips/0123456789abcdef.mp3?exp=1&sig=s");
  });

  it("falls back to opening the YouTube video in a new tab when the clip cannot be made", async () => {
    mockApi({ ...base, "GET /api/inbox": { items: [yt] },
      "GET /api/inbox/7/preview": { url: "", clip: "", open_url: "https://music.youtube.com/watch?v=x", reason: "couldn't make a preview (could not fetch the audio from YouTube); open it to listen" } });
    renderApp(<Inbox />);
    await screen.findByRole("heading", { name: "Band – Houdini" });
    fireEvent.click(screen.getByRole("button", { name: "Preview Band – Houdini (Live)" }));
    const link = (await screen.findByRole("link", { name: /listen/i })) as HTMLAnchorElement;
    expect(link.href).toBe("https://music.youtube.com/watch?v=x");
    expect(link.target).toBe("_blank");
    expect(link.rel).toContain("noopener");
    await screen.findByText(/couldn't make a preview/i);
  });

  it("also falls back to the link when the preview request itself fails", async () => {
    mockApi({ ...base, "GET /api/inbox": { items: [yt] }, "GET /api/inbox/7/preview": { status: 500, body: { error: "boom", message: "boom" } } });
    renderApp(<Inbox />);
    await screen.findByRole("heading", { name: "Band – Houdini" });
    fireEvent.click(screen.getByRole("button", { name: "Preview Band – Houdini (Live)" }));
    await screen.findByRole("link", { name: /listen/i });
  });

  it("says so when there is no preview", async () => {
    mockApi({ ...base, "GET /api/inbox": { items: [item] }, "GET /api/inbox/7/preview": { url: "", open_url: "", reason: "Deezer has no preview for this track" } });
    renderApp(<Inbox />);
    await screen.findByRole("heading", { name: "Band – Houdini" });
    fireEvent.click(screen.getByRole("button", { name: "Preview Band – Houdini" }));
    await screen.findByText("Deezer has no preview for this track");
  });

  it("searches with a trimmed query, skips, and shows the empty state", async () => {
    const calls = mockApi({ ...base, "GET /api/inbox": { items: [item] }, "POST /api/inbox/7/search": { ok: true }, "POST /api/inbox/7/skip": { ok: true } });
    renderApp(<Inbox />);
    await screen.findByRole("heading", { name: "Band – Houdini" });
    expect((screen.getByRole("button", { name: "Search" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/search again for/i), { target: { value: "  band houdini  " } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/inbox/7/search")?.body).toEqual({ query: "band houdini" }));
    fireEvent.click(screen.getByRole("button", { name: "Skip" }));
    await screen.findByText("Skipped");
  });

  it("empty inbox, and a refused action shows the reason", async () => {
    mockApi({ ...base, "GET /api/inbox": { items: [] } });
    renderApp(<Inbox />);
    await screen.findByText(/nothing needs your attention/i);
    vi.unstubAllGlobals();
    mockApi({ ...base, "GET /api/inbox": { items: [item] }, "POST /api/inbox/7/choose": { status: 400, body: { error: "not_awaiting", message: "item is pending" } } });
    renderApp(<Inbox />);
    fireEvent.click((await screen.findAllByRole("button", { name: "Download this" }))[0]);
    await screen.findByText("item is pending");
  });
});

describe("queue", () => {
  const LIST = { items: [qi(1, "downloading"), qi(2, "failed", { error: "all downloads failed" }), qi(3, "awaiting_user"), qi(4, "pending")] };
  const HIST = { items: [qi(5, "done", { final_path: "/music/a.mp3" })] };

  it("groups work into Now, Up next, Failed and Finished, and points to the inbox", async () => {
    mockApi({ ...base, "GET /api/queue": LIST, "GET /api/queue/history": HIST });
    renderApp(<Queue />);
    await screen.findByText("Song 1");
    for (const h of ["Now", "Up next", "Failed", "Finished"]) expect(screen.getByRole("heading", { name: new RegExp(`^${h}`) })).toBeTruthy();
    expect(screen.getByText("45%".replace("45", "40"))).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open inbox" })).toBeTruthy();
    expect(screen.queryByText("Song 3")).toBeNull();                       // awaiting_user lives in the inbox
  });

  it("retry, cancel, front-of-queue and clear all give feedback", async () => {
    const calls = mockApi({ ...base, "GET /api/queue": LIST, "GET /api/queue/history": HIST, "POST /api/queue/2/retry": { ok: true },
      "POST /api/queue/1/cancel": { ok: true }, "POST /api/queue/4/priority": { ok: true }, "POST /api/queue/clear-completed": { ok: true } });
    renderApp(<Queue />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await screen.findByText("Trying again");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await screen.findByText("Cancelled");
    fireEvent.click(screen.getByRole("button", { name: /move song 4 to the front/i }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/queue/4/priority")?.body).toEqual({ priority: 1 }));
    fireEvent.click(screen.getByRole("button", { name: "Clear completed" }));
    await screen.findByText("Cleared");
  });

  it("expanding an item shows its events and error; an empty queue links to Discover", async () => {
    mockApi({ ...base, "GET /api/queue": { items: [qi(2, "failed", { error: "boom" })] }, "GET /api/queue/history": { items: [] },
      "GET /api/queue/2": { item: qi(2, "failed", { error: "boom" }), candidates: [], events: [{ id: 1, event: "failed", detail: "boom", created_at: "2026-01-01T10:00:09" }] } });
    renderApp(<Queue />);
    fireEvent.click(await screen.findByRole("button", { name: "Song 2" }));
    await screen.findByText(/failed · boom/);
    vi.unstubAllGlobals();
    mockApi({ ...base, "GET /api/queue": { items: [] }, "GET /api/queue/history": { items: [] } });
    renderApp(<Queue />);
    await screen.findByText(/the queue is empty/i);
  });
});

describe("library", () => {
  const pl = (id: number) => ({ id, spotify_id: `PL${id}`, name: "Road Trip", enabled: 1, image_url: null, track_count: 10, last_synced_at: null,
    have_count: 6, missing_count: 3, queued_count: 1, new_count: 2, removed_count: 0 });
  it("shows coverage, queues the missing ones, and confirms before removing", async () => {
    const calls = mockApi({ ...base, "GET /api/playlists": { playlists: [pl(1)] }, "POST /api/playlists/1/enqueue-missing": { ok: true, enqueued: 3 }, "DELETE /api/playlists/1": { ok: true } });
    renderApp(<Playlists />);
    await screen.findByText("Road Trip");
    expect(screen.getByText("6 have")).toBeTruthy(); expect(screen.getByText("3 missing")).toBeTruthy(); expect(screen.getByText("2 new")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Queue missing" }));
    await screen.findByText("Queued 3 missing tracks");
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it("shows the Navidrome mirror only when it is configured, and controls it", async () => {
    const mirror = { enabled: true, state: "ok", name: "Spotify: Road Trip", matched: 5, unresolved: 2, synced_at: null, note: "", error: "" };
    const calls = mockApi({ ...base, "GET /api/playlists": { playlists: [{ ...pl(1), mirror }] }, "POST /api/playlists/1/mirror": { ok: true } });
    renderApp(<Playlists />);
    await screen.findByText("5 tracks, 2 not in Navidrome yet");
    fireEvent.click(screen.getByRole("button", { name: "Mirror now" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/playlists/1/mirror")?.body).toEqual({ action: "now" }));
    fireEvent.click(screen.getByLabelText("Mirror Road Trip to Navidrome"));
    await waitFor(() => expect(calls.filter((c) => c.path === "/api/playlists/1/mirror").pop()?.body).toEqual({ action: "off" }));
  });

  it("has no mirror controls when the mirror is off", async () => {
    mockApi({ ...base, "GET /api/playlists": { playlists: [{ ...pl(1), mirror: null }] } });
    renderApp(<Playlists />);
    await screen.findByText("Road Trip");
    expect(screen.queryByText(/navidrome playlist/i)).toBeNull();
  });

  it("adds a playlist and says when Spotify isn't connected", async () => {
    const calls = mockApi({ ...base, "GET /api/playlists": { playlists: [] }, "POST /api/playlists": { ok: true, connected: false } });
    renderApp(<Playlists />);
    fireEvent.change(await screen.findByLabelText(/spotify playlist url/i), { target: { value: " https://open.spotify.com/playlist/A " } });
    fireEvent.click(screen.getByRole("button", { name: /watch playlist/i }));
    await screen.findByText(/connect spotify to sync it/i);
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/playlists")?.body).toEqual({ value: "https://open.spotify.com/playlist/A" });
  });
});

describe("settings", () => {
  it("saves only what changed, applies it, and offers a live example of the path template", async () => {
    const calls = mockApi({ ...base, "GET /api/settings": SETTINGS, "POST /api/settings": { ok: true, applied: ["output_format"], restart_required: [] } });
    renderApp(<Settings />);
    await screen.findByText(/dua lipa\/future nostalgia\/05 - levitating\.mp3/i);
    expect(screen.getByText("All saved")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/output format/i), { target: { value: "flac" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByText("Saved and applied");
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/settings")?.body).toEqual({ output_format: "flac" });
  });

  it("numbers that didn't change aren't sent, and validation errors are shown", async () => {
    mockApi({ ...base, "GET /api/settings": SETTINGS, "POST /api/settings": { status: 400, body: { error: "invalid_setting", message: "ask_threshold cannot be above auto_accept_threshold" } } });
    renderApp(<Settings />);
    await screen.findByText("All saved");
    fireEvent.change(screen.getByLabelText(/^ask below/i), { target: { value: "0.95" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByText(/cannot be above/i);
  });

  it("tests the Deezer ARL and ntfy and reports the result", async () => {
    mockApi({ ...base, "GET /api/settings": SETTINGS, "POST /api/settings/test-deezer": { ok: false, error: "Deezer rejected the ARL" }, "POST /api/settings/test-ntfy": { ok: true } });
    renderApp(<Settings />);
    fireEvent.click((await screen.findAllByRole("button", { name: "Test" }))[0]);
    await screen.findByText("Deezer rejected the ARL");
    fireEvent.click(screen.getByRole("button", { name: "Send test" }));
    await screen.findByText("Works");
  });
});

describe("owned tracks", () => {
  const track = (i: number) => ({ path: `Band/LP/${i}.mp3`, title: `Owned ${i}`, artist: "Band", album: "LP", album_artist: "Band", duration_ms: 185000, year: 2020 });

  it("lists the library, filters with a typed query and loads the next page", async () => {
    const calls = mockApi({
      ...base,
      "GET /api/library": (c: { path: string }) => {
        const u = new URL(c.path, "http://x");
        const offset = Number(u.searchParams.get("offset")), q = u.searchParams.get("q") ?? "";
        if (q) return { tracks: [track(9)], total: 1, limit: 100, offset: 0, q, index: "filesystem" };
        return { tracks: offset ? [track(101)] : [track(1), track(2)], total: 3, limit: 100, offset, q: "", index: "filesystem" };
      },
    });
    renderApp(<Owned />);
    await screen.findByText("Owned 1");
    expect(screen.getByText("2 of 3")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /show 1 more/i }));
    await screen.findByText("Owned 101");
    expect(calls.some((c) => c.path.includes("offset=2"))).toBe(true);
    fireEvent.change(screen.getByLabelText("Filter owned tracks"), { target: { value: " nine " } });
    await screen.findByText("Owned 9", {}, { timeout: 2000 });
    expect(calls.some((c) => c.path.includes("q=nine"))).toBe(true);
  });

  it("opens a row to see its artist, album and related artists, looked up only then", async () => {
    const calls = mockApi({
      ...base,
      "GET /api/library": { tracks: [track(1), track(2)], total: 2, limit: 100, offset: 0, q: "", index: "filesystem" },
      "GET /api/library/related": { found: true, matched_by: "isrc", artist: { dz_id: "7", name: "Band" }, album: { dz_id: "55", title: "LP" },
        track: { dz_id: "101", title: "Owned 1" }, related: [{ dz_id: "20", name: "Dojja", image_url: "", fans: 5 }] },
    });
    renderApp(<Owned />);
    await screen.findByText("Owned 1");
    expect(calls.some((c) => c.path.startsWith("/api/library/related"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Related to Owned 1" }));
    await screen.findByText("Dojja");
    expect(screen.getByRole("link", { name: "Artist: Band" }).getAttribute("href")).toBe("/artist/7");
    expect(screen.getByRole("link", { name: "Album: LP" }).getAttribute("href")).toBe("/album/55");
    expect(screen.getByRole("link", { name: "Track on Deezer" }).getAttribute("href")).toBe("https://www.deezer.com/track/101");
    expect(screen.getByRole("link", { name: "Band" }).getAttribute("href")).toBe("/library/artists/Band");            // the local pages
    expect(screen.getByRole("link", { name: "LP" }).getAttribute("href")).toBe("/library/albums/Band/LP");
    expect(calls.filter((c) => c.path.startsWith("/api/library/related"))).toHaveLength(1);
    expect(decodeURIComponent(calls.find((c) => c.path.startsWith("/api/library/related"))!.path)).toContain("path=Band/LP/1.mp3");
  });

  it("says so when the artist can't be found with certainty", async () => {
    mockApi({
      ...base,
      "GET /api/library": { tracks: [track(1)], total: 1, limit: 100, offset: 0, q: "", index: "filesystem" },
      "GET /api/library/related": { found: false, matched_by: null, artist: null, album: null, track: null, related: [] },
    });
    renderApp(<Owned />);
    fireEvent.click(await screen.findByRole("button", { name: "Related to Owned 1" }));
    await screen.findByText(/can't find this artist on Deezer with certainty/i);
  });

  it("explains an empty index", async () => {
    mockApi({ ...base, "GET /api/library": { tracks: [], total: 0, limit: 100, offset: 0, q: "", index: "filesystem" } });
    renderApp(<Owned />);
    await screen.findByText(/library index is empty/i);
  });
});
