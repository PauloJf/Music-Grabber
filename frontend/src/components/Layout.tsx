import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { get } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { InboxItem, Status } from "../lib/types";
import SpotifyConnect from "./SpotifyConnect";

export default function Layout({ children }: { children: ReactNode }) {
  const { logout } = useAuth();
  const status = useQuery({ queryKey: ["status"], queryFn: () => get<Status>("/api/status"), refetchInterval: 5000 });
  const inbox = useQuery({
    queryKey: ["inbox"],
    queryFn: () => get<{ items: InboxItem[] }>("/api/inbox"),
    refetchInterval: 10000,
  });
  const waiting = inbox.data?.items.length ?? 0;
  const q = status.data?.queue ?? {};
  const active = Object.entries(q).filter(([s]) => !["done", "failed", "skipped", "awaiting_user"].includes(s))
    .reduce((n, [, c]) => n + c, 0);

  return (
    <div className="container">
      <header className="header">
        <span className="brand">Music Grabber</span>
        <nav className="nav" aria-label="Main">
          <NavLink to="/inbox">Inbox{waiting > 0 && <span className="chip chip--missing" style={{ marginLeft: 6 }}>{waiting}</span>}</NavLink>
          <NavLink to="/queue">Queue{active > 0 && <span className="chip chip--queued" style={{ marginLeft: 6 }}>{active}</span>}</NavLink>
          <NavLink to="/playlists">Playlists</NavLink>
        </nav>
        <span className="spacer" />
        {status.data?.dry_run && <span className="badge badge--warn" title="Searches and scores, never downloads">dry run</span>}
        {status.data && <SpotifyConnect status={status.data} />}
        <button className="btn btn-ghost btn-sm" onClick={logout}>Sign out</button>
      </header>
      {status.isError && <div className="flash error" role="alert">Cannot reach Music Grabber.</div>}
      {children}
      {status.data && (
        <p className="muted mono" style={{ marginTop: 28, fontSize: 12 }}>
          v{status.data.version} · library {status.data.library.tracks} tracks via {status.data.library.index} ·
          analysis {status.data.analysis} · providers {status.data.providers.join(", ") || "none"}
        </p>
      )}
    </div>
  );
}
