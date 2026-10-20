import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, errorText } from "../lib/api";
import { useAuth } from "../lib/auth";

type Mode = "account" | "token";

export default function Login() {
  const { login, loginWithToken, setup } = useAuth();
  // Unauthenticated: this endpoint is open and only says whether the first admin is still to be made.
  const state = useQuery({ queryKey: ["auth-state"], queryFn: () => api<{ needs_setup: boolean }>("GET", "/api/auth/state", undefined, ""), retry: false });
  const needsSetup = state.data?.needs_setup === true;
  const [mode, setMode] = useState<Mode>("account");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault(); setError("");
    if (needsSetup && password !== again) { setError("The two passwords differ."); return; }
    setBusy(true);
    try {
      if (needsSetup) await setup(username, password, token);
      else if (mode === "token") await loginWithToken(token);
      else await login(username, password);
    } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  }

  const tokenOnly = !needsSetup && mode === "token";
  return (
    <main className="shell" style={{ maxWidth: 440, paddingTop: "12vh" }}>
      <div className="wordmark" style={{ fontSize: 30 }}>Music<i>·</i>Grabber</div>
      <p className="ink2">{needsSetup ? "Welcome. Create the first account; it will be the administrator." : tokenOnly ? "Enter the API token to continue." : "Sign in to continue."}</p>
      <form onSubmit={submit} className="stack">
        {error && <div className="banner err" role="alert">{error}</div>}
        {state.isError && <div className="banner warn">Can't reach Music Grabber. Check that it is running.</div>}
        {!tokenOnly && <>
          <label className="field"><span>Username</span>
            <input autoComplete="username" autoCapitalize="off" spellCheck={false} value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required /></label>
          <label className="field"><span>Password</span>
            <input type="password" autoComplete={needsSetup ? "new-password" : "current-password"} minLength={needsSetup ? 10 : undefined} value={password} onChange={(e) => setPassword(e.target.value)} required />
            {needsSetup && <small className="muted">At least 10 characters.</small>}</label>
        </>}
        {needsSetup && <label className="field"><span>Repeat password</span>
          <input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required /></label>}
        {(needsSetup || tokenOnly) && <label className="field"><span>API token</span>
          <input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} autoFocus={tokenOnly} required />
          {needsSetup && <small className="muted">The server's <code>API_TOKEN</code>. It proves you own this installation.</small>}</label>}
        <button className="btn primary" disabled={busy || state.isLoading}>{busy ? "Checking…" : needsSetup ? "Create account" : "Sign in"}</button>
        {!needsSetup && (
          <button type="button" className="btn quiet sm" onClick={() => { setMode(tokenOnly ? "account" : "token"); setError(""); }}>
            {tokenOnly ? "Use my username and password" : "Use the API token instead"}
          </button>
        )}
        <p className="muted" style={{ fontSize: 13, margin: 0 }}>Kept for this browser tab only.</p>
      </form>
    </main>
  );
}
