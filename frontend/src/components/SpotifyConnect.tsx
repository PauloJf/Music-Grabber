import { useMutation, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../lib/api";
import type { Status } from "../lib/types";

export default function SpotifyConnect({ status }: { status: Status }) {
  const qc = useQueryClient();
  const connect = useMutation({
    mutationFn: () => get<{ url: string }>("/api/spotify/authorize-url"),
    onSuccess: ({ url }) => window.open(url, "_blank", "noopener"),
  });
  const disconnect = useMutation({
    mutationFn: () => post("/api/spotify/disconnect"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["status"] }),
  });
  const sp = status.spotify;
  if (!sp.configured) return <span className="badge badge--warn" title="Set SPOTIFY_CLIENT_ID / _SECRET / _REDIRECT_URI">Spotify not configured</span>;
  if (sp.connected)
    return (
      <span className="row" style={{ gap: 6 }}>
        <span className="badge badge--ok">Spotify connected</span>
        <button className="btn btn-ghost btn-sm" onClick={() => disconnect.mutate()} disabled={disconnect.isPending}>Disconnect</button>
      </span>
    );
  return (
    <button className="btn btn-primary btn-sm" onClick={() => connect.mutate()} disabled={connect.isPending}
      title={sp.last_error || "Opens Spotify to approve access; this page's server receives the callback"}>
      Connect Spotify
    </button>
  );
}
