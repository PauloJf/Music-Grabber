import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { tokenStore } from "../lib/api";
import { ACTIVITY, STATUS, mockApi, renderApp } from "../test-utils";

afterEach(() => vi.unstubAllGlobals());
const ME = { user: { id: 1, username: "paulo", role: "admin", prefs: {} }, via: "session" };
const fill = (label: RegExp | string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const bad = { status: 401, body: { error: "bad_credentials", message: "wrong username or password" } };

describe("login", () => {
  it("rejects a wrong password without storing anything", async () => {
    mockApi({ "GET /api/auth/state": { needs_setup: false }, "POST /api/auth/login": bad });
    renderApp(<App />, { token: "" });
    await screen.findByText(/sign in to continue/i);
    fill("Username", "paulo"); fill("Password", "nope");
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/wrong username or password/);
    expect(tokenStore.get()).toBe("");
  });

  it("signs in with username and password and lands on Discover", async () => {
    const calls = mockApi({ "GET /api/auth/state": { needs_setup: false }, "POST /api/auth/login": { token: "sess-123", user: ME.user },
      "GET /api/status": STATUS, "GET /api/activity": ACTIVITY, "GET /api/me": ME });
    renderApp(<App />, { token: "" });
    await screen.findByText(/sign in to continue/i);
    fill("Username", "  paulo "); fill("Password", "a long password");
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));
    await screen.findByRole("searchbox", { name: /search deezer/i });
    expect(tokenStore.get()).toBe("sess-123");
    expect(calls.find((c) => c.path === "/api/auth/login")?.body).toEqual({ username: "paulo", password: "a long password" });
  });

  it("can fall back to the API token", async () => {
    mockApi({ "GET /api/auth/state": { needs_setup: false }, "GET /api/status": STATUS, "GET /api/activity": ACTIVITY });
    renderApp(<App />, { token: "" });
    await screen.findByText(/sign in to continue/i);
    fireEvent.click(screen.getByRole("button", { name: /api token instead/i }));
    fill(/api token/i, "  good-token  ");
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));
    await screen.findByRole("searchbox", { name: /search deezer/i });
    expect(tokenStore.get()).toBe("good-token");
  });

  it("first run: creates the admin with the API token, checking the passwords match", async () => {
    const calls = mockApi({ "GET /api/auth/state": { needs_setup: true }, "POST /api/auth/setup": { token: "sess-1", user: ME.user },
      "GET /api/status": STATUS, "GET /api/activity": ACTIVITY, "GET /api/me": ME });
    renderApp(<App />, { token: "" });
    await screen.findByText(/create the first account/i);
    fill("Username", "paulo"); fill(/^Password/, "a long password"); fill("Repeat password", "different one!"); fill(/api token/i, "tok");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/differ/);
    expect(calls.some((c) => c.path === "/api/auth/setup")).toBe(false);
    fill("Repeat password", "a long password");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await screen.findByRole("searchbox", { name: /search deezer/i });
    expect(calls.find((c) => c.path === "/api/auth/setup")?.body).toEqual({ username: "paulo", password: "a long password", api_token: "tok" });
  });

  it("returns to the login when the session stops working", async () => {
    const no = { status: 401, body: { error: "unauthorized", message: "x" } };
    mockApi({ "GET /api/status": no, "GET /api/activity": no, "GET /api/me": no, "GET /api/auth/state": { needs_setup: false } });
    renderApp(<App />);
    await waitFor(() => expect(screen.getByLabelText("Username")).toBeTruthy());
    expect(tokenStore.get()).toBe("");
  });
});
