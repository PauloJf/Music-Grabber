import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, onUnauthorized, tokenStore } from "./api";

interface Auth {
  authed: boolean;
  /** Username + password. */
  login: (username: string, password: string) => Promise<void>;
  /** The shared API token instead of an account. */
  loginWithToken: (token: string) => Promise<void>;
  /** First run: create the first admin (needs the API token), then sign in as it. */
  setup: (username: string, password: string, apiToken: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [authed, setAuthed] = useState(() => tokenStore.get() !== "");

  const drop = useCallback(() => {
    tokenStore.clear();
    qc.clear();                      // nothing of the last user's data may linger for the next
    setAuthed(false);
  }, [qc]);

  const logout = useCallback(() => {
    // Revoke the session server-side too (best effort: we're leaving either way).
    if (tokenStore.get()) api("POST", "/api/auth/logout", {}).catch(() => undefined);
    drop();
  }, [drop]);

  // A 401 anywhere (session expired or revoked, token rotated) returns to the login.
  useEffect(() => onUnauthorized(drop), [drop]);

  const enter = useCallback((token: string) => { tokenStore.set(token); setAuthed(true); }, []);
  const login = useCallback(async (username: string, password: string) => {
    const r = await api<{ token: string }>("POST", "/api/auth/login", { username: username.trim(), password }, "");
    enter(r.token);
  }, [enter]);
  const loginWithToken = useCallback(async (token: string) => {
    await api("GET", "/api/status", undefined, token.trim()); // throws ApiError on a bad token
    enter(token.trim());
  }, [enter]);
  const setup = useCallback(async (username: string, password: string, apiToken: string) => {
    const r = await api<{ token: string }>("POST", "/api/auth/setup", { username: username.trim(), password, api_token: apiToken.trim() }, "");
    enter(r.token);
  }, [enter]);

  const value = useMemo(() => ({ authed, login, loginWithToken, setup, logout }), [authed, login, loginWithToken, setup, logout]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): Auth {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
