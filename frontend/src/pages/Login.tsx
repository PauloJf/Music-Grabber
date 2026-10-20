import { useState, type FormEvent } from "react";
import { errorText } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Flash } from "../components/bits";

export default function Login() {
  const { login } = useAuth();
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(token);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="container">
      <form className="card login" onSubmit={submit}>
        <h1 style={{ marginBottom: 6 }}>Music Grabber</h1>
        <p className="muted" style={{ marginTop: 0 }}>Enter the API token (<code>API_TOKEN</code>).</p>
        {error && <Flash kind="error">{error}</Flash>}
        <label className="field">
          <span className="eyebrow">API token</span>
          <input type="password" autoComplete="current-password" value={token}
            onChange={(e) => setToken(e.target.value)} autoFocus required />
        </label>
        <div style={{ marginTop: 14 }}>
          <button className="btn btn-primary" disabled={busy || !token.trim()}>{busy ? "Checking…" : "Sign in"}</button>
        </div>
        <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>Kept for this browser tab only.</p>
      </form>
    </div>
  );
}
