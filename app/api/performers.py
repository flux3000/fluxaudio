"""
api/performers.py — Performer (act) endpoints: browse, catalog, search, members.

A Performer is the act you browse and tag by. Its member Artists (people) are
managed here too. Grouping "everything by a person" lives on the Artist side
(api/artists.py), not here.
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required
from sqlalchemy import func

from app.extensions import db
from app.models.performer import Performer, PerformerResource
from app.models.artist import Artist
from app.models.performance import Performance
from app.models.recording import Recording
from app.utils.serialize import recording_summary
from app.utils.performers import set_performer_members

bp = Blueprint("performers", __name__)


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
        "members":   [{"id": a.id, "name": a.name} for a in p.artists],
        "resources": [{"id": r.id, "label": r.label, "url": r.url} for r in p.resources],
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
    for f in ["name", "sort_name", "bio"]:
        if f in data:
            setattr(p, f, data[f])
    if data.get("members") is not None:
        set_performer_members(p, data["members"])
    if data.get("resources") is not None:
        _set_resources(p, data["resources"])
    db.session.commit()
    return jsonify({"id": p.id})


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
