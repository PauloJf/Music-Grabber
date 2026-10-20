import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, onUnauthorized, tokenStore } from "./api";

interface Auth {
  authed: boolean;
  login: (token: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState(() => tokenStore.get() !== "");

  const logout = useCallback(() => {
    tokenStore.clear();
    setAuthed(false);
  }, []);

  // A 401 anywhere (token rotated, server restarted with another one) returns to the login.
  useEffect(() => onUnauthorized(logout), [logout]);

  const login = useCallback(async (token: string) => {
    await api("GET", "/api/status", undefined, token.trim()); // throws ApiError on a bad token
    tokenStore.set(token.trim());
    setAuthed(true);
  }, []);

  const value = useMemo(() => ({ authed, login, logout }), [authed, login, logout]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): Auth {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
