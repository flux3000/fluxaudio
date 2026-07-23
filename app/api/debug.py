"""
api/debug.py — Debug endpoints. DEV_MODE only.

Routes:
  POST /api/debug/log   receive a JS log entry (in-memory buffer)
  GET  /api/debug/live  current in-memory log (polled by pop-out AND the
                        in-app panel's Live Server Activity section)
  GET  /api/debug/info  general app state

The in-memory buffer itself lives in app/utils/debug_log.py (2026-07-19) so
server-side pipelines (scan_folder, read_flac_tags, parse_info_file, Paula
scoring) can push "step" checkpoints into it directly via log_step(), not
just receive entries forwarded from the JS fetch wrapper after the fact —
see that module's docstring for why (a stuck Batch Import scan was
completely invisible before this).

(The on-disk FLAC tag viewer moved to the always-on "File Tags" pane:
 GET /api/recordings/<id>/tags.)
"""

from flask import Blueprint, jsonify, request, current_app, abort
from flask_login import login_required, current_user

from app.utils.debug_log import log_entry, all_entries

from app.extensions import db
from app.models.recording import Recording

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
    log_entry(entry)
    return '', 204


# ── GET /api/debug/live — polled by the pop-out browser window AND the ───────
# in-app panel (while open) for live server-side step checkpoints.

@bp.route("/live")
@login_required
def debug_live():
    """Return the full in-memory log as JSON."""
    _require_dev()
    return jsonify(all_entries())


# ── GET /api/debug/info ───────────────────────────────────────────────────────

@bp.route("/info")
@login_required
def debug_info():
    _require_dev()

    from app.models.performer import Performer
    from app.models.artist import Artist
    from app.models.track import Track

    return jsonify({
        "dev_mode":    current_app.config.get("DEV_MODE"),
        "library_root": str(current_app.config.get("LIBRARY_ROOT", "")),
        "db_path":     str(current_app.config.get("SQLALCHEMY_DATABASE_URI", "")),
        "user":        {"id": current_user.id, "username": current_user.username},
        "counts": {
            "performers": db.session.query(Performer).count(),
            "artists":    db.session.query(Artist).count(),
            "recordings": db.session.query(Recording).count(),
            "tracks":     db.session.query(Track).count(),
        },
    })


