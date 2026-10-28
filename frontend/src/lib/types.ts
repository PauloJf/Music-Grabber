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

/** The Navidrome mirror of a playlist; null/absent when the mirror is not configured. */
export interface MirrorInfo {
  enabled: boolean;
  state: "pending" | "off" | "ok" | "empty" | "name_taken" | "deleted_remotely" | "error";
  name: string | null;
  matched: number;
  unresolved: number;
  synced_at: string | null;
  note: string;
  error: string;
}

export interface Playlist {
  mirror?: MirrorInfo | null;
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
  requested_by?: string | null;
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
  url?: string | null;
}

export interface InboxPreview { url: string; open_url: string; reason: string }

export interface InboxItem extends QueueItem {
  candidates: Candidate[];
}

export interface QueueEvent {
  id: number;
  event: string;
  detail: string | null;
  created_at: string;
}

export type TrackState =
  | { kind: "none" }
  | { kind: "library" }
  | { kind: "queued" | "active" | "review"; item_id: number; status: string; progress: number }
  | { kind: "failed"; item_id: number; error: string };

export interface CatTrack {
  dz_id: string; title: string; artist: string; artist_id: string; album: string; album_id: string;
  duration_ms: number | null; preview_url: string; cover_url: string; explicit: boolean;
  track_no: number | null; disc_no: number | null; year: number | null; state: TrackState;
}
export interface CatArtist { dz_id: string; name: string; image_url: string; fans: number | null; album_count: number | null }
export interface CatAlbum { dz_id: string; title: string; artist: string; artist_id: string; cover_url: string; year: number | null; track_count: number | null; kind: string; explicit: boolean }

export interface Activity {
  active: { id: number; status: string; progress: number | null; title: string; artist: string }[];
  inbox: number;
  recent: { id: number; status: string; title: string; artist: string; updated_at: string; error: string | null }[];
  counts: Record<string, number>;
}
export interface QueueResult { ok: boolean; queued: number; item_ids: number[]; results: { dz_id: string; result: string }[]; album?: string }

export interface User { id: number | null; username: string; role: "admin" | "user"; prefs: { theme?: "system" | "light" | "dark"; preview_volume?: number }; created_at?: string | null; last_login?: string | null }
export interface ApiKey { id: number; name: string; scopes: string[]; created_by: string | null; created_at: string | null; last_used_at: string | null; revoked_at: string | null }
export interface Release { dz_artist_id: string; dz_album_id: string; title: string; kind: string; release_date: string; cover_url: string; artist_name: string }
export interface ReleaseFeed {
  enabled: boolean; days: number; releases: Release[]; following: string[];
  status: { artists: number; mapped: number; last_run: string | null; last_checked: string | null; last_error: string; more_to_do: boolean } | null;
}
export interface ArtistPref { dz_artist_id: string; name: string; follow: number; hide_releases: number; hide_suggestions: number }
export interface ReleasesHidden { artists: ArtistPref[]; releases: Release[] }
export interface SuggestedArtist { dz_id: string; name: string; image_url: string; fans: number | null; score: number; because: string[] }
export interface AlbumGap { dz_artist_id: string; dz_album_id: string; title: string; release_date: string; cover_url: string; artist_name: string; track_count: number | null; owned: number; state: "missing" | "partial" }
export interface Suggestions {
  enabled: boolean; gaps_enabled: boolean; artists_enabled: boolean; gaps: AlbumGap[]; artists: SuggestedArtist[];
  status: { more_to_do: boolean } | null;
}
export interface SuggestHidden {
  artists: { dz_artist_id: string; name: string; hide_suggestions?: number }[];
  albums: { dz_album_id: string; dz_artist_id: string; title: string; artist_name: string }[];
}
export interface Me { user: User; via: "token" | "session" }

export interface OwnedTrack { path: string; title: string | null; artist: string | null; album: string | null; album_artist: string | null; duration_ms: number | null; year: number | null }
export interface OwnedPage { tracks: OwnedTrack[]; total: number; limit: number; offset: number; q: string; index: string | null }
