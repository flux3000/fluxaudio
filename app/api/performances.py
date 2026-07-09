"""
api/performances.py — Performance endpoints.

Routes:
  GET  /api/performances/              — list performances (filter: artist_id, year)
  GET  /api/performances/<id>          — performance detail + recordings
  POST /api/performances/              — create (archivist+)
  PUT  /api/performances/<id>          — update (archivist+)
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required

from app.extensions import db
from app.models.performance import Performance
from app.models.performer import Performer, PerformerArtist

bp = Blueprint("performances", __name__)


def _fmt_date(year, month, day):
    """Format nullable year/month/day into a display string."""
    if not year:
        return None
    if month and day:
        return f"{year}-{month:02d}-{day:02d}"
    if month:
        return f"{year}-{month:02d}"
    return str(year)


# ── GET /api/performances/ ─────────────────────────────────────────────────────

@bp.route("/")
@login_required
def list_performances():
    artist_id = request.args.get("artist_id", type=int)
    year      = request.args.get("year",      type=int)

    q = db.session.query(Performance)
    if artist_id:
        q = (q.join(Performer,       Performer.id == Performance.performer_id)
               .join(PerformerArtist, PerformerArtist.performer_id == Performer.id)
               .filter(PerformerArtist.artist_id == artist_id))
    if year:
        q = q.filter(Performance.start_year == year)

    perfs = q.order_by(Performance.start_year.desc()).limit(200).all()
    return jsonify([
        {
            "id":        p.id,
            "performer": p.performer.name,
            "date":      _fmt_date(p.start_year, p.start_month, p.start_day),
            "venue":     p.venue.name if p.venue else None,
            "city":      p.venue.city  if p.venue else p.city,
            "state":     p.venue.state if p.venue else p.state,
        }
        for p in perfs
    ])


# ── GET /api/performances/<id> ─────────────────────────────────────────────────

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
        "notes":        p.notes,
        "recordings": [
            {
                "id":              r.id,
                "source":          r.source,
                "source_modifier": r.source_modifier,
                "quality":         r.quality,
                "is_complete":     r.is_complete,
                "track_count":     len(r.tracks),
            }
            for r in p.recordings
        ],
    })


# ── POST /api/performances/ ────────────────────────────────────────────────────

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


# ── PUT /api/performances/<id> ─────────────────────────────────────────────────

@bp.route("/<int:performance_id>", methods=["PUT"])
@login_required
def update_performance(performance_id):
    p = db.session.get(Performance, performance_id)
    if not p:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json()
    for f in ["title", "stage", "start_year", "start_month", "start_day",
              "end_year", "end_month", "end_day", "venue_id",
              "city", "state", "country", "notes"]:
        if f in data:
            setattr(p, f, data[f])
    db.session.commit()
    return jsonify({"id": p.id})
