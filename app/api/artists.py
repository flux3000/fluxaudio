"""
api/artists.py — Artist (person) endpoints: search, list, and person→performers
aggregation (the "everything by Béla Fleck" view). Used to pick members in the
Add/Edit forms.
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required
from sqlalchemy import func

from app.extensions import db
from app.models.artist import Artist, Membership
from app.models.performer import Performer
from app.utils.performers import resolve_or_create_performer

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
    # Skip any dangling membership whose performer was removed.
    performers = [m.performer for m in a.memberships if m.performer is not None]
    performers.sort(key=lambda p: (p.sort_name or p.name).lower())
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


@bp.route("/<int:artist_id>", methods=["PUT"])
@login_required
def update_artist(artist_id):
    a = db.session.get(Artist, artist_id)
    if not a:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json()
    for f in ["name", "sort_name", "bio"]:
        if f in data:
            setattr(a, f, data[f])
    db.session.commit()
    return jsonify({"id": a.id})


@bp.route("/<int:artist_id>", methods=["DELETE"])
@login_required
def delete_artist(artist_id):
    """Delete a person. Refuses while they're still a member of any Performer —
    remove them from those acts first."""
    a = db.session.get(Artist, artist_id)
    if not a:
        return jsonify({"error": "Not found"}), 404
    n = len(a.memberships)
    if n:
        return jsonify({"error": f"Artist is a member of {n} performer(s) — "
                                 "remove them from those acts first."}), 409
    db.session.delete(a)
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/<int:artist_id>/performers", methods=["POST"])
@login_required
def add_performer_association(artist_id):
    """Associate this person with a Performer (by id or name; creates the
    performer if only a new name is given). Appends to the act's roster."""
    a = db.session.get(Artist, artist_id)
    if not a:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json() or {}
    pid  = data.get("performer_id")
    if pid:
        performer = db.session.get(Performer, pid)
        if not performer:
            return jsonify({"error": "performer not found"}), 404
    else:
        name = (data.get("performer_name") or "").strip()
        if not name:
            return jsonify({"error": "performer_id or performer_name required"}), 400
        performer = resolve_or_create_performer(name)

    exists = db.session.query(Membership).filter_by(
        performer_id=performer.id, artist_id=artist_id).first()
    if not exists:
        order = db.session.query(Membership).filter_by(performer_id=performer.id).count()
        db.session.add(Membership(performer_id=performer.id, artist_id=artist_id, order=order))
    db.session.commit()
    return jsonify({"id": performer.id, "name": performer.name})


@bp.route("/<int:artist_id>/performers/<int:performer_id>", methods=["DELETE"])
@login_required
def remove_performer_association(artist_id, performer_id):
    """Remove this person from a Performer's roster (the performer itself stays)."""
    db.session.query(Membership).filter_by(
        performer_id=performer_id, artist_id=artist_id).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"ok": True})
