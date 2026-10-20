import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { tokenStore } from "../lib/api";
import { STATUS, mockApi, renderApp } from "../test-utils";

afterEach(() => vi.unstubAllGlobals());

describe("login", () => {
  it("asks for the token when none is stored", () => {
    mockApi({});
    renderApp(<App />, { token: "" });
    expect(screen.getByLabelText(/api token/i)).toBeTruthy();
  });

  it("rejects a wrong token without storing it", async () => {
    mockApi({ "GET /api/status": { status: 401, body: { error: "unauthorized", message: "send 'Authorization: Bearer <API_TOKEN>'" } } });
    renderApp(<App />, { token: "" });
    fireEvent.change(screen.getByLabelText(/api token/i), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await screen.findByRole("alert");
    expect(tokenStore.get()).toBe("");
  });

  it("stores a good token and shows the app", async () => {
    mockApi({ "GET /api/status": STATUS, "GET /api/inbox": { items: [] } });
    renderApp(<App />, { token: "" });
    fireEvent.change(screen.getByLabelText(/api token/i), { target: { value: "  good-token  " } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await screen.findByText(/nothing needs your attention/i);
    expect(tokenStore.get()).toBe("good-token");
  });

  it("returns to the login when a request is refused later (token rotated)", async () => {
    mockApi({ "GET /api/status": { status: 401, body: { error: "unauthorized", message: "x" } }, "GET /api/inbox": { status: 401, body: { error: "unauthorized", message: "x" } } });
    renderApp(<App />);
    await waitFor(() => expect(screen.getByLabelText(/api token/i)).toBeTruthy());
    expect(tokenStore.get()).toBe("");
  });
});
