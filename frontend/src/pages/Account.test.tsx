import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { ACTIVITY, STATUS, mockApi, renderApp } from "../test-utils";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); document.documentElement.removeAttribute("data-theme"); });
const user = (role: string, prefs = {}) => ({ user: { id: 1, username: "paulo", role, prefs, last_login: new Date().toISOString() }, via: "session" });
const base = { "GET /api/status": STATUS, "GET /api/activity": ACTIVITY };
const fill = (label: RegExp | string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

describe("account", () => {
  it("applies the saved theme and saves a new one", async () => {
    const calls = mockApi({ ...base, "GET /api/me": user("admin", { theme: "dark" }), "GET /api/users": { users: [] }, "PATCH /api/me/prefs": { ok: true, prefs: {} } });
    renderApp(<App />, { route: "/account" });
    await screen.findByRole("heading", { name: "paulo" });
    await waitFor(() => expect(document.documentElement.getAttribute("data-theme")).toBe("dark"));
    fireEvent.change(screen.getByLabelText("Theme"), { target: { value: "light" } });
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ theme: "light" }));
  });

  it("changes the password only when the two new ones match", async () => {
    const calls = mockApi({ ...base, "GET /api/me": user("user"), "POST /api/me/password": { ok: true } });
    renderApp(<App />, { route: "/account" });
    await screen.findByRole("heading", { name: "paulo" });
    fill(/^Current password/, "old password!!"); fill(/^New password/, "new password!!"); fill("Repeat new password", "typo password");
    fireEvent.click(screen.getByRole("button", { name: /change password/i }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/differ/);
    expect(calls.some((c) => c.path === "/api/me/password")).toBe(false);
    fill("Repeat new password", "new password!!");
    fireEvent.click(screen.getByRole("button", { name: /change password/i }));
    await screen.findByText(/other devices were signed out/i);
    expect(calls.find((c) => c.path === "/api/me/password")?.body).toEqual({ current: "old password!!", new: "new password!!" });
  });

  it("regular users get no People section and no Settings tab", async () => {
    mockApi({ ...base, "GET /api/me": user("user") });
    renderApp(<App />, { route: "/account" });
    await screen.findByRole("heading", { name: "paulo" });
    expect(screen.queryByText("People")).toBeNull();
    await waitFor(() => expect(screen.queryAllByRole("link", { name: "Settings" })).toHaveLength(0));
  });

  it("admins manage people: add and list", async () => {
    const users = [{ id: 1, username: "paulo", role: "admin", prefs: {} }, { id: 2, username: "bob", role: "user", prefs: {}, last_login: null }];
    const calls = mockApi({ ...base, "GET /api/me": user("admin"), "GET /api/users": { users }, "POST /api/users": { user: users[1] } });
    renderApp(<App />, { route: "/account" });
    await screen.findByText("bob");
    expect(screen.getByRole("button", { name: "Make admin" })).toBeTruthy();       // only for the other person
    fill("Username", "carol"); fill(/^Temporary password/, "temporary-pass");
    fireEvent.click(screen.getByRole("button", { name: /add person/i }));
    await screen.findByText("Account created");
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/users")?.body).toEqual({ username: "carol", password: "temporary-pass", role: "user" });
  });

  it("the API token session explains it has no profile", async () => {
    mockApi({ ...base, "GET /api/me": { user: { id: null, username: "api-token", role: "admin", prefs: {} }, via: "token" }, "GET /api/users": { users: [] } });
    renderApp(<App />, { route: "/account" });
    await screen.findByText(/no personal profile/i);
    expect(screen.queryByLabelText("Theme")).toBeNull();
  });
});
