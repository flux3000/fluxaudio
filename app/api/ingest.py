"""
api/ingest.py — Full ingest confirmation endpoint.

POST /api/ingest/confirm

Handles the full "resolve or create" chain from a single payload:
  CanonicalArtist → Artist → Venue (optional) → Performance → Recording + Tracks

This avoids the frontend needing to pre-resolve IDs. The user just
provides names and dates; this endpoint does the lookup/create work.
"""

import os
import json
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from sqlalchemy import func

_AUDIO_EXTS = {'.flac', '.mp3', '.wav', '.aiff', '.aif', '.m4a', '.ogg', '.ape', '.wv'}

from app.extensions import db
from app.models.performer import Performer
from app.models.artist import Artist, Membership
from app.utils.performers import resolve_or_create_performer, set_performer_members
from app.models.venue import Venue
from app.models.event import Event
from app.models.performance import Performance
from app.models.recording import Recording, RecordingFingerprint
from app.models.recording_event import RecordingEvent
from app.models.track import Track
from app.models.user_preference import UserPreference
from app.utils.ingest import move_to_library
from app.utils.folder_naming import build_folder_name
from app.utils.ai_assist import run_ai_assist, AiAssistError
from app.utils.prefs import get_api_key, get_pref
from app.utils.health import compute_health

bp = Blueprint("ingest", __name__)


@bp.route("/health", methods=["POST"])
@login_required
def health():
    """
    POST /api/ingest/health
    Thin wrapper over compute_health(scan). The client sends a scan-shaped
    payload (original for the initial score, or proposal-overlaid for the
    projected post-AI score) and gets back {score, band, factors}.
    """
    return jsonify(compute_health(request.get_json() or {}))


# In-memory AI-research jobs. The research call is far too slow (30-90s) to hold
# a synchronous HTTP request open — the webview aborts the fetch at ~60s. So we run
# it in a background thread and let the client poll for the result.
_AI_JOBS = {}  # job_id -> {"status": running|done|error, "result"/"error", "t0"}


def _run_ai_job(job_id, folder_path, current, api_key, model):
    import time as _time
    import traceback as _tb
    t0 = _time.time()
    try:
        result = run_ai_assist(folder_path, current, api_key, model)
        _AI_JOBS[job_id] = {"status": "done", "result": result}
        print("[ai-assist] job %s ok in %.1fs" % (job_id[:8], _time.time() - t0), flush=True)
    except AiAssistError as e:
        _AI_JOBS[job_id] = {"status": "error", "error": str(e)}
        print("[ai-assist] job %s failed after %.1fs: %s" % (job_id[:8], _time.time() - t0, e), flush=True)
    except Exception as e:  # noqa: BLE001
        _tb.print_exc()
        _AI_JOBS[job_id] = {"status": "error", "error": "Unexpected error: %s" % e}


@bp.route("/ai-assist", methods=["POST"])
@login_required
def ai_assist():
    """
    POST /api/ingest/ai-assist
    Kick off an AI research job (background thread) and return a job id
    immediately. Poll GET /api/ingest/ai-assist/<job_id> for the result.

    Body: { folder_path, current: {...} }
    """
    import threading
    import uuid

    data        = request.get_json() or {}
    folder_path = (data.get("folder_path") or "").strip()
    if not folder_path or not os.path.isdir(folder_path):
        return jsonify({"error": "Invalid or inaccessible folder path"}), 400

    api_key = get_api_key(current_user.id)
    if not api_key:
        # 428 → frontend routes the user to add their key in Settings.
        return jsonify({"error": "no_api_key"}), 428
    model = get_pref(current_user.id, "ai_model") or "claude-sonnet-5"

    job_id = uuid.uuid4().hex
    _AI_JOBS[job_id] = {"status": "running"}
    # Key/model are resolved here (request context); the thread needs no DB access.
    threading.Thread(
        target=_run_ai_job,
        args=(job_id, folder_path, data.get("current") or {}, api_key, model),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id}), 202


@bp.route("/ai-assist/<job_id>", methods=["GET"])
@login_required
def ai_assist_status(job_id):
    """Poll an AI research job. Returns running, or done+result / error (one-shot)."""
    job = _AI_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    if job["status"] == "running":
        return jsonify({"status": "running"})
    _AI_JOBS.pop(job_id, None)  # deliver terminal state once, then discard
    if job["status"] == "error":
        return jsonify({"status": "error", "error": job["error"]})
    return jsonify({"status": "done", "result": job["result"]})


@bp.route("/confirm", methods=["POST"])
@login_required
def confirm_ingest():
    """
    Resolve or create the full object chain, then ingest the recording.

    Expected payload:
    {
      "source_folder_path": "/absolute/path/to/source",
      "artist_name":        "Grateful Dead",
      "start_year":         1972,
      "start_month":        9,
      "start_day":          3,
      "venue_name":         "CU Events Center",   # optional
      "city":               "Boulder",
      "state":              "CO",
      "country":            "US",                 # optional
      "source":             "SBD",
      "source_modifier":    null,
      "quality":            "B+",
      "lineage":            "...",
      "notes":              "",
      "is_complete":        true,
      "info_file_content":  "...",
      "event_name":         "Bonnaroo 2009",  # optional — name-resolved to Event record
      "event_id":           null,             # optional — use existing Event ID directly
      "fingerprints":       [{"type":"ffp","filename":"...","content":"..."}],
      "tracks": [
        {"track_number":1,"title":"Dark Star","set":"Set 1","duration":1200,"filename":"t01.flac"}
      ]
    }
    """
    data = request.get_json()

    source_folder = (data.get("source_folder_path") or "").strip()
    artist_name   = (data.get("artist_name")        or "").strip()

    if not source_folder or not os.path.isdir(source_folder):
        return jsonify({"error": f"Source folder not found: {source_folder!r}"}), 400
    if not artist_name:
        return jsonify({"error": "artist_name is required"}), 400

    city        = (data.get("city")       or "").strip() or None
    state       = (data.get("state")      or "").strip() or None
    country     = (data.get("country")    or "").strip() or None
    venue_name  = (data.get("venue_name") or "").strip() or None
    event_name  = (data.get("event_name") or "").strip() or None
    start_year  = data.get("start_year")
    start_month = data.get("start_month")
    start_day   = data.get("start_day")
    end_year    = data.get("end_year")
    end_month   = data.get("end_month")
    end_day     = data.get("end_day")

    # ── 1. Find or create Performer (the act) + its member Artists ────────────
    # `members` is an ordered list of Artist (person) names. A new Performer
    # auto-seeds a single member matching its name; if members are supplied they
    # set the roster (applies to an existing Performer too — members belong to
    # the act, not the show).
    member_names = data.get("members") or []
    performer = resolve_or_create_performer(artist_name)
    if member_names:
        set_performer_members(performer, member_names)

    # ── 3. Find or create Venue (optional) ────────────────────────────────────
    venue = None
    venue_id_in = data.get("venue_id")
    if venue_id_in:
        # User selected an existing venue — use it directly
        venue = db.session.get(Venue, int(venue_id_in))
    elif venue_name:
        # No id — look up by name or create new
        venue = db.session.query(Venue).filter(
            func.lower(Venue.name) == venue_name.lower()
        ).first()
        if not venue:
            venue = Venue(
                name    = venue_name,
                city    = city    or None,
                state   = state   or None,
                country = country or None,
            )
            db.session.add(venue)
            db.session.flush()

    # ── 3.5. Find or create Event (optional) ─────────────────────────────────
    event = None
    event_id_in = data.get("event_id")
    if event_id_in:
        event = db.session.get(Event, int(event_id_in))
    elif event_name:
        event = db.session.query(Event).filter(
            func.lower(Event.name) == event_name.lower()
        ).first()
        if not event:
            event = Event(
                name    = event_name,
                city    = city    or None,
                state   = state   or None,
                country = country or None,
            )
            db.session.add(event)
            db.session.flush()

    # ── 4. Find or create Performance ─────────────────────────────────────────
    perf_q = db.session.query(Performance).filter(
        Performance.performer_id == performer.id,
        Performance.start_year  == start_year,
        Performance.start_month == start_month,
        Performance.start_day   == start_day,
    )
    if venue:
        perf_q = perf_q.filter(Performance.venue_id == venue.id)

    performance = perf_q.first()
    if not performance:
        performance = Performance(
            performer_id = performer.id,
            venue_id     = venue.id  if venue  else None,
            event_id     = event.id  if event  else None,
            start_year   = start_year,
            start_month  = start_month,
            start_day    = start_day,
            end_year     = end_year,
            end_month    = end_month,
            end_day      = end_day,
            # Location fallback when no venue record
            city    = city    if not venue else None,
            state   = state   if not venue else None,
            country = country if not venue else None,
        )
        db.session.add(performance)
        db.session.flush()

    # ── 5. Build canonical folder name ────────────────────────────────────────
    folder_name = build_folder_name(
        artist_name     = artist_name,
        start_year      = start_year,
        start_month     = start_month,
        start_day       = start_day,
        venue_name      = venue_name,
        city            = city,
        state           = state,
        country         = country,
        source          = data.get("source"),
        source_modifier = data.get("source_modifier"),
    )

    # ── 6. Move / copy folder into library ────────────────────────────────────
    # Behavior precedence: explicit request payload → saved user preference →
    # "copy" (safe default — never destroy the source unless asked to).
    behavior = (data.get("behavior") or "").strip().lower()
    if behavior not in ("move", "copy"):
        pref = db.session.query(UserPreference).filter_by(
            user_id=current_user.id, key="ingest_file_behavior"
        ).first()
        behavior = pref.value if pref else "copy"
    library_root = str(current_app.config["LIBRARY_ROOT"])

    try:
        new_folder_path = move_to_library(
            source_folder = source_folder,
            library_root  = library_root,
            artist_name   = artist_name,
            folder_name   = folder_name,
            behavior      = behavior,
        )
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"File operation failed: {str(e)}"}), 500

    # ── 7. Create Recording ───────────────────────────────────────────────────
    rec_is_official = bool(data.get("is_official", False))
    try:
        rating_val = int(data["rating"]) if data.get("rating") not in (None, "") else None
    except (ValueError, TypeError):
        rating_val = None
    rec = Recording(
        performance_id       = performance.id,
        source               = data.get("source"),
        source_modifier      = data.get("source_modifier"),
        lineage              = data.get("lineage"),
        quality              = data.get("quality"),
        rating               = rating_val,
        is_complete          = data.get("is_complete", True),
        is_official          = rec_is_official,
        folder_path          = new_folder_path,
        original_folder_name = os.path.basename(source_folder),
        info_file_content    = data.get("info_file_content"),
        notes                = data.get("notes"),
    )
    db.session.add(rec)
    db.session.flush()

    # ── 8. Create Tracks ──────────────────────────────────────────────────────
    for t in data.get("tracks", []):
        # If recording is marked official, cascade to all tracks
        track_official = rec_is_official or bool(t.get("is_official", False))
        flags_raw      = t.get("flags") or []
        db.session.add(Track(
            recording_id = rec.id,
            track_number = t.get("track_number"),
            title        = t.get("title") or f"Track {t.get('track_number', '?')}",
            set          = t.get("set") or None,
            duration     = t.get("duration"),
            file_path    = t.get("filename", ""),
            is_official  = track_official,
            flags        = json.dumps(flags_raw) if flags_raw else None,
            songwriter   = t.get("songwriter") or None,
            notes        = t.get("notes") or None,
        ))

    # ── 9. Store fingerprints ─────────────────────────────────────────────────
    for fp in data.get("fingerprints", []):
        db.session.add(RecordingFingerprint(
            recording_id     = rec.id,
            fingerprint_type = fp.get("type"),
            filename         = fp.get("filename"),
            content          = fp.get("content"),
        ))

    # ── 10. Irrevocable ingest event ──────────────────────────────────────────
    db.session.add(RecordingEvent(
        recording_id = rec.id,
        user_id      = current_user.id,
        event_type   = "ingested",
        note         = f"behavior={behavior} original={os.path.basename(source_folder)}",
    ))

    db.session.commit()

    return jsonify({
        "recording_id":  rec.id,
        "performer_id":  performer.id,
        "folder_name":   folder_name,
        "event_id":      event.id if event else None,
    }), 201


import re as _re
from collections import Counter as _Counter

_TEXT_EXTS   = {'.txt', '.nfo', '.md', '.text', '.log'}
_LOSSY_EXTS  = {'.mp3', '.aac', '.ogg', '.m4a'}
_LOSSLESS_EXTS = {'.flac', '.wav', '.aiff', '.aif', '.ape', '.wv'}
_DATE_RE     = _re.compile(r'\b(19|20)\d{2}[-._](0[1-9]|1[0-2])[-._](0[1-9]|[12]\d|3[01])\b')
_TRACK_NUM_RE = _re.compile(r'^(?:track\s*)?(\d{1,3})[.\s\-_]', _re.IGNORECASE)


def _audit_incoming_folder(folder_path):
    """
    Lightweight walk of an incoming folder. Returns (audio_count, size_mb, issues).
    No parsing of file content — pure filesystem inspection.
    """
    issues   = []
    ext_sets = _Counter()   # ext → count of audio files with that ext
    total_bytes = 0
    has_text    = False
    track_nums  = []
    subdir_count = 0        # how many subdirs contain audio
    audio_in_root = 0

    for root, dirs, files in os.walk(folder_path):
        dirs.sort()
        depth = root[len(folder_path):].count(os.sep)
        has_audio_here = False

        for fname in files:
            ext  = os.path.splitext(fname)[1].lower()
            fpath = os.path.join(root, fname)
            try:
                fsize = os.path.getsize(fpath)
            except OSError:
                fsize = 0

            if ext in _AUDIO_EXTS:
                ext_sets[ext] += 1
                total_bytes   += fsize
                has_audio_here = True
                if depth == 0:
                    audio_in_root += 1
                # try to parse track number from filename
                m = _TRACK_NUM_RE.match(fname)
                if m:
                    track_nums.append(int(m.group(1)))

            if ext in _TEXT_EXTS:
                has_text = True

        if has_audio_here and depth > 0:
            subdir_count += 1

    audio_count = sum(ext_sets.values())
    size_mb     = round(total_bytes / (1024 * 1024), 1)

    # ── Issue checks ──────────────────────────────────────────────────────────

    if audio_count == 0:
        issues.append({"severity": "error", "msg": "No audio files"})
        return audio_count, size_mb, issues

    # Format checks
    exts_present = set(ext_sets.keys())
    lossy  = exts_present & _LOSSY_EXTS
    flacs  = ext_sets.get('.flac', 0)
    wavs   = ext_sets.get('.wav', 0) + ext_sets.get('.aiff', 0) + ext_sets.get('.aif', 0)

    if lossy:
        labels = '/'.join(e.lstrip('.').upper() for e in sorted(lossy))
        issues.append({"severity": "warn", "msg": f"Lossy format ({labels})"})
    elif wavs and not flacs:
        issues.append({"severity": "warn", "msg": "WAV/AIFF — not FLAC"})
    elif wavs and flacs:
        issues.append({"severity": "warn", "msg": "Mixed FLAC + WAV"})
    elif len(exts_present & _LOSSLESS_EXTS) > 1:
        issues.append({"severity": "warn", "msg": "Mixed lossless formats"})

    # Text file
    if not has_text:
        issues.append({"severity": "warn", "msg": "No text file"})

    # Track numbering
    if track_nums:
        dupes = [n for n, c in _Counter(track_nums).items() if c > 1]
        if dupes:
            issues.append({"severity": "warn",
                           "msg": f"Duplicate track numbers: {sorted(dupes)}"})
        else:
            nums = sorted(track_nums)
            expected = list(range(nums[0], nums[0] + len(nums)))
            gaps = sorted(set(expected) - set(nums))
            if gaps:
                issues.append({"severity": "warn",
                               "msg": f"Track number gap(s): {gaps}"})
    else:
        # Audio files but none have leading track numbers
        issues.append({"severity": "info", "msg": "No track numbers in filenames"})

    # Multi-set / multi-disc detection
    if subdir_count >= 2:
        issues.append({"severity": "info",
                       "msg": f"Multi-set ({subdir_count} subdirs with audio)"})
    elif subdir_count == 1 and audio_in_root == 0:
        # All audio in a single subdir — usually fine but worth flagging
        issues.append({"severity": "info", "msg": "Audio in subdir"})

    # Folder name — should contain a date
    fname = os.path.basename(folder_path)
    if not _DATE_RE.search(fname):
        issues.append({"severity": "info", "msg": "No date in folder name"})

    return audio_count, size_mb, issues


@bp.route("/batch-scan", methods=["POST"])
@login_required
def batch_scan():
    """
    POST /api/ingest/batch-scan
    Walk a source directory, scan every show subfolder, and return a
    confidence-tiered list of candidates for batch ingest.

    Request body:
      { "source_dir": "/absolute/path/to/Import Processed" }

    Response: list of candidates, each with:
      - name, path, audio_count, size_mb, issues
      - tier: "green" | "yellow" | "red"
      - confidence: { artist, date, tracks, venue }  — per-field scores
      - extracted: { artist, year, month, day, venue, city, state, country,
                     source, lineage, track_count, tracks_titled }
      - already_ingested: bool  (folder path already in DB)
    """
    from app.utils.ingest import scan_folder, read_flac_tags, parse_info_file
    from app.models.recording import Recording

    data       = request.get_json() or {}
    source_dir = (data.get("source_dir") or "").strip()

    if not source_dir or not os.path.isdir(source_dir):
        return jsonify({"error": f"Directory not found: {source_dir!r}"}), 400

    # Known artist names for fuzzy artist matching
    known_performers = [p.name for p in db.session.query(Performer.name).all()]

    # Already-ingested folder paths (relative or basename match)
    ingested_paths = {
        os.path.basename(r.folder_path)
        for r in db.session.query(Recording.folder_path).all()
    }

    results = []

    def _root_audio_count(path):
        """Count audio files directly in path (non-recursive)."""
        try:
            return sum(
                1 for f in os.scandir(path)
                if f.is_file() and os.path.splitext(f.name)[1].lower() in _AUDIO_EXTS
            )
        except OSError:
            return 0

    def _audio_subdirs(path):
        """Return immediate subdirs of path that contain audio (at any depth)."""
        result = []
        try:
            for sub in os.scandir(path):
                if not sub.is_dir():
                    continue
                # Quick check: any audio anywhere under sub
                for _, _, files in os.walk(sub.path):
                    if any(os.path.splitext(f)[1].lower() in _AUDIO_EXTS for f in files):
                        result.append(sub)
                        break
        except OSError:
            pass
        return result

    def _resolve_shows(path):
        """
        Recursively resolve a directory to its actual show-level paths.

        Logic:
          - Has root audio → it's a show, return it.
          - Has ≥2 audio-containing subdirs → grouping folder, expand each recursively.
          - Has exactly 1 audio-containing subdir → could be a transparent wrapper
            (e.g. 'flac/') OR another nesting level; recurse to find out.
          - Has no audio at all → return it as-is (gets red tier from scanner).
        """
        if _root_audio_count(path) > 0:
            return [path]
        subs = _audio_subdirs(path)
        if not subs:
            return [path]
        if len(subs) == 1:
            # Could be 'flac/' wrapper or another artist-grouping level — recurse
            return _resolve_shows(subs[0].path)
        # Multiple audio subdirs → grouping folder, expand each
        result = []
        for sub in sorted(subs, key=lambda e: e.name.lower()):
            result.extend(_resolve_shows(sub.path))
        return result

    # Resolve every top-level entry to its actual show paths before scanning.
    # This handles arbitrary nesting depth (artist → year → show, etc.)
    show_paths = []
    for entry in sorted(os.scandir(source_dir), key=lambda e: e.name.lower()):
        if entry.is_dir():
            show_paths.extend(_resolve_shows(entry.path))

    for folder_path in show_paths:
        already_ingested = os.path.basename(folder_path) in ingested_paths

        # ── Filesystem audit ──────────────────────────────────────────────────
        audio_count, size_mb, issues = _audit_incoming_folder(folder_path)

        if audio_count == 0:
            results.append({
                "name": os.path.basename(folder_path), "path": folder_path,
                "audio_count": 0, "size_mb": size_mb,
                "tier": "red", "issues": issues,
                "confidence": {}, "extracted": {},
                "already_ingested": already_ingested,
            })
            continue

        # ── Tag + info file scan ───────────────────────────────────────────────
        try:
            files    = scan_folder(folder_path)
            tags     = read_flac_tags(files["audio_files"])
            tag_c    = tags["container"]
            tag_trks = tags["tracks"]
        except Exception:
            tag_c    = {}
            tag_trks = []

        info_parsed = {}
        if files.get("text_files"):
            try:
                info_parsed = parse_info_file(
                    files["text_files"][0]["path"],
                    known_artists=known_performers,
                )
            except Exception:
                info_parsed = {}

        # ── Field resolution: tags win, info file fills gaps ──────────────────
        # Artist
        artist = (tag_c.get("artist") or info_parsed.get("artist") or "").strip()
        artist_in_db = bool(
            artist and any(
                artist.lower() == p.lower() for p in known_performers
            )
        )
        artist_fuzzy = bool(info_parsed.get("artist_match"))

        # Date — prefer CONCERTDATE tag, then individual fields from info file
        concert_date_tag = tag_c.get("concert_date") or ""
        year = month = day = None
        if concert_date_tag:
            # Parse "YYYY-MM-DD" or "YYYY-MM" or "YYYY"
            import re as _re2
            m = _re2.match(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", concert_date_tag)
            if m:
                year  = int(m.group(1))
                month = int(m.group(2)) if m.group(2) else None
                day   = int(m.group(3)) if m.group(3) else None
        if not year:
            year  = info_parsed.get("year")
            month = info_parsed.get("month")
            day   = info_parsed.get("day")
        # Also try folder name if still no date
        if not year:
            m = _DATE_RE.search(os.path.basename(folder_path))
            if m:
                year  = int(m.group(0)[:4])
                month = int(m.group(0)[5:7])
                day   = int(m.group(0)[8:10])

        # Tracks
        titled_count = sum(
            1 for t in tag_trks if t.get("title") and t["title"].strip()
        )
        info_tracks  = info_parsed.get("tracks", [])

        # Venue / location
        venue   = (tag_c.get("venue")   or info_parsed.get("venue")   or "").strip() or None
        city    = (info_parsed.get("city")    or "").strip() or None
        state   = (info_parsed.get("state")   or "").strip() or None
        country = (info_parsed.get("country") or "").strip() or None
        # Try parsing CONCERTLOCATION tag
        location_tag = tag_c.get("location") or ""
        if location_tag and not (city or state):
            parts = [p.strip() for p in location_tag.split(",")]
            if len(parts) == 3:
                city, state, country = parts[0], parts[1], parts[2]
            elif len(parts) == 2:
                city, country = parts[0], parts[1]

        source  = (tag_c.get("source")  or info_parsed.get("source")  or "").strip() or None
        lineage = (tag_c.get("lineage") or info_parsed.get("lineage") or "").strip() or None

        # ── Confidence scoring ────────────────────────────────────────────────
        # Each dimension: "high" | "medium" | "low"

        # Artist confidence — measures name clarity, not DB presence
        # (bulk import creates new artists; DB match is a nice-to-have signal only)
        if artist_in_db:
            conf_artist = "high"    # exact DB match
        elif artist and artist_fuzzy:
            conf_artist = "medium"  # fuzzy DB match
        elif artist:
            conf_artist = "low"     # name found in tags/folder, not yet in DB
        else:
            conf_artist = "none"    # no artist name found at all → blocks green

        # Date confidence
        if year and month and day:
            conf_date = "high"
        elif year and month:
            conf_date = "medium"
        elif year:
            conf_date = "low"
        else:
            conf_date = "none"

        # Track title confidence
        if titled_count == audio_count and audio_count > 0:
            conf_tracks = "high"
        elif titled_count > 0:
            conf_tracks = "medium"
        elif info_tracks:
            conf_tracks = "medium"   # info file has titles even if tags don't
        else:
            conf_tracks = "low"

        # Venue confidence (nice-to-have, doesn't block green)
        conf_venue = "high" if venue else "low"

        # ── Tier assignment ───────────────────────────────────────────────────
        # Green: have an artist name + full date + tracks titled + no count mismatch.
        #        DB match is irrelevant — bulk import creates new artists by design.
        # Red:   artist name or date completely missing
        # Yellow: everything else (partial date, no track titles, count mismatch, etc.)
        info_count = len(info_tracks)
        info_count_mismatch = info_count > 0 and info_count != audio_count
        if (conf_artist != "none"
                and conf_date == "high"
                and conf_tracks in ("high", "medium")
                and not info_count_mismatch):
            tier = "green"
        elif conf_artist == "none" or conf_date == "none":
            tier = "red"
        else:
            tier = "yellow"

        results.append({
            "name":        os.path.basename(folder_path),
            "path":        folder_path,
            "audio_count": audio_count,
            "size_mb":     size_mb,
            "tier":        tier,
            "issues":      issues,
            "confidence": {
                "artist": conf_artist,
                "date":   conf_date,
                "tracks": conf_tracks,
                "venue":  conf_venue,
            },
            "extracted": {
                "artist":        artist or None,
                "year":          year,
                "month":         month,
                "day":           day,
                "venue":         venue,
                "city":          city,
                "state":         state,
                "country":       country,
                "source":        source,
                "lineage":       lineage,
                "track_count":      audio_count,
                "tracks_titled":    titled_count,
                "info_track_count": len(info_tracks),  # total from info file (not capped)
            },
            "already_ingested": already_ingested,
        })

    # Sort: green first, then yellow, then red; alpha within tier
    tier_order = {"green": 0, "yellow": 1, "red": 2}
    results.sort(key=lambda r: (tier_order[r["tier"]], r["name"].lower()))

    green  = sum(1 for r in results if r["tier"] == "green")
    yellow = sum(1 for r in results if r["tier"] == "yellow")
    red    = sum(1 for r in results if r["tier"] == "red")

    return jsonify({
        "source_dir": source_dir,
        "total":  len(results),
        "green":  green,
        "yellow": yellow,
        "red":    red,
        "items":  results,
    })


# NOTE: the standalone _incoming/ queue was removed — per-recording health is now
# surfaced in the Add Recording review step (see compute_health). _audit_incoming_folder
# is retained (used by batch-scan's filesystem audit).
