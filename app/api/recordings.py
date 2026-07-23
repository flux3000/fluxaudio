"""
api/recordings.py — Recording endpoints.

Routes:
  GET  /api/recordings/<id>                 full recording detail (incl. analysis)
  POST /api/recordings/scan                 scan a folder, return suggestions (no DB write)
  PUT  /api/recordings/<id>                update recording metadata
  POST /api/recordings/<id>/write-tags     write Vorbis comments to FLAC files
  POST /api/recordings/<id>/reprocess      re-run Librosa analysis on all tracks
  POST /api/recordings/<id>/verify-checksums  (re-)validate fingerprint checksums
"""

import json as _json
import os
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user

from app.extensions import db
from app.models.recording import Recording, RecordingFingerprint
from app.models.collection import CollectionRecording
from app.models.recording_event import RecordingEvent
from app.models.track import Track
from app.models.performer import Performer
from app.models.venue import Venue
from app.utils.ingest import (build_scan_payload, write_flac_tags, read_recording_tags)
from app.utils.analysis import analyse_recording
from app.utils.pruning import prune_after_recording_delete
from app.utils.serialize import recording_row
from app.utils.paula import compute_paula_score
from app.utils.checksums import (
    discover_fingerprint_files, parse_checksum_file,
    match_entries_to_tracks, verify_track_checksum, FINGERPRINT_TYPE_PRIORITY,
)

bp = Blueprint("recordings", __name__)


# ── GET /api/recordings/recent ────────────────────────────────────────────────
# Virtual "Recently Added" view — not a stored grouping, just the N newest
# recordings by ingest timestamp. Always exactly correct, nothing to keep in sync.

@bp.route("/recent")
@login_required
def recent_recordings():
    limit = request.args.get("limit", 50, type=int) or 50
    limit = max(1, min(limit, 200))
    recs = (Recording.query
            .order_by(Recording.created_at.desc())
            .limit(limit)
            .all())
    return jsonify([recording_row(r) for r in recs])


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
        "lineage":              rec.lineage,
        "quality":              rec.quality,
        "rating":               rec.rating,
        "is_complete":          rec.is_complete,
        "is_official":          bool(rec.is_official),
        "info_file_content":    rec.info_file_content,
        "notes":                rec.notes,
        "ai_research":          _json.loads(rec.ai_research_json) if rec.ai_research_json else None,
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
                "checksum": {
                    "type":            t.checksum_type,
                    "expected":        t.expected_checksum,
                    "status":          t.checksum_status,
                    "verified_at":     t.checksum_verified_at.isoformat() if t.checksum_verified_at else None,
                } if t.checksum_type else None,
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
    Delegates to build_scan_payload() — the same function batch import uses,
    so a folder's health score never differs between the two flows.

    Also runs Paula (app.utils.paula.compute_paula_score) — the free,
    non-AI completeness/confidence scorer, 2026-07-16. Paula needs real DB
    data to fuzzy-match tag/txt-inferred Performer and Venue names, which is
    why she runs here (DB access) rather than inside build_scan_payload
    (kept DB-free/pure, same as compute_health()). Scoped to the interactive
    Add Recording flow only for now — batch-scan is untouched.
    """
    from app.utils.debug_log import log_step

    data        = request.get_json()
    folder_path = data.get("folder_path", "").strip()
    job         = f"scan:{folder_path}"

    if not folder_path or not os.path.isdir(folder_path):
        return jsonify({"error": "Invalid or inaccessible folder path"}), 400

    log_step(job, "request received", "POST /api/recordings/scan")
    resp = build_scan_payload(folder_path)
    if resp is None:
        return jsonify({"error": "No audio files found in folder"}), 422

    known_performers = [p.name for p in Performer.query.all()]
    known_venues = [
        {"name": v.name, "city": v.city, "state": v.state, "country": v.country}
        for v in Venue.query.all()
    ]
    log_step(job, "queried known performers/venues",
             f"{len(known_performers)} performers, {len(known_venues)} venues")
    resp["paula"] = compute_paula_score(resp, known_performers, known_venues)
    log_step(job, "response ready", "Paula scoring done")

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
    updatable = ["title", "source", "lineage",
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


# ── POST /api/recordings/<id>/verify-checksums ───────────────────────────────

@bp.route("/<int:recording_id>/verify-checksums", methods=["POST"])
@login_required
def verify_checksums(recording_id):
    """
    (Re-)validate this recording's fingerprint checksums against the audio
    files currently sitting in the library. Safe to call any time — nothing
    here depends on the original source folder still existing.

    Also opportunistically discovers fingerprint files that were copied into
    the library with this recording but never parsed into RecordingFingerprint
    rows (covers shows ingested before this feature existed) — so this one
    endpoint serves both "re-validate" and "go back and process the ones I
    already have in the library."
    """
    rec = db.session.get(Recording, recording_id)
    if not rec:
        return jsonify({"error": "Not found"}), 404

    library_root = current_app.config.get("LIBRARY_ROOT", "")
    folder_abs   = os.path.join(str(library_root), rec.folder_path)

    # Collect into a local list rather than re-reading rec.fingerprints after
    # adding to it — the relationship collection was already cached by the
    # line above and db.session.flush() doesn't invalidate that cache, so a
    # freshly-discovered row wouldn't show up in it this same request.
    all_fingerprints = list(rec.fingerprints)
    known_filenames  = {fp.filename for fp in all_fingerprints}
    for found in discover_fingerprint_files(folder_abs):
        if found["filename"] in known_filenames:
            continue
        try:
            with open(os.path.join(folder_abs, found["rel_path"]),
                      "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            content = None
        new_fp = RecordingFingerprint(
            recording_id     = rec.id,
            fingerprint_type = found["type"],
            filename         = found["filename"],
            content          = content,
        )
        db.session.add(new_fp)
        all_fingerprints.append(new_fp)

    # Same FINGERPRINT_TYPE_PRIORITY tie-break as ingest (see
    # api/ingest.py _do_confirm) when more than one fingerprint file
    # is present: ffp, then md5, then st5.
    fingerprints = sorted(all_fingerprints,
                          key=lambda fp: FINGERPRINT_TYPE_PRIORITY.get(fp.fingerprint_type, 9))
    now = datetime.now(timezone.utc)
    checked = 0
    for fp in fingerprints:
        if not fp.content:
            continue
        matches = match_entries_to_tracks(parse_checksum_file(fp.content), rec.tracks)
        for track, expected in matches.items():
            abs_path = os.path.join(folder_abs, track.file_path)
            track.checksum_type        = fp.fingerprint_type
            track.expected_checksum    = expected
            track.checksum_status      = verify_track_checksum(abs_path, fp.fingerprint_type, expected)
            track.checksum_verified_at = now
            checked += 1
    db.session.commit()

    return jsonify({
        "verified_at": now.isoformat(),
        "checked":     checked,
        "tracks": [
            {"id": t.id, "checksum_type": t.checksum_type,
             "checksum_status": t.checksum_status, "expected_checksum": t.expected_checksum}
            for t in rec.tracks
        ],
    })
