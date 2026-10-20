import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Layout from "./Layout";
import { STATUS, mockApi, renderApp } from "../test-utils";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const inbox = (n: number) => ({ items: Array.from({ length: n }, (_, i) => ({ id: i + 1 })) });

describe("layout", () => {
  it("badges the inbox and the active queue count (awaiting_user is not 'active')", async () => {
    mockApi({ "GET /api/status": { ...STATUS, queue: { downloading: 2, pending: 1, awaiting_user: 3, done: 9, failed: 1 } },
              "GET /api/inbox": inbox(3) });
    renderApp(<Layout><p>page</p></Layout>);
    expect(await screen.findByRole("link", { name: /queue 3/i })).toBeTruthy();   // downloading 2 + pending 1
    expect(await screen.findByRole("link", { name: /inbox 3/i })).toBeTruthy();   // awaiting_user, by the inbox list
  });

  it("shows dry run and the footer facts", async () => {
    mockApi({ "GET /api/status": { ...STATUS, dry_run: true }, "GET /api/inbox": inbox(0) });
    renderApp(<Layout><p>page</p></Layout>);
    await screen.findByText("dry run");
    expect(screen.getByText(/library 10 tracks via filesystem/)).toBeTruthy();
  });

  it("offers Connect Spotify when configured but not connected, and opens the authorize URL", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    mockApi({ "GET /api/status": { ...STATUS, spotify: { ...STATUS.spotify, connected: false } }, "GET /api/inbox": inbox(0),
              "GET /api/spotify/authorize-url": { url: "https://accounts.example/authorize?state=s", state: "s" } });
    renderApp(<Layout><p>page</p></Layout>);
    fireEvent.click(await screen.findByRole("button", { name: /connect spotify/i }));
    await waitFor(() => expect(open).toHaveBeenCalledWith("https://accounts.example/authorize?state=s", "_blank", "noopener"));
  });

  it("says so when the Spotify client isn't configured, and can disconnect when connected", async () => {
    mockApi({ "GET /api/status": { ...STATUS, spotify: { ...STATUS.spotify, configured: false, connected: false } }, "GET /api/inbox": inbox(0) });
    renderApp(<Layout><p>page</p></Layout>);
    await screen.findByText(/spotify not configured/i);
    vi.unstubAllGlobals();
    const calls = mockApi({ "GET /api/status": STATUS, "GET /api/inbox": inbox(0), "POST /api/spotify/disconnect": { ok: true } });
    renderApp(<Layout><p>page</p></Layout>);
    fireEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/spotify/disconnect")).toBe(true));
  });

  it("signing out clears the token", async () => {
    mockApi({ "GET /api/status": STATUS, "GET /api/inbox": inbox(0) });
    renderApp(<Layout><p>page</p></Layout>);
    fireEvent.click(await screen.findByRole("button", { name: /sign out/i }));
    expect(sessionStorage.getItem("mg.token")).toBeNull();
  });
});
