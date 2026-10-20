import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Queue from "./Queue";
import { mockApi, renderApp } from "../test-utils";

afterEach(() => vi.unstubAllGlobals());

const it_ = (id: number, status: string, extra: object = {}) => ({
  id, status, title: `Song ${id}`, artist: "Band", album: "", duration_ms: 1000, isrc: null, provider: "deezer",
  progress: 0.4, priority: 0, note: null, error: null, final_path: null, updated_at: null, ...extra,
});

const LIST = { items: [it_(1, "downloading"), it_(2, "failed", { error: "all downloads failed" }), it_(3, "awaiting_user"), it_(4, "pending")] };
const HISTORY = { items: [it_(5, "done", { final_path: "/music/a.mp3" }), it_(2, "failed")] };

describe("queue", () => {
  it("active shows in-flight work only; awaiting_user belongs to the inbox", async () => {
    mockApi({ "GET /api/queue": LIST, "GET /api/queue/history": HISTORY });
    renderApp(<Queue />);
    await screen.findByText("Band – Song 1");
    expect(screen.getByText("Band – Song 4")).toBeTruthy();
    expect(screen.queryByText("Band – Song 3")).toBeNull();
    expect(screen.queryByText("Band – Song 2")).toBeNull();
    expect(screen.getAllByRole("progressbar")).toHaveLength(2);
  });

  it("the failed tab can retry one or all", async () => {
    const calls = mockApi({ "GET /api/queue": LIST, "GET /api/queue/history": HISTORY,
      "POST /api/queue/2/retry": { ok: true }, "POST /api/queue/retry-failed": { ok: true, requeued: 1 } });
    renderApp(<Queue />);
    fireEvent.click(await screen.findByRole("tab", { name: /failed \(1\)/i }));
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/queue/2/retry")).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: /retry all failed/i }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/queue/retry-failed")).toBe(true));
  });

  it("cancel and priority act on an in-flight item", async () => {
    const calls = mockApi({ "GET /api/queue": LIST, "GET /api/queue/history": HISTORY,
      "POST /api/queue/1/cancel": { ok: true }, "POST /api/queue/1/priority": { ok: true } });
    renderApp(<Queue />);
    await screen.findByText("Band – Song 1");
    fireEvent.click(screen.getAllByRole("button", { name: "Cancel" })[0]);
    await waitFor(() => expect(calls.some((c) => c.path === "/api/queue/1/cancel")).toBe(true));
    fireEvent.click(screen.getAllByTitle("Move to the front")[0]);
    await waitFor(() => expect(calls.find((c) => c.path === "/api/queue/1/priority")?.body).toEqual({ priority: 1 }));
  });

  it("history can remove an item and clear all completed", async () => {
    const calls = mockApi({ "GET /api/queue": LIST, "GET /api/queue/history": HISTORY,
      "DELETE /api/queue/5": { ok: true }, "POST /api/queue/clear-completed": { ok: true, removed: 1 } });
    renderApp(<Queue />);
    fireEvent.click(await screen.findByRole("tab", { name: "History" }));
    await screen.findByText("Band – Song 5");
    fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[0]);
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/queue/5")).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: /clear completed/i }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/queue/clear-completed")).toBe(true));
  });

  it("expanding a row loads its events and error", async () => {
    mockApi({ "GET /api/queue": LIST, "GET /api/queue/history": HISTORY,
      "GET /api/queue/2": { item: it_(2, "failed", { error: "all downloads failed — deezer: boom" }), candidates: [],
        events: [{ id: 1, event: "searching", detail: "", created_at: "2026-01-01T10:00:00" }, { id: 2, event: "failed", detail: "boom", created_at: "2026-01-01T10:00:09" }] } });
    renderApp(<Queue />);
    fireEvent.click(await screen.findByRole("tab", { name: /failed/i }));
    fireEvent.click(await screen.findByRole("button", { name: "Band – Song 2" }));
    await screen.findByText(/all downloads failed — deezer: boom/);
    expect(screen.getByText(/failed · boom/)).toBeTruthy();
  });

  it("an empty queue says so and a refused action shows the server's reason", async () => {
    mockApi({ "GET /api/queue": { items: [] }, "GET /api/queue/history": { items: [] } });
    renderApp(<Queue />);
    await screen.findByText(/nothing is downloading/i);
  });

  it("shows the server's message when an action is refused", async () => {
    mockApi({ "GET /api/queue": LIST, "GET /api/queue/history": HISTORY,
      "POST /api/queue/1/cancel": { status: 400, body: { error: "already_terminal", message: "item is done" } } });
    renderApp(<Queue />);
    await screen.findByText("Band – Song 1");
    fireEvent.click(screen.getAllByRole("button", { name: "Cancel" })[0]);
    expect((await screen.findByRole("alert")).textContent).toContain("item is done");
  });
});
