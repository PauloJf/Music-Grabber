"""Shared constants for the db package."""

# Grab-queue status state machine. Non-terminal states mean the item is still "in
# flight"; at most one non-terminal item may exist per spotify_track_id.
GRAB_TERMINAL = ("done", "failed", "skipped")
GRAB_NONTERMINAL = ("pending", "searching", "awaiting_user", "downloading",
                    "transcoding", "tagging", "placing")
