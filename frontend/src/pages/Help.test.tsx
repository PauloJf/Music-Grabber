import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import Help from "./Help";
import { ACTIVITY, STATUS, mockApi, renderApp } from "../test-utils";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("help", () => {
  it("explains the app, with an in-page index for every section", () => {
    mockApi({ "GET /api/activity": ACTIVITY, "GET /api/status": STATUS });
    renderApp(<Help />);
    expect(screen.getByRole("heading", { name: "How Music Grabber works" })).toBeTruthy();
    for (const h of ["Getting started", "Finding and queueing music", "What the badges mean", "Queue and Inbox",
      "Watching Spotify playlists", "Settings", "When something goes wrong", "Keeping it safe"]) {
      expect(screen.getByRole("heading", { name: h })).toBeTruthy();
      const link = screen.getAllByRole("link", { name: h }).find((a) => a.getAttribute("href")?.startsWith("#"));
      expect(link).toBeTruthy();
    }
    expect(screen.getByText(/There are no user accounts/)).toBeTruthy();
    fireEvent.click(screen.getByRole("link", { name: "Getting started" }));
  });

  it("is reachable from the header", () => {
    mockApi({ "GET /api/activity": ACTIVITY, "GET /api/status": STATUS });
    renderApp(<App />, { route: "/help" });
    expect(screen.getByRole("link", { name: "Help" }).getAttribute("href")).toBe("/help");
    expect(screen.getByRole("heading", { name: "How Music Grabber works" })).toBeTruthy();
  });
});
