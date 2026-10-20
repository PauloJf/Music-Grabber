import { fireEvent, screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import PlaylistDetail from "./PlaylistDetail";
import Playlists from "./Playlists";
import { mockApi, renderApp } from "../test-utils";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const pl = (id: number, name: string, extra: object = {}) => ({
  id, spotify_id: `PL${id}`, name, enabled: 1, image_url: null, track_count: 10, last_synced_at: null,
  have_count: 6, missing_count: 3, queued_count: 1, new_count: 0, removed_count: 0, ...extra,
});

describe("playlists", () => {
  it("shows coverage per playlist", async () => {
    mockApi({ "GET /api/playlists": { playlists: [pl(1, "Road Trip", { new_count: 2 })] } });
    renderApp(<Playlists />);
    await screen.findByText("Road Trip");
    expect(screen.getByText("6 have")).toBeTruthy();
    expect(screen.getByText("3 missing")).toBeTruthy();
    expect(screen.getByText("1 queued")).toBeTruthy();
    expect(screen.getByText("2 new")).toBeTruthy();
  });

  it("adds a playlist from a pasted URL and says when Spotify isn't connected", async () => {
    const calls = mockApi({ "GET /api/playlists": { playlists: [] }, "POST /api/playlists": { ok: true, connected: false, playlist: pl(1, "x") } });
    renderApp(<Playlists />);
    await screen.findByText(/no playlists yet/i);
    fireEvent.change(screen.getByLabelText(/spotify playlist url/i), { target: { value: " https://open.spotify.com/playlist/AAA " } });
    fireEvent.click(screen.getByRole("button", { name: /watch playlist/i }));
    await screen.findByText(/connect spotify to sync it/i);
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/playlists")?.body).toEqual({ value: "https://open.spotify.com/playlist/AAA" });
  });

  it("an invalid URL shows the server's message", async () => {
    mockApi({ "GET /api/playlists": { playlists: [] }, "POST /api/playlists": { status: 400, body: { error: "invalid_playlist", message: "give a Spotify playlist URL, URI or id" } } });
    renderApp(<Playlists />);
    fireEvent.change(await screen.findByLabelText(/spotify playlist url/i), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: /watch playlist/i }));
    expect((await screen.findByRole("alert")).textContent).toContain("give a Spotify playlist URL");
  });

  it("sync, queue missing and the watch toggle call the right endpoints", async () => {
    const calls = mockApi({ "GET /api/playlists": { playlists: [pl(1, "Road Trip")] },
      "POST /api/playlists/1/sync": { ok: true }, "POST /api/sync": { ok: true },
      "POST /api/playlists/1/enqueue-missing": { ok: true, enqueued: 3 }, "PATCH /api/playlists/1": { ok: true } });
    renderApp(<Playlists />);
    await screen.findByText("Road Trip");
    fireEvent.click(screen.getByRole("button", { name: "Sync" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/playlists/1/sync")).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Sync all" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/sync")).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: /queue missing/i }));
    await screen.findByText(/queued 3 missing/i);
    fireEvent.click(screen.getByLabelText("Watch Road Trip"));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ enabled: false }));
  });

  it("removing asks first", async () => {
    const calls = mockApi({ "GET /api/playlists": { playlists: [pl(1, "Road Trip")] }, "DELETE /api/playlists/1": { ok: true } });
    renderApp(<Playlists />);
    await screen.findByText("Road Trip");
    vi.spyOn(window, "confirm").mockReturnValueOnce(false);
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    vi.spyOn(window, "confirm").mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/playlists/1")).toBe(true));
  });

  it("the detail page lists tracks and filters by status through the API", async () => {
    const tracks = [{ id: 1, position: 0, title: "Song A", artist: "Band", album: "LP", derived_status: "have", matched_file_path: "a.mp3" },
                    { id: 2, position: 1, title: "Song B", artist: "Band", album: "LP", derived_status: "missing", matched_file_path: null }];
    const calls = mockApi({
      "GET /api/playlists/1": { playlist: pl(1, "Road Trip"), tracks },
      "GET /api/playlists/1?status=missing": { playlist: pl(1, "Road Trip"), tracks: [tracks[1]] },
    });
    renderApp(<Routes><Route path="/playlists/:id" element={<PlaylistDetail />} /></Routes>, { route: "/playlists/1" });
    await screen.findByText("Song A");
    expect(screen.getByText("Song B")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "missing" }));
    await waitFor(() => expect(screen.queryByText("Song A")).toBeNull());
    expect(calls.some((c) => c.path === "/api/playlists/1?status=missing")).toBe(true);
  });
});
