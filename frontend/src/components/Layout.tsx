import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useActivity, useMe, useStatus } from "../lib/hooks";
import { usePlayer } from "../lib/player";
import ActivityTray from "./ActivityTray";
import { Gear, Inbox, Library, Queue, Search, Spark } from "./icons";

const TABS = [
  { to: "/", label: "For you", icon: Spark, end: true },          // only while New releases and suggestions are on
  { to: "/discover", label: "Discover", icon: Search },
  { to: "/queue", label: "Queue", icon: Queue },
  { to: "/inbox", label: "Inbox", icon: Inbox },
  { to: "/playlists", label: "Library", icon: Library },
];

export default function Layout({ children }: { children: ReactNode }) {
  const me = useMe();
  const player = usePlayer();
  const user = me.data?.user;
  const isAdmin = !user || user.role === "admin";          // until we know, don't flash the tab away
  const status = useStatus();
  const forYou = !!status.data?.releases_enabled;
  const tabs = TABS.filter((t) => t.to !== "/" || forYou);
  const theme = user?.prefs.theme, volume = user?.prefs.preview_volume;
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme); else root.removeAttribute("data-theme");
  }, [theme]);
  useEffect(() => { player.setVolume(volume ?? 1); }, [volume, player.setVolume]);
  const { data: act } = useActivity();
  const nav = useNavigate();
  const loc = useLocation();
  const [q, setQ] = useState("");
  const pips: Record<string, number> = { "/queue": act?.active.length ?? 0, "/inbox": act?.inbox ?? 0 };
  const sp = status.data?.spotify;
  const submit = (e: FormEvent) => { e.preventDefault(); if (q.trim()) nav(`/discover?q=${encodeURIComponent(q.trim())}`); };

  return (
    <>
      <header className="topbar">
        <div className="topbar-in">
          <NavLink to="/" className="wordmark">Music<i>·</i>Grabber</NavLink>
          <nav className="navlinks" aria-label="Main">
            {tabs.map((t) => (
              <NavLink key={t.to} to={t.to} end={t.end}>{t.label}{pips[t.to] ? <span className="pill signal" style={{ marginLeft: 6 }}>{pips[t.to]}</span> : null}</NavLink>
            ))}
          </nav>
          <div className="grow" />
          {loc.pathname !== "/discover" && (
            <form className="search topsearch" role="search" onSubmit={submit}>
              <Search />
              <input type="search" data-search aria-label="Search Deezer" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} style={{ minHeight: 36 }} />
            </form>
          )}
          {isAdmin && <NavLink to="/settings" className="helplink" aria-label="Settings" title="Settings"><Gear width={18} height={18} /></NavLink>}
          <NavLink to="/account" className="acctlink" title="Account">{user?.username ?? "Account"}</NavLink>
          <NavLink to="/help" className="helplink" aria-label="Help" title="Help">?</NavLink>
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
        {tabs.map((t) => (
          <NavLink key={t.to} to={t.to} end={t.end}>
            <t.icon />{t.label}{pips[t.to] ? <span className="pip">{pips[t.to]}</span> : null}
          </NavLink>
        ))}
      </nav>
    </>
  );
}
