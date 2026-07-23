"""
api/performers.py — Performer (act) endpoints: browse, catalog, search, members.

A Performer is the act you browse and tag by. Its member Artists (people) are
managed here too. Grouping "everything by a person" lives on the Artist side
(api/artists.py), not here.
"""

import os
from pathlib import Path

import json

from flask import Blueprint, jsonify, request, send_file, current_app
from flask_login import login_required, current_user
from sqlalchemy import func
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models.performer import Performer, PerformerResource
from app.models.artist import Artist, Membership
from app.models.performance import Performance
from app.models.recording import Recording
from app.utils.serialize import recording_summary
from app.utils.ingest import _sanitize_path
from app.utils.performers import (
    set_performer_members, add_membership_stint,
    update_membership_stint_bounds, remove_membership_stint,
)
from app.utils.performer_research import run_performer_research
from app.utils.ai_assist import AiAssistError
from app.utils.prefs import get_api_key, get_pref

bp = Blueprint("performers", __name__)

_ALLOWED_IMAGE_EXTS = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                       ".png": "image/png", ".webp": "image/webp"}


def _performer_images_dir(performer):
    """
    LIBRARY_ROOT/<sanitized name>/_images — the leading underscore sorts it
    first alongside/before recording folders in a Finder listing (Ryan,
    2026-07-22). NOTE: derived from the Performer's CURRENT name at request
    time, not a stored path — see Performer.image_ext's docstring for the
    rename-orphan caveat this carries (matches how existing recording
    folders already behave on a rename: nothing moves those either).
    """
    library_root = current_app.config["LIBRARY_ROOT"]
    return Path(library_root) / _sanitize_path(performer.name) / "_images"


def _serialize_roster(performer):
    """
    Member Artists deduped by person (see Performer.artists), each carrying
    their stint row(s) — usually one unbounded row ('always a member'), but
    possibly several for someone with real tenure gaps (Mickey Hart). Powers
    the Performer page's stint editor.
    """
    by_artist = {}
    for m in performer.memberships:   # already ordered by Membership.order
        by_artist.setdefault(m.artist_id, []).append(m)
    roster = []
    for artist_id, stints in by_artist.items():
        roster.append({
            "id":   artist_id,
            "name": stints[0].artist.name,
            "stints": [
                {
                    "id": s.id,
                    "start_year": s.start_year, "start_month": s.start_month,
                    "start_day":  s.start_day,
                    "end_year":   s.end_year,   "end_month":   s.end_month,
                    "end_day":    s.end_day,
                }
                for s in sorted(stints, key=lambda s: s.order)
            ],
        })
    return roster


@bp.route("/search")
@login_required
def search_performers():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    rows = (db.session.query(Performer)
            .filter(Performer.name.ilike(f"%{q}%"))
            .order_by(Performer.name).limit(12).all())
    return jsonify([{"id": p.id, "name": p.name} for p in rows])


@bp.route("/")
@login_required
def list_performers():
    """All performers with recording counts + member names — powers the sidebar."""
    rows = (
        db.session.query(Performer, func.count(Recording.id).label("rc"))
        .outerjoin(Performance, Performance.performer_id == Performer.id)
        .outerjoin(Recording,   Recording.performance_id == Performance.id)
        .group_by(Performer.id)
        .order_by(func.coalesce(Performer.sort_name, Performer.name))
        .all()
    )
    return jsonify([
        {
            "id":              p.id,
            "name":            p.name,
            "sort_name":       p.sort_name,
            "recording_count": rc,
            "members":         [a.name for a in p.artists],
        }
        for p, rc in rows
    ])


@bp.route("/all-recordings")
@login_required
def all_recordings():
    """Every performer (alpha) with their performances (oldest first). Library view."""
    performers = (
        db.session.query(Performer)
        .order_by(func.coalesce(Performer.sort_name, Performer.name))
        .all()
    )
    result = []
    for pf in performers:
        performances = (
            db.session.query(Performance)
            .filter(Performance.performer_id == pf.id)
            .order_by(
                Performance.start_year.asc().nullslast(),
                Performance.start_month.asc().nullslast(),
                Performance.start_day.asc().nullslast(),
            ).all()
        )
        if not performances:
            continue
        perf_list = []
        for p in performances:
            v = p.venue
            perf_list.append({
                "performance_id": p.id,
                "performer_name": p.performer.name,
                "title":          p.title,
                "start_year":     p.start_year,
                "start_month":    p.start_month,
                "start_day":      p.start_day,
                "venue_name":     v.name    if v else None,
                "city":           v.city    if v else p.city,
                "state":          v.state   if v else p.state,
                "country":        v.country if v else p.country,
                "recordings":     [recording_summary(r) for r in p.recordings],
            })
        result.append({
            "performer_id":      pf.id,
            "performer_name":    pf.name,
            "performance_count": len(perf_list),
            "recording_count":   sum(len(p["recordings"]) for p in perf_list),
            "performances":      perf_list,
        })
    return jsonify(result)


@bp.route("/<int:performer_id>")
@login_required
def get_performer(performer_id):
    p = db.session.get(Performer, performer_id)
    if not p:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "id":        p.id,
        "name":      p.name,
        "sort_name": p.sort_name,
        "bio":       p.bio,
        "default_personnel_mode": p.default_personnel_mode,
        # Each entry still has {id, name} (existing frontend code reading
        # just those two keys keeps working unchanged) plus a new `stints`
        # list the Performer page's stint editor uses.
        "members":   _serialize_roster(p),
        "resources": [{"id": r.id, "label": r.label, "url": r.url} for r in p.resources],
        "has_image": bool(p.image_ext),
        "dossier":   json.loads(p.dossier_json) if p.dossier_json else None,
    })


@bp.route("/<int:performer_id>/recordings")
@login_required
def get_performer_recordings(performer_id):
    p = db.session.get(Performer, performer_id)
    if not p:
        return jsonify({"error": "Not found"}), 404
    performances = (
        db.session.query(Performance)
        .filter(Performance.performer_id == performer_id)
        .order_by(
            Performance.start_year.desc().nullsfirst(),
            Performance.start_month.desc().nullsfirst(),
            Performance.start_day.desc().nullsfirst(),
        ).all()
    )
    out = []
    for perf in performances:
        v = perf.venue
        out.append({
            "performance_id": perf.id,
            "performer_name": perf.performer.name,
            "title":          perf.title,
            "stage":          perf.stage,
            "start_year":     perf.start_year,
            "start_month":    perf.start_month,
            "start_day":      perf.start_day,
            "end_year":       perf.end_year,
            "end_month":      perf.end_month,
            "end_day":        perf.end_day,
            "venue_name":     v.name    if v else None,
            "city":           v.city    if v else perf.city,
            "state":          v.state   if v else perf.state,
            "country":        v.country if v else perf.country,
            "recordings":     [recording_summary(r) for r in perf.recordings],
        })
    return jsonify(out)


@bp.route("/", methods=["POST"])
@login_required
def create_performer():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    if db.session.query(Performer).filter(func.lower(Performer.name) == name.lower()).first():
        return jsonify({"error": "Performer already exists"}), 409
    p = Performer(name=name, sort_name=data.get("sort_name"), bio=data.get("bio"))
    db.session.add(p)
    db.session.flush()
    # Artists are optional — only set members if the caller supplied any.
    if data.get("members"):
        set_performer_members(p, data["members"])
    db.session.commit()
    return jsonify({"id": p.id, "name": p.name}), 201


@bp.route("/<int:performer_id>", methods=["PUT"])
@login_required
def update_performer(performer_id):
    p = db.session.get(Performer, performer_id)
    if not p:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json()
    if "default_personnel_mode" in data and data["default_personnel_mode"] not in ("inherit", "explicit"):
        return jsonify({"error": "default_personnel_mode must be 'inherit' or 'explicit'"}), 400
    for f in ["name", "sort_name", "bio", "default_personnel_mode"]:
        if f in data:
            setattr(p, f, data[f])
    if data.get("members") is not None:
        set_performer_members(p, data["members"])
    if data.get("resources") is not None:
        _set_resources(p, data["resources"])
    db.session.commit()
    return jsonify({"id": p.id})


# ── Profile picture (2026-07-22) ─────────────────────────────────────────────
# One image slot per Performer, stored on disk (not in the DB) at
# LIBRARY_ROOT/<sanitized name>/_images/profile<ext> — see
# _performer_images_dir() above and Performer.image_ext's docstring.

@bp.route("/<int:performer_id>/image", methods=["POST"])
@login_required
def upload_performer_image(performer_id):
    p = db.session.get(Performer, performer_id)
    if not p:
        return jsonify({"error": "Not found"}), 404
    f = request.files.get("image")
    if not f or not f.filename:
        return jsonify({"error": "No image file provided"}), 400
    ext = os.path.splitext(secure_filename(f.filename))[1].lower()
    if ext not in _ALLOWED_IMAGE_EXTS:
        return jsonify({"error": "Unsupported image type '%s' — use jpg, png, or webp" % ext}), 400

    images_dir = _performer_images_dir(p)
    images_dir.mkdir(parents=True, exist_ok=True)
    # Overwrite semantics: exactly one profile image ever exists for this
    # Performer. If the new upload has a different extension than the old
    # one, remove the old file first so it doesn't linger orphaned on disk.
    if p.image_ext and p.image_ext != ext:
        old = images_dir / ("profile" + p.image_ext)
        if old.exists():
            old.unlink()
    dest = images_dir / ("profile" + ext)
    f.save(str(dest))
    p.image_ext = ext
    db.session.commit()
    return jsonify({"image_ext": ext})


@bp.route("/<int:performer_id>/image", methods=["GET"])
@login_required
def get_performer_image(performer_id):
    p = db.session.get(Performer, performer_id)
    if not p or not p.image_ext:
        return jsonify({"error": "No image"}), 404
    path = _performer_images_dir(p) / ("profile" + p.image_ext)
    if not path.exists():
        return jsonify({"error": "Image file missing on disk"}), 404
    return send_file(str(path), mimetype=_ALLOWED_IMAGE_EXTS[p.image_ext])


@bp.route("/<int:performer_id>/image", methods=["DELETE"])
@login_required
def delete_performer_image(performer_id):
    p = db.session.get(Performer, performer_id)
    if not p:
        return jsonify({"error": "Not found"}), 404
    if p.image_ext:
        path = _performer_images_dir(p) / ("profile" + p.image_ext)
        if path.exists():
            path.unlink()
        p.image_ext = None
        db.session.commit()
    return jsonify({"ok": True})


# ── Dossier — AI-drafted biography + suggested resource links (2026-07-22) ──
# Background job, same shape as ingest.py's AI Assist (_AI_JOBS / poll):
# the synchronous Anthropic call is too slow for the webview's fetch timeout,
# so this starts a daemon thread and the client polls for the result. On
# success the raw result is persisted to Performer.dossier_json — nothing
# else is auto-applied (see performer_research.py's module docstring).
_DOSSIER_JOBS = {}  # job_id -> {"status": running|done|error, "result"/"error"}


def _run_dossier_job(job_id, performer_id, performer_name, current_bio, api_key, model, app):
    import traceback as _tb
    try:
        result = run_performer_research(
            performer_name, current_bio, api_key, model)
        _DOSSIER_JOBS[job_id] = {"status": "done", "result": result}
        try:
            with app.app_context():
                p = db.session.get(Performer, performer_id)
                if p:
                    p.dossier_json = json.dumps(result)
                    db.session.commit()
        except Exception:
            _tb.print_exc()   # best-effort — client already has the result via the job dict
    except AiAssistError as e:
        _DOSSIER_JOBS[job_id] = {"status": "error", "error": str(e)}
    except Exception as e:  # noqa: BLE001
        _tb.print_exc()
        _DOSSIER_JOBS[job_id] = {"status": "error", "error": "Unexpected error: %s" % e}


@bp.route("/<int:performer_id>/dossier", methods=["POST"])
@login_required
def start_dossier(performer_id):
    import threading
    import uuid

    p = db.session.get(Performer, performer_id)
    if not p:
        return jsonify({"error": "Not found"}), 404
    api_key = get_api_key(current_user.id)
    if not api_key:
        return jsonify({"error": "no_api_key"}), 428
    model = get_pref(current_user.id, "ai_model") or "claude-sonnet-5"

    job_id = uuid.uuid4().hex
    _DOSSIER_JOBS[job_id] = {"status": "running"}
    threading.Thread(
        target=_run_dossier_job,
        args=(job_id, performer_id, p.name, p.bio or "", api_key, model, current_app._get_current_object()),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id}), 202


@bp.route("/<int:performer_id>/dossier/<job_id>", methods=["GET"])
@login_required
def dossier_status(performer_id, job_id):
    job = _DOSSIER_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    if job["status"] == "running":
        return jsonify({"status": "running"})
    _DOSSIER_JOBS.pop(job_id, None)   # deliver terminal state once, then discard
    if job["status"] == "error":
        return jsonify({"status": "error", "error": job["error"]})
    return jsonify({"status": "done", "result": job["result"]})


@bp.route("/<int:performer_id>/members/<int:artist_id>/stints", methods=["POST"])
@login_required
def add_stint(performer_id, artist_id):
    """
    Add a NEW stint for an existing member — how a second tenure (Mickey
    Hart's post-1974 return) gets recorded without touching the first. Does
    NOT create the membership from scratch if none exists yet; use the
    plain roster (PUT .../members) to add someone for the first time.
    """
    performer = db.session.get(Performer, performer_id)
    if not performer:
        return jsonify({"error": "Performer not found"}), 404
    artist = db.session.get(Artist, artist_id)
    if not artist:
        return jsonify({"error": "Artist not found"}), 404
    data = request.get_json() or {}
    m = add_membership_stint(
        performer, artist.name,
        start_year=data.get("start_year"), start_month=data.get("start_month"),
        start_day=data.get("start_day"),
        end_year=data.get("end_year"), end_month=data.get("end_month"),
        end_day=data.get("end_day"),
    )
    db.session.commit()
    return jsonify({"id": m.id}), 201


@bp.route("/stints/<int:stint_id>", methods=["PUT"])
@login_required
def update_stint(stint_id):
    """Edit one stint's date bounds. Does not affect a person's other stints."""
    data = request.get_json() or {}
    m = update_membership_stint_bounds(
        stint_id,
        start_year=data.get("start_year"), start_month=data.get("start_month"),
        start_day=data.get("start_day"),
        end_year=data.get("end_year"), end_month=data.get("end_month"),
        end_day=data.get("end_day"),
    )
    if not m:
        return jsonify({"error": "Not found"}), 404
    db.session.commit()
    return jsonify({"id": m.id})


@bp.route("/stints/<int:stint_id>", methods=["DELETE"])
@login_required
def delete_stint(stint_id):
    """
    Remove one stint row. Refuses if it's the member's ONLY stint — dropping
    someone to zero stints via a raw delete here would leave them dangling
    in a different way than the roster-remove path (set_performer_members'
    drop-a-name flow, which goes through its own orphan/prune-safe logic).
    To remove someone entirely, drop them from the plain roster instead.
    """
    m = db.session.get(Membership, stint_id)
    if not m:
        return jsonify({"error": "Not found"}), 404
    remaining = db.session.query(Membership).filter_by(
        performer_id=m.performer_id, artist_id=m.artist_id).count()
    if remaining <= 1:
        return jsonify({"error": "This is the member's only stint — remove them from "
                                 "the roster instead of deleting their last stint."}), 409
    remove_membership_stint(stint_id)
    db.session.commit()
    return jsonify({"ok": True})


def _set_resources(performer, resources):
    """Replace a performer's reference resources with the given ordered list of
    {label, url} dicts (rows with a blank url are skipped)."""
    db.session.query(PerformerResource).filter_by(performer_id=performer.id).delete(
        synchronize_session=False)
    db.session.flush()
    for i, r in enumerate(resources or []):
        url = (r.get("url") or "").strip()
        if not url:
            continue
        db.session.add(PerformerResource(
            performer_id=performer.id, url=url,
            label=(r.get("label") or "").strip() or None, order=i))
    db.session.flush()


@bp.route("/<int:performer_id>", methods=["DELETE"])
@login_required
def delete_performer(performer_id):
    """Delete a performer. Refuses if it still has performances/recordings —
    reassign or delete those first. Member Artists left orphaned are pruned."""
    p = db.session.get(Performer, performer_id)
    if not p:
        return jsonify({"error": "Not found"}), 404
    n_perf = db.session.query(Performance).filter_by(performer_id=performer_id).count()
    if n_perf:
        return jsonify({"error": f"Performer has {n_perf} performance(s) — "
                                 "delete or reassign its recordings first."}), 409
    member_ids = [a.id for a in p.artists]
    db.session.delete(p)          # memberships cascade
    db.session.flush()
    # Prune any member Artist that now belongs to no performer.
    for aid in member_ids:
        a = db.session.get(Artist, aid)
        if a and not a.memberships:
            db.session.delete(a)
    db.session.commit()
    return jsonify({"ok": True})
