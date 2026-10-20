# Provenance

Music Grabber was extracted from [BPM Tagger](https://github.com/PauloJf/BPM-Tagger)
(public, `main`, v2.18.2) at commit

    eae3f0c2662f590ac8cf0334a282389147152d2f   (eae3f0c)

## How

`git filter-repo` was run on a fresh clone of BPM Tagger, keeping only the grabber's
paths and renaming them, then the result was merged into this repository with
`--allow-unrelated-histories` (the merge commit is "Merge extracted BPM-Tagger grabber
history"). `git log --follow` and `git blame` therefore reach back into BPM Tagger's
history for the moved files.

| BPM Tagger path | Here |
|---|---|
| `bpm_tagger/grabber/*` | `music_grabber/*` |
| `bpm_tagger/db/grabber.py` | `music_grabber/db/queue.py` |
| `bpm_tagger/text.py` | `music_grabber/text.py` (deliberate duplicate; pinned by `tests/test_text_golden.py`) |
| `bpm_tagger/integrations/lrclib.py` | `music_grabber/lrclib.py` |
| `bpm_tagger/integrations/deezer_catalog.py` | `music_grabber/deezer_catalog.py` (subset) |
| `bpm_tagger/notify/ntfy.py` | `music_grabber/notify.py` (grabber half) |
| `bpm_tagger/bpm/lyrics.py` | `music_grabber/lyrics.py` (embed only) |
| `LICENSE` | `LICENSE` |
| `tests/test_{worker,providers,matching,path_template,tagging,enqueue,grab_delete,grab_events}.py` | `tests/` |

The Spotify half of `tests/test_playlist_sync.py` was ported by hand (not in the history).

Commit author emails were rewritten with a mailmap to the GitHub noreply address
(`PauloJf@users.noreply.github.com`), because GitHub rejects pushes that would expose the
private address. Content is otherwise untouched by the extraction; every change after the
merge commit is an ordinary commit.

## Not carried over

Monochrome provider (on hold upstream), the suggestions engine and page, BPM detection and
loudness measurement (BPM Tagger owns analysis), the web UI. See `docs/plans/extraction.md`.
