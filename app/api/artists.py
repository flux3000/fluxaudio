"""
api/artists.py — Artist endpoints.

Routes:
  GET  /api/artists/                — list all artists with recording counts
  GET  /api/artists/all-recordings  — all artists (alpha) with performances (oldest first)
  GET  /api/artists/<id>            — artist detail + aliases
  GET  /api/artists/<id>/recordings — performances + recordings for catalog view
  POST /api/artists/                — create artist (archivist+)
  PUT  /api/artists/<id>            — update artist (archivist+)
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required
from sqlalchemy import func

from app.extensions import db
from app.models.artist import Artist, ArtistAlias
from app.models.performer import Performer, PerformerArtist
from app.models.performance import Performance
from app.models.recording import Recording

bp = Blueprint("artists", __name__)


# ── GET /api/artists/search?q= ────────────────────────────────────────────────

@bp.route("/search")
@login_required
def search_artists():
    """
    Return performer names matching q (case-insensitive substring).
    Powers the artist autocomplete in the ingest form.
    """
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    from app.models.performer import Performer
    rows = (
        db.session.query(Performer.name)
        .filter(Performer.name.ilike(f"%{q}%"))
        .order_by(Performer.name)
        .limit(12)
        .all()
    )
    return jsonify([r[0] for r in rows])


# ── GET /api/artists/ ──────────────────────────────────────────────────────────

@bp.route("/")
@login_required
def list_artists():
    """
    Return all artists sorted by sort_name, with recording counts.

    Also includes `performers` — the distinct linked Performer names — so the
    sidebar nav can show an expand affordance for artists with sub-artists
    (e.g. "Bill Evans" linked to "Bill Evans Trio", "... + Friends", etc.)
    without a follow-up request per row.
    """
    rows = (
        db.session.query(Artist, func.count(Recording.id).label("rc"))
        .outerjoin(PerformerArtist, PerformerArtist.artist_id == Artist.id)
        .outerjoin(Performer,       Performer.id == PerformerArtist.performer_id)
        .outerjoin(Performance,     Performance.performer_id == Performer.id)
        .outerjoin(Recording,       Recording.performance_id == Performance.id)
        .group_by(Artist.id)
        .order_by(func.coalesce(Artist.sort_name, Artist.name))
        .all()
    )

    # One extra query for linked performer names, grouped by artist_id
    performer_rows = (
        db.session.query(PerformerArtist.artist_id, Performer.name)
        .join(Performer, Performer.id == PerformerArtist.performer_id)
        .all()
    )
    performers_by_artist = {}
    for artist_id, performer_name in performer_rows:
        performers_by_artist.setdefault(artist_id, []).append(performer_name)

    return jsonify([
        {
            "id":              a.id,
            "name":            a.name,
            "sort_name":       a.sort_name,
            "recording_count": rc,
            # Sub-artists distinct from the canonical name itself (for the
            # sidebar's expandable tree). Sorted for stable display.
            "sub_artists": sorted(
                p for p in set(performers_by_artist.get(a.id, []))
                if p != a.name
            ),
        }
        for a, rc in rows
    ])


# ── GET /api/artists/all-recordings ───────────────────────────────────────────

@bp.route("/all-recordings")
@login_required
def get_all_recordings():
    """
    Return every artist (alphabetical) with their performances (oldest first).
    Powers the default library view. Artists with no recordings are omitted.
    """
    artists = (
        db.session.query(Artist)
        .order_by(func.coalesce(Artist.sort_name, Artist.name))
        .all()
    )

    result = []
    for artist in artists:
        performances = (
            db.session.query(Performance)
            .join(Performer,       Performer.id == Performance.performer_id)
            .join(PerformerArtist, PerformerArtist.performer_id == Performer.id)
            .filter(PerformerArtist.artist_id == artist.id)
            .order_by(
                Performance.start_year.asc().nullslast(),
                Performance.start_month.asc().nullslast(),
                Performance.start_day.asc().nullslast(),
            )
            .all()
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
                "recordings": [
                    {
                        "id":              r.id,
                        "source":          r.source,
                        "source_modifier": r.source_modifier,
                        "quality":         r.quality,
                        "rating":          r.rating,
                        "is_complete":     r.is_complete,
                        "track_count":     len(r.tracks),
                    }
                    for r in p.recordings
                ],
            })

        result.append({
            "artist_id":       artist.id,
            "artist_name":     artist.name,
            "performance_count": len(perf_list),
            "recording_count": sum(len(p["recordings"]) for p in perf_list),
            "performances":    perf_list,
        })

    return jsonify(result)


# ── GET /api/artists/performers — all performers (for picker) ─────────────────

@bp.route("/performers")
@login_required
def list_performers():
    """Return all performers with their currently linked artist IDs."""
    rows = (
        db.session.query(Performer)
        .order_by(Performer.name)
        .all()
    )
    return jsonify([
        {
            "id":         p.id,
            "name":       p.name,
            "artist_ids": [link.artist_id for link in p.artist_links],
        }
        for p in rows
    ])


# ── GET /api/artists/<id> ──────────────────────────────────────────────────────

@bp.route("/<int:artist_id>")
@login_required
def get_artist(artist_id):
    a = db.session.get(Artist, artist_id)
    if not a:
        return jsonify({"error": "Not found"}), 404

    # Linked performers (via PerformerArtist junction)
    linked = (
        db.session.query(Performer)
        .join(PerformerArtist, PerformerArtist.performer_id == Performer.id)
        .filter(PerformerArtist.artist_id == artist_id)
        .order_by(Performer.name)
        .all()
    )

    return jsonify({
        "id":         a.id,
        "name":       a.name,
        "sort_name":  a.sort_name,
        "bio":        a.bio,
        "aliases":    [alias.alias for alias in a.aliases],
        "performers": [{"id": p.id, "name": p.name} for p in linked],
    })


# ── POST /api/artists/<id>/performers — link a performer ──────────────────────

@bp.route("/<int:artist_id>/performers", methods=["POST"])
@login_required
def link_performer(artist_id):
    a = db.session.get(Artist, artist_id)
    if not a:
        return jsonify({"error": "Not found"}), 404
    data         = request.get_json()
    performer_id = data.get("performer_id")
    p = db.session.get(Performer, performer_id)
    if not p:
        return jsonify({"error": "Performer not found"}), 404
    # Idempotent — skip if already linked
    exists = db.session.query(PerformerArtist).filter_by(
        artist_id=artist_id, performer_id=performer_id
    ).first()
    if not exists:
        db.session.add(PerformerArtist(artist_id=artist_id, performer_id=performer_id, order=0))
        db.session.commit()
    return jsonify({"ok": True, "performer": {"id": p.id, "name": p.name}}), 201


# ── DELETE /api/artists/<id>/performers/<performer_id> — unlink ───────────────

@bp.route("/<int:artist_id>/performers/<int:performer_id>", methods=["DELETE"])
@login_required
def unlink_performer(artist_id, performer_id):
    link = db.session.query(PerformerArtist).filter_by(
        artist_id=artist_id, performer_id=performer_id
    ).first()
    if link:
        db.session.delete(link)
        db.session.commit()
    return jsonify({"ok": True})


# ── GET /api/artists/<id>/recordings ──────────────────────────────────────────

@bp.route("/<int:artist_id>/recordings")
@login_required
def get_artist_recordings(artist_id):
    """
    Return all performances for this artist with nested recordings.
    This powers the catalog browser — performances ordered newest first.
    """
    artist = db.session.get(Artist, artist_id)
    if not artist:
        return jsonify({"error": "Not found"}), 404

    performances = (
        db.session.query(Performance)
        .join(Performer,       Performer.id == Performance.performer_id)
        .join(PerformerArtist, PerformerArtist.performer_id == Performer.id)
        .filter(PerformerArtist.artist_id == artist_id)
        .order_by(
            Performance.start_year.desc().nullsfirst(),
            Performance.start_month.desc().nullsfirst(),
            Performance.start_day.desc().nullsfirst(),
        )
        .all()
    )

    out = []
    for p in performances:
        v = p.venue  # may be None
        out.append({
            "performance_id":  p.id,
            "performer_name":  p.performer.name,
            "title":           p.title,
            "stage":           p.stage,
            "start_year":      p.start_year,
            "start_month":     p.start_month,
            "start_day":       p.start_day,
            "end_year":        p.end_year,
            "end_month":       p.end_month,
            "end_day":         p.end_day,
            "venue_name":      v.name   if v else None,
            "city":            v.city   if v else p.city,
            "state":           v.state  if v else p.state,
            "country":         v.country if v else p.country,
            "recordings": [
                {
                    "id":              r.id,
                    "source":          r.source,
                    "source_modifier": r.source_modifier,
                    "quality":         r.quality,
                    "rating":          r.rating,
                    "is_complete":     r.is_complete,
                    "track_count":     len(r.tracks),
                }
                for r in p.recordings
            ],
        })
    return jsonify(out)


# ── POST /api/artists/ ─────────────────────────────────────────────────────────

@bp.route("/", methods=["POST"])
@login_required
def create_artist():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    if db.session.query(Artist).filter_by(name=name).first():
        return jsonify({"error": "Artist already exists"}), 409
    a = Artist(name=name, sort_name=data.get("sort_name"), bio=data.get("bio"))
    db.session.add(a)
    db.session.commit()
    return jsonify({"id": a.id, "name": a.name}), 201


# ── PUT /api/artists/<id> ──────────────────────────────────────────────────────

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
