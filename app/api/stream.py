"""
api/stream.py — Audio streaming endpoint.

Serves FLAC audio by track ID. The actual file path is resolved server-side
from the database and LIBRARY_ROOT — it is never exposed to the frontend.

Supports HTTP Range requests so the browser audio player can seek.

Route:
  GET /api/stream/<int:track_id>
"""

import os
from flask import Blueprint, current_app, request, Response, abort
from flask_login import login_required
from app.extensions import db
from app.models.track import Track
from app.models.recording import Recording

bp = Blueprint("stream", __name__)

CHUNK_SIZE = 1024 * 256  # 256 KB chunks


@bp.route("/<int:track_id>")
@login_required
def stream_track(track_id):
    # Resolve track → recording → full filesystem path
    track = db.session.get(Track, track_id)
    if not track:
        abort(404)

    recording = db.session.get(Recording, track.recording_id)
    if not recording:
        abort(404)

    library_root = current_app.config["LIBRARY_ROOT"]
    full_path    = os.path.join(library_root, recording.folder_path, track.file_path)

    if not os.path.isfile(full_path):
        abort(404)

    file_size = os.path.getsize(full_path)
    mimetype  = "audio/flac"

    # ── Handle Range requests (enables seeking) ────────────────
    range_header = request.headers.get("Range")
    if range_header:
        byte_start, byte_end = _parse_range(range_header, file_size)
        length = byte_end - byte_start + 1

        def generate_range():
            with open(full_path, "rb") as f:
                f.seek(byte_start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        headers = {
            "Content-Range":  f"bytes {byte_start}-{byte_end}/{file_size}",
            "Accept-Ranges":  "bytes",
            "Content-Length": str(length),
            "Content-Type":   mimetype,
        }
        return Response(generate_range(), status=206, headers=headers)

    # ── Full file stream ───────────────────────────────────────
    def generate_full():
        with open(full_path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                yield chunk

    headers = {
        "Content-Length": str(file_size),
        "Accept-Ranges":  "bytes",
        "Content-Type":   mimetype,
    }
    return Response(generate_full(), status=200, headers=headers)


@bp.route("/ingest-preview")
@login_required
def stream_ingest_preview():
    """
    Stream a pre-ingest audio file by folder + filename.
    Used during ingest review so archivists can preview tracks
    before they exist in the DB.
    """
    folder   = request.args.get("folder", "").strip()
    filename = request.args.get("file",   "").strip()
    if not folder or not filename:
        abort(400)

    # Resolve both paths and confirm filename stays inside the folder (no traversal)
    real_folder = os.path.realpath(folder)
    full_path   = os.path.realpath(os.path.join(real_folder, filename))
    if not full_path.startswith(real_folder + os.sep):
        abort(403)
    if not os.path.isfile(full_path):
        abort(404)

    file_size = os.path.getsize(full_path)
    mimetype  = "audio/flac"

    range_header = request.headers.get("Range")
    if range_header:
        byte_start, byte_end = _parse_range(range_header, file_size)
        length = byte_end - byte_start + 1

        def generate_range():
            with open(full_path, "rb") as f:
                f.seek(byte_start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        headers = {
            "Content-Range":  f"bytes {byte_start}-{byte_end}/{file_size}",
            "Accept-Ranges":  "bytes",
            "Content-Length": str(length),
            "Content-Type":   mimetype,
        }
        return Response(generate_range(), status=206, headers=headers)

    def generate_full():
        with open(full_path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                yield chunk

    headers = {
        "Content-Length": str(file_size),
        "Accept-Ranges":  "bytes",
        "Content-Type":   mimetype,
    }
    return Response(generate_full(), status=200, headers=headers)


def _parse_range(range_header, file_size):
    """Parse a Range header and return (start, end) byte positions."""
    try:
        units, range_spec = range_header.split("=")
        start_str, end_str = range_spec.split("-")
        byte_start = int(start_str)
        byte_end   = int(end_str) if end_str else file_size - 1
    except (ValueError, AttributeError):
        byte_start = 0
        byte_end   = file_size - 1
    return byte_start, min(byte_end, file_size - 1)
