"""
api/performances.py — Performance endpoints.

A Performance belongs to one Performer (the act). The performer's member Artists
(people) are exposed as `members`.
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required

from app.extensions import db
from app.models.performance import Performance
from app.models.performer import Performer
from app.utils.format import format_partial_date
from app.utils.serialize import recording_summary
from app.utils.performers import resolve_or_create_performer, set_performer_members
from app.utils.pruning import prune_performer_if_orphaned

bp = Blueprint("performances", __name__)


@bp.route("/")
@login_required
def list_performances():
    performer_id = request.args.get("performer_id", type=int)
    year         = request.args.get("year",         type=int)

    q = db.session.query(Performance)
    if performer_id:
        q = q.filter(Performance.performer_id == performer_id)
    if year:
        q = q.filter(Performance.start_year == year)

    perfs = q.order_by(Performance.start_year.desc()).limit(200).all()
    return jsonify([
        {
            "id":        p.id,
            "performer": p.performer.name,
            "date":      format_partial_date(p.start_year, p.start_month, p.start_day),
            "venue":     p.venue.name if p.venue else None,
            "city":      p.venue.city  if p.venue else p.city,
            "state":     p.venue.state if p.venue else p.state,
        }
        for p in perfs
    ])


@bp.route("/<int:performance_id>")
@login_required
def get_performance(performance_id):
    p = db.session.get(Performance, performance_id)
    if not p:
        return jsonify({"error": "Not found"}), 404

    v = p.venue
    return jsonify({
        "id":           p.id,
        "performer_id": p.performer_id,
        "performer":    p.performer.name,
        "members":      [{"id": a.id, "name": a.name} for a in p.performer.artists],
        "title":        p.title,
        "stage":        p.stage,
        "start_year":   p.start_year,
        "start_month":  p.start_month,
        "start_day":    p.start_day,
        "end_year":     p.end_year,
        "end_month":    p.end_month,
        "end_day":      p.end_day,
        "venue_id":     v.id      if v else None,
        "venue_name":   v.name    if v else None,
        "city":         v.city    if v else p.city,
        "state":        v.state   if v else p.state,
        "country":      v.country if v else p.country,
        "event_id":     p.event_id,
        "event_name":   p.event.name if p.event else None,
        "notes":        p.notes,
        "recordings":   [recording_summary(r) for r in p.recordings],
    })


@bp.route("/", methods=["POST"])
@login_required
def create_performance():
    data = request.get_json()
    if not data.get("performer_id"):
        return jsonify({"error": "performer_id is required"}), 400
    p = Performance(
        performer_id = data["performer_id"],
        venue_id     = data.get("venue_id"),
        event_id     = data.get("event_id"),
        title        = data.get("title"),
        stage        = data.get("stage"),
        start_year   = data.get("start_year"),
        start_month  = data.get("start_month"),
        start_day    = data.get("start_day"),
        end_year     = data.get("end_year"),
        end_month    = data.get("end_month"),
        end_day      = data.get("end_day"),
        city         = data.get("city"),
        state        = data.get("state"),
        country      = data.get("country"),
        notes        = data.get("notes"),
    )
    db.session.add(p)
    db.session.commit()
    return jsonify({"id": p.id}), 201


@bp.route("/<int:performance_id>", methods=["PUT"])
@login_required
def update_performance(performance_id):
    p = db.session.get(Performance, performance_id)
    if not p:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json()

    # Reassign the Performer when a new name is supplied and differs from the
    # current one. Scoped to this performance; the old performer is pruned if it
    # is left with no performances.
    reassigned = None
    new_name = (data.get("performer_name") or "").strip()
    if new_name and new_name.lower() != p.performer.name.lower():
        old_performer_id = p.performer_id
        performer = resolve_or_create_performer(new_name)
        p.performer_id = performer.id
        db.session.flush()
        prune_performer_if_orphaned(old_performer_id)
        reassigned = performer.name

    # Update the performer's roster if members supplied (global to the act).
    if data.get("members") is not None:
        set_performer_members(p.performer, data["members"])

    for f in ["title", "stage", "start_year", "start_month", "start_day",
              "end_year", "end_month", "end_day", "venue_id", "event_id",
              "city", "state", "country", "notes"]:
        if f in data:
            setattr(p, f, data[f])
    db.session.commit()
    return jsonify({"id": p.id, "reassigned_to": reassigned})
