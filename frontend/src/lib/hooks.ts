import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { get } from "./api";
import type { Activity, CatTrack, Me, Status } from "./types";

export const useStatus = () => useQuery({ queryKey: ["status"], queryFn: () => get<Status>("/api/status"), refetchInterval: 8000 });

/** Live job feed: polls fast while anything is running, slowly otherwise. */
export function useActivity() {
  return useQuery({
    queryKey: ["activity"], queryFn: () => get<Activity>("/api/activity"),
    refetchInterval: (q) => ((q.state.data?.active.length ?? 0) > 0 ? 1500 : 6000),
  });
}

export const hasLive = (tracks?: CatTrack[]) => !!tracks?.some((t) => ["queued", "active"].includes(t.state.kind));

/** After an action: refresh everything that shows job state. */
export function useRefreshJobs() {
  const qc = useQueryClient();
  return useCallback(() => {
    for (const k of ["activity", "queue", "inbox", "status", "browse", "playlists", "playlist"]) qc.invalidateQueries({ queryKey: [k] });
  }, [qc]);
}

export const fmtDuration = (ms?: number | null) => {
  if (!ms) return "";
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
export const fmtCount = (n?: number | null) => (n == null ? "" : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(n));
export const ago = (iso?: string | null) => {
  if (!iso) return "";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : `${Math.floor(s / 86400)} d ago`;
};

export const useMe = () => useQuery({ queryKey: ["me"], queryFn: () => get<Me>("/api/me"), staleTime: 60_000 });
