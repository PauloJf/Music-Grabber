import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { ACTIVITY, STATUS, mockApi, renderApp } from "../test-utils";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const me = { user: { id: 1, username: "paulo", role: "admin", prefs: {} }, via: "session" };
const base = { "GET /api/status": STATUS, "GET /api/activity": ACTIVITY, "GET /api/me": me };

const ARTISTS = [
  { key: "dua lipa", name: "Dua Lipa", tracks: 6, albums: 3, image_url: "https://cdn/dua.jpg" },
  { key: "exito", name: "Éxito", tracks: 2, albums: 1, image_url: "" },
];
const alb = (over: object = {}) => ({ artist_key: "dua lipa", album_key: "future nostalgia", artist: "Dua Lipa", title: "Future Nostalgia", year: 2020, tracks: 3,
  cover_url: "https://cdn/fn.jpg", dz_album_id: "55", dz_artist_id: "7", ...over });

describe("library browser", () => {
  it("has the four tabs and lists artists with their counts, filtering on the server", async () => {
    const calls = mockApi({ ...base, "GET /api/library/artists": (c: { path: string }) => {
      const q = new URL(c.path, "http://x").searchParams.get("q");
      const list = q ? ARTISTS.filter((a) => a.name.toLowerCase().includes(q)) : ARTISTS;
      return { artists: list, total: list.length };
    } });
    renderApp(<App />, { route: "/library/artists" });
    await screen.findByText("Dua Lipa");
    const tabs = within(screen.getByRole("navigation", { name: "Library" }));
    expect(tabs.getAllByRole("link").map((l) => l.textContent)).toEqual(["Playlists", "Artists", "Albums", "Tracks"]);
    expect(screen.getByText("3 albums · 6 tracks")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Filter artists"), { target: { value: "exi" } });
    await waitFor(() => expect(calls.some((c) => c.path.includes("q=exi"))).toBe(true));
    await waitFor(() => expect(screen.queryByText("Dua Lipa")).toBeNull());
  });

  it("an artist page shows their albums, where they appear, and a way over to Deezer", async () => {
    mockApi({ ...base, "GET /api/library/artist": { key: "dua lipa", name: "Dua Lipa", tracks: 6, image_url: "", dz_artist_id: "7",
      albums: [alb(), alb({ title: "(no album)", album_key: "", tracks: 1, cover_url: "", dz_album_id: null, year: null })],
      appears_on: [alb({ artist: "Various Artists", title: "Now 100", tracks: 1, cover_url: "", dz_album_id: null })] } });
    renderApp(<App />, { route: "/library/artists/Dua%20Lipa" });
    await screen.findByRole("heading", { name: "Dua Lipa" });
    expect(screen.getByRole("link", { name: "See everything on Deezer" }).getAttribute("href")).toBe("/artist/7");
    expect(screen.getByRole("link", { name: /Future Nostalgia/ }).getAttribute("href")).toBe("/library/albums/Dua%20Lipa/Future%20Nostalgia");
    expect(screen.getByRole("link", { name: /Now 100/ }).getAttribute("href")).toBe("/library/albums/Various%20Artists/Now%20100");
    expect(screen.getByText("Appears on")).toBeTruthy();
    expect(screen.getByRole("link", { name: /\(no album\)/ }).getAttribute("href")).toBe("/library/albums/Dua%20Lipa/(no%20album)");
  });

  it("an album page is read-only: the tracks you own, the artist, and a link to queue the rest on Deezer", async () => {
    mockApi({ ...base, "GET /api/library/album": { ...alb(), discs: 1, tracks_list: [
      { path: "a/1.mp3", title: "Levitating", artist: "Dua Lipa", track_no: 1, disc_no: 1, duration_ms: 203000 },
      { path: "a/3.mp3", title: "Levitating (feat. DaBaby)", artist: "Dua Lipa feat. DaBaby", track_no: 3, disc_no: 1, duration_ms: 200000 }] } });
    renderApp(<App />, { route: "/library/albums/Dua%20Lipa/Future%20Nostalgia" });
    await screen.findByRole("heading", { name: "Future Nostalgia" });
    expect(screen.getByRole("link", { name: "Dua Lipa" }).getAttribute("href")).toBe("/library/artists/Dua%20Lipa");
    expect(screen.getByRole("link", { name: /queue what's missing/ }).getAttribute("href")).toBe("/album/55");
    expect(screen.getByText("Levitating")).toBeTruthy();
    expect(screen.getByText("Dua Lipa feat. DaBaby")).toBeTruthy();        // only shown when it differs from the album artist
    expect(screen.queryAllByRole("button", { name: /queue|download/i })).toHaveLength(0);
  });

  it("the albums list pages through a big library client-side", async () => {
    const many = Array.from({ length: 450 }, (_, i) => alb({ album_key: `a${i}`, title: `Album ${String(i).padStart(3, "0")}`, cover_url: "" }));
    mockApi({ ...base, "GET /api/library/albums": { albums: many, total: 450 } });
    renderApp(<App />, { route: "/library/albums" });
    await screen.findByText("Album 000");
    expect(screen.getByText("200 of 450")).toBeTruthy();
    expect(screen.queryByText("Album 250")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show 200 more" }));
    await screen.findByText("Album 250");
    expect(screen.getByText("400 of 450")).toBeTruthy();
  });

  it("says so when the index is empty or a page is gone", async () => {
    mockApi({ ...base, "GET /api/library/artists": { artists: [], total: 0 }, "GET /api/library/artist": { status: 404, body: { error: "not_found", message: "artist not found" } } });
    renderApp(<App />, { route: "/library/artists" });
    await screen.findByText("The library index is empty");
    renderApp(<App />, { route: "/library/artists/Nobody" });
    await screen.findByText("artist not found");
  });
});
