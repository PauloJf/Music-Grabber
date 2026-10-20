import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Layout from "./Layout";
import { ACTIVITY, STATUS, mockApi, renderApp } from "../test-utils";

afterEach(() => vi.unstubAllGlobals());

describe("layout + activity", () => {
  it("badges queue and inbox counts and shows the running download", async () => {
    mockApi({ "GET /api/status": STATUS, "GET /api/activity": { ...ACTIVITY, inbox: 2, active: [{ id: 1, status: "downloading", progress: 0.5, title: "Levitating", artist: "Dua Lipa" }] } });
    renderApp(<Layout><p>page</p></Layout>);
    expect(await screen.findByRole("button", { name: /1 downloading/i })).toBeTruthy();
    expect((await screen.findAllByText("2")).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: /queue/i }).length).toBeGreaterThan(0);
  });

  it("toasts the moment a download finishes", async () => {
    let call = 0;
    mockApi({ "GET /api/status": STATUS, "GET /api/activity": () => (++call < 2
      ? { ...ACTIVITY, active: [{ id: 1, status: "downloading", progress: 0.9, title: "Levitating", artist: "D" }] }
      : { ...ACTIVITY, recent: [{ id: 1, status: "done", title: "Levitating", artist: "D", updated_at: new Date().toISOString(), error: null }] }) });
    renderApp(<Layout><p>page</p></Layout>);
    await screen.findByRole("button", { name: /downloading/i });
    await waitFor(() => expect(screen.getByText("Downloaded: Levitating")).toBeTruthy(), { timeout: 5000 });
  });

  it("warns when Spotify isn't configured and when dry run is on", async () => {
    mockApi({ "GET /api/status": { ...STATUS, dry_run: true, spotify: { ...STATUS.spotify, configured: false, connected: false } }, "GET /api/activity": ACTIVITY });
    renderApp(<Layout><p>page</p></Layout>);
    await screen.findByText(/spotify isn't configured/i);
    expect(screen.getByText(/dry run is on/i)).toBeTruthy();
  });
});
