"""
api/venues.py — Venue endpoints.

Routes:
  GET  /api/venues/        — list venues (q= for search, limit=100)
  GET  /api/venues/<id>    — venue detail + performances
  POST /api/venues/        — create venue
  PUT  /api/venues/<id>    — update venue
"""

from flask import Blueprint, request, jsonify
from flask_login import login_required
from sqlalchemy import or_

from app.extensions import db
from app.models.venue import Venue

bp = Blueprint("venues", __name__)


@bp.route("/")
@login_required
def list_venues():
    q = request.args.get("q", "").strip()
    query = db.session.query(Venue)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(Venue.name.ilike(like), Venue.city.ilike(like), Venue.state.ilike(like))
        )
    venues = query.order_by(Venue.name).limit(200).all()
    return jsonify([
        {
            "id":                v.id,
            "name":              v.name,
            "city":              v.city,
            "state":             v.state,
            "country":           v.country,
            "performance_count": len(v.performances),
        }
        for v in venues
    ])


@bp.route("/<int:venue_id>")
@login_required
def get_venue(venue_id):
    v = db.session.get(Venue, venue_id)
    if not v:
        return jsonify({"error": "Not found"}), 404

    perfs = sorted(
        v.performances,
        key=lambda p: (p.start_year or 0, p.start_month or 0, p.start_day or 0),
        reverse=True,
    )

    def fmt_date(p):
        parts = [str(p.start_year or "?")]
        if p.start_month:
            parts.append(str(p.start_month).zfill(2))
        if p.start_day:
            parts.append(str(p.start_day).zfill(2))
        return "-".join(parts)

    return jsonify({
        "id":                v.id,
        "name":              v.name,
        "city":              v.city,
        "state":             v.state,
        "country":           v.country,
        "bio":               v.bio,
        "performance_count": len(v.performances),
        "performances": [
            {
                "id":        p.id,
                "performer": p.performer.name,
                "date":      fmt_date(p),
            }
            for p in perfs
        ],
    })


@bp.route("/", methods=["POST"])
@login_required
def create_venue():
    data = request.get_json()
    if not data.get("name", "").strip():
        return jsonify({"error": "name is required"}), 400
    v = Venue(
        name    = data["name"].strip(),
        city    = data.get("city", "").strip() or None,
        state   = data.get("state", "").strip() or None,
        country = data.get("country", "").strip() or None,
        bio     = data.get("bio", "").strip() or None,
    )
    db.session.add(v)
    db.session.commit()
    return jsonify({"id": v.id, "name": v.name}), 201


@bp.route("/<int:venue_id>", methods=["PUT"])
@login_required
def update_venue(venue_id):
    v = db.session.get(Venue, venue_id)
    if not v:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json()
    for field in ["name", "city", "state", "country", "bio"]:
        if field in data:
            setattr(v, field, data[field])
    db.session.commit()
    return jsonify({"id": v.id})
