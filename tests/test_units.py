"""
tests/test_units.py — pure-function unit tests (no DB needed).
"""

from app.utils.format import format_partial_date
from app.utils.ingest import detect_track_flags


def test_format_partial_date_full():
    assert format_partial_date(1977, 5, 8) == "1977-05-08"

def test_format_partial_date_month():
    assert format_partial_date(1977, 5, None) == "1977-05"

def test_format_partial_date_year():
    assert format_partial_date(1977, None, None) == "1977"

def test_format_partial_date_none():
    assert format_partial_date(None, 5, 8) is None


def test_flags_structural_markers():
    assert detect_track_flags("// Dark Star") == ["start_truncated"]
    assert detect_track_flags("Dark Star //") == ["end_truncated"]
    assert detect_track_flags("Dark Star (x)") == ["incomplete"]

def test_flags_keyword_segments():
    assert detect_track_flags("tuning") == ["tuning"]
    assert detect_track_flags("tuning and banter (Bobby)") == ["banter", "tuning"]
    assert detect_track_flags("Band Intros") == ["band_intros"]
    assert detect_track_flags("Announcement") == ["announcement"]

def test_flags_no_false_positives_on_real_titles():
    # Musical segues / real songs must NOT be flagged
    for title in ["Speak Low", "Don't Talk (Put Your Head On My Shoulder)",
                  "Piano Intro >", "Dark Star Intro -> Fields of Gray",
                  "Cryptical Envelopment >"]:
        assert detect_track_flags(title) == [], title
