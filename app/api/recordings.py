"""
api/recordings.py — Recording endpoints.

Routes:
  GET  /api/recordings/<id>              full recording detail (incl. analysis)
  POST /api/recordings/scan              scan a folder, return suggestions (no DB write)
  PUT  /api/recordings/<id>             update recording metadata
  POST /api/recordings/<id>/write-tags  write Vorbis comments to FLAC files
  POST /api/recordings/<id>/reprocess   re-run Librosa analysis on all tracks
"""

import json as _json
import os
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user

from app.extensions import db
from app.models.recording import Recording, RecordingFingerprint
from app.models.collection import CollectionRecording
from app.models.recording_event import RecordingEvent
from app.models.track import Track
from app.utils.ingest import (scan_folder, read_flac_tags, parse_info_file,
                              write_flac_tags, read_recording_tags, _parse_location)
from app.utils.analysis import analyse_recording
from app.utils.health import compute_health
from app.utils.pruning import prune_after_recording_delete

bp = Blueprint("recordings", __name__)


# ── GET /api/recordings/<id> ──────────────────────────────────────────────────

@bp.route("/<int:recording_id>")
@login_required
def get_recording(recording_id):
    rec = db.session.get(Recording, recording_id)
    if not rec:
        return jsonify({"error": "Not found"}), 404

    def _analysis(ta):
        """Serialise a TrackAnalysis row, or return None if not yet run."""
        if ta is None:
            return None
        return {
            "sample_rate_hz":       ta.sample_rate_hz,
            "bit_depth":            ta.bit_depth,
            "bitrate_kbps":         ta.bitrate_kbps,
            "rms_db":               ta.rms_db,
            "peak_db":              ta.peak_db,
            "noise_floor_db":       ta.noise_floor_db,
            "dynamic_range_db":     ta.dynamic_range_db,
            "clipping_pct":         ta.clipping_pct,
            "dc_offset":            ta.dc_offset,
            "spectral_centroid_hz": ta.spectral_centroid_hz,
            "spectral_cutoff_hz":   ta.spectral_cutoff_hz,
            "bpm":                  ta.bpm,
            "waveform":             _json.loads(ta.waveform_json) if ta.waveform_json else [],
            "analyzed_at":          ta.analyzed_at.isoformat() if ta.analyzed_at else None,
        }

    return jsonify({
        "id":                   rec.id,
        "performance_id":       rec.performance_id,
        "title":                rec.title,
        "source":               rec.source,
        "source_modifier":      rec.source_modifier,
        "lineage":              rec.lineage,
        "quality":              rec.quality,
        "rating":               rec.rating,
        "is_complete":          rec.is_complete,
        "is_official":          bool(rec.is_official),
        "info_file_content":    rec.info_file_content,
        "notes":                rec.notes,
        "collections": [
            {"id": l.collection.id, "name": l.collection.name}
            for l in db.session.query(CollectionRecording).filter_by(recording_id=rec.id).all()
        ],
        "tracks": [
            {
                "id":           t.id,
                "track_number": t.track_number,
                "title":        t.title,
                "set":          t.set,
                "duration":     t.duration,
                "is_official":  bool(t.is_official),
                "flags":        _json.loads(t.flags) if t.flags else [],
                "songwriter":   t.songwriter,
                "notes":        t.notes,
                "stream_url":   f"/api/stream/{t.id}",
                "analysis":     _analysis(t.analysis),
            }
            for t in rec.tracks
        ],
        "fingerprints": [
            {
                "type":     fp.fingerprint_type,
                "filename": fp.filename,
            }
            for fp in rec.fingerprints
        ],
        "events": [
            {
                "event_type": e.event_type,
                "note":       e.note,
                "created_at": e.created_at.isoformat(),
                "user_id":    e.user_id,
            }
            for e in rec.events
        ],
    })


# ── POST /api/recordings/scan ─────────────────────────────────────────────────

@bp.route("/scan", methods=["POST"])
@login_required
def scan_recording():
    """
    Step 1 of ingest — non-destructive scan of a source folder.
    Returns two parallel metadata sets (from_tags, from_info_file) for
    the user to review field by field in the UI. Nothing is written to DB.
    """
    data        = request.get_json()
    folder_path = data.get("folder_path", "").strip()

    if not folder_path or not os.path.isdir(folder_path):
        return jsonify({"error": "Invalid or inaccessible folder path"}), 400

    # Scan for files
    files = scan_folder(folder_path)

    if not files["audio_files"]:
        return jsonify({"error": "No audio files found in folder"}), 422

    # Read FLAC tags
    from_tags = read_flac_tags(files["audio_files"])

    # Parse CONCERTLOCATION tag into city/state/country using the same
    # geonamescache-backed parser as the info file (best-effort, graceful fallback)
    tag_city = tag_state = tag_country = None
    tag_location = from_tags["container"].get("location") or ""
    if tag_location:
        try:
            tag_city, tag_state, tag_country = _parse_location(tag_location)
        except Exception:
            pass

    # Parse ALL text file candidates (scored/sorted best-first by scan_folder).
    # Include content + suggestions for each so the frontend switcher needs no
    # extra API call. Text files are small; parsing all is cheap.
    from_info         = {}
    info_file_content = None
    parsed_candidates = []

    for tf in files["text_files"]:
        parsed = parse_info_file(tf["path"])
        entry  = {
            "filename":    tf["filename"],
            "score":       tf.get("score", 0),
            "content":     parsed.get("raw_content", ""),
            "suggestions": {
                "artist":       parsed.get("artist"),
                "artist_match": parsed.get("artist_match"),
                "year":         parsed.get("year"),
                "month":        parsed.get("month"),
                "day":          parsed.get("day"),
                "venue":        parsed.get("venue"),
                "venue_match":  parsed.get("venue_match"),
                "city":         parsed.get("city"),
                "state":        parsed.get("state"),
                "country":      parsed.get("country"),
                "source":       parsed.get("source"),
                "lineage":      parsed.get("lineage"),
                "tracks": [
                    {"number": t["number"], "title": t["title"]}
                    for t in parsed.get("tracks", [])
                ],
            },
        }
        parsed_candidates.append(entry)

    # Use best-scored candidate as primary
    if parsed_candidates:
        from_info         = parsed_candidates[0]["suggestions"]
        info_file_content = parsed_candidates[0]["content"]

    # Read fingerprint file contents
    fingerprints = []
    for fp in files["fingerprints"]:
        try:
            with open(fp["path"], "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            content = None
        fingerprints.append({
            "type":     fp["type"],
            "filename": fp["filename"],
            "content":  content,
        })

    resp = {
        "folder_path":      folder_path,
        "folder_name":      os.path.basename(folder_path),
        "audio_file_count": len(files["audio_files"]),
        "sets_detected":    files.get("sets_detected", False),
        "audio_files": [
            {
                "index":    f["index"],
                "filename": f["filename"],            # display name (basename)
                "rel_path": f.get("rel_path", f["filename"]),  # path relative to source root
                "set":      f.get("set"),
            }
            for f in files["audio_files"]
        ],
        "info_file_content": info_file_content,
        # All text file candidates, best-first — includes content + parsed suggestions
        # so the UI switcher works without an extra API call
        "text_file_candidates": parsed_candidates,
        "fingerprints":      fingerprints,
        "suggestions": {
            # Metadata from existing FLAC tags
            "from_tags": {
                "artist":       from_tags["container"].get("artist"),
                "concert_date": from_tags["container"].get("concert_date"),
                "venue":        from_tags["container"].get("venue"),
                "location":     from_tags["container"].get("location"),
                "city":         tag_city,
                "state":        tag_state,
                "country":      tag_country,
                "source":       from_tags["container"].get("source"),
                "lineage":      from_tags["container"].get("lineage"),
                "tracks": [
                    {
                        "index":        t["index"],
                        "filename":     t["filename"],
                        "rel_path":     t.get("rel_path", t["filename"]),
                        "track_number": t["track_number"],
                        "title":        t["title"],
                        "duration":     t["duration"],
                        "raw":          t.get("raw", {}),
                    }
                    for t in from_tags["tracks"]
                ],
            },
            # Metadata parsed from the info/text file
            "from_info_file": {
                "artist":       from_info.get("artist"),
                "artist_match": from_info.get("artist_match"),
                "year":         from_info.get("year"),
                "month":        from_info.get("month"),
                "day":          from_info.get("day"),
                "venue":        from_info.get("venue"),
                "venue_match":  from_info.get("venue_match"),
                "city":         from_info.get("city"),
                "state":        from_info.get("state"),
                "country":      from_info.get("country"),
                "source":       from_info.get("source"),
                "lineage":      from_info.get("lineage"),
                "tracks": [
                    {"number": t["number"], "title": t["title"]}
                    for t in from_info.get("tracks", [])
                ],
            },
        },
    }
    resp["health"] = compute_health(resp)
    return jsonify(resp)


# ── PUT /api/recordings/<id> ──────────────────────────────────────────────────

@bp.route("/<int:recording_id>", methods=["PUT"])
@login_required
def update_recording(recording_id):
    """
    Update recording metadata in DB.
    Logs a metadata_updated event.
    Does NOT write FLAC tags — that is a separate deliberate action.
    """
    rec = db.session.get(Recording, recording_id)
    if not rec:
        return jsonify({"error": "Not found"}), 404

    # TODO: validate archivist permission for this recording's artist

    data = request.get_json()
    updatable = ["title", "source", "source_modifier", "lineage",
                 "quality", "rating", "is_complete", "notes", "info_file_content"]
    for field in updatable:
        if field in data:
            setattr(rec, field, data[field])

    # is_official — cascade True to all tracks; never force-cascade False
    if "is_official" in data:
        rec.is_official = bool(data["is_official"])
        if rec.is_official:
            for t in rec.tracks:
                t.is_official = True

    # Log the change
    event = RecordingEvent(
        recording_id = rec.id,
        user_id      = current_user.id,
        event_type   = "metadata_updated",
        note         = data.get("change_note"),
    )
    db.session.add(event)
    db.session.commit()

    return jsonify({"id": rec.id, "updated_at": rec.updated_at.isoformat()})


# ── DELETE /api/recordings/<id> ──────────────────────────────────────────────

def _delete_tracks_of_recording(recording_id):
    """
    Delete every track of a recording along with its dependent child rows
    (track_analysis, play_log). Done in app code because SQLite FK cascades
    are not enforced on the existing schema — see delete_recording.
    """
    from app.models.track import Track
    from app.models.track_analysis import TrackAnalysis
    from app.models.play_log import PlayLog

    track_ids = [
        t.id for t in db.session.query(Track.id).filter_by(recording_id=recording_id).all()
    ]
    if track_ids:
        db.session.query(TrackAnalysis).filter(TrackAnalysis.track_id.in_(track_ids)).delete(
            synchronize_session=False)
        db.session.query(PlayLog).filter(PlayLog.track_id.in_(track_ids)).delete(
            synchronize_session=False)
        db.session.query(Track).filter(Track.id.in_(track_ids)).delete(
            synchronize_session=False)


@bp.route("/<int:recording_id>", methods=["DELETE"])
@login_required
def delete_recording(recording_id):
    """
    Delete a recording and all its child records (tracks, events, fingerprints).
    Then prune any performance/performer/canonical-artist left empty by the
    delete. Does NOT touch files on disk — audio files are left in place.
    """
    from app.models.recording import RecordingFingerprint
    rec = db.session.get(Recording, recording_id)
    if not rec:
        return jsonify({"error": "Recording not found"}), 404

    performance_id = rec.performance_id

    # Delete children explicitly, bottom-up. SQLite FK enforcement is off and
    # the existing tables were created without ON DELETE actions, so nothing
    # cascades for us — we clear every child of every track (analysis, play
    # logs) plus the recording's own children, or they orphan silently. Bulk
    # deletes (no ORM cascade) keep this predictable.
    _delete_tracks_of_recording(recording_id)
    db.session.query(RecordingFingerprint).filter_by(recording_id=recording_id).delete(
        synchronize_session=False)
    db.session.query(RecordingEvent).filter_by(recording_id=recording_id).delete(
        synchronize_session=False)
    db.session.query(Recording).filter_by(id=recording_id).delete(synchronize_session=False)
    db.session.flush()

    # Prune the now-empty chain above the recording.
    pruned = prune_after_recording_delete(performance_id)

    db.session.commit()

    return jsonify({"deleted": recording_id, "pruned": pruned}), 200


# ── POST /api/recordings/<id>/write-tags ──────────────────────────────────────

@bp.route("/<int:recording_id>/write-tags", methods=["POST"])
@login_required
def write_tags(recording_id):
    """
    Write current DB metadata as Vorbis comments to every FLAC file in the
    recording. Existing tags are replaced entirely.

    This is a deliberate, explicit action — not triggered by metadata saves.
    Logs a tags_written event on full or partial success.

    Returns:
      200  { written: n, errors: [(filename, msg), ...] }
      404  if recording not found
      500  if all files failed
    """
    rec = db.session.get(Recording, recording_id)
    if not rec:
        return jsonify({"error": "Not found"}), 404

    library_root = current_app.config.get("LIBRARY_ROOT", "")
    n_written, errors = write_flac_tags(rec, library_root)

    # Log even on partial success so the event trail is accurate
    if n_written > 0:
        note = f"{n_written} file(s) written"
        if errors:
            note += f"; {len(errors)} error(s): " + "; ".join(f[0] for f in errors)
        event = RecordingEvent(
            recording_id = rec.id,
            user_id      = current_user.id,
            event_type   = "tags_written",
            note         = note,
        )
        db.session.add(event)
        db.session.commit()

    if n_written == 0:
        return jsonify({"error": "No files written", "errors": errors}), 500

    return jsonify({"written": n_written, "errors": errors})


# ── GET /api/recordings/<id>/tags ─────────────────────────────────────────────

@bp.route("/<int:recording_id>/tags")
@login_required
def get_recording_file_tags(recording_id):
    """
    Return the actual on-disk Vorbis comments for every FLAC file in the
    recording. Powers the "File Tags" viewer so the effect of "Write Tags to
    Files" is visible. File paths are never exposed.
    """
    rec = db.session.get(Recording, recording_id)
    if not rec:
        return jsonify({"error": "Not found"}), 404
    library_root = current_app.config.get("LIBRARY_ROOT", "")
    return jsonify({
        "recording_id": rec.id,
        "tracks":       read_recording_tags(rec, library_root),
    })


# ── POST /api/recordings/<id>/reprocess ───────────────────────────────────────

@bp.route("/<int:recording_id>/reprocess", methods=["POST"])
@login_required
def reprocess_recording(recording_id):
    """
    Re-run Librosa analysis on every track in the recording.
    Results are upserted into track_analysis. Safe to call multiple times.

    Returns:
      200  { analysed: n, errors: [(filename, msg), ...] }
      404  if recording not found
      500  if librosa is unavailable or all tracks failed
    """
    rec = db.session.get(Recording, recording_id)
    if not rec:
        return jsonify({"error": "Not found"}), 404

    library_root = current_app.config.get("LIBRARY_ROOT", "")
    n_ok, errors = analyse_recording(rec, library_root, db.session)

    # Log the reprocess event
    db.session.add(RecordingEvent(
        recording_id = rec.id,
        user_id      = current_user.id,
        event_type   = "reprocessed",
        note         = f"{n_ok} track(s) analysed" + (
            f"; {len(errors)} error(s)" if errors else ""
        ),
    ))
    db.session.commit()

    if n_ok == 0:
        return jsonify({"error": "Analysis failed for all tracks", "errors": errors}), 500

    return jsonify({"analysed": n_ok, "errors": errors})
