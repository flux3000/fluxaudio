"""
api/collections.py — Collections: optional user-defined groupings of Recordings
(many-to-many). CRUD + add/remove a recording.
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required

from app.extensions import db
from app.models.collection import Collection, CollectionRecording
from app.models.recording import Recording
from app.utils.serialize import recording_row

bp = Blueprint("collections", __name__)


@bp.route("/")
@login_required
def list_collections():
    cols = db.session.query(Collection).order_by(Collection.name).all()
    return jsonify([
        {"id": c.id, "name": c.name, "description": c.description,
         "recording_count": len(c.recording_links)}
        for c in cols
    ])


@bp.route("/<int:collection_id>")
@login_required
def get_collection(collection_id):
    c = db.session.get(Collection, collection_id)
    if not c:
        return jsonify({"error": "Not found"}), 404
    # card=True: the collection page renders handbill cards (Ryan, 2026-08-07),
    # which need the performer's genre colour and primary photo. Collections
    # are small — a few dozen rows — so the extra joins are cheap here in a way
    # they would not be on the 544-row flat List.
    rows = [recording_row(l.recording, card=True) for l in c.recording_links]
    rows.sort(key=lambda r: ((r["performer"] or "").lower(),
                             r["start_year"] or 0, r["start_month"] or 0, r["start_day"] or 0))
    return jsonify({"id": c.id, "name": c.name, "description": c.description, "recordings": rows})


@bp.route("/", methods=["POST"])
@login_required
def create_collection():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    c = Collection(name=name, description=data.get("description"))
    db.session.add(c)
    db.session.commit()
    return jsonify({"id": c.id, "name": c.name}), 201


@bp.route("/<int:collection_id>", methods=["PUT"])
@login_required
def update_collection(collection_id):
    c = db.session.get(Collection, collection_id)
    if not c:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json() or {}
    for f in ["name", "description"]:
        if f in data:
            setattr(c, f, data[f])
    db.session.commit()
    return jsonify({"id": c.id})


@bp.route("/<int:collection_id>", methods=["DELETE"])
@login_required
def delete_collection(collection_id):
    c = db.session.get(Collection, collection_id)
    if c:
        db.session.delete(c)
        db.session.commit()
    return jsonify({"ok": True})


@bp.route("/<int:collection_id>/recordings", methods=["POST"])
@login_required
def add_recording(collection_id):
    c = db.session.get(Collection, collection_id)
    if not c:
        return jsonify({"error": "Not found"}), 404
    rid = (request.get_json() or {}).get("recording_id")
    if not db.session.get(Recording, rid):
        return jsonify({"error": "recording not found"}), 404
    exists = db.session.query(CollectionRecording).filter_by(
        collection_id=collection_id, recording_id=rid).first()
    if not exists:
        n = db.session.query(CollectionRecording).filter_by(collection_id=collection_id).count()
        db.session.add(CollectionRecording(collection_id=collection_id, recording_id=rid, order=n))
        db.session.commit()
    return jsonify({"ok": True})


@bp.route("/<int:collection_id>/recordings/<int:recording_id>", methods=["DELETE"])
@login_required
def remove_recording(collection_id, recording_id):
    link = db.session.query(CollectionRecording).filter_by(
        collection_id=collection_id, recording_id=recording_id).first()
    if link:
        db.session.delete(link)
        db.session.commit()
    return jsonify({"ok": True})
