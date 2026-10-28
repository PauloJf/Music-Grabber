// A thin fetch wrapper: bearer token from sessionStorage, typed errors, and a 401 hook so
// the app can drop back to the login page when the token is wrong or was rotated.

const KEY = "mg.token";

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) {
    super(message);
  }
}

export const tokenStore = {
  get(): string {
    try {
      return sessionStorage.getItem(KEY) ?? "";
    } catch {
      return "";
    }
  },
  set(token: string) {
    try {
      sessionStorage.setItem(KEY, token);
    } catch {
      /* storage blocked: the token then lives for this page load only */
    }
  },
  clear() {
    try {
      sessionStorage.removeItem(KEY);
    } catch {
      /* ignore */
    }
  },
};

type Listener = () => void;
const unauthorized = new Set<Listener>();
export function onUnauthorized(fn: Listener): () => void {
  unauthorized.add(fn);
  return () => unauthorized.delete(fn);
}

export async function api<T = unknown>(
  method: string,
  path: string,
  body?: unknown,
  token: string = tokenStore.get(),
): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(path, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("unreachable", "Cannot reach Music Grabber", 0);
  }
  let data: { error?: string; message?: string } & Record<string, unknown> = {};
  try {
    data = await resp.json();
  } catch {
    /* an empty or non-JSON body */
  }
  if (!resp.ok) {
    if (resp.status === 401) unauthorized.forEach((fn) => fn());
    throw new ApiError(data.error ?? `http_${resp.status}`, data.message ?? `HTTP ${resp.status}`, resp.status);
  }
  return data as T;
}

export const get = <T,>(path: string) => api<T>("GET", path);
export const post = <T,>(path: string, body: unknown = {}) => api<T>("POST", path, body);
export const put = <T,>(path: string, body: unknown) => api<T>("PUT", path, body);
export const patch = <T,>(path: string, body: unknown) => api<T>("PATCH", path, body);
export const del = <T,>(path: string) => api<T>("DELETE", path);

export const TERMINAL = ["done", "failed", "skipped"];
export const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));
