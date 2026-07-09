"""
api/debug.py — Debug endpoints. DEV_MODE only.

Routes:
  GET /api/debug/info                general app state
  GET /api/debug/tags/<recording_id> raw FLAC tags from files vs. DB values
"""

from collections import deque
from flask import Blueprint, jsonify, request, current_app, abort
from flask_login import login_required, current_user

# In-memory circular log shared across requests (process-scoped, not thread-safe
# but fine for single-dev DEV_MODE use)
_dbg_log = deque(maxlen=200)

from app.extensions import db
from app.models.recording import Recording

from mutagen.flac import FLAC
from mutagen import MutagenError
import os

bp = Blueprint("debug", __name__)


def _require_dev():
    """Abort 404 if not in DEV_MODE — debug routes are invisible in prod."""
    if not current_app.config.get("DEV_MODE"):
        abort(404)


# ── POST /api/debug/log — JS forwards entries here ───────────────────────────

@bp.route("/log", methods=["POST"])
@login_required
def debug_log():
    """Receive a log entry from the JS layer and append to the in-memory buffer."""
    _require_dev()
    entry = request.get_json(silent=True) or {}
    _dbg_log.appendleft(entry)
    return '', 204


# ── GET /api/debug/live — polled by the pop-out browser window ────────────────

@bp.route("/live")
@login_required
def debug_live():
    """Return the full in-memory log as JSON for the pop-out debug page."""
    _require_dev()
    return jsonify(list(_dbg_log))


# ── GET /api/debug/info ───────────────────────────────────────────────────────

@bp.route("/info")
@login_required
def debug_info():
    _require_dev()

    from app.models.artist import Artist
    from app.models.track import Track

    return jsonify({
        "dev_mode":    current_app.config.get("DEV_MODE"),
        "library_root": str(current_app.config.get("LIBRARY_ROOT", "")),
        "db_path":     str(current_app.config.get("SQLALCHEMY_DATABASE_URI", "")),
        "user":        {"id": current_user.id, "username": current_user.username},
        "counts": {
            "artists":    db.session.query(Artist).count(),
            "recordings": db.session.query(Recording).count(),
            "tracks":     db.session.query(Track).count(),
        },
    })


# ── GET /api/debug/tags/<recording_id> ───────────────────────────────────────

@bp.route("/tags/<int:recording_id>")
@login_required
def debug_tags(recording_id):
    """
    Read raw Vorbis comments from every FLAC file in the recording.
    Returns both the file-level tags and the corresponding DB values so you
    can spot drift between what's in the files and what's in the DB.
    """
    _require_dev()

    rec = db.session.get(Recording, recording_id)
    if not rec:
        return jsonify({"error": "Not found"}), 404

    library_root = current_app.config.get("LIBRARY_ROOT", "")
    perf  = rec.performance
    venue = perf.venue if perf else None

    # ── DB snapshot (what write_flac_tags would produce) ──────────────────────
    date_parts = [perf.start_year, perf.start_month, perf.start_day] if perf else []
    if perf and all(date_parts):
        concert_date = f"{perf.start_year}-{perf.start_month:02d}-{perf.start_day:02d}"
    elif perf and perf.start_year and perf.start_month:
        concert_date = f"{perf.start_year}-{perf.start_month:02d}"
    elif perf and perf.start_year:
        concert_date = str(perf.start_year)
    else:
        concert_date = None

    loc_parts  = [p for p in ([venue.city, venue.state, venue.country] if venue
                               else [perf.city, perf.state, perf.country] if perf
                               else []) if p]
    source_str = rec.source
    if rec.source_modifier:
        source_str = f"{source_str} - {rec.source_modifier}" if source_str else rec.source_modifier

    artist_name = perf.performer.name if (perf and perf.performer) else None
    album_parts = [p for p in [artist_name,
                                concert_date,
                                venue.name if venue else None] if p]

    db_tags = {
        "ARTIST":          artist_name,
        "ALBUM":           " - ".join(album_parts) if album_parts else None,
        "DATE":            str(perf.start_year) if (perf and perf.start_year) else None,
        "CONCERTDATE":     concert_date,
        "CONCERTVENUE":    venue.name if venue else None,
        "CONCERTLOCATION": ", ".join(loc_parts) if loc_parts else None,
        "RECORDINGSOURCE": source_str,
        "LINEAGE":         rec.lineage,
        # track-level tags are per-file below
    }
    db_tracks = {
        t.track_number: {"TITLE": t.title, "TRACKNUMBER": str(t.track_number),
                         "TRACKTOTAL": str(len(rec.tracks))}
        for t in rec.tracks
    }

    # ── Read actual tags from files ───────────────────────────────────────────
    file_results = []
    for track in sorted(rec.tracks, key=lambda t: t.track_number):
        abs_path = os.path.join(library_root, rec.folder_path, track.file_path)
        entry = {
            "track_number": track.track_number,
            "filename":     track.file_path,
            "abs_path":     abs_path,
            "tags":         None,
            "error":        None,
        }
        try:
            audio = FLAC(abs_path)
            # Vorbis comments are multi-valued lists; unwrap single values
            entry["tags"] = {k: (v[0] if len(v) == 1 else v)
                             for k, v in (audio.tags or {}).items()}
        except FileNotFoundError:
            entry["error"] = "File not found"
        except MutagenError as e:
            entry["error"] = f"Mutagen: {e}"
        except Exception as e:
            entry["error"] = f"Error: {e}"

        file_results.append(entry)

    return jsonify({
        "recording_id":  rec.id,
        "folder_path":   rec.folder_path,
        "db_tags":       db_tags,
        "db_tracks":     db_tracks,
        "files":         file_results,
    })
