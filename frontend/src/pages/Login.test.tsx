import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { tokenStore } from "../lib/api";
import { ACTIVITY, STATUS, mockApi, renderApp } from "../test-utils";

afterEach(() => vi.unstubAllGlobals());

describe("login", () => {
  it("rejects a wrong token without storing it", async () => {
    mockApi({ "GET /api/status": { status: 401, body: { error: "unauthorized", message: "send the token" } } });
    renderApp(<App />, { token: "" });
    fireEvent.change(screen.getByLabelText(/api token/i), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await screen.findByRole("alert");
    expect(tokenStore.get()).toBe("");
  });

  it("stores a good token and lands on Discover", async () => {
    mockApi({ "GET /api/status": STATUS, "GET /api/activity": ACTIVITY });
    renderApp(<App />, { token: "" });
    fireEvent.change(screen.getByLabelText(/api token/i), { target: { value: "  good-token  " } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await screen.findByRole("searchbox", { name: /search deezer/i });
    expect(tokenStore.get()).toBe("good-token");
  });

  it("returns to the login when the token stops working", async () => {
    const no = { status: 401, body: { error: "unauthorized", message: "x" } };
    mockApi({ "GET /api/status": no, "GET /api/activity": no });
    renderApp(<App />);
    await waitFor(() => expect(screen.getByLabelText(/api token/i)).toBeTruthy());
    expect(tokenStore.get()).toBe("");
  });
});
