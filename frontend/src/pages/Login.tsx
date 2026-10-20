import { useState, type FormEvent } from "react";
import { errorText } from "../lib/api";
import { useAuth } from "../lib/auth";

export default function Login() {
  const { login } = useAuth();
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try { await login(token); } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  }
  return (
    <main className="shell" style={{ maxWidth: 440, paddingTop: "14vh" }}>
      <div className="wordmark" style={{ fontSize: 30 }}>Music<i>·</i>Grabber</div>
      <p className="ink2">Enter the API token to continue.</p>
      <form onSubmit={submit} className="stack">
        {error && <div className="banner err" role="alert">{error}</div>}
        <label className="field"><span>API token</span>
          <input type="password" autoComplete="current-password" value={token} onChange={(e) => setToken(e.target.value)} autoFocus required /></label>
        <button className="btn primary" disabled={busy || !token.trim()}>{busy ? "Checking…" : "Sign in"}</button>
        <p className="muted" style={{ fontSize: 13, margin: 0 }}>Kept for this browser tab only.</p>
      </form>
    </main>
  );
}
