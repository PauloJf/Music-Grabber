import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Discover from "./Discover";
import { ACTIVITY, STATUS, mockApi, renderApp, track } from "../test-utils";

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

const base = { "GET /api/activity": ACTIVITY, "GET /api/status": STATUS };
const submit = (text: string) => {
  fireEvent.change(screen.getByRole("searchbox", { name: /search deezer/i }), { target: { value: text } });
  fireEvent.submit(screen.getByRole("search"));
};

describe("discover", () => {
  it("invites a search when empty, and remembers what you searched", async () => {
    mockApi({ ...base, "GET /api/browse/search?q=dua&type=track": { results: [track(1, "Levitating")] } });
    renderApp(<Discover />);
    expect(screen.getByText(/start with a name/i)).toBeTruthy();
    submit("dua");
    await screen.findByText("Levitating");
    expect(JSON.parse(localStorage.getItem("mg.recent")!)).toEqual(["dua"]);
  });

  it("shows each song's state: queue button, in library, progress, needs you, failed", async () => {
    mockApi({ ...base, "GET /api/browse/search?q=x&type=track": { results: [
      track(1, "Fresh"), track(2, "Owned", { kind: "library" }), track(3, "Busy", { kind: "active", item_id: 9, status: "downloading", progress: 0.4 }),
      track(4, "Waiting", { kind: "queued", item_id: 8, status: "pending", progress: 0 }), track(5, "Unsure", { kind: "review", item_id: 7, status: "awaiting_user", progress: 0 }),
      track(6, "Broken", { kind: "failed", item_id: 6, error: "boom" })] } });
    renderApp(<Discover />, { route: "/?q=x" });
    await screen.findByRole("button", { name: "Queue Fresh" });
    expect(screen.getByText("In library")).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "" }).getAttribute("aria-valuenow")).toBe("40");
    expect(screen.getByText("Queued")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Needs you" }).getAttribute("href")).toBe("/inbox/7");
    expect(screen.getByRole("button", { name: "Retry Broken" })).toBeTruthy();
  });

  it("queueing responds at once, confirms with Undo, and Undo removes it", async () => {
    let release: (v: unknown) => void = () => {};
    const gate = new Promise((r) => { release = r; });
    const calls = mockApi({ ...base,
      "GET /api/browse/search?q=x&type=track": { results: [track(1, "Fresh")] },
      "POST /api/browse/queue": async () => { await gate; return { ok: true, queued: 1, item_ids: [42], results: [{ dz_id: "1", result: "queued" }] }; },
      "POST /api/browse/undo": { ok: true, removed: 1 } });
    renderApp(<Discover />, { route: "/?q=x" });
    fireEvent.click(await screen.findByRole("button", { name: "Queue Fresh" }));
    await screen.findByRole("status", { name: "Queuing" });               // optimistic: before the server answers
    release(null);
    await screen.findByText(/queued fresh/i);
    expect(calls.find((c) => c.path === "/api/browse/queue")?.body).toMatchObject({ tracks: [{ dz_id: "1", title: "Fresh" }] });
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/browse/undo")?.body).toEqual({ item_ids: [42] }));
    await screen.findByText(/removed from the queue/i);
  });

  it("explains an already-owned track instead of silently doing nothing", async () => {
    mockApi({ ...base, "GET /api/browse/search?q=x&type=track": { results: [track(1, "Fresh")] },
      "POST /api/browse/queue": { ok: true, queued: 0, item_ids: [], results: [{ dz_id: "1", result: "in_library" }] } });
    renderApp(<Discover />, { route: "/?q=x" });
    fireEvent.click(await screen.findByRole("button", { name: "Queue Fresh" }));
    await screen.findByText(/already in your library/i);
  });

  it("a failed queue request says why and the button comes back", async () => {
    mockApi({ ...base, "GET /api/browse/search?q=x&type=track": { results: [track(1, "Fresh")] },
      "POST /api/browse/queue": { status: 502, body: { error: "deezer_unreachable", message: "Deezer is unreachable" } } });
    renderApp(<Discover />, { route: "/?q=x" });
    fireEvent.click(await screen.findByRole("button", { name: "Queue Fresh" }));
    await screen.findByText(/couldn't queue: deezer is unreachable/i);
    expect(await screen.findByRole("button", { name: "Queue Fresh" })).toBeTruthy();
  });

  it("switches to artists and albums and links to their pages", async () => {
    mockApi({ ...base,
      "GET /api/browse/search?q=dua&type=artist": { results: [{ dz_id: "7", name: "Dua Lipa", image_url: "", fans: 6200000, album_count: 5 }] },
      "GET /api/browse/search?q=dua&type=album": { results: [{ dz_id: "55", title: "Future Nostalgia", artist: "Dua Lipa", artist_id: "7", cover_url: "", year: 2020, track_count: 11, kind: "album", explicit: false }] } });
    renderApp(<Discover />, { route: "/?q=dua" });
    fireEvent.click(screen.getByRole("tab", { name: "Artists" }));
    expect((await screen.findByRole("link", { name: /dua lipa/i })).getAttribute("href")).toBe("/artist/7");
    expect(screen.getByText("6.2M fans")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Albums" }));
    expect((await screen.findByRole("link", { name: /future nostalgia/i })).getAttribute("href")).toBe("/album/55");
  });

  it("no results and Deezer-down both get a helpful message", async () => {
    mockApi({ ...base, "GET /api/browse/search?q=zzz&type=track": { results: [] },
      "GET /api/browse/search?q=boom&type=track": { status: 502, body: { error: "deezer_unreachable", message: "Deezer is unreachable (ConnectionError)" } } });
    renderApp(<Discover />, { route: "/?q=zzz" });
    await screen.findByText(/nothing for “zzz”/i);
  });

  it("Deezer being down offers Try again", async () => {
    mockApi({ ...base, "GET /api/browse/search?q=boom&type=track": { status: 502, body: { error: "deezer_unreachable", message: "Deezer is unreachable (ConnectionError)" } } });
    renderApp(<Discover />, { route: "/?q=boom" });
    expect((await screen.findByRole("alert")).textContent).toContain("Deezer is unreachable");
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("previews a track with the shared player", async () => {
    const play = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("Audio", class { src = ""; play = play; pause = vi.fn(); addEventListener = vi.fn(); });
    mockApi({ ...base, "GET /api/browse/search?q=x&type=track": { results: [track(1, "Fresh")] } });
    renderApp(<Discover />, { route: "/?q=x" });
    fireEvent.click(await screen.findByRole("button", { name: "Preview Fresh" }));
    expect(play).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Pause Fresh" }).getAttribute("aria-pressed")).toBe("true");
  });
});
