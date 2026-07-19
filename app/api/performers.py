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
from app.models.artist import Artist, Membership
from app.models.performance import Performance
from app.models.recording import Recording
from app.utils.serialize import recording_summary
from app.utils.performers import (
    set_performer_members, add_membership_stint,
    update_membership_stint_bounds, remove_membership_stint,
)

bp = Blueprint("performers", __name__)


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
