import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import Settings from "./Settings";
import { ACTIVITY, SETTINGS, STATUS, mockApi, renderApp } from "../test-utils";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const me = { user: { id: 1, username: "paulo", role: "admin", prefs: {} }, via: "session" };
const base = { "GET /api/status": STATUS, "GET /api/activity": ACTIVITY, "GET /api/me": me };
// The mock treats an object with a "status" key as {status, body}: wrap replies that carry one.
const reply = (body: object) => ({ status: 200, body });
const artist = { dz_id: "20", name: "Dojja", image_url: "", fans: 1200, score: 3, because: ["Dua Lipa", "Prince"] };
const gap = { dz_artist_id: "7", dz_album_id: "702", title: "Old Album", release_date: "2019-01-01", cover_url: "", artist_name: "Dua Lipa", track_count: 10, owned: 2, state: "partial" };
const on = { enabled: true, gaps_enabled: true, artists_enabled: true, gaps: [gap], artists: [artist], status: { more_to_do: false } };

describe("for you", () => {
  it("shows artists and gaps with why, and dismisses one", async () => {
    const calls = mockApi({ ...base, "GET /api/suggest": reply(on), "POST /api/suggest/artist/20/dismiss": { ok: true } });
    renderApp(<App />, { route: "/" });
    await screen.findByText("Dojja");
    expect(screen.getByText(/Because you have Dua Lipa and Prince/)).toBeTruthy();
    expect(screen.getByText(/you have 2 of 10/)).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Not interested" })[0]);
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/suggest/artist/20/dismiss")).toBe(true));
  });

  it("a switched-off part is not shown, and everything off says so", async () => {
    mockApi({ ...base, "GET /api/suggest": reply({ ...on, gaps_enabled: false, gaps: [] }) });
    renderApp(<App />, { route: "/" });
    await screen.findByText("Artists you may like");
    expect(screen.queryByText("Albums you're missing")).toBeNull();
  });

  it("is the home page, with a preview of the new releases", async () => {
    mockApi({ ...base, "GET /api/suggest": reply(on),
      "GET /api/releases": reply({ enabled: true, days: 90, releases: [{ dz_artist_id: "7", dz_album_id: "701", title: "New Album", kind: "album", release_date: "2026-10-18", cover_url: "", artist_name: "Dua Lipa" }], following: [], status: { more_to_do: false } }) });
    renderApp(<App />, { route: "/" });
    await screen.findByText("New Album");
    expect(screen.getByRole("link", { name: "See all and manage" })).toBeTruthy();
  });

  it("falls back to Discover when the feature is off", async () => {
    mockApi({ ...base, "GET /api/suggest": reply({ ...on, enabled: false, gaps_enabled: false, artists_enabled: false, gaps: [], artists: [], status: null }) });
    renderApp(<App />, { route: "/" });
    await screen.findByLabelText("Search Deezer");
  });

  it("the old /for-you address lands on the home page", async () => {
    mockApi({ ...base, "GET /api/suggest": reply(on), "GET /api/releases": reply({ enabled: true, days: 90, releases: [], following: [], status: null }) });
    renderApp(<App />, { route: "/for-you" });
    await screen.findByText("Artists you may like");
  });
});

describe("settings switches", () => {
  it("turns the gaps off on their own and sends only that", async () => {
    const calls = mockApi({
      ...base,
      "GET /api/settings": { ...SETTINGS, settings: { ...SETTINGS.settings, releases_enabled: true, suggest_gaps: true, suggest_artists: true, suggest_stars: true } },
      "POST /api/settings": { ok: true, applied: ["suggest_gaps"], restart_required: [] },
    });
    renderApp(<Settings />);
    fireEvent.click(await screen.findByLabelText(/Albums you're missing/));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByText("Saved and applied");
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/settings")?.body).toEqual({ suggest_gaps: false });
  });

  it("the parts are greyed out while the master switch is off", async () => {
    mockApi({ ...base, "GET /api/settings": { ...SETTINGS, settings: { ...SETTINGS.settings, releases_enabled: false, suggest_gaps: true, suggest_artists: true, suggest_stars: true } } });
    renderApp(<Settings />);
    const gaps = await screen.findByLabelText(/Albums you're missing/);
    expect((gaps as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText(/Artists you may like/) as HTMLInputElement).disabled).toBe(true);
  });
});

describe("the menu", () => {
  const on2 = { ...STATUS, releases_enabled: true };
  it("has For you first while the feature is on, and Settings in the header, not the tab bar", async () => {
    mockApi({ ...base, "GET /api/status": on2, "GET /api/suggest": reply(on), "GET /api/releases": reply({ enabled: true, days: 90, releases: [], following: [], status: null }) });
    renderApp(<App />, { route: "/discover" });
    await screen.findByLabelText("Search Deezer");
    await waitFor(() => expect(within(screen.getAllByRole("navigation", { name: "Main" })[0]).queryByText("For you")).not.toBeNull());
    const main = screen.getAllByRole("navigation", { name: "Main" });
    for (const nav of main) {
      const names = Array.from(nav.querySelectorAll("a")).map((a) => a.textContent);
      expect(names[0]).toBe("For you");
      expect(names).toContain("Discover");
      expect(names).not.toContain("Settings");
    }
    expect(screen.getByRole("link", { name: "Settings" }).getAttribute("href")).toBe("/settings");
  });

  it("hides For you while the feature is off", async () => {
    mockApi({ ...base });
    renderApp(<App />, { route: "/discover" });
    await screen.findByLabelText("Search Deezer");
    await waitFor(() => expect(screen.getAllByRole("navigation", { name: "Main" })[0].textContent).toContain("Queue"));
    for (const nav of screen.getAllByRole("navigation", { name: "Main" })) expect(within(nav).queryByText("For you")).toBeNull();
  });
});

describe("tile previews", () => {
  const trk = (id: string, url: string) => ({ dz_id: id, title: `T${id}`, artist: "A", artist_id: "20", album: "L", album_id: "702", duration_ms: 1000, preview_url: url, cover_url: "", explicit: false, track_no: 1, disc_no: 1, year: 2020, state: { kind: "none" } });
  const withAudio = () => {
    const play = vi.fn().mockResolvedValue(undefined);
    const srcs: string[] = [];
    vi.stubGlobal("Audio", class { set src(v: string) { srcs.push(v); } play = play; pause = vi.fn(); addEventListener = vi.fn(); volume = 1; });
    return { play, srcs };
  };

  it("plays the first previewable track of an artist and of an album, fetching only when pressed", async () => {
    const { play, srcs } = withAudio();
    const calls = mockApi({ ...base, "GET /api/suggest": reply(on), "GET /api/releases": reply({ enabled: false, days: 90, releases: [], following: [], status: null }),
      "GET /api/browse/artist/20": { artist: {}, top: [trk("1", ""), trk("2", "https://cdn/a.mp3")], albums: [], related: [] },
      "GET /api/browse/album/702": { album: {}, tracks: [trk("3", "https://cdn/b.mp3")] } });
    renderApp(<App />, { route: "/" });
    await screen.findByText("Dojja");
    expect(calls.some((c) => c.path.startsWith("/api/browse/"))).toBe(false);
    const buttons = screen.getAllByRole("button", { name: /^Preview/ });
    fireEvent.click(buttons[0]);                                             // the artist
    await waitFor(() => expect(srcs).toContain("https://cdn/a.mp3"));
    fireEvent.click(screen.getAllByRole("button", { name: /Preview album/ })[0]);
    await waitFor(() => expect(srcs).toContain("https://cdn/b.mp3"));
    expect(play).toHaveBeenCalledTimes(2);
  });

  it("says so when there is nothing to play", async () => {
    withAudio();
    mockApi({ ...base, "GET /api/suggest": reply(on), "GET /api/releases": reply({ enabled: false, days: 90, releases: [], following: [], status: null }),
      "GET /api/browse/artist/20": { artist: {}, top: [trk("1", "")], albums: [], related: [] } });
    renderApp(<App />, { route: "/" });
    fireEvent.click((await screen.findAllByRole("button", { name: "Preview artist" }))[0]);
    await screen.findByText("No preview on Deezer for this one, so it probably can't be downloaded from there");
  });
});
