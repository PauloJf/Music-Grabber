import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { errorText, post } from "./api";
import { useRefreshJobs } from "./hooks";
import { useToast } from "./toast";
import type { CatTrack, QueueResult } from "./types";

// Queueing from any page. Rows flip to "Queuing…" the instant you press the button (before the
// server answers), a toast confirms with Undo, and the lists refresh so the real state takes over.
interface Q {
  pending: Set<string>;
  queueTracks: (tracks: CatTrack[], what?: string) => Promise<QueueResult | null>;
  queueAlbum: (albumId: string, title: string) => Promise<QueueResult | null>;
}
const Ctx = createContext<Q | null>(null);

export function QueueingProvider({ children }: { children: ReactNode }) {
  const toast = useToast();
  const refresh = useRefreshJobs();
  const qc = useQueryClient();
  const [pending, setPending] = useState<Set<string>>(new Set());

  const mark = useCallback((ids: string[], on: boolean) => setPending((cur) => {
    const n = new Set(cur);
    ids.forEach((i) => (on ? n.add(i) : n.delete(i)));
    return n;
  }), []);

  const finish = useCallback(async (r: QueueResult, what: string) => {
    const inLib = r.results.filter((x) => x.result === "in_library").length;
    const dup = r.results.filter((x) => x.result === "already_queued").length;
    if (r.queued > 0) {
      toast.push({
        kind: "ok", text: `Queued ${r.queued === 1 ? what : `${r.queued} tracks`}${inLib || dup ? ` · ${inLib + dup} skipped` : ""}`,
        action: { label: "Undo", run: () => { post("/api/browse/undo", { item_ids: r.item_ids }).then(() => { refresh(); toast.push({ kind: "info", text: "Removed from the queue" }); }).catch((e) => toast.push({ kind: "err", text: errorText(e) })); } },
      });
    } else {
      toast.push({ kind: "info", text: inLib ? "Already in your library" : dup ? "Already queued" : "Nothing to queue" });
    }
  }, [toast, refresh]);

  const run = useCallback(async (ids: string[], what: string, req: () => Promise<QueueResult>) => {
    mark(ids, true);
    try {
      const r = await req();
      await finish(r, what);
      await Promise.all(["activity", "queue", "inbox", "status", "browse", "playlist", "playlists"].map((k) => qc.invalidateQueries({ queryKey: [k] })));
      return r;
    } catch (e) {
      toast.push({ kind: "err", text: `Couldn't queue: ${errorText(e)}` });
      return null;
    } finally {
      mark(ids, false);
    }
  }, [mark, finish, qc, toast]);

  const queueTracks = useCallback((tracks: CatTrack[], what = "track") =>
    run(tracks.map((t) => t.dz_id), what, () => post<QueueResult>("/api/browse/queue", { tracks })), [run]);
  const queueAlbum = useCallback((albumId: string, title: string) =>
    run([`album:${albumId}`], title, () => post<QueueResult>("/api/browse/queue", { album_id: albumId })), [run]);

  const value = useMemo(() => ({ pending, queueTracks, queueAlbum }), [pending, queueTracks, queueAlbum]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useQueueing(): Q {
  const v = useContext(Ctx);
  if (!v) throw new Error("useQueueing outside QueueingProvider");
  return v;
}
