"""Regenerate tests/golden/text_golden.json from BPM Tagger's text.py.

    python scripts/gen_text_golden.py /path/to/BPM-Tagger/bpm_tagger/text.py <source-sha>

The golden file pins Music Grabber's duplicated ``music_grabber/text.py`` to BPM Tagger's
behaviour. Regenerating it is a conscious decision to follow (or diverge from) BPM Tagger.
"""

import importlib.util
import json
import sys
from pathlib import Path

TITLES = [
    "Blinding Lights", "Blinding Lights - Remastered 2011", "Song (feat. Other Artist)",
    "Song [feat. A & B]", "Song ft. Someone", "Song featuring Someone Else",
    "Bohemian Rhapsody (Remastered Version)", "Hey Jude - Single Version", "Track - Radio Edit",
    "Track (Original Mix)", "Track (Album Version)", "Love Me Do (Mono)", "Title (Deluxe Edition)",
    "Title (Expanded Edition)", "Title (2009 Remaster)", "Title - 2015 Version", "Song (Live)",
    "Song (Kaytranada Remix)", "Song (Acoustic)", "Song (Instrumental)", "Song (Sped Up)",
    "Beyoncé - Halo", "Sigur Rós – Hoppípolla", "Mötley Crüe: Kickstart My Heart", "Ça Plane Pour Moi",
    "Naïve", "Über Alles", "Don't Stop Believin'", "Don’t Stop Believin’", "Rock 'n' Roll",
    "Rock & Roll", "Rock and Roll", "AC/DC", "P!nk", "Ke$ha", "Mr. Brightside", "Mr Brightside",
    "99 Problems", "1979", "#1 Crush", "What's Going On", "Whats Going On", "THE SONG", "the song",
    "  spaced   out  ", "Title...", "Title!?", "Title: Subtitle", "Title / Other", "Title _ Under",
    "夜に駆ける", "千本桜", "Привет", "Αγάπη", "", "   ", "A", "(Only Brackets)", "[Only Square]",
    "Song - Live at Wembley", "Song (Remastered) (feat. X)", "Song - Remastered - Radio Edit",
    "Song (Bonus Track)", "Song (Anniversary Edition)", "Song (Reissue)", "Song (Digital Remaster)",
    "Song (Stereo)", "Song (Original Version)", "Song (feat. A, B & C)", "Song ft. A feat. B",
    "Song (with Someone)", "Song (Cover)", "Song (Demo)", "Song (Karaoke Version)",
    "Don't You (Forget About Me) - Remastered", "Comfortably Numb (2011 Remastered Version)",
]

ARTISTS = [
    "The Weeknd", "Weeknd", "The Beatles", "Beatles, The", "A & B", "A and B", "A, B", "A; B",
    "A feat. B", "A ft. B", "A featuring B", "A x B", "A × B", "A / B", "A vs. B", "A with B",
    "Simon & Garfunkel", "Earth, Wind & Fire", "Crosby, Stills, Nash & Young", "Tyler, The Creator",
    "AC/DC", "P!nk", "Ke$ha", "Beyoncé", "Björk", "Sigur Rós", "Mötley Crüe", "Motley Crue",
    "Tiësto", "Zoé", "Jean-Michel Jarre", "Guns N' Roses", "Guns N’ Roses", "Guns N Roses",
    "Florence + The Machine", "Florence and the Machine", "Hall & Oates", "Daryl Hall & John Oates",
    "Mr. Oizo", "Mr Oizo", "DJ Snake", "DJ  Snake", "dj snake", "Jay-Z", "JAY Z", "Jay Z",
    "宇多田ヒカル", "YOASOBI", "Кино", "", "  ", "Various Artists", "Unknown Artist",
    "Artist One, Artist Two, Artist Three", "Artist One & Artist Two feat. Artist Three",
    "Kaytranada, Aaliyah", "Calvin Harris, Dua Lipa", "Lil Nas X", "Lil Nas X, Jack Harlow",
    "The Chemical Brothers", "Chemical Brothers", "Röyksopp", "Royksopp", "Ólafur Arnalds",
    "Sébastien Tellier", "Mylène Farmer", "Céline Dion", "Édith Piaf", "Nick Cave & the Bad Seeds",
    "Bob Marley & The Wailers", "Prince and The Revolution", "Prince & The New Power Generation",
]


def main(path: str, sha: str) -> None:
    spec = importlib.util.spec_from_file_location("bt_text", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = {
        "_source": f"BPM Tagger bpm_tagger/text.py @ {sha}",
        "titles": [{"in": t, "normalize_title": mod.normalize_title(t),
                    "extract_feat": list(mod.extract_feat(t))} for t in TITLES],
        "artists": [{"in": a, "normalize_artist": mod.normalize_artist(a),
                     "normalize_artist_name": mod.normalize_artist_name(a),
                     "split_artist_credits": mod.split_artist_credits(a)} for a in ARTISTS],
    }
    out = Path(__file__).resolve().parent.parent / "tests" / "golden" / "text_golden.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(TITLES)} titles, {len(ARTISTS)} artists)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
