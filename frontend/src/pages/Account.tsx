import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { Banner, ErrorState, Pill, Skeleton } from "../components/ui";
import { del, errorText, get, patch, post } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago, useMe } from "../lib/hooks";
import { useToast } from "../lib/toast";
import type { User } from "../lib/types";

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return <section style={{ margin: "30px 0", maxWidth: 560 }}><h2>{title}</h2>{hint && <p className="muted" style={{ margin: "2px 0 12px" }}>{hint}</p>}<div className="stack" style={{ marginTop: 12 }}>{children}</div></section>;
}

function Preferences({ user }: { user: User }) {
  const qc = useQueryClient();
  const toast = useToast();
  const save = useMutation({
    mutationFn: (c: Record<string, unknown>) => patch("/api/me/prefs", c),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me"] }),
    onError: (e) => toast.push({ kind: "err", text: errorText(e) }),
  });
  const vol = Math.round((user.prefs.preview_volume ?? 1) * 100);
  return (
    <Section title="Display and sound" hint="Saved on your account, so they follow you to other devices.">
      <label className="field"><span>Theme</span>
        <select value={user.prefs.theme ?? "system"} onChange={(e) => save.mutate({ theme: e.target.value })}>
          <option value="system">Match my device</option><option value="light">Light</option><option value="dark">Dark</option>
        </select></label>
      <label className="field"><span>Preview volume · {vol}%</span>
        <input type="range" min={0} max={100} step={5} defaultValue={vol} onMouseUp={(e) => save.mutate({ preview_volume: Number(e.currentTarget.value) / 100 })}
          onKeyUp={(e) => save.mutate({ preview_volume: Number(e.currentTarget.value) / 100 })} onTouchEnd={(e) => save.mutate({ preview_volume: Number(e.currentTarget.value) / 100 })} /></label>
    </Section>
  );
}

function ChangePassword() {
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const change = useMutation({
    mutationFn: () => post("/api/me/password", { current, new: next }),
    onSuccess: () => { setCurrent(""); setNext(""); setAgain(""); toast.push({ kind: "ok", text: "Password changed. Your other devices were signed out." }); },
    onError: (e) => setError(errorText(e)),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault(); setError("");
    if (next !== again) { setError("The two new passwords differ."); return; }
    change.mutate();
  };
  return (
    <Section title="Password">
      <form className="stack" onSubmit={submit}>
        {error && <div className="banner err" role="alert">{error}</div>}
        <label className="field"><span>Current password</span><input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required /></label>
        <label className="field"><span>New password</span><input type="password" autoComplete="new-password" minLength={10} value={next} onChange={(e) => setNext(e.target.value)} required /><small className="muted">At least 10 characters.</small></label>
        <label className="field"><span>Repeat new password</span><input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required /></label>
        <div><button className="btn" disabled={change.isPending}>{change.isPending ? "Changing…" : "Change password"}</button></div>
      </form>
    </Section>
  );
}

function Users({ meId }: { meId: number | null }) {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["users"], queryFn: () => get<{ users: User[] }>("/api/users") });
  const [form, setForm] = useState({ username: "", password: "", role: "user" });
  const [error, setError] = useState("");
  const done = (text: string) => () => { toast.push({ kind: "ok", text }); qc.invalidateQueries({ queryKey: ["users"] }); };
  const fail = (e: unknown) => toast.push({ kind: "err", text: errorText(e) });
  const add = useMutation({ mutationFn: () => post("/api/users", form), onError: (e) => setError(errorText(e)),
    onSuccess: () => { setForm({ username: "", password: "", role: "user" }); setError(""); done("Account created")(); } });
  const update = useMutation({ mutationFn: ({ id, changes }: { id: number; changes: object }) => patch(`/api/users/${id}`, changes), onSuccess: done("Updated"), onError: fail });
  const remove = useMutation({ mutationFn: (id: number) => del(`/api/users/${id}`), onSuccess: done("Account deleted"), onError: fail });
  const reset = (u: User) => {
    const pw = window.prompt(`New password for ${u.username} (at least 10 characters). They will be signed out everywhere.`);
    if (pw) update.mutate({ id: u.id as number, changes: { password: pw } });
  };
  return (
    <Section title="People" hint="Everyone here can browse and queue music. Administrators can also change settings and manage accounts.">
      {q.isLoading && <Skeleton rows={2} />}
      {q.isError && <ErrorState error={q.error} retry={() => q.refetch()} />}
      <ul className="stack" style={{ listStyle: "none", padding: 0, margin: 0, gap: 10 }}>
        {(q.data?.users ?? []).map((u) => (
          <li key={u.id} className="row" style={{ justifyContent: "space-between", borderBottom: "1px solid var(--rule)", paddingBottom: 10 }}>
            <div><b>{u.username}</b> {u.id === meId && <span className="muted">(you)</span>}<br />
              <span className="muted" style={{ fontSize: 13 }}>{u.last_login ? `last sign-in ${ago(u.last_login)}` : "never signed in"}</span></div>
            <div className="row tight">
              <Pill kind={u.role === "admin" ? "signal" : "mute"}>{u.role}</Pill>
              {u.id !== meId && <>
                <button className="btn sm" onClick={() => update.mutate({ id: u.id as number, changes: { role: u.role === "admin" ? "user" : "admin" } })}>{u.role === "admin" ? "Make user" : "Make admin"}</button>
                <button className="btn sm" onClick={() => reset(u)}>Reset password</button>
                <button className="btn sm" onClick={() => { if (window.confirm(`Delete ${u.username}? Their sign-ins stop working.`)) remove.mutate(u.id as number); }}>Delete</button>
              </>}
            </div>
          </li>
        ))}
      </ul>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); setError(""); add.mutate(); }} aria-label="Add a person">
        <h3 style={{ margin: "10px 0 0" }}>Add a person</h3>
        {error && <div className="banner err" role="alert">{error}</div>}
        <label className="field"><span>Username</span><input autoComplete="off" autoCapitalize="off" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required /></label>
        <label className="field"><span>Temporary password</span><input type="password" autoComplete="new-password" minLength={10} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required /></label>
        <label className="field"><span>Role</span><select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}><option value="user">User</option><option value="admin">Administrator</option></select></label>
        <div><button className="btn primary" disabled={add.isPending}>{add.isPending ? "Adding…" : "Add person"}</button></div>
      </form>
    </Section>
  );
}

export default function Account() {
  const { logout } = useAuth();
  const me = useMe();
  if (me.isLoading) return <Skeleton rows={3} />;
  if (me.isError || !me.data) return <ErrorState error={me.error} retry={() => me.refetch()} />;
  const { user, via } = me.data;
  return (
    <>
      <section className="hero" style={{ paddingBottom: 0 }}>
        <div className="eyebrow">Account</div><h1>{via === "token" ? "API token session" : user.username}</h1>
        <p>{user.role === "admin" ? "Administrator" : "User"}{user.last_login ? ` · last sign-in ${ago(user.last_login)}` : ""}</p>
      </section>
      {via === "token"
        ? <div style={{ maxWidth: 560, marginTop: 16 }}><Banner kind="warn">You signed in with the shared API token, which has no personal profile. Sign out and sign in with an account to set preferences or a password.</Banner></div>
        : <><Preferences user={user} /><ChangePassword /></>}
      {user.role === "admin" && <Users meId={user.id} />}
      <div style={{ margin: "10px 0 70px" }}><button className="btn" onClick={logout}>Sign out</button></div>
    </>
  );
}
