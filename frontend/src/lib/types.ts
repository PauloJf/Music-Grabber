export interface Status {
  version: string;
  uptime_seconds: number;
  spotify: { configured: boolean; connected: boolean; last_error: string; redirect_uri: string };
  library: { index: string | null; tracks: number };
  analysis: string;
  hooks: string[];
  providers: string[];
  queue: Record<string, number>;
  grabbed_total: number;
  managed_files: number;
  last_synced_epoch: number | null;
  playlists: { total: number; watched: number; have: number; missing: number; queued: number };
  dry_run: boolean;
}

export interface Playlist {
  id: number;
  spotify_id: string;
  name: string;
  enabled: number;
  image_url: string | null;
  track_count: number;
  last_synced_at: string | null;
  have_count: number;
  missing_count: number;
  queued_count: number;
  new_count: number;
  removed_count: number;
}

export interface PlaylistTrack {
  id: number;
  position: number;
  title: string;
  artist: string;
  album: string;
  derived_status: "have" | "queued" | "missing" | "removed";
  matched_file_path: string | null;
}

export interface QueueItem {
  id: number;
  status: string;
  title: string | null;
  artist: string | null;
  album: string | null;
  duration_ms: number | null;
  isrc: string | null;
  provider: string | null;
  progress: number | null;
  priority: number | null;
  note: string | null;
  error: string | null;
  final_path: string | null;
  updated_at: string | null;
}

export interface Candidate {
  id: number;
  queue_item_id: number;
  provider: string;
  title: string | null;
  artist: string | null;
  album: string | null;
  duration_ms: number | null;
  quality: string | null;
  score: number | null;
  rank: number;
}

export interface InboxItem extends QueueItem {
  candidates: Candidate[];
}

export interface QueueEvent {
  id: number;
  event: string;
  detail: string | null;
  created_at: string;
}
