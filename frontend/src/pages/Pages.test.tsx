import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Inbox from "./Inbox";
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
