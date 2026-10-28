import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { ACTIVITY, STATUS, mockApi, renderApp } from "../test-utils";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const me = { user: { id: 1, username: "paulo", role: "user", prefs: {} }, via: "session" };
// The mock treats an object with a "status" key as {status, body}, and the feed has one: wrap it.
const base = { "GET /api/status": STATUS, "GET /api/activity": ACTIVITY, "GET /api/me": me };
const release = { dz_artist_id: "7", dz_album_id: "701", title: "New Album", kind: "album", release_date: "2026-10-18", cover_url: "", artist_name: "Dua Lipa" };
const status = { artists: 3, mapped: 2, last_run: null, last_checked: new Date().toISOString(), last_error: "", more_to_do: false };

describe("new releases", () => {
  it("says how to turn it on when it is off", async () => {
    mockApi({ ...base, "GET /api/releases": { status: 200, body: { enabled: false, days: 90, releases: [], following: [], status: null } } });
    renderApp(<App />, { route: "/releases" });
    await screen.findByText("New releases are off");
  });

  it("lists releases, hides an artist and shows the hidden list", async () => {
    const calls = mockApi({
      ...base,
      "GET /api/releases": { status: 200, body: { enabled: true, days: 90, releases: [release], following: [], status } },
      "PUT /api/artist-prefs/7": { ok: true },
      "GET /api/releases/hidden": { artists: [{ dz_artist_id: "9", name: "Prince", follow: 0, hide_releases: 1, hide_suggestions: 0 }], releases: [] },
    });
    renderApp(<App />, { route: "/releases" });
    await screen.findByText("New Album");
    expect(screen.getByText(/2 of 3 artists matched/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Hide artist" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ hide_releases: true, name: "Dua Lipa" }));
    fireEvent.click(screen.getByRole("button", { name: "Hidden artists and releases" }));
    await screen.findByText("Prince");
  });

  it("an empty feed explains itself", async () => {
    mockApi({ ...base, "GET /api/releases": { status: 200, body: { enabled: true, days: 90, releases: [], following: [], status } } });
    renderApp(<App />, { route: "/releases" });
    await screen.findByText("Nothing new in 90 days");
  });
});
