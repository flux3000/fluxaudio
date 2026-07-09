"""
api/events.py — Event endpoints.

An Event is a named container for one or more performances.
Examples: "Bonnaroo 2009" (festival), "Fall 1989 Tour" (tour run).

Routes:
  GET  /api/events/          — list events (q= for search, limit=200)
  GET  /api/events/search    — name autocomplete (q=)
  GET  /api/events/<id>      — event detail + linked performances
  POST /api/events/          — create event
  PUT  /api/events/<id>      — update event
"""

from flask import Blueprint, request, jsonify
from flask_login import login_required
from sqlalchemy import or_, func

from app.extensions import db
from app.models.event import Event
from app.models.venue import Venue

bp = Blueprint("events", __name__)


# ── Autocomplete (must come before /<int:event_id>) ───────────────────────────

@bp.route("/search")
@login_required
def search_events():
    """Name autocomplete for the ingest form. Returns [{id, name}]."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    like = f"%{q}%"
    rows = (
        db.session.query(Event.id, Event.name)
        .filter(Event.name.ilike(like))
        .order_by(Event.name)
        .limit(12)
        .all()
    )
    return jsonify([{"id": r.id, "name": r.name} for r in rows])


# ── List ──────────────────────────────────────────────────────────────────────

@bp.route("/")
@login_required
def list_events():
    q = request.args.get("q", "").strip()
    query = db.session.query(Event)
    if q:
        like = f"%{q}%"
        query = query.filter(Event.name.ilike(like))
    events = query.order_by(Event.name).limit(200).all()
    return jsonify([_event_summary(e) for e in events])


# ── Detail ────────────────────────────────────────────────────────────────────

@bp.route("/<int:event_id>")
@login_required
def get_event(event_id):
    e = db.session.get(Event, event_id)
    if not e:
        return jsonify({"error": "Not found"}), 404

    perfs = sorted(
        e.performances,
        key=lambda p: (p.start_year or 0, p.start_month or 0, p.start_day or 0),
    )

    return jsonify({
        **_event_summary(e),
        "notes": e.notes,
        "venue": {"id": e.venue.id, "name": e.venue.name} if e.venue else None,
        "performances": [
            {
                "id":        p.id,
                "performer": p.performer.name if p.performer else None,
                "date":      _fmt_date(p),
                "stage":     p.stage,
                "recording_count": len(p.recordings),
            }
            for p in perfs
        ],
    })


# ── Create ────────────────────────────────────────────────────────────────────

@bp.route("/", methods=["POST"])
@login_required
def create_event():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    # Prevent exact-name duplicates
    existing = db.session.query(Event).filter(
        func.lower(Event.name) == name.lower()
    ).first()
    if existing:
        return jsonify({"error": "Event already exists", "id": existing.id}), 409

    venue_id = data.get("venue_id") or None
    e = Event(
        name        = name,
        venue_id    = int(venue_id) if venue_id else None,
        city        = (data.get("city")    or "").strip() or None,
        state       = (data.get("state")   or "").strip() or None,
        country     = (data.get("country") or "").strip() or None,
        start_year  = data.get("start_year"),
        start_month = data.get("start_month"),
        start_day   = data.get("start_day"),
        end_year    = data.get("end_year"),
        end_month   = data.get("end_month"),
        end_day     = data.get("end_day"),
        notes       = (data.get("notes") or "").strip() or None,
    )
    db.session.add(e)
    db.session.commit()
    return jsonify({"id": e.id, "name": e.name}), 201


# ── Update ────────────────────────────────────────────────────────────────────

@bp.route("/<int:event_id>", methods=["PUT"])
@login_required
def update_event(event_id):
    e = db.session.get(Event, event_id)
    if not e:
        return jsonify({"error": "Not found"}), 404

    data = request.get_json() or {}

    if "name" in data:
        e.name = (data["name"] or "").strip() or e.name
    if "venue_id" in data:
        e.venue_id = int(data["venue_id"]) if data["venue_id"] else None
    if "city" in data:
        e.city = (data["city"] or "").strip() or None
    if "state" in data:
        e.state = (data["state"] or "").strip() or None
    if "country" in data:
        e.country = (data["country"] or "").strip() or None
    if "start_year"  in data: e.start_year  = data["start_year"]
    if "start_month" in data: e.start_month = data["start_month"]
    if "start_day"   in data: e.start_day   = data["start_day"]
    if "end_year"    in data: e.end_year    = data["end_year"]
    if "end_month"   in data: e.end_month   = data["end_month"]
    if "end_day"     in data: e.end_day     = data["end_day"]
    if "notes"       in data: e.notes       = (data["notes"] or "").strip() or None

    db.session.commit()
    return jsonify(_event_summary(e))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _event_summary(e):
    return {
        "id":               e.id,
        "name":             e.name,
        "city":             e.city,
        "state":            e.state,
        "country":          e.country,
        "start_year":       e.start_year,
        "start_month":      e.start_month,
        "start_day":        e.start_day,
        "end_year":         e.end_year,
        "end_month":        e.end_month,
        "end_day":          e.end_day,
        "performance_count": len(e.performances),
    }


def _fmt_date(p):
    parts = [str(p.start_year or "?")]
    if p.start_month: parts.append(str(p.start_month).zfill(2))
    if p.start_day:   parts.append(str(p.start_day).zfill(2))
    return "-".join(parts)
