"""
api/quality.py — Listening Quality: analysis jobs and triage.

The first stage of the unified ingestion flow.  A folder (one show or a parent
holding many) is resolved to its shows, each is analysed, and the user accepts
or rejects each one before any metadata work happens.  LQ goes first because it
is the cheap objective gate: metadata review is the expensive human step, and
there is no point spending it on a recording that is not worth keeping.

Routes:
    POST /api/quality/analyze          start a background analysis job
    GET  /api/quality/analyze/<job_id> poll progress + results so far
    POST /api/quality/triage           accept / reject / reset one folder
    POST /api/quality/triage-bulk      accept or reject many at once
    GET  /api/quality/staging          rows for one scanned directory
    GET  /api/quality/recording/<id>   permanent score for one recording

Deliberately reused rather than rebuilt:
  * `utils.ingest.resolve_shows_in_dir()` — the same show resolution batch
    scanning uses, so the two lists can never disagree.
  * `/api/stream/ingest-preview` — pre-ingest audio playback already exists and
    is already IMPORT_ROOTS-guarded.  The standalone harness's own streaming
    endpoint deliberately does NOT come across.

Job state is in-memory, following the `/api/ingest/confirm` pattern.  It does
not survive a restart — acceptable because the STAGING ROWS are the durable
part: a restart mid-run loses the progress bar, not the analysis.
"""

import os
import threading
import traceback as _tb
import uuid

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required

from app.extensions import db
from app.utils import quality_store as qs
from app.utils.ingest import resolve_shows_in_dir

bp = Blueprint("quality", __name__)

# job_id → {status, total, done, current, folders, error}
_QUALITY_JOBS = {}


# ═════════════════════════════════════════════════════════════════════════════
# Analysis job
# ═════════════════════════════════════════════════════════════════════════════
def _analyse_one(folder_path, source_dir):
    """
    Analyse a single show folder and upsert its staging row.

    Never raises: a folder that fails to decode records its error on the row so
    the UI can show WHY a card is empty.  One bad folder must not abort a
    50-folder run.
    """
    # Imported lazily — numpy/scipy/soundfile are heavyweight and only the
    # analysis path needs them, so app boot stays fast.
    from app.utils.quality import (extract_recording_features, score_recording,
                                   guess_source_from_name)

    name = os.path.basename(folder_path.rstrip("/"))
    try:
        features = extract_recording_features(folder_path)
        if "error" in features:
            qs.upsert_staging(folder_path, source_dir=source_dir, name=name,
                              error=str(features["error"]))
            return
        # Source is read off the folder name because this runs at TRIAGE time —
        # there is no Recording row yet. It matters: source is the strongest
        # single predictor of grade in the whole model (CV r = +0.314 on its
        # own), so skipping it here would throw away the largest accuracy gain
        # of the 2026-07-31 rework. Unreadable source is neutral, not a penalty.
        scored = score_recording(features,
                                 source=guess_source_from_name(name))
        qs.upsert_staging(folder_path, source_dir=source_dir, name=name,
                          scored=scored, features=features, error=None)
    except Exception as e:  # noqa: BLE001
        _tb.print_exc()
        try:
            qs.upsert_staging(folder_path, source_dir=source_dir, name=name,
                              error=str(e))
        except Exception:  # noqa: BLE001
            _tb.print_exc()


def _run_quality_job(job_id, app, source_dir, folders, reanalyze):
    """Background worker: analyse each folder in turn, updating job progress."""
    job = _QUALITY_JOBS[job_id]
    try:
        with app.app_context():
            for i, folder in enumerate(folders):
                job["current"] = os.path.basename(folder.rstrip("/"))
                job["done"] = i
                # Skip folders already analysed at the current engine version
                # unless explicitly asked to redo them — re-decoding audio is
                # the only genuinely slow thing here.
                if not reanalyze and _is_current(folder):
                    continue
                _analyse_one(folder, source_dir)
            job["done"] = len(folders)
            job["current"] = None
            job["status"] = "done"
    except Exception as e:  # noqa: BLE001
        _tb.print_exc()
        job["error"] = str(e)
        job["status"] = "error"


def _is_current(folder_path):
    """
    True when this folder already has an analysis at the CURRENT engine version.

    Only `analysis_version` gates re-analysis.  A `score_version` bump is
    handled by re-scoring stored features with no audio decode — that split is
    the entire reason extraction and scoring are separate modules.
    """
    from app.utils.quality import QUALITY_ANALYSIS_VERSION

    row = qs.get_staging(folder_path)
    return (row is not None
            and row.error is None
            and row.analysis_version == QUALITY_ANALYSIS_VERSION)


@bp.route("/analyze", methods=["POST"])
@login_required
def analyze():
    """
    POST /api/quality/analyze
      { "source_dir": "/path/to/folder", "reanalyze": false }

    Resolves the directory to its show folders and starts a background job.
    Returns immediately with a job_id plus the resolved folder list, so the UI
    can render one placeholder card per show before any analysis finishes.
    """
    data = request.get_json() or {}
    source_dir = (data.get("source_dir") or "").strip()
    reanalyze = bool(data.get("reanalyze"))

    if not source_dir or not os.path.isdir(source_dir):
        return jsonify({"error": f"Directory not found: {source_dir!r}"}), 400

    # A single show folder passed directly is a legitimate case (it resolves to
    # itself), which is exactly why single-folder and bulk stopped being two
    # different features.
    from app.utils.ingest import resolve_shows
    folders = (resolve_shows(source_dir)
               if _has_root_audio(source_dir)
               else resolve_shows_in_dir(source_dir))

    if not folders:
        return jsonify({"error": "No audio folders found under that directory."}), 400

    job_id = uuid.uuid4().hex
    _QUALITY_JOBS[job_id] = {
        "status": "running", "total": len(folders), "done": 0,
        "current": None, "error": None,
    }
    threading.Thread(
        target=_run_quality_job,
        args=(job_id, current_app._get_current_object(),
              source_dir, folders, reanalyze),
        daemon=True,
    ).start()

    return jsonify({
        "job_id": job_id,
        "source_dir": qs.norm_path(source_dir),
        "folders": [{"folder_path": qs.norm_path(f),
                     "name": os.path.basename(f.rstrip("/"))} for f in folders],
    }), 202


def _has_root_audio(path):
    from app.utils.ingest import _root_audio_count
    return _root_audio_count(path) > 0


@bp.route("/analyze/<job_id>", methods=["GET"])
@login_required
def analyze_status(job_id):
    """
    Poll an analysis job.

    Returns the CURRENT staging rows every time, not just at the end, so cards
    fill in as each ~2 s analysis lands rather than the user watching a bar for
    a minute.  The job entry is kept until the client has seen "done" once.
    """
    job = _QUALITY_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404

    source_dir = request.args.get("source_dir")
    rows = qs.list_staging(source_dir) if source_dir else []

    payload = {
        "status": job["status"],
        "total": job["total"],
        "done": job["done"],
        "current": job.get("current"),
        "results": [qs.serialize(r, include_features=True) for r in rows],
    }
    _attach_interpretation(payload["results"])
    _attach_metadata(payload["results"])
    if job["status"] == "error":
        payload["error"] = job["error"]
    if job["status"] in ("done", "error"):
        _QUALITY_JOBS.pop(job_id, None)
    return jsonify(payload)


# ═════════════════════════════════════════════════════════════════════════════
# Metadata completeness — the OTHER score on each triage card
# ═════════════════════════════════════════════════════════════════════════════
# folder_path → (folder_mtime, payload). A scan opens EVERY FLAC in the folder
# with mutagen to read its tags (~0.2 s for a 20-track show), and the triage
# page polls every ~1 s while analysing — so without this a 30-show bulk run
# would re-read several hundred FLAC headers per second for no reason.
#
# Keyed on the folder's own mtime so an edit to the info file, or files being
# added/removed, invalidates it naturally. Bounded because a single triage run
# only ever touches the folders under one scanned directory.
_META_CACHE = {}
_META_CACHE_MAX = 500


def _scan_metadata(folder_path):
    """
    Metadata suggestions + completeness score for one folder, cached.

    Uses `build_scan_payload()` — NOT raw `scan_folder()` — because that is the
    shared foundation both Add Recording and batch import score from, and
    `compute_health()` reads `suggestions.from_tags` / `from_info_file` which
    only exist on the full payload. Feeding it a bare `scan_folder()` result
    silently produces a score of 0 for every folder.
    """
    from app.utils.ingest import build_scan_payload

    try:
        mtime = os.path.getmtime(folder_path)
    except OSError:
        mtime = None

    hit = _META_CACHE.get(folder_path)
    if hit and hit[0] == mtime:
        return hit[1]

    scan = build_scan_payload(folder_path)
    if scan is None:                      # no audio in the folder
        payload = {"health": None, "extracted": None}
    else:
        sug = (scan.get("suggestions") or {}).get("from_info_file") or {}
        tags = (scan.get("suggestions") or {}).get("from_tags") or {}
        payload = {
            # build_scan_payload already computed this; recomputing would just
            # risk the two disagreeing.
            "health": scan.get("health"),
            "extracted": {
                "artist":  tags.get("artist") or sug.get("artist"),
                "year":    tags.get("year")   or sug.get("year"),
                "month":   tags.get("month")  or sug.get("month"),
                "day":     tags.get("day")    or sug.get("day"),
                "venue":   tags.get("venue")  or sug.get("venue"),
                "city":    tags.get("city")   or sug.get("city"),
                "state":   tags.get("state")  or sug.get("state"),
                "country": tags.get("country") or sug.get("country"),
                "source":  tags.get("source") or sug.get("source"),
                "lineage": tags.get("lineage") or sug.get("lineage"),
                "track_count": len(scan.get("audio_files") or []),
            },
        }

    if len(_META_CACHE) >= _META_CACHE_MAX:
        _META_CACHE.clear()
    _META_CACHE[folder_path] = (mtime, payload)
    return payload


def _attach_interpretation(results):
    """
    Add the plain-English reading of each score to every row.

    Cheap enough to do on every poll: `interpret_full` is a pure function over
    the already-stored feature dict, so this costs a JSON parse and some
    lookups — no audio, no filesystem.

    The FULL interpretation goes in, metrics and range ladders included, because
    the card renders its whole detail panel up front and just hides it — same as
    the standalone harness. That costs payload, so the client stops polling once
    analysis finishes rather than re-fetching it every second forever.
    """
    from app.utils.quality import interpret_full

    for r in results:
        r["interp"] = None
        if r.get("error") or r.get("listening_quality") is None:
            continue
        try:
            # Rows arrive serialized WITH features (include_features=True); the
            # raw dict is popped again below — the interpretation supersedes it.
            r["interp"] = interpret_full(r, r.get("features") or {})
        except Exception:  # noqa: BLE001
            _tb.print_exc()
        finally:
            r.pop("features", None)


def _attach_metadata(results):
    """
    Add the metadata completeness score and extracted fields to each row.

    The triage card shows BOTH numbers because they answer different questions:
    Listening Quality is "is this worth keeping?", completeness is "how much
    typing will it cost me?".  Deciding to straight-ingest versus hand-edit
    needs both, and making the user visit two screens to see them would defeat
    the point of merging the flows.

    Scan failures are swallowed per-row: a folder whose metadata cannot be read
    still has a perfectly good audio score and must not vanish from triage.
    """
    for r in results:
        r["health"] = None
        r["extracted"] = None
        # Staging rows are keyed by folder path and outlive the folder itself:
        # a MOVE ingest relocates the source into the library, so the row is
        # still here but the directory is not. Without this flag the UI offered
        # to ingest a folder that no longer existed, showed a blank metadata
        # score, and failed with "artist_name is required" when clicked
        # (2026-07-31).
        r["exists"] = bool(r.get("folder_path")) and os.path.isdir(r["folder_path"])
        if r.get("error") or not r["exists"]:
            continue
        try:
            r.update(_scan_metadata(r["folder_path"]))
        except Exception:  # noqa: BLE001
            _tb.print_exc()


# ═════════════════════════════════════════════════════════════════════════════
# Triage
# ═════════════════════════════════════════════════════════════════════════════
@bp.route("/triage", methods=["POST"])
@login_required
def triage():
    """
    POST /api/quality/triage
      { "folder_path": "...", "status": "accepted" | "rejected" | "pending" }
    """
    data = request.get_json() or {}
    folder_path = (data.get("folder_path") or "").strip()
    status = (data.get("status") or "").strip()

    if not folder_path:
        return jsonify({"error": "folder_path is required"}), 400
    try:
        row = qs.set_triage(folder_path, status)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if row is None:
        return jsonify({"error": "No analysis for that folder"}), 404
    return jsonify(qs.serialize(row))


@bp.route("/triage-bulk", methods=["POST"])
@login_required
def triage_bulk():
    """
    POST /api/quality/triage-bulk
      { "folder_paths": [...], "status": "accepted" }

    For "accept everything above N" — the common case on a clean bulk run.
    """
    data = request.get_json() or {}
    paths = data.get("folder_paths") or []
    status = (data.get("status") or "").strip()

    if status not in qs.TRIAGE_STATUSES:
        return jsonify({"error": f"unknown triage status {status!r}"}), 400

    updated = 0
    for p in paths:
        row = qs.get_staging(p)
        if row is not None:
            row.triage_status = status
            updated += 1
    db.session.commit()
    return jsonify({"updated": updated})


@bp.route("/browse", methods=["GET"])
@login_required
def browse():
    """
    List sub-folders of a path so the UI can offer a real directory picker.

    Ported from the standalone harness (tools/quality/quality_app.py) so the
    in-app source step behaves identically — same breadcrumbs, same shortcuts,
    same "audio" tags. PyWebView's native folder dialog exists, but the picker
    is faster for the common case of walking one artist folder at a time, and
    it tells you which folders are analysable BEFORE you click into them.

    Read-only, and constrained to IMPORT_ROOTS like every other filesystem
    endpoint here.
    """
    AUDIO_EXT = (".flac", ".mp3", ".wav", ".aiff", ".aif", ".m4a", ".ogg",
                 ".ape", ".wv")

    raw = (request.args.get("path") or "").strip()
    path = os.path.abspath(os.path.expanduser(
        raw or current_app.config.get("IMPORT_DIR", "/")))

    roots = [os.path.realpath(r) for r in current_app.config.get("IMPORT_ROOTS", [])]
    real = os.path.realpath(path)
    # "/" and other ancestors of a root are allowed so the crumbs stay
    # navigable; only descending OUTSIDE every root is refused.
    if not any(real == r or real.startswith(r + os.sep) or r.startswith(real + os.sep)
               or real == "/" for r in roots):
        return jsonify({"error": "Outside the permitted import roots"}), 403
    if not os.path.isdir(path):
        return jsonify({"error": f"Not a folder: {path}"}), 400
    try:
        entries = sorted(os.listdir(path), key=lambda s: s.lower())
    except PermissionError:
        return jsonify({"error": f"No permission to read {path}"}), 403

    dirs = []
    for name in entries:
        if name.startswith("."):
            continue
        full = os.path.join(path, name)
        if not os.path.isdir(full):
            continue
        try:
            kids = os.listdir(full)
        except (PermissionError, OSError):
            kids = []
        has_audio = any(k.lower().endswith(AUDIO_EXT) for k in kids)
        subdirs = [k for k in kids
                   if not k.startswith(".") and os.path.isdir(os.path.join(full, k))]
        # Look one level deeper before declaring a folder audio-free: a
        # recording that still carries CD1/CD2 subdirs has no audio at its own
        # root, and marking it empty would say a perfectly analysable show has
        # nothing in it.
        if not has_audio:
            for sub in subdirs:
                try:
                    if any(k.lower().endswith(AUDIO_EXT)
                           for k in os.listdir(os.path.join(full, sub))):
                        has_audio = True
                        break
                except (PermissionError, OSError):
                    continue
        dirs.append({"name": name, "path": full,
                     "audio": has_audio, "subdirs": bool(subdirs)})

    parent = os.path.dirname(path.rstrip(os.sep)) or "/"
    return jsonify({
        "path": path,
        "parent": None if parent == path else parent,
        "dirs": dirs,
        "here_has_audio": any(e.lower().endswith(AUDIO_EXT) for e in entries),
        "shortcuts": _shortcuts(),
    })


def _shortcuts():
    """Import folder first, then library, home, all volumes. Deduped."""
    out = []
    for s in (current_app.config.get("IMPORT_DIR"),
              str(current_app.config.get("LIBRARY_ROOT", "")),
              os.path.expanduser("~"), "/Volumes"):
        if s and os.path.isdir(s) and s not in out:
            out.append(s)
    return out


@bp.route("/move", methods=["POST"])
@login_required
def move_out_of_queue():
    """
    POST /api/quality/move
      { "folder_path": "...", "destination": "backlog" | "working" }

    Physically moves a show out of the ingest queue — the triage "not this one,
    not now" action.  Because the folder leaves the scanned directory it simply
    stops appearing, with no need for a filter.

    This MOVES USER FILES, so it is deliberately paranoid:
      * destination must be one of the configured TRIAGE_DIRS (no arbitrary
        paths from the client)
      * source must resolve inside an IMPORT_ROOTS entry — the same allowlist
        that guards ingest preview
      * never overwrites: a name collision gets " (2)", " (3)", … rather than
        merging two shows together
      * refuses anything that isn't a directory, and refuses a mount point or
        filesystem root
    """
    data = request.get_json() or {}
    folder_path = (data.get("folder_path") or "").strip()
    destination = (data.get("destination") or "").strip().lower()

    triage_dirs = current_app.config.get("TRIAGE_DIRS", {})
    if destination not in triage_dirs:
        return jsonify({"error": f"Unknown destination {destination!r}; "
                                 f"expected one of {sorted(triage_dirs)}"}), 400

    src = os.path.realpath(folder_path)
    if not os.path.isdir(src):
        return jsonify({"error": f"Not a folder: {folder_path!r}"}), 400
    if os.path.ismount(src) or os.path.dirname(src) == src:
        return jsonify({"error": "Refusing to move a mount point or root"}), 400

    roots = [os.path.realpath(r) for r in current_app.config.get("IMPORT_ROOTS", [])]
    if not any(src == r or src.startswith(r + os.sep) for r in roots):
        return jsonify({"error": "Folder is outside the permitted import roots"}), 403

    dest_root = triage_dirs[destination]
    try:
        os.makedirs(dest_root, exist_ok=True)
    except OSError as e:
        return jsonify({"error": f"Cannot create {dest_root}: {e}"}), 500

    base = os.path.basename(src)
    target = os.path.join(dest_root, base)
    n = 2
    while os.path.exists(target):
        target = os.path.join(dest_root, f"{base} ({n})")
        n += 1

    import shutil
    try:
        shutil.move(src, target)
    except OSError as e:
        return jsonify({"error": f"Move failed: {e}"}), 500

    # Keep the analysis, repointed at the new location: re-scanning Backlog or
    # Working later shows the existing score instead of paying to redo it.
    row = qs.get_staging(folder_path)
    if row is not None:
        row.folder_path = qs.norm_path(target)
        row.source_dir = qs.norm_path(dest_root)
        row.triage_status = qs.TRIAGE_REJECTED
        db.session.commit()

    return jsonify({"moved_to": target, "destination": destination})


@bp.route("/staging", methods=["GET"])
@login_required
def staging():
    """
    GET /api/quality/staging?source_dir=...

    Rows for one scanned directory, so returning to the page is instant and a
    restart mid-review loses nothing.
    """
    source_dir = (request.args.get("source_dir") or "").strip()
    if not source_dir:
        return jsonify({"error": "source_dir is required"}), 400
    rows = qs.list_staging(source_dir)
    results = [qs.serialize(r, include_features=True) for r in rows]
    _attach_interpretation(results)
    _attach_metadata(results)
    return jsonify({
        "source_dir": qs.norm_path(source_dir),
        "results": results,
    })


@bp.route("/staging/features", methods=["GET"])
@login_required
def staging_features():
    """
    GET /api/quality/staging/features?folder_path=...

    The full raw feature dict for one folder — the Advanced Metrics panel.
    Split out from the list payload because it is large and only wanted on
    expand.
    """
    folder_path = (request.args.get("folder_path") or "").strip()
    row = qs.get_staging(folder_path)
    if row is None:
        return jsonify({"error": "No analysis for that folder"}), 404

    from app.utils.quality import interpret_full
    payload = qs.serialize(row, include_features=True)
    try:
        payload["interpretation"] = interpret_full(payload, payload.get("features") or {})
    except Exception:  # noqa: BLE001
        # Plain-English rendering must never take down the metrics panel.
        _tb.print_exc()
        payload["interpretation"] = None
    return jsonify(payload)


@bp.route("/recording/<int:recording_id>", methods=["GET"])
@login_required
def for_recording(recording_id):
    """Permanent score for one ingested recording — the Fidelity tab."""
    row = qs.get_for_recording(recording_id)
    if row is None:
        return jsonify({"error": "No quality analysis for that recording"}), 404
    include = request.args.get("features") == "1"
    return jsonify(qs.serialize(row, include_features=include))
