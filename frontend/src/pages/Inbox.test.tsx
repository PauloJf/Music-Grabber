import { fireEvent, screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Inbox from "./Inbox";
import { STATUS, mockApi, renderApp } from "../test-utils";

afterEach(() => vi.unstubAllGlobals());

const item = (id: number, title = "Blinding Lights") => ({
  id, status: "awaiting_user", title, artist: "The Weeknd", album: "After Hours", duration_ms: 200000, isrc: "USUG11904206",
  provider: null, progress: 0, priority: 0, note: "best 0.70 below auto-accept", error: null, final_path: null, updated_at: null,
  candidates: [
    { id: 40, queue_item_id: id, provider: "deezer", title, artist: "The Weeknd", album: "After Hours", duration_ms: 200000, quality: "MP3_128", score: 0.7, rank: 0 },
    { id: 41, queue_item_id: id, provider: "ytdlp", title: `${title} (live)`, artist: "The Weeknd", album: "", duration_ms: 260000, quality: "", score: 0.4, rank: 1 },
  ],
});

describe("inbox", () => {
  it("lists the waiting items with their candidates, scores and the reason", async () => {
    mockApi({ "GET /api/inbox": { items: [item(7)] }, "GET /api/status": STATUS });
    renderApp(<Inbox />);
    await screen.findByRole("heading", { name: "The Weeknd – Blinding Lights" });
    expect(screen.getByText("best 0.70 below auto-accept")).toBeTruthy();
    expect(screen.getByText("0.70")).toBeTruthy();
    expect(screen.getByText("0.40")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /download this/i })).toHaveLength(2);
  });

  it("choosing a candidate posts its id for that item and refreshes", async () => {
    let waiting = [item(7)];
    const calls = mockApi({
      "GET /api/inbox": () => ({ items: waiting }), "GET /api/status": STATUS,
      "POST /api/inbox/7/choose": () => { waiting = []; return { ok: true }; },
    });
    renderApp(<Inbox />);
    const buttons = await screen.findAllByRole("button", { name: /download this/i });
    fireEvent.click(buttons[1]);
    await screen.findByText(/nothing needs your attention/i);
    expect(calls.find((c) => c.path === "/api/inbox/7/choose")?.body).toEqual({ candidate_id: 41 });
  });

  it("searches with a custom query, trimmed, and not when empty", async () => {
    const calls = mockApi({ "GET /api/inbox": { items: [item(7)] }, "GET /api/status": STATUS, "POST /api/inbox/7/search": { ok: true } });
    renderApp(<Inbox />);
    await screen.findByRole("heading", { name: /Blinding Lights/ });
    const input = screen.getByLabelText(/search again for/i);
    expect((screen.getByRole("button", { name: /^search$/i }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(input, { target: { value: "  weeknd blinding  " } });
    fireEvent.click(screen.getByRole("button", { name: /^search$/i }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/inbox/7/search")?.body).toEqual({ query: "weeknd blinding" }));
  });

  it("skip and search-again hit their endpoints", async () => {
    const calls = mockApi({ "GET /api/inbox": { items: [item(7)] }, "GET /api/status": STATUS,
      "POST /api/inbox/7/skip": { ok: true }, "POST /api/inbox/7/research": { ok: true } });
    renderApp(<Inbox />);
    await screen.findByRole("heading", { name: /Blinding Lights/ });
    fireEvent.click(screen.getByRole("button", { name: /search again$/i }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/inbox/7/research")).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/inbox/7/skip")).toBe(true));
  });

  it("shows the server's message when an action is refused", async () => {
    mockApi({ "GET /api/inbox": { items: [item(7)] }, "GET /api/status": STATUS,
      "POST /api/inbox/7/choose": { status: 400, body: { error: "not_awaiting", message: "item is pending" } } });
    renderApp(<Inbox />);
    fireEvent.click((await screen.findAllByRole("button", { name: /download this/i }))[0]);
    expect((await screen.findByRole("alert")).textContent).toContain("item is pending");
  });

  it("an item with no candidates says so, and a route id filters to that item", async () => {
    const empty = { ...item(8, "Lonely"), candidates: [] };
    mockApi({ "GET /api/inbox": { items: [item(7), empty] }, "GET /api/status": STATUS });
    renderApp(<Routes><Route path="/inbox/:id" element={<Inbox />} /></Routes>, { route: "/inbox/8" });
    await screen.findByRole("heading", { name: "The Weeknd – Lonely" });
    expect(screen.getByText(/no candidates were found/i)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /Blinding Lights/ })).toBeNull();
  });
});
