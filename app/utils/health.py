"""
app/utils/health.py — ingest-readiness health score for a scanned folder.

compute_health(scan) is a PURE function over the scan payload produced by
POST /api/recordings/scan (suggestions.from_tags + from_info_file + audio_files).
It returns a 0-100 score, a colour band, and an itemised list of factors so the
UI can show *why* a recording scored what it did — no opaque colours.

Weighting (max 100):
    Identity          30   artist 7 · date 10 · venue 7 · location 6
    Source agreement  15   tags+info agree 15 · single source 9 · disagree 4
    Tracks            35   titles 20 · files-vs-setlist reconcile 10 · numbering 5
    Audio integrity   12   all FLAC 12 · mixed 8 · WAV-only 5 · lossy 2   (NOT AI-recoverable)
    Provenance         8   source code 4 · lineage 4

Track titles are weighted heavily (20) and their absence GATES the band: a
recording with zero track titles can never score green, no matter how clean the
rest — a missing track listing is the thing people most want and most often lack.

Bands: >=85 green · 60-84 yellow · <60 red · no audio = 0/red.

Note: the signed rubric split provenance as source 4 / lineage 2 / taper 2, but the
info-file parser does not currently extract a taper field, so taper's 2 points are
folded into lineage (=4). Split them back out once taper parsing exists.
"""

import os


def _f(dimension, delta, msg, ai_recoverable):
    """One factor row (delta is the points LOST, i.e. negative)."""
    return {"dimension": dimension, "delta": int(delta), "msg": msg,
            "ai_recoverable": ai_recoverable}


def _norm(v):
    """Lowercase, strip, collapse internal whitespace — for lenient comparison."""
    return " ".join(str(v).strip().lower().split()) if v else ""


def _present(v):
    return bool(v and str(v).strip())


# ── Identity: date ────────────────────────────────────────────────────────────

def _date_precision(tags, info):
    """Best available date precision: 3=full, 2=Y-M, 1=Y, 0=none."""
    prec = 0
    cd = (tags.get("concert_date") or "").strip()
    if cd:
        parts = cd.split("-")
        if len(parts) >= 3 and all(parts[:3]):
            prec = 3
        elif len(parts) == 2 and all(parts):
            prec = 2
        elif parts and parts[0]:
            prec = 1
    if info.get("day"):
        prec = max(prec, 3)
    elif info.get("month"):
        prec = max(prec, 2)
    elif info.get("year"):
        prec = max(prec, 1)
    return prec


def _location_points(tags, info):
    city    = tags.get("city")    or info.get("city")
    country = tags.get("country") or info.get("country")
    pts = (4 if _present(city) else 0) + (2 if _present(country) else 0)
    if pts == 6:
        return 6, ""
    if pts == 0:
        return 0, "No location"
    return pts, ("No city (country only)" if not _present(city) else "No country")


# ── Source agreement ──────────────────────────────────────────────────────────

def _date_conflict(tags, info):
    """True if tags and info give genuinely different dates at overlapping precision."""
    cd = (tags.get("concert_date") or "").strip()
    if not cd or not info.get("year"):
        return False
    tp = cd.split("-")
    checks = [(0, "year"), (1, "month"), (2, "day")]
    for idx, key in checks:
        if idx < len(tp) and tp[idx] and info.get(key):
            try:
                if int(tp[idx]) != int(info[key]):
                    return True
            except (ValueError, TypeError):
                pass
    return False


def _agreement(tags, info, has_info):
    tags_present = any(_present(tags.get(k)) for k in ("artist", "venue", "concert_date", "city"))
    info_present = has_info and any(_present(info.get(k)) for k in ("artist", "venue", "year", "city"))

    if tags_present and info_present:
        disagree = []
        if _present(tags.get("venue")) and _present(info.get("venue")) \
                and _norm(tags["venue"]) != _norm(info["venue"]):
            disagree.append("venue")
        if _present(tags.get("city")) and _present(info.get("city")) \
                and _norm(tags["city"]) != _norm(info["city"]):
            disagree.append("city")
        if _date_conflict(tags, info):
            disagree.append("date")
        if disagree:
            return 4, "Tags and text file info disagree on " + ", ".join(disagree)
        return 15, ""

    if tags_present or info_present:
        return 9, "Single metadata source (%s)" % ("tags only" if tags_present else "info file only")
    return 0, "No metadata source"


# ── Tracks ────────────────────────────────────────────────────────────────────

def _title_coverage(tracks, n):
    if not n or not tracks:
        return 0.0
    titled = sum(1 for t in tracks if _present(t.get("title")))
    return min(1.0, titled / n)


def _tracks_points(tags, info, n_audio):
    msgs = []
    pts = 0
    tag_tracks  = tags.get("tracks") or []
    info_tracks = info.get("tracks") or []

    # Titles (20) — the single most valuable and most-missing field. Info-file
    # titles only count when the setlist reconciles 1:1 with the files; a mismatched
    # setlist can't be mapped. ZERO titles GATES the band (can't be green — a
    # recording with no track listing is not "complete", no matter how clean the rest).
    info_cov = _title_coverage(info_tracks, n_audio) if len(info_tracks) == n_audio else 0.0
    cov  = max(_title_coverage(tag_tracks, n_audio), info_cov)
    tpts = round(20 * cov)
    pts += tpts
    if tpts < 20:
        f_titles = _f("Tracks", tpts - 20,
                      "No track titles in file metadata or inferrable from info file"
                      if tpts == 0 else "Some tracks untitled", True)
        if tpts == 0:
            f_titles["gate"] = True
        msgs.append(f_titles)

    # Files-vs-setlist reconcile (10) — a mismatch GATES the band (can't be green:
    # we don't know which tracks the recording actually contains).
    if info_tracks:
        if len(info_tracks) == n_audio:
            pts += 10
        else:
            pts += 3
            n_info = len(info_tracks)
            factor = _f("Tracks", -7,
                        "%d audio files present, but %d track title%s inferred from info file"
                        % (n_audio, n_info, "" if n_info == 1 else "s"), True)
            factor["gate"] = True
            msgs.append(factor)
    else:
        pts += 6
        msgs.append(_f("Tracks", -4, "No setlist to reconcile against files", True))

    # Numbering (5) — from tag track numbers
    nums = [t.get("track_number") for t in tag_tracks if t.get("track_number")]
    if nums:
        try:
            s = sorted(int(n) for n in nums)
        except (ValueError, TypeError):
            s = []
        if not s:
            pts += 3; msgs.append(_f("Tracks", -2, "Unparseable track numbers", True))
        elif len(set(s)) != len(s):
            pts += 2; msgs.append(_f("Tracks", -3, "Duplicate track numbers", True))
        elif s == list(range(s[0], s[0] + len(s))):
            pts += 5
        else:
            pts += 2; msgs.append(_f("Tracks", -3, "Track number gaps", True))
    else:
        pts += 3; msgs.append(_f("Tracks", -2, "No track numbers", True))

    return pts, msgs


# ── Audio integrity (NOT AI-recoverable) ──────────────────────────────────────

_LOSSLESS = {".flac", ".alac", ".ape", ".wav", ".aiff", ".aif"}
_LOSSY    = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wma"}
_UNCOMPRESSED = {".wav", ".aiff", ".aif"}


def _audio_integrity(audio_files):
    exts = set()
    for f in audio_files:
        name = f.get("filename") or f.get("rel_path") or ""
        e = os.path.splitext(name)[1].lower()
        if e:
            exts.add(e)
    if not exts:
        return 12, ""  # unknown extension — don't penalize
    lossy = exts & _LOSSY
    if lossy:
        return 2, "Lossy audio (%s)" % "/".join(sorted(e[1:].upper() for e in lossy))
    if exts == {".flac"}:
        return 12, ""
    if exts <= _UNCOMPRESSED:
        return 5, "Uncompressed WAV/AIFF (not FLAC)"
    if ".flac" in exts and (exts & _UNCOMPRESSED):
        return 8, "Mixed FLAC + WAV"
    return 8, "Mixed lossless formats"


# ── Provenance ────────────────────────────────────────────────────────────────

def _provenance(tags, info):
    msgs = []
    pts = 0
    if _present(tags.get("source") or info.get("source")):
        pts += 4
    else:
        msgs.append(_f("Provenance", -4, "No source code (SBD/AUD)", True))
    if _present(tags.get("lineage") or info.get("lineage")):
        pts += 4
    else:
        msgs.append(_f("Provenance", -4, "No lineage", True))
    return pts, msgs


# ── Public entry point ────────────────────────────────────────────────────────

def compute_health(scan):
    """Score a scan payload. Returns {score, band, factors:[...]}."""
    sugg  = scan.get("suggestions") or {}
    tags  = sugg.get("from_tags") or {}
    info  = sugg.get("from_info_file") or {}
    audio_files = scan.get("audio_files") or []
    n_audio     = scan.get("audio_file_count") or len(audio_files)
    has_info    = bool(scan.get("info_file_content"))

    factors = []
    score = 0

    # Identity (30)
    if _present(tags.get("artist") or info.get("artist")):
        score += 7
    else:
        factors.append(_f("Identity", -7, "No artist name", True))

    date_pts = {3: 10, 2: 7, 1: 4, 0: 0}[_date_precision(tags, info)]
    score += date_pts
    if date_pts < 10:
        label = {7: "Date missing day", 4: "Year only", 0: "No date"}[date_pts]
        factors.append(_f("Identity", date_pts - 10, label, True))

    if _present(tags.get("venue") or info.get("venue")):
        score += 7
    else:
        factors.append(_f("Identity", -7, "No venue", True))

    loc_pts, loc_msg = _location_points(tags, info)
    score += loc_pts
    if loc_pts < 6:
        factors.append(_f("Identity", loc_pts - 6, loc_msg, True))

    # Source agreement (15)
    agr_pts, agr_msg = _agreement(tags, info, has_info)
    score += agr_pts
    if agr_pts < 15:
        factors.append(_f("Source agreement", agr_pts - 15, agr_msg, True))

    # Tracks (35)
    tr_pts, tr_msgs = _tracks_points(tags, info, n_audio)
    score += tr_pts
    factors.extend(tr_msgs)

    # Audio integrity (12)
    ai_pts, ai_msg = _audio_integrity(audio_files)
    score += ai_pts
    if ai_pts < 12:
        factors.append(_f("Audio integrity", ai_pts - 12, ai_msg, False))

    # Provenance (8)
    pv_pts, pv_msgs = _provenance(tags, info)
    score += pv_pts
    factors.extend(pv_msgs)

    score = max(0, min(100, round(score)))
    if n_audio == 0:
        score = 0
    band = "green" if score >= 85 else "yellow" if score >= 60 else "red"

    # Gate: an unresolved content-integrity factor (e.g. files-vs-setlist mismatch)
    # can't score green regardless of how clean the rest of the metadata is.
    if band == "green" and any(f.get("gate") for f in factors):
        band = "yellow"

    # Largest losses first, so the banner can show the top few
    factors.sort(key=lambda x: x["delta"])
    return {"score": score, "band": band, "factors": factors}
