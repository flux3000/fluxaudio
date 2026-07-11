"""
api/artists.py — Artist (person) endpoints: search, list, and person→performers
aggregation (the "everything by Béla Fleck" view). Used to pick members in the
Add/Edit forms.
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required
from sqlalchemy import func

from app.extensions import db
from app.models.artist import Artist

bp = Blueprint("artists", __name__)


@bp.route("/search")
@login_required
def search_artists():
    """Person-name autocomplete for the Members multi-select."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    rows = (db.session.query(Artist)
            .filter(Artist.name.ilike(f"%{q}%"))
            .order_by(Artist.name).limit(12).all())
    return jsonify([{"id": a.id, "name": a.name} for a in rows])


@bp.route("/")
@login_required
def list_artists():
    rows = db.session.query(Artist).order_by(
        func.coalesce(Artist.sort_name, Artist.name)).all()
    return jsonify([
        {"id": a.id, "name": a.name, "sort_name": a.sort_name} for a in rows
    ])


@bp.route("/<int:artist_id>")
@login_required
def get_artist(artist_id):
    """A person + every Performer (act) they're a member of."""
    a = db.session.get(Artist, artist_id)
    if not a:
        return jsonify({"error": "Not found"}), 404
    performers = [m.performer for m in a.memberships]
    return jsonify({
        "id":         a.id,
        "name":       a.name,
        "sort_name":  a.sort_name,
        "bio":        a.bio,
        "performers": [{"id": p.id, "name": p.name} for p in performers],
    })


@bp.route("/", methods=["POST"])
@login_required
def create_artist():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    existing = db.session.query(Artist).filter(func.lower(Artist.name) == name.lower()).first()
    if existing:
        return jsonify({"id": existing.id, "name": existing.name}), 200
    a = Artist(name=name, sort_name=data.get("sort_name"), bio=data.get("bio"))
    db.session.add(a)
    db.session.commit()
    return jsonify({"id": a.id, "name": a.name}), 201
