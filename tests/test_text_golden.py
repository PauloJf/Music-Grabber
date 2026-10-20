"""music_grabber/text.py is a duplicate of BPM Tagger's text.py: pin it to golden output
generated from BPM Tagger (scripts/gen_text_golden.py), so drift is a decision, not an accident."""

import json
from pathlib import Path

import pytest

from music_grabber import text

GOLDEN = json.loads((Path(__file__).parent / "golden" / "text_golden.json").read_text(encoding="utf-8"))


def test_golden_records_its_provenance():
    assert "BPM Tagger" in GOLDEN["_source"] and "eae3f0c" in GOLDEN["_source"]
    assert len(GOLDEN["titles"]) >= 60 and len(GOLDEN["artists"]) >= 60


@pytest.mark.parametrize("case", GOLDEN["titles"], ids=lambda c: repr(c["in"]))
def test_title_normalization_matches_bpm_tagger(case):
    assert text.normalize_title(case["in"]) == case["normalize_title"]
    assert list(text.extract_feat(case["in"])) == case["extract_feat"]


@pytest.mark.parametrize("case", GOLDEN["artists"], ids=lambda c: repr(c["in"]))
def test_artist_normalization_matches_bpm_tagger(case):
    assert text.normalize_artist(case["in"]) == case["normalize_artist"]
    assert text.normalize_artist_name(case["in"]) == case["normalize_artist_name"]
    assert text.split_artist_credits(case["in"]) == case["split_artist_credits"]
