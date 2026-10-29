import { fireEvent, screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Album from "./Album";
import Artist from "./Artist";
import { ACTIVITY, STATUS, mockApi, renderApp, track } from "../test-utils";

afterEach(() => vi.unstubAllGlobals());
const ALBUM = { dz_id: "55", title: "Future Nostalgia", artist: "Dua Lipa", artist_id: "7", cover_url: "", year: 2020, track_count: 3, kind: "album", explicit: false };
const base = { "GET /api/activity": ACTIVITY, "GET /api/status": STATUS };
const page = (tracks: object[]) => ({ "GET /api/browse/album/55": { album: ALBUM, tracks } });
const renderAlbum = () => renderApp(<Routes><Route path="/album/:id" element={<Album />} /></Routes>, { route: "/album/55" });

describe("album page", () => {
  it("queues only what's missing and says how many", async () => {
    const calls = mockApi({ ...base, ...page([track(1, "A"), track(2, "B", { kind: "library" }), track(3, "C", { kind: "queued", item_id: 1, status: "pending", progress: 0 })]),
      "POST /api/browse/queue": { ok: true, queued: 1, item_ids: [5], results: [] } });
    renderAlbum();
    fireEvent.click(await screen.findByRole("button", { name: "Queue 1 missing" }));
    await waitFor(() => expect((calls.find((c) => c.path === "/api/browse/queue")?.body as { tracks: unknown[] }).tracks).toHaveLength(1));
  });

  it("previews a track from its row, and keeps the slot when a track has none", async () => {
    const play = vi.fn().mockResolvedValue(undefined);
    const srcs: string[] = [];
    vi.stubGlobal("Audio", class { set src(v: string) { srcs.push(v); } play = play; pause = vi.fn(); addEventListener = vi.fn(); volume = 1; });
    mockApi({ ...base, ...page([{ ...track(1, "A"), preview_url: "https://cdn/a.mp3" }, { ...track(2, "B"), preview_url: "" }]) });
    renderAlbum();
    fireEvent.click(await screen.findByRole("button", { name: "Preview A" }));
    await waitFor(() => expect(srcs).toEqual(["https://cdn/a.mp3"]));
    expect(screen.queryByRole("button", { name: "Preview B" })).toBeNull();
    expect(screen.getAllByText("No preview on Deezer")).toHaveLength(1);              // only B, and it says why on hover
    expect(screen.getByText("No preview on Deezer").getAttribute("title")).toMatch(/can't download/);
    expect(screen.getByRole("button", { name: "Pause A" })).toBeTruthy();
  });

  it("says everything is queued or owned, with the button disabled", async () => {
    mockApi({ ...base, ...page([track(1, "A", { kind: "library" })]) });
    renderAlbum();
    expect(((await screen.findByRole("button", { name: /everything queued or owned/i })) as HTMLButtonElement).disabled).toBe(true);
  });

  it("selecting tracks shows a bar to queue just those; owned tracks can't be selected", async () => {
    const calls = mockApi({ ...base, ...page([track(1, "A"), track(2, "B"), track(3, "C", { kind: "library" })]),
      "POST /api/browse/queue": { ok: true, queued: 1, item_ids: [5], results: [] } });
    renderAlbum();
    fireEvent.click(await screen.findByLabelText("Select B"));
    expect((screen.getByLabelText("Select C") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText("1 selected")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Queue selected" }));
    await waitFor(() => expect((calls.find((c) => c.path === "/api/browse/queue")?.body as { tracks: { title: string }[] }).tracks.map((t) => t.title)).toEqual(["B"]));
    await waitFor(() => expect(screen.queryByText("1 selected")).toBeNull());
  });
});

describe("artist page", () => {
  const artist = { dz_id: "7", name: "Dua Lipa", image_url: "", fans: 6200000, album_count: 5 };
  it("shows popular tracks, releases split into albums and singles, and related artists", async () => {
    mockApi({ ...base, "GET /api/browse/artist/7": { artist, top: [track(1, "Levitating"), track(2, "Cool", { kind: "library" })],
      albums: [ALBUM, { ...ALBUM, dz_id: "58", title: "Levitating (Single)", kind: "single" }], related: [{ dz_id: "8", name: "Dojja", image_url: "", fans: 1, album_count: 1 }] } });
    renderApp(<Routes><Route path="/artist/:id" element={<Artist />} /></Routes>, { route: "/artist/7" });
    await screen.findByRole("heading", { name: "Dua Lipa" });
    expect(screen.getByRole("button", { name: "Queue 1 missing" })).toBeTruthy();
    expect(screen.getAllByRole("link", { name: /future nostalgia/i }).every((l) => l.getAttribute("href") === "/album/55")).toBe(true);
    fireEvent.click(screen.getByRole("tab", { name: /singles/i }));
    expect(screen.getByRole("link", { name: /levitating \(single\)/i })).toBeTruthy();
    expect(screen.getByRole("link", { name: /dojja/i }).getAttribute("href")).toBe("/artist/8");
  });

  it("says how much of each release the library has, and wraps fans-also-like instead of scrolling", async () => {
    const rel = (id: string, title: string, over: object) => ({ ...ALBUM, dz_id: id, title, ...over });
    mockApi({ ...base, "GET /api/browse/artist/7": { artist, top: [], related: [{ dz_id: "8", name: "Dojja", image_url: "", fans: 1, album_count: 1 }],
      albums: [rel("1", "Whole", { owned: 3, owned_total: 3, own_state: "full" }), rel("2", "Half", { owned: 2, owned_total: 4, own_state: "partial" }),
        rel("3", "Unknown Length", { owned: 1, owned_total: null, own_state: "some" }), rel("4", "Nothing", { owned: 0, owned_total: null, own_state: "none" }),
        rel("5", "Plain") ] } });
    renderApp(<Routes><Route path="/artist/:id" element={<Artist />} /></Routes>, { route: "/artist/7" });
    await screen.findByRole("heading", { name: "Dua Lipa" });
    expect(screen.getByText("In library")).toBeTruthy();
    expect(screen.getByText("2 of 4 in library")).toBeTruthy();
    expect(screen.getByText("1 track in library")).toBeTruthy();
    expect(screen.getAllByText(/in library/i)).toHaveLength(3);                           // none of Nothing / Plain
    const chips = screen.getByRole("link", { name: /dojja/i }).parentElement!;
    expect(chips.style.flexWrap).toBe("wrap");
  });

  it("an unknown artist shows the error with a retry", async () => {
    mockApi({ ...base, "GET /api/browse/artist/7": { status: 502, body: { error: "deezer_unreachable", message: "no data" } } });
    renderApp(<Routes><Route path="/artist/:id" element={<Artist />} /></Routes>, { route: "/artist/7" });
    expect((await screen.findByRole("alert")).textContent).toContain("no data");
  });
});
