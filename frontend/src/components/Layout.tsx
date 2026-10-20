import { useState, type FormEvent, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useActivity, useStatus } from "../lib/hooks";
import ActivityTray from "./ActivityTray";
import { Gear, Inbox, Library, Queue, Search } from "./icons";

const TABS = [
  { to: "/", label: "Discover", icon: Search, end: true },
  { to: "/queue", label: "Queue", icon: Queue },
  { to: "/inbox", label: "Inbox", icon: Inbox },
  { to: "/playlists", label: "Library", icon: Library },
  { to: "/settings", label: "Settings", icon: Gear },
];

export default function Layout({ children }: { children: ReactNode }) {
  const status = useStatus();
  const { data: act } = useActivity();
  const nav = useNavigate();
  const loc = useLocation();
  const [q, setQ] = useState("");
  const pips: Record<string, number> = { "/queue": act?.active.length ?? 0, "/inbox": act?.inbox ?? 0 };
  const sp = status.data?.spotify;
  const submit = (e: FormEvent) => { e.preventDefault(); if (q.trim()) nav(`/?q=${encodeURIComponent(q.trim())}`); };

  return (
    <>
      <header className="topbar">
        <div className="topbar-in">
          <NavLink to="/" className="wordmark">Music<i>·</i>Grabber</NavLink>
          <nav className="navlinks" aria-label="Main">
            {TABS.map((t) => (
              <NavLink key={t.to} to={t.to} end={t.end}>{t.label}{pips[t.to] ? <span className="pill signal" style={{ marginLeft: 6 }}>{pips[t.to]}</span> : null}</NavLink>
            ))}
          </nav>
          <div className="grow" />
          {loc.pathname !== "/" && (
            <form className="search topsearch" role="search" onSubmit={submit}>
              <Search />
              <input type="search" data-search aria-label="Search Deezer" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} style={{ minHeight: 36 }} />
            </form>
          )}
          <div id="tray-slot" className="tray" />
        </div>
      </header>
      <main className="shell">
        {sp && !sp.configured && <div className="banner warn" style={{ marginTop: 14 }}>Spotify isn't configured. You can still browse Deezer and queue downloads; playlist watching needs Spotify (see Settings).</div>}
        {status.isError && <div className="banner err" role="alert" style={{ marginTop: 14 }}>Can't reach Music Grabber. Retrying…</div>}
        {status.data?.dry_run && <div className="banner warn" style={{ marginTop: 14 }}><b>Dry run is on.</b> Searches and scoring happen, but nothing is downloaded. Turn it off in Settings.</div>}
        {children}
      </main>
      <ActivityTray />
      <nav className="tabbar" aria-label="Main">
        {TABS.map((t) => (
          <NavLink key={t.to} to={t.to} end={t.end}>
            <t.icon />{t.label}{pips[t.to] ? <span className="pip">{pips[t.to]}</span> : null}
          </NavLink>
        ))}
      </nav>
    </>
  );
}
