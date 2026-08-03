"""
api/genres.py — Genre endpoints.

Genre is a proper dimension (see the Genre design spec in Context Library,
2026-08-02): its own table, one FK from Performer, guarded delete — matching
how Venue, Collection and Artist deletes already behave. Nothing here ever
creates a genre implicitly; every picker in the frontend selects from this
list only.

Routes:
  GET    /api/genres/         — list genres (q= for search)
  GET    /api/genres/<id>     — genre detail: its performers + their recordings
  POST   /api/genres/         — create genre
  PUT    /api/genres/<id>     — update genre
  DELETE /api/genres/<id>     — delete genre (409 while referenced)
"""

from flask import Blueprint, request, jsonify
from flask_login import login_required
from sqlalchemy import func

from app.extensions import db
from app.models.genre import Genre
from app.models.performer import Performer
from app.models.performance import Performance
from app.models.recording import Recording
from app.utils.serialize import recording_summary

bp = Blueprint("genres", __name__)


@bp.route("/")
@login_required
def list_genres():
    q = request.args.get("q", "").strip()
    query = db.session.query(Genre)
    if q:
        query = query.filter(Genre.name.ilike(f"%{q}%"))
    genres = query.order_by(Genre.name).all()

    performer_counts = dict(
        db.session.query(Performer.genre_id, func.count(Performer.id))
        .filter(Performer.genre_id.isnot(None))
        .group_by(Performer.genre_id).all()
    )
    recording_counts = dict(
        db.session.query(Performer.genre_id, func.count(Recording.id))
        .join(Performance, Performance.performer_id == Performer.id)
        .join(Recording, Recording.performance_id == Performance.id)
        .filter(Performer.genre_id.isnot(None))
        .group_by(Performer.genre_id).all()
    )
    return jsonify([
        {
            "id":              g.id,
            "name":            g.name,
            "description":     g.description,
            "performer_count": performer_counts.get(g.id, 0),
            "recording_count": recording_counts.get(g.id, 0),
        }
        for g in genres
    ])


@bp.route("/<int:genre_id>")
@login_required
def get_genre(genre_id):
    g = db.session.get(Genre, genre_id)
    if not g:
        return jsonify({"error": "Not found"}), 404

    performers = (
        db.session.query(Performer)
        .filter(Performer.genre_id == genre_id)
        .order_by(func.coalesce(Performer.sort_name, Performer.name))
        .all()
    )
    perf_rows = []
    total_recordings = 0
    for p in performers:
        performances = (
            db.session.query(Performance)
            .filter(Performance.performer_id == p.id)
            .order_by(
                Performance.start_year.desc().nullsfirst(),
                Performance.start_month.desc().nullsfirst(),
                Performance.start_day.desc().nullsfirst(),
            ).all()
        )
        # Flatten to one row per Recording, decorated with the performance's
        # date/venue — same shape the Performer page's flat recording table
        # expects (see get_performer_recordings / all_recordings).
        recordings = []
        for perf in performances:
            v = perf.venue
            for r in perf.recordings:
                row = recording_summary(r)
                row.update({
                    "performer":   p.name,
                    "start_year":  perf.start_year,
                    "start_month": perf.start_month,
                    "start_day":   perf.start_day,
                    "venue":       v.name    if v else None,
                    "city":        v.city    if v else perf.city,
                    "state":       v.state   if v else perf.state,
                    "country":     v.country if v else perf.country,
                })
                recordings.append(row)
        total_recordings += len(recordings)
        perf_rows.append({
            "id":              p.id,
            "name":            p.name,
            "recording_count": len(recordings),
            "recordings":      recordings,
        })

    return jsonify({
        "id":              g.id,
        "name":            g.name,
        "description":     g.description,
        "performer_count": len(perf_rows),
        "recording_count": total_recordings,
        "performers":      perf_rows,
    })


@bp.route("/", methods=["POST"])
@login_required
def create_genre():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    if db.session.query(Genre).filter(func.lower(Genre.name) == name.lower()).first():
        return jsonify({"error": "Genre already exists"}), 409
    g = Genre(name=name, description=(data.get("description") or "").strip() or None)
    db.session.add(g)
    db.session.commit()
    return jsonify({"id": g.id, "name": g.name}), 201


@bp.route("/<int:genre_id>", methods=["PUT"])
@login_required
def update_genre(genre_id):
    g = db.session.get(Genre, genre_id)
    if not g:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json() or {}
    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400
        dup = db.session.query(Genre).filter(
            func.lower(Genre.name) == name.lower(), Genre.id != genre_id).first()
        if dup:
            return jsonify({"error": "Genre already exists"}), 409
        g.name = name
    if "description" in data:
        g.description = (data["description"] or "").strip() or None
    db.session.commit()
    return jsonify({"id": g.id})


@bp.route("/<int:genre_id>", methods=["DELETE"])
@login_required
def delete_genre(genre_id):
    """Delete a genre. Refuses while performers still reference it."""
    g = db.session.get(Genre, genre_id)
    if not g:
        return jsonify({"error": "Not found"}), 404
    n = db.session.query(Performer).filter_by(genre_id=genre_id).count()
    if n:
        return jsonify({"error": f"Genre has {n} performer(s) — reassign or "
                                 "clear those first."}), 409
    db.session.delete(g)
    db.session.commit()
    return jsonify({"ok": True})
