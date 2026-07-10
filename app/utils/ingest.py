"""
utils/ingest.py — Recording ingestion utilities.

Handles:
  - Scanning a source folder for audio, text, and fingerprint files
  - Reading existing FLAC tags via mutagen
  - Parsing the info/text file for metadata suggestions
  - Moving or copying the folder into the library
  - Writing the canonical folder name
"""

import os
import re
import shutil
import datetime
from difflib import get_close_matches
from pathlib import Path
from mutagen.flac import FLAC
from mutagen import MutagenError
from dateutil import parser as _dateutil_parser
from dateutil.parser import ParserError as _ParserError
import geonamescache as _geonamescache

from app.utils.format import format_partial_date


# ── File classification ────────────────────────────────────────────────────────

AUDIO_EXTENSIONS    = {".flac", ".mp3", ".wav"}
FINGERPRINT_MARKERS = {"ffp", "md5", "eac", "shntool", "fingerprint"}
TEXT_EXTENSION      = ".txt"

# Subdir name patterns that indicate multi-set/disc folder structure.
# Matched case-insensitively against the subdir basename.
_SET_PATTERNS = re.compile(
    r"^(cd|disc|disk|set|volume|vol|part|tape|show|d)\s*(\d+)$",
    re.IGNORECASE,
)

# Keywords that suggest a text file is the info/setlist file rather than
# a README or technical notes. Higher score = preferred.
_TEXT_PREFER_WORDS = {
    "setlist": 10, "set list": 10, "info": 8, "readme": -5,
    "lineage": 6,  "source": 4,   "notes": 3, "taper": 6,
    "show": 4,     "concert": 4,
}


def _auto_set_label(subdir_name):
    """
    Convert a subdir name like 'cd1', 'Disc 2', 'Set 1' into a canonical
    set label like 'CD 1', 'Disc 2', 'Set 1'.  Returns None if no match.
    """
    m = _SET_PATTERNS.match(subdir_name.strip())
    if not m:
        return None
    prefix = m.group(1).upper()
    number = m.group(2)
    # Normalise common abbreviations
    prefix = {"D": "Disc", "CD": "CD", "VOL": "Vol", "VOLUME": "Vol",
              "PART": "Part", "TAPE": "Tape", "SET": "Set",
              "SHOW": "Show", "DISK": "Disc"}.get(prefix, prefix.title())
    return f"{prefix} {number}"


def _score_text_file(filename):
    """
    Return a preference score for a text file.  Higher → more likely to be
    the main info/setlist file.
    """
    low = filename.lower()
    score = 0
    for kw, pts in _TEXT_PREFER_WORDS.items():
        if kw in low:
            score += pts
    # Penalise very short filenames (e.g. 'md5.txt') — likely checksums
    if len(Path(filename).stem) <= 3:
        score -= 4
    # Bonus for files with a date pattern in the name (common in ROIO)
    if re.search(r"\d{4}[-_.]\d{2}[-_.]\d{2}", filename):
        score += 5
    return score


def scan_folder(folder_path):
    """
    Walk a source folder and classify all files.

    Handles three structural cases:
      1. Flat folder — all audio in root (no subdirs with audio)
      2. Single transparent subdir — e.g. a 'flac/' subfolder; treated as flat
      3. Multi-set structure — subdirs named cd1/cd2, disc1/disc2, set1/set2, etc.
         Audio files get a 'set' field auto-populated from the subdir name.

    When multiple .txt files are present, the most likely info file is surfaced
    as text_files[0] based on filename scoring. All candidates are returned so
    the UI can offer a switcher.

    Returns:
      {
        "audio_files":    [ { index, filename, path, set } ],
        "text_files":     [ { filename, path, score } ],   # sorted best-first
        "fingerprints":   [ { type, filename, path } ],
        "other_files":    [ { filename, path } ],
        "sets_detected":  bool,   # True when multi-set subdir structure was used
      }
    """
    folder_path = str(folder_path)
    result = {
        "audio_files":   [],
        "text_files":    [],
        "fingerprints":  [],
        "other_files":   [],
        "sets_detected": False,
    }

    # ── Detect subdir structure ────────────────────────────────────────────────
    # We intentionally do NOT split by CD/disc/set subdirs — physical media
    # splits are irrelevant for live recording archival. All audio is treated
    # as one flat list regardless of how it's organised on disk.
    try:
        top_entries = os.listdir(folder_path)
    except OSError:
        return result

    subdirs = [
        e for e in top_entries
        if os.path.isdir(os.path.join(folder_path, e)) and not e.startswith(".")
    ]
    root_audio = [
        f for f in top_entries
        if os.path.isfile(os.path.join(folder_path, f))
        and Path(f).suffix.lower() in AUDIO_EXTENSIONS
    ]

    # Determine scan mode
    if len(subdirs) == 1 and not root_audio:
        # Single transparent subdir (e.g. 'flac/', 'cd1/') — treat as flat
        scan_dirs = [(folder_path, None), (os.path.join(folder_path, subdirs[0]), None)]
    else:
        # Flat walk — collect everything (including multiple subdirs like cd1+cd2)
        scan_dirs = None   # sentinel: use os.walk

    # ── File collection ────────────────────────────────────────────────────────
    audio_index = 0
    all_text    = []

    def _classify(fname, dirpath, set_label):
        nonlocal audio_index
        full = os.path.join(dirpath, fname)
        ext  = Path(fname).suffix.lower()
        low  = fname.lower()

        if ext in AUDIO_EXTENSIONS:
            audio_index += 1
            result["audio_files"].append({
                "index":    audio_index,
                "filename": fname,
                # rel_path is relative to the scan root — includes any subdir prefix
                # (e.g. "flac/01 - Dark Star.flac" or "disc1/01.flac").
                # Use this as file_path in the DB so streams resolve correctly after copy.
                "rel_path": os.path.relpath(full, folder_path),
                "path":     full,
                "set":      set_label,   # None for flat; "CD 1" etc. for multi-set
            })
        elif ext == TEXT_EXTENSION:
            if any(m in low for m in FINGERPRINT_MARKERS):
                result["fingerprints"].append({
                    "type":     _detect_fp_type(low),
                    "filename": fname,
                    "path":     full,
                })
            else:
                all_text.append({"filename": fname, "path": full})
        elif any(m in low for m in FINGERPRINT_MARKERS):
            result["fingerprints"].append({
                "type":     _detect_fp_type(low),
                "filename": fname,
                "path":     full,
            })
        else:
            result["other_files"].append({"filename": fname, "path": full})

    if scan_dirs is not None:
        # Structured walk: visit each (dir, set_label) pair, non-recursive
        seen_dirs = set()
        for dir_path, set_label in scan_dirs:
            if dir_path in seen_dirs:
                continue
            seen_dirs.add(dir_path)
            try:
                for fname in sorted(os.listdir(dir_path)):
                    full = os.path.join(dir_path, fname)
                    if os.path.isfile(full):
                        _classify(fname, dir_path, set_label)
            except OSError:
                pass
    else:
        # Flat walk
        for dirpath, _, filenames in os.walk(folder_path):
            for fname in sorted(filenames):
                _classify(fname, dirpath, None)

    # ── Score and sort text files ──────────────────────────────────────────────
    for tf in all_text:
        tf["score"] = _score_text_file(tf["filename"])
    all_text.sort(key=lambda x: x["score"], reverse=True)
    result["text_files"] = all_text

    return result


def _detect_fp_type(filename_lower):
    if "ffp" in filename_lower:
        return "ffp"
    if "md5" in filename_lower:
        return "md5"
    return "other"


# ── FLAC tag reading ───────────────────────────────────────────────────────────

# Map FLAC tag keys → our field names
_TAG_MAP = {
    "ARTIST":          "artist",
    "ALBUM":           "album",
    "DATE":            "year",
    "CONCERTDATE":     "concert_date",
    "CONCERTVENUE":    "venue",
    "CONCERTLOCATION": "location",
    "RECORDINGSOURCE": "source",
    "LINEAGE":         "lineage",
    "TITLE":           "title",
    "TRACKNUMBER":     "track_number",
    "TRACKTOTAL":      "track_total",
}


def read_flac_tags(audio_files):
    """
    Read FLAC tags from all audio files.

    Returns:
      {
        "container": { artist, album, concert_date, venue, location, source, lineage },
        "tracks":    [ { index, filename, title, track_number, duration } ]
      }
    Container fields are read from the first successfully tagged file.
    """
    container = {}
    tracks    = []

    for f in audio_files:
        path = f["path"]
        try:
            audio = FLAC(path)
            tags  = audio.tags or {}

            # Capture container-level tags from first file that has them
            if not container:
                for tag_key, field in _TAG_MAP.items():
                    if tag_key in tags and field not in ("title", "track_number", "track_total"):
                        container[field] = tags[tag_key][0]

            # Track-level
            track_entry = {
                "index":        f["index"],
                "filename":     f["filename"],
                "rel_path":     f.get("rel_path", f["filename"]),
                "title":        tags.get("TITLE",       [None])[0],
                "track_number": tags.get("TRACKNUMBER", [None])[0],
                "duration":     int(audio.info.length) if audio.info else None,
            }
            tracks.append(track_entry)

        except (MutagenError, Exception):
            # Unreadable file — add placeholder so index stays consistent
            tracks.append({
                "index":        f["index"],
                "filename":     f["filename"],
                "rel_path":     f.get("rel_path", f["filename"]),
                "title":        None,
                "track_number": None,
                "duration":     None,
            })

    return {"container": container, "tracks": tracks}


# ── FLAC tag writing ───────────────────────────────────────────────────────────

def build_recording_tags(recording):
    """
    Build the container-level Vorbis comment dict for a recording from its
    Recording → Performance → Artist → Venue chain. Single source of truth
    for the DB→tag mapping, shared by write_flac_tags (which writes it to disk)
    and the debug endpoint (which compares it against on-disk tags).

    Returns (container_tags: dict, track_total: str). Only non-empty values are
    included in container_tags.
    """
    perf   = recording.performance
    venue  = perf.venue if perf else None
    tracks = recording.tracks

    # ── Concert date string ───────────────────────────────────────────────────
    concert_date = format_partial_date(
        perf.start_year, perf.start_month, perf.start_day) if perf else None

    # ── Venue name + location ─────────────────────────────────────────────────
    venue_name = venue.name if venue else None
    if venue:
        location_parts = [p for p in [venue.city, venue.state, venue.country] if p]
    elif perf:
        location_parts = [p for p in [perf.city, perf.state, perf.country] if p]
    else:
        location_parts = []

    # ── Source string ─────────────────────────────────────────────────────────
    source_str = recording.source
    if recording.source_modifier:
        source_str = (f"{source_str} - {recording.source_modifier}"
                      if source_str else recording.source_modifier)

    # ── Artist / album labels ─────────────────────────────────────────────────
    artist_name = perf.artist.name if (perf and perf.artist) else None
    album_parts = [p for p in [artist_name, concert_date, venue_name] if p]
    album_str   = " - ".join(album_parts) if album_parts else None

    container_tags = {}
    if artist_name:       container_tags["ARTIST"]          = artist_name
    if album_str:         container_tags["ALBUM"]           = album_str
    if perf and perf.start_year: container_tags["DATE"]     = str(perf.start_year)
    if concert_date:      container_tags["CONCERTDATE"]     = concert_date
    if venue_name:        container_tags["CONCERTVENUE"]    = venue_name
    if location_parts:    container_tags["CONCERTLOCATION"] = ", ".join(location_parts)
    if source_str:        container_tags["RECORDINGSOURCE"] = source_str
    if recording.lineage: container_tags["LINEAGE"]         = recording.lineage

    return container_tags, str(len(tracks))


def write_flac_tags(recording, library_root):
    """
    Write Vorbis comments from DB records to every FLAC file in a recording.

    Builds container-level tags via build_recording_tags(), then per-track
    TITLE/TRACKNUMBER/TRACKTOTAL for each Track. Existing Vorbis comments are
    replaced entirely (clean write).

    Args:
        recording:    Recording ORM object with relationships loaded
        library_root: Absolute path string for the library root

    Returns:
        (n_written, errors) where errors is a list of (filename, message) tuples.
    """
    tracks = recording.tracks  # ordered by track_number via relationship
    container_tags, track_total = build_recording_tags(recording)

    n_written = 0
    errors    = []

    for track in tracks:
        abs_path = os.path.join(library_root, recording.folder_path, track.file_path)
        try:
            audio = FLAC(abs_path)

            # Clear all existing Vorbis comments
            audio.clear()

            # Container tags
            for tag_key, value in container_tags.items():
                audio[tag_key] = value

            # Track-specific tags
            audio["TITLE"]       = track.title
            audio["TRACKNUMBER"] = str(track.track_number)
            audio["TRACKTOTAL"]  = track_total
            if track.songwriter:
                audio["COMPOSER"] = track.songwriter

            audio.save()
            n_written += 1

        except FileNotFoundError:
            errors.append((track.file_path, "File not found"))
        except MutagenError as e:
            errors.append((track.file_path, f"Mutagen error: {e}"))
        except Exception as e:
            errors.append((track.file_path, f"Unexpected error: {e}"))

    return n_written, errors


# ── Info file parsing ──────────────────────────────────────────────────────────

# Initialise geonamescache once at import time (pure local JSON, fast)
_gc             = _geonamescache.GeonamesCache()
_CITY_NAMES     = {c["name"].lower() for c in _gc.get_cities().values()}
_US_STATES      = _gc.get_us_states()                                      # {CA: {name:"California",...}}
_US_STATE_CODES = set(_US_STATES.keys())                                   # {"CA","NY",...}
_US_STATE_NAMES = {v["name"].lower(): k for k, v in _US_STATES.items()}   # {"california":"CA",...}
_COUNTRIES      = _gc.get_countries()
_COUNTRY_NAMES  = {v["name"].lower() for v in _COUNTRIES.values()}

_CURRENT_YEAR   = datetime.date.today().year

# Month names for date-signal detection
_MONTH_NAMES = {
    "january","february","march","april","may","june",
    "july","august","september","october","november","december",
    "jan","feb","mar","apr","jun","jul","aug","sep","oct","nov","dec",
}

# Track line: "01 Title", "1. Title", "1 - Title", "11: Title"
_TRACK_PATTERN = re.compile(r"^\s*(\d{1,3})[.:\-\s]\s*(.+)$")

# Trailing timestamp appended by tapers: "Dark Star 12:34", "Intro :45", "Help > Slip 1:23:45"
_TRAILING_TS_RE = re.compile(r'\s+\d*:[\d:]+$')

# Words kept lowercase in title case (unless first word)
_TC_LOWER = frozenset({
    'a', 'an', 'the', 'and', 'but', 'or', 'for', 'nor', 'at', 'by',
    'in', 'of', 'on', 'to', 'up', 'as', 'is', 'it', 'if', 'so', 'vs',
})

def _title_case(s):
    """Title-case a track title without mangling apostrophes (str.title() does 'Don'T')."""
    words = s.split()
    out = []
    for i, w in enumerate(words):
        low = w.lower()
        if i == 0 or low not in _TC_LOWER:
            out.append(w[0].upper() + w[1:] if w else w)
        else:
            out.append(low)
    return ' '.join(out)


# ── Track flag auto-detection ──────────────────────────────────────────────────
#
# Suggests NON_MUSIC_FLAGS-style flags from a track's title text alone. Kept
# deliberately conservative: several words that indicate a non-music segment
# ("talk", "speak", "crowd") also show up in real song titles ("Don't Talk",
# "Speak Low"), so this only fires on whole-word/whole-segment matches for the
# ambiguous cases, not loose substring checks. These are *suggestions* the
# archivist approves in the ingest wizard — not applied silently.
#
# Structural markers (used by Grateful Dead and others):
#   "// Title"  -> leading "//"  = start_truncated
#   "Title //"  -> trailing "//" = end_truncated
#   "Title (x)" -> trailing "(x)" (any case) = incomplete
#
# Keyword segments: a title is split on " and "/","/"/"/"&" (after stripping
# one trailing parenthetical, e.g. "(Bobby)") so compound titles like
# "tuning and banter (Bobby)" resolve to both ['tuning', 'banter'].

_FLAG_START_TRUNC = re.compile(r'^\s*//')
_FLAG_END_TRUNC   = re.compile(r'//\s*$')
_FLAG_INCOMPLETE  = re.compile(r'\(\s*x\s*\)\s*$', re.IGNORECASE)
_FLAG_TRAILING_PAREN = re.compile(r'^(.*?)\s*\([^)]*\)\s*$')
_FLAG_SEGMENT_SPLIT  = re.compile(r'\s*(?:,|/|&|\band\b)\s*', re.IGNORECASE)

# Whole-segment patterns — the ENTIRE segment must match (not a substring),
# so a musical segue like "Piano Intro >" or "Dark Star Intro -> Fields of
# Gray" is never mistaken for a spoken "Intro" track.
_FLAG_SEGMENT_PATTERNS = [
    ('tuning',       re.compile(r'^tunings?$', re.IGNORECASE)),
    ('banter',       re.compile(r'^(banter|dialogue)s?$', re.IGNORECASE)),
    ('audience',     re.compile(r'^(audience|crowd)s?$', re.IGNORECASE)),
    ('band_intros',  re.compile(r'^band intro(duction)?s?$', re.IGNORECASE)),
    ('introduction', re.compile(r'^intro(duction)?s?\.?$', re.IGNORECASE)),
]

# Whole-word/anywhere-in-segment patterns — safe as substrings because these
# words essentially never appear inside real song titles.
_FLAG_WORD_PATTERNS = [
    ('announcement', re.compile(r'\bannouncements?\b', re.IGNORECASE)),
    ('interview',    re.compile(r'\binterviews?\b',    re.IGNORECASE)),
]


def detect_track_flags(title):
    """
    Return a sorted list of suggested flag keys for a track title.
    Pure function of the title string — no DB access, safe to call from the
    ingest wizard's scan step or a one-off backfill script.
    """
    if not title:
        return []

    flags = set()
    raw = title.strip()

    if _FLAG_START_TRUNC.match(raw):
        flags.add('start_truncated')
    if _FLAG_END_TRUNC.search(raw):
        flags.add('end_truncated')
    if _FLAG_INCOMPLETE.search(raw):
        flags.add('incomplete')

    # Strip one trailing parenthetical (usually an attribution, e.g. "(Bobby)")
    # before splitting into segments, so it doesn't get treated as its own
    # segment or block a match on the segment before it.
    m = _FLAG_TRAILING_PAREN.match(raw)
    base = m.group(1).strip() if m else raw

    for segment in _FLAG_SEGMENT_SPLIT.split(base):
        segment = segment.strip()
        if not segment:
            continue
        for key, pattern in _FLAG_SEGMENT_PATTERNS:
            if pattern.match(segment):
                flags.add(key)

    for key, pattern in _FLAG_WORD_PATTERNS:
        if pattern.search(base):
            flags.add(key)

    return sorted(flags)

# Source type keywords (scan full file text)
_SOURCE_KEYWORDS = {
    "sbd": "SBD", "soundboard": "SBD",
    "aud": "AUD", "audience":   "AUD",
    "mtx": "MTX", "matrix":     "MTX",
    "fm":  "FM",  "broadcast":  "FM",
}

# Lineage section triggers — explicit labels only (bare ">" removed to avoid false positives)
_LINEAGE_LABELS = {"lineage", "source:", "transfer", "recording info", "recorded by", "chain:"}

# Venue keywords
_VENUE_WORDS = {
    "theater","theatre","stadium","arena","festival","amphitheater",
    "hall","halle","saal","kursaal",
    "concert","club","studio","radio","pavilion","auditorium","center","centre",
    "ballroom","opera","university","college","fillmore","ryman","birchmere",
    "inn","stage","coffeehouse","tent","café","cafe","lounge","saloon",
    "fairground","garden","park","ranch","farm","museum","coliseum",
    "field","court","bowl","forum","palace","pier","warehouse","dome","barn",
}


# ── Private helpers ────────────────────────────────────────────────────────────

def _is_filename_line(line):
    """Detect identifier lines like 'BillEvans.1980-02-22.ECM260F' — no spaces, has dots."""
    return "." in line and " " not in line.strip()


def _looks_like_date_line(line):
    """Quick check: does this line likely contain a date?"""
    low = line.lower()
    if re.search(r"\b(19|20)\d{2}\b", line):
        return True
    if re.search(r"\b\d{1,2}[-./]\d{1,2}[-./]\d{2,4}\b", line):
        return True
    if any(m in low.split() for m in _MONTH_NAMES):
        return True
    return False


def _parse_date(line):
    """
    Try to extract a date from a line via dateutil.
    Only attempts lines with a strong date signal.
    Returns (year, month, day, raw_str) or None.
    """
    low       = line.lower()
    has_4yr   = bool(re.search(r"\b(19|20)\d{2}\b", line))
    has_2yr   = bool(re.search(r"\b\d{1,2}[-./]\d{1,2}[-./]\d{2}\b", line))
    has_month = any(m in low.split() for m in _MONTH_NAMES)

    if not (has_4yr or has_2yr or has_month):
        return None

    try:
        dt   = _dateutil_parser.parse(line, fuzzy=True, dayfirst=False)
        year = dt.year
        if year > _CURRENT_YEAR:       # 2-digit year fix: "89" → 1989
            year -= 100
        if not (1900 <= year <= _CURRENT_YEAR):
            return None
        return year, dt.month, dt.day, line.strip()
    except (_ParserError, ValueError, OverflowError):
        return None


def _parse_location(line):
    """
    Try to extract (city, state, country) from a line.
    Validates city against geonamescache; state/country against known codes.
    Returns (city, state, country) — any element may be None if not found.
    """
    line = line.strip()

    # Split on the LAST comma to handle "Music Hall, San Francisco, CA"
    if "," in line:
        left, region_raw = line.rsplit(",", 1)
        region_raw = region_raw.strip()
        city_raw   = left.strip()
    else:
        # No comma — last word might be a bare state code: "Ann Arbor MI"
        words = line.split()
        if len(words) < 2:
            return None, None, None
        region_raw = words[-1]
        city_raw   = " ".join(words[:-1])

    # Validate region (US state or country)
    region_up  = region_raw.upper()
    region_low = region_raw.lower()
    state   = None
    country = None

    if region_up in _US_STATE_CODES:
        state, country = region_up, "US"
    elif region_low in _US_STATE_NAMES:
        state, country = _US_STATE_NAMES[region_low], "US"
    elif region_low in _COUNTRY_NAMES:
        country = region_raw.title()
    else:
        return None, None, None     # unrecognised region → not a location line

    # Extract city — try last 1, 2, then 3 words of city_raw against known cities
    city_words = city_raw.split()
    city = None
    for n in (1, 2, 3):
        if len(city_words) >= n:
            candidate = " ".join(city_words[-n:])
            if candidate.lower() in _CITY_NAMES:
                city = candidate.title()
                break
    if city is None:
        # Region validated but city unknown (small/unusual) — accept last word
        city = city_words[-1].title() if city_words else None

    return city, state, country


def _extract_venue(header_lines):
    """
    Two-pass venue extraction:
      1. Positional: first line after artist that survives all filters (most reliable)
      2. Keyword scan on date/location lines we skipped (catches embedded venues like
         "1-28-89 Birchmere, Alexandria, VA")
    """
    skipped_date_loc = []   # date/location lines saved for keyword fallback

    # Pass 1 — positional
    for line in header_lines[1:]:
        low = line.lower()

        # Save date and location lines for keyword fallback, but skip them here
        is_date = _looks_like_date_line(line)
        city, state, country = _parse_location(line)
        is_location = bool(city or state or country)
        if is_date or is_location:
            skipped_date_loc.append(line)
            continue

        if any(lbl in low for lbl in _LINEAGE_LABELS):
            continue
        # Skip lines that start with a source keyword (e.g. "SBD (analog 4th gen...)")
        first_word = low.split()[0] if low.split() else ""
        if first_word in _SOURCE_KEYWORDS:
            continue
        # Skip band-member lines: "Firstname Lastname - instrument"
        if re.match(r"^[A-Z][a-z]+\s+[A-Z][a-z].*\s[-:]\s+[a-z]", line):
            continue
        # Skip short all-caps section labels (SETLIST, NOTES, etc.)
        if line.isupper() and len(line.split()) <= 2:
            continue
        # Skip numbered/ordinal event lines: "27. Internationale Jazzwoche", "3rd Jazz Festival"
        if re.match(r"^\d+(st|nd|rd|th)?\s*[.\s]", line, re.IGNORECASE):
            continue

        return line.strip()

    # Pass 2 — keyword scan on skipped date/location lines
    for line in skipped_date_loc:
        low = line.lower()
        if any(re.search(r"\b" + re.escape(w) + r"\b", low) for w in _VENUE_WORDS):
            segments = re.split(r",\s*|@\s*", line)
            for seg in segments:
                if any(re.search(r"\b" + re.escape(w) + r"\b", seg.lower()) for w in _VENUE_WORDS):
                    # Strip any leading date token (e.g. "1-28-89 Birchmere")
                    seg = re.sub(r"^\d{1,2}[-./]\d{1,2}[-./]\d{2,4}\s*", "", seg).strip()
                    if seg:
                        return seg

    return None


def _fuzzy_match(candidate, known_names, cutoff=0.85):
    """Return the best match from known_names above cutoff, or None."""
    if not known_names:
        return None
    norm       = candidate.title()
    norm_known = [n.title() for n in known_names]
    matches    = get_close_matches(norm, norm_known, n=1, cutoff=cutoff)
    if matches:
        return known_names[norm_known.index(matches[0])]
    return None


def _is_track_noise(title):
    """Return True if a matched track 'title' is actually noise — hash, filename, date fragment."""
    low = title.lower()
    if ".flac" in low:
        return True
    if re.search(r"\bflac\b", low):                           # audio format spec line
        return True
    if re.search(r"\bkhz\b", low):                            # audio spec line
        return True
    if re.match(r"^[\da-f]{8,}", low):                        # hex checksum
        return True
    if re.match(r"^\d{1,2}[-./]\d{4}$", title):              # date fragment "19-1978"
        return True
    if re.match(r"^\d{1,2}[-./]\d{1,2}", title) and len(title) < 15:  # short date range "6-15.1978"
        return True
    return False


def _read_text_auto(file_path):
    """
    Read a text file and return a clean unicode string regardless of encoding.
    Handles UTF-16 LE/BE (with BOM), UTF-8 BOM, and plain UTF-8/Latin-1.
    """
    with open(file_path, "rb") as fh:
        raw_bytes = fh.read()

    # Detect BOM and decode accordingly
    if raw_bytes.startswith(b"\xff\xfe"):          # UTF-16 LE BOM
        return raw_bytes.decode("utf-16-le", errors="replace").lstrip("﻿")
    if raw_bytes.startswith(b"\xfe\xff"):          # UTF-16 BE BOM
        return raw_bytes.decode("utf-16-be", errors="replace").lstrip("﻿")
    if raw_bytes.startswith(b"\xef\xbb\xbf"):      # UTF-8 BOM
        return raw_bytes[3:].decode("utf-8", errors="replace")

    # No BOM — try UTF-8, then Windows-1252 (covers ASCII, Latin-1, and CP1252 curly quotes etc.)
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return raw_bytes.decode("cp1252", errors="replace")


def parse_info_file(file_path, known_artists=None, known_venues=None):
    """
    Parse a ROIO info/text file and extract structured metadata suggestions.

    Args:
        file_path:      path to .txt info file
        known_artists:  list of artist name strings for fuzzy matching (optional)
        known_venues:   list of venue name strings for fuzzy matching (optional)

    Returns dict:
        raw_content, artist, artist_match, year, month, day, date_str,
        venue, venue_match, city, state, country, source, lineage,
        tracks [ {number, title} ]
    """
    try:
        raw = _read_text_auto(file_path)
    except OSError:
        return {"raw_content": "", "tracks": []}

    lines = raw.splitlines()

    # ── Pass 1: split into header block and track block ───────────────────────
    header_lines = []
    track_pairs  = []       # [(number, title), ...]
    in_tracks    = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        m = _TRACK_PATTERN.match(stripped)
        if m:
            num   = int(m.group(1))
            title = _title_case(_TRAILING_TS_RE.sub('', m.group(2).strip()))
            if not _is_track_noise(title) and (in_tracks or len(header_lines) >= 2):
                in_tracks = True
                track_pairs.append((num, title))
                continue

        if not in_tracks:
            header_lines.append(stripped)

    # ── Pass 2: extract fields from header ────────────────────────────────────
    result = {
        "raw_content":  raw,
        "artist":       None,
        "artist_match": None,
        "year":         None,
        "month":        None,
        "day":          None,
        "date_str":     None,
        "venue":        None,
        "venue_match":  None,
        "city":         None,
        "state":        None,
        "country":      None,
        "source":       None,
        "lineage":      None,
        "tracks":       [],
    }

    # Artist — first non-blank, non-filename line in the first 3 lines
    for line in header_lines[:3]:
        if not _is_filename_line(line) and not _looks_like_date_line(line):
            result["artist"]       = line.title()
            result["artist_match"] = _fuzzy_match(line, known_artists or [])
            break

    # Date — first header line with a strong date signal
    for line in header_lines:
        parsed = _parse_date(line)
        if parsed:
            result["year"], result["month"], result["day"], result["date_str"] = parsed
            break

    # Venue — keyword scan then positional fallback
    venue_raw = _extract_venue(header_lines)
    if venue_raw:
        result["venue"]       = venue_raw.title()
        result["venue_match"] = _fuzzy_match(venue_raw, known_venues or [])

    # City / State / Country — first header line that validates
    for line in header_lines:
        city, state, country = _parse_location(line)
        if city or state or country:
            result["city"]    = city
            result["state"]   = state
            result["country"] = country
            break

    # Source type — scan full file text for keywords
    full_low = raw.lower()
    for kw, val in _SOURCE_KEYWORDS.items():
        if kw in full_low:
            result["source"] = val
            break

    # Lineage — collect lines after an explicit lineage label
    lineage_buf = []
    in_lineage  = False
    for line in lines:
        low = line.strip().lower()
        if any(lbl in low for lbl in _LINEAGE_LABELS):
            in_lineage = True
        if in_lineage and line.strip():
            lineage_buf.append(line.strip())
    if lineage_buf:
        result["lineage"] = " ".join(lineage_buf)

    # Tracks
    result["tracks"] = [{"number": n, "title": t} for n, t in track_pairs]

    return result


def _titlecase(s):
    """Simple title-case that preserves all-caps abbreviations (SBD, AUD, etc.)."""
    words = s.split()
    out   = []
    for w in words:
        if w.upper() == w and len(w) > 1:
            out.append(w)
        else:
            out.append(w.capitalize())
    return " ".join(out)


# ── File system operations ─────────────────────────────────────────────────────

def move_to_library(source_folder, library_root, artist_name, folder_name, behavior="copy"):
    """
    Move or copy a source folder into the library under the artist directory.

    Args:
        source_folder : str  — absolute path to source folder
        library_root  : str  — LIBRARY_ROOT from config
        artist_name   : str  — canonical artist name (used as subdirectory)
        folder_name   : str  — canonical folder name from build_folder_name()
        behavior      : "copy" | "move"

    Returns:
        str — new folder path relative to library_root
    """
    dest_dir    = Path(library_root) / _sanitize_path(artist_name)
    dest_folder = dest_dir / folder_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    if behavior == "move":
        shutil.move(str(source_folder), str(dest_folder))
    else:
        shutil.copytree(str(source_folder), str(dest_folder))

    # Return path relative to library_root for storage in DB
    return str(dest_folder.relative_to(library_root))


def _sanitize_path(name):
    """Strip characters illegal in macOS directory names."""
    return re.sub(r'[:/\x00]', '-', name).strip()
