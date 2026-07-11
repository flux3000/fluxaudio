"""
tests/test_units.py — pure-function unit tests (no DB needed).
"""

from app.utils.format import format_partial_date
from app.utils.ingest import detect_track_flags, _parse_location
from app.utils.health import compute_health


# ── compute_health ────────────────────────────────────────────────────────────

def _scan(tags=None, info=None, audio=None, info_content="x"):
    """Minimal scan payload builder for health tests."""
    audio = audio if audio is not None else [{"filename": f"Track {i:02d}.flac"} for i in range(1, 3)]
    return {
        "audio_file_count": len(audio),
        "audio_files": audio,
        "info_file_content": info_content,
        "suggestions": {"from_tags": tags or {}, "from_info_file": info or {}},
    }


def _flac(n):
    return [{"filename": f"Track {i:02d}.flac"} for i in range(1, n + 1)]


def test_health_clean_show_is_green():
    # Complete tags, agreeing info, clean 17/17, all FLAC — Billy-Joel-like.
    tags = {"artist": "Billy Joel", "concert_date": "1993-07-01", "venue": "Logan Hall",
            "city": "London", "country": "UK", "source": "AUD", "lineage": "AUD>CDR",
            "tracks": [{"track_number": i, "title": f"S{i}"} for i in range(1, 18)]}
    info = {"artist": "Billy Joel", "year": 1993, "month": 7, "day": 1, "venue": "Logan Hall",
            "city": "London", "country": "UK",
            "tracks": [{"number": i, "title": f"S{i}"} for i in range(1, 18)]}
    h = compute_health(_scan(tags, info, _flac(17)))
    assert h["band"] == "green"
    assert h["score"] >= 85


def test_health_flags_track_count_mismatch():
    # 16-song setlist vs 14 files (ABB) — must surface a Tracks reconcile factor.
    tags = {"artist": "Allman Brothers Band", "concert_date": "1973-07-28",
            "venue": "Racecourse", "city": "Watkins Glen", "country": "US",
            "source": "SBD", "tracks": [{"track_number": i, "title": ""} for i in range(1, 15)]}
    info = {"artist": "Allman Brothers Band", "year": 1973, "month": 7, "day": 28,
            "venue": "Racecourse", "city": "Watkins Glen", "country": "US",
            "tracks": [{"number": i, "title": f"S{i}"} for i in range(1, 17)]}
    h = compute_health(_scan(tags, info, _flac(14)))
    msgs = " ".join(f["msg"] for f in h["factors"])
    assert "16" in msgs and "14" in msgs  # names the mismatch
    assert h["band"] in ("yellow", "red")


def test_health_missing_text_file_still_green_if_tags_complete():
    # No info file must NOT tank the score when tags carry everything.
    tags = {"artist": "X", "concert_date": "1980-05-08", "venue": "Fillmore",
            "city": "New York", "country": "US", "source": "SBD", "lineage": "SBD>DAT",
            "tracks": [{"track_number": i, "title": f"S{i}"} for i in range(1, 6)]}
    h = compute_health(_scan(tags, info=None, audio=_flac(5), info_content=None))
    # Single-source (tags only) costs 8; everything else full → still green-ish
    assert h["score"] >= 80
    assert any("Single metadata source" in f["msg"] for f in h["factors"])


def test_health_source_disagreement_flagged():
    tags = {"artist": "X", "concert_date": "1980-05-08", "venue": "Fillmore East",
            "city": "New York", "country": "US"}
    info = {"artist": "X", "year": 1980, "month": 5, "day": 8, "venue": "Capitol Theatre",
            "city": "Passaic", "country": "US", "tracks": []}
    h = compute_health(_scan(tags, info, _flac(4)))
    assert any(f["dimension"] == "Source agreement" and "disagree" in f["msg"]
               for f in h["factors"])


def test_health_lossy_audio_not_ai_recoverable():
    tags = {"artist": "X", "concert_date": "1980-05-08", "venue": "V",
            "city": "NY", "country": "US", "source": "AUD", "lineage": "L",
            "tracks": [{"track_number": 1, "title": "a"}]}
    h = compute_health(_scan(tags, tags, [{"filename": "Track 01.mp3"}]))
    lossy = [f for f in h["factors"] if f["dimension"] == "Audio integrity"]
    assert lossy and lossy[0]["ai_recoverable"] is False


def test_health_no_audio_is_zero():
    h = compute_health(_scan({}, {}, audio=[]))
    assert h["score"] == 0 and h["band"] == "red"


def test_health_no_track_titles_never_green():
    # Everything else perfect, but zero track titles → must not be green (gated).
    tags = {"artist": "Bela Fleck", "concert_date": "1999-06-20", "venue": "KOTO Radio",
            "city": "Telluride", "state": "CO", "country": "US", "source": "FM", "lineage": "FM>DAT",
            "tracks": [{"track_number": i, "title": ""} for i in range(1, 11)]}
    info = dict(tags, year=1999, month=6, day=20, tracks=[])
    h = compute_health(_scan(tags, info, _flac(10)))
    assert h["band"] != "green"
    assert any(f.get("gate") and "track titles" in f["msg"].lower() for f in h["factors"])


def test_parse_location_multiword_city_not_truncated():
    # Regression: "New York" must not become "York".
    assert _parse_location("New York, NY") == ("New York", "NY", "US")
    assert _parse_location("New York, NY, USA") == ("New York", "NY", "US")
    assert _parse_location("San Francisco, CA") == ("San Francisco", "CA", "US")

def test_parse_location_drops_venue_prefix():
    assert _parse_location("Fillmore East, New York, NY") == ("New York", "NY", "US")

def test_parse_location_countries_and_aliases():
    assert _parse_location("Osaka, Japan") == ("Osaka", None, "Japan")
    assert _parse_location("London, UK") == ("London", None, "UK")
    assert _parse_location("London, England") == ("London", None, "UK")

def test_parse_location_no_comma():
    assert _parse_location("Ann Arbor MI") == ("Ann Arbor", "MI", "US")

def test_parse_location_rejects_non_location():
    for line in ["SBD", "Soundboard recording", "Set 1", ""]:
        assert _parse_location(line) == (None, None, None), line


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
