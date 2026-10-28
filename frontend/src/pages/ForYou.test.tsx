import { fireEvent, screen, waitFor } from "@testing-library/react";
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
    renderApp(<App />, { route: "/for-you" });
    await screen.findByText("Dojja");
    expect(screen.getByText(/Because you have Dua Lipa and Prince/)).toBeTruthy();
    expect(screen.getByText(/you have 2 of 10/)).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Not interested" })[0]);
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/suggest/artist/20/dismiss")).toBe(true));
  });

  it("a switched-off part is not shown, and everything off says so", async () => {
    mockApi({ ...base, "GET /api/suggest": reply({ ...on, gaps_enabled: false, gaps: [] }) });
    renderApp(<App />, { route: "/for-you" });
    await screen.findByText("Artists you may like");
    expect(screen.queryByText("Albums you're missing")).toBeNull();
  });

  it("explains itself when the feature is off", async () => {
    mockApi({ ...base, "GET /api/suggest": reply({ ...on, enabled: false, gaps_enabled: false, artists_enabled: false, gaps: [], artists: [], status: null }) });
    renderApp(<App />, { route: "/for-you" });
    await screen.findByText("Suggestions are off");
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
