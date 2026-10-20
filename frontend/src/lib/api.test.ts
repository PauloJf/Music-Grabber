import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, onUnauthorized, tokenStore } from "./api";
import { mockApi } from "../test-utils";

afterEach(() => vi.unstubAllGlobals());

describe("api", () => {
  it("sends the bearer token and a JSON body", async () => {
    tokenStore.set("secret-token-value");
    const calls = mockApi({ "POST /api/queue": { ok: true, id: 3 } });
    expect(await api("POST", "/api/queue", { title: "x" })).toEqual({ ok: true, id: 3 });
    expect(calls[0]).toMatchObject({ method: "POST", auth: "Bearer secret-token-value", body: { title: "x" } });
  });

  it("turns an error reply into an ApiError with the server's code and message", async () => {
    mockApi({ "GET /api/x": { status: 409, body: { error: "already_queued", message: "already queued" } } });
    await expect(api("GET", "/api/x")).rejects.toMatchObject({ code: "already_queued", message: "already queued", status: 409 });
  });

  it("notifies listeners on a 401 so the app can return to the login", async () => {
    mockApi({ "GET /api/status": { status: 401, body: { error: "unauthorized", message: "no" } } });
    const fn = vi.fn();
    const off = onUnauthorized(fn);
    await expect(api("GET", "/api/status")).rejects.toBeInstanceOf(ApiError);
    expect(fn).toHaveBeenCalledTimes(1);
    off();
  });

  it("reports an unreachable server clearly", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network"); }));
    await expect(api("GET", "/api/status")).rejects.toMatchObject({ code: "unreachable", status: 0 });
  });

  it("survives blocked storage", () => {
    const real = Storage.prototype.getItem;
    Storage.prototype.getItem = () => { throw new Error("blocked"); };
    expect(tokenStore.get()).toBe("");
    Storage.prototype.getItem = real;
  });
});
