import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useLocation } from "react-router-dom";
import { ago, useActivity } from "../lib/hooks";
import { useToast } from "../lib/toast";
import { CloseButton, Pill } from "./ui";

const WORD: Record<string, string> = { pending: "Waiting", searching: "Searching", downloading: "Downloading", transcoding: "Converting", tagging: "Tagging", placing: "Filing" };

/** Always-on view of background work: a floating button with the running count, a sheet with
 *  progress, and a toast the moment a download finishes or fails. */
export default function ActivityTray() {
  const { data } = useActivity();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const seen = useRef<Map<number, { title: string }> | null>(null);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);

  useEffect(() => {
    if (!data) return;
    const now = new Map(data.active.map((a) => [a.id, { title: a.title }]));
    if (seen.current) {
      for (const [id, info] of seen.current) {
        if (now.has(id)) continue;
        const done = data.recent.find((r) => r.id === id);
        if (done?.status === "done") toast.push({ kind: "ok", text: `Downloaded: ${info.title}` });
        else if (done?.status === "failed") toast.push({ kind: "err", text: `Failed: ${info.title}`, action: { label: "Details", run: () => (window.location.href = "/queue") } });
      }
    }
    seen.current = now;
  }, [data, toast]);

  const active = data?.active ?? [];
  const slot = document.getElementById("tray-slot");
  if (!data || (active.length === 0 && !open) || !slot || loc.pathname === "/queue") return null;
  const lead = active[0];
  const pct = Math.round((lead?.progress || 0) * 100);
  return (
    <>
      {open && <div className="scrim" onClick={() => setOpen(false)} />}
      {open && (
        <div className="sheet" role="dialog" aria-label="Activity">
          <div className="row" style={{ justifyContent: "space-between" }}><h3>Activity</h3><CloseButton onClick={() => setOpen(false)} /></div>
          {active.length === 0 && <p className="muted">Nothing running.</p>}
          {active.map((a) => (
            <div key={a.id} style={{ padding: "8px 0", borderBottom: "1px solid var(--rule)" }}>
              <div className="row" style={{ justifyContent: "space-between", flexWrap: "nowrap" }}>
                <div className="clamp"><b>{a.title}</b> <span className="muted">{a.artist}</span></div>
                <span className="mono muted">{WORD[a.status] ?? a.status}</span>
              </div>
              <div className="bar" style={{ marginTop: 6 }}><i style={{ width: `${Math.round((a.progress || 0) * 100)}%` }} /></div>
            </div>
          ))}
          {data.recent.length > 0 && <div className="eyebrow" style={{ margin: "14px 0 4px" }}>Recently</div>}
          {data.recent.slice(0, 5).map((r) => (
            <div key={r.id} className="row" style={{ justifyContent: "space-between", flexWrap: "nowrap", padding: "4px 0" }}>
              <span className="clamp">{r.title} <span className="muted">{r.artist}</span></span>
              <span className="row tight" style={{ flexWrap: "nowrap" }}><Pill kind={r.status === "done" ? "ok" : r.status === "failed" ? "err" : "mute"}>{r.status}</Pill><span className="muted" style={{ fontSize: 12 }}>{ago(r.updated_at)}</span></span>
            </div>
          ))}
          <div className="row" style={{ marginTop: 14 }}>
            <Link className="btn sm" to="/queue">Open queue</Link>
            {data.inbox > 0 && <Link className="btn sm signal" to="/inbox">{data.inbox} need you</Link>}
          </div>
        </div>
      )}
      {active.length > 0 && !open && createPortal(
          <button className="traybtn" onClick={() => setOpen(true)} aria-label={`${active.length} downloading, open activity`}>
            <div className="ring" style={{ ["--p" as string]: pct, width: 28, height: 28 }}><span style={{ color: "var(--ink)" }}>{pct}</span></div>
            <span>{active.length === 1 ? lead.title : `${active.length} downloading`}</span>
          </button>, slot)}
    </>
  );
}
