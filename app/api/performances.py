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
from sqlalchemy import func

from app.extensions import db
from app.models.performance import Performance
from app.models.artist import Artist, ArtistCanonical
from app.models.canonical_artist import CanonicalArtist
from app.utils.format import format_partial_date
from app.utils.serialize import recording_summary
from app.utils.pruning import prune_artist_if_orphaned

bp = Blueprint("performances", __name__)


def _resolve_or_create_artist(name):
    """
    Resolve an artist by name (case-insensitive) or create it with a 1:1
    canonical artist, mirroring the ingest chain. In the decoupled model, if a
    canonical of that name already exists it is reused (a new bare artist is
    linked to it) rather than duplicated.
    """
    artist = db.session.query(Artist).filter(
        func.lower(Artist.name) == name.lower()
    ).first()
    if artist:
        return artist

    artist = Artist(name=name)
    db.session.add(artist)
    db.session.flush()

    canonical = db.session.query(CanonicalArtist).filter(
        func.lower(CanonicalArtist.name) == name.lower()
    ).first()
    if not canonical:
        canonical = CanonicalArtist(name=name)
        db.session.add(canonical)
        db.session.flush()
    db.session.add(ArtistCanonical(artist_id=artist.id, canonical_artist_id=canonical.id, order=0))
    db.session.flush()
    return artist


# ── GET /api/performances/ ─────────────────────────────────────────────────────

@bp.route("/")
@login_required
def list_performances():
    artist_id = request.args.get("artist_id", type=int)
    year      = request.args.get("year",      type=int)

    # `artist_id` query param filters by CANONICAL artist (sidebar grouping).
    q = db.session.query(Performance)
    if artist_id:
        q = (q.join(Artist,          Artist.id == Performance.artist_id)
               .join(ArtistCanonical, ArtistCanonical.artist_id == Artist.id)
               .filter(ArtistCanonical.canonical_artist_id == artist_id))
    if year:
        q = q.filter(Performance.start_year == year)

    perfs = q.order_by(Performance.start_year.desc()).limit(200).all()
    return jsonify([
        {
            "id":        p.id,
            "performer": p.artist.name,
            "date":      format_partial_date(p.start_year, p.start_month, p.start_day),
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
        # JSON keys kept stable (performer* = the performing Artist) for the frontend.
        "id":           p.id,
        "performer_id": p.artist_id,
        "performer":    p.artist.name,
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
        "recordings":   [recording_summary(r) for r in p.recordings],
    })


# ── POST /api/performances/ ────────────────────────────────────────────────────

@bp.route("/", methods=["POST"])
@login_required
def create_performance():
    data = request.get_json()
    if not data.get("performer_id"):
        return jsonify({"error": "performer_id is required"}), 400
    p = Performance(
        artist_id    = data["performer_id"],   # JSON param kept stable; maps to artist
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

    # Reassign the Artist when a new name is supplied and it differs from the
    # current one. Scoped to this performance — the recording(s) under it move
    # to the resolved/created artist. The old artist is pruned if it's left
    # with no performances. (JSON param `performer_name` kept stable.)
    reassigned = None
    new_name = (data.get("performer_name") or "").strip()
    if new_name and new_name.lower() != p.artist.name.lower():
        old_artist_id = p.artist_id
        artist = _resolve_or_create_artist(new_name)
        p.artist_id = artist.id
        db.session.flush()
        prune_artist_if_orphaned(old_artist_id)
        reassigned = artist.name

    for f in ["title", "stage", "start_year", "start_month", "start_day",
              "end_year", "end_month", "end_day", "venue_id",
              "city", "state", "country", "notes"]:
        if f in data:
            setattr(p, f, data[f])
    db.session.commit()
    return jsonify({"id": p.id, "reassigned_to": reassigned})
