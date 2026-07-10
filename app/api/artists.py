"""
api/artists.py — Canonical-artist endpoints (the sidebar grouping).

These routes operate on CanonicalArtist (the navigation grouping); the linked
performing entities are `Artist`. JSON field names, URL paths, and request
params are kept in their pre-rename form (`performers`, `performer_id`,
`performer_name`, `sub_artists`, `artist_ids`) so the frontend is unaffected —
only the Python data-model layer changed in the 2026-07-09 rename.

Routes:
  GET  /api/artists/                — list canonical artists with recording counts
  GET  /api/artists/all-recordings  — all canonical artists (alpha) with performances
  GET  /api/artists/<id>            — canonical detail + linked artists
  GET  /api/artists/<id>/recordings — performances + recordings for catalog view
  POST /api/artists/                — create canonical artist (archivist+)
  PUT  /api/artists/<id>            — update canonical artist (archivist+)
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required
from sqlalchemy import func

from app.extensions import db
from app.models.artist import Artist, ArtistCanonical
from app.models.canonical_artist import CanonicalArtist
from app.models.performance import Performance
from app.models.recording import Recording
from app.utils.serialize import recording_summary

bp = Blueprint("artists", __name__)


# ── GET /api/artists/search?q= ────────────────────────────────────────────────

@bp.route("/search")
@login_required
def search_artists():
    """
    Return Artist (performing credit) names matching q (case-insensitive
    substring). Powers the artist autocomplete in the ingest / edit forms.
    """
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    rows = (
        db.session.query(Artist.name)
        .filter(Artist.name.ilike(f"%{q}%"))
        .order_by(Artist.name)
        .limit(12)
        .all()
    )
    return jsonify([r[0] for r in rows])


# ── GET /api/artists/ ──────────────────────────────────────────────────────────

@bp.route("/")
@login_required
def list_artists():
    """
    Return all canonical artists sorted by sort_name, with recording counts.

    Also includes `sub_artists` — the distinct linked Artist names (excluding
    the one matching the canonical name) — so the sidebar can show an expand
    affordance without a follow-up request per row.
    """
    rows = (
        db.session.query(CanonicalArtist, func.count(Recording.id).label("rc"))
        .outerjoin(ArtistCanonical, ArtistCanonical.canonical_artist_id == CanonicalArtist.id)
        .outerjoin(Artist,          Artist.id == ArtistCanonical.artist_id)
        .outerjoin(Performance,     Performance.artist_id == Artist.id)
        .outerjoin(Recording,       Recording.performance_id == Performance.id)
        .group_by(CanonicalArtist.id)
        .order_by(func.coalesce(CanonicalArtist.sort_name, CanonicalArtist.name))
        .all()
    )

    # One extra query for linked artist names, grouped by canonical artist id
    link_rows = (
        db.session.query(ArtistCanonical.canonical_artist_id, Artist.name)
        .join(Artist, Artist.id == ArtistCanonical.artist_id)
        .all()
    )
    names_by_canonical = {}
    for canonical_id, artist_name in link_rows:
        names_by_canonical.setdefault(canonical_id, []).append(artist_name)

    return jsonify([
        {
            "id":              c.id,
            "name":            c.name,
            "sort_name":       c.sort_name,
            "recording_count": rc,
            "sub_artists": sorted(
                n for n in set(names_by_canonical.get(c.id, []))
                if n != c.name
            ),
        }
        for c, rc in rows
    ])


# ── GET /api/artists/all-recordings ───────────────────────────────────────────

@bp.route("/all-recordings")
@login_required
def get_all_recordings():
    """
    Return every canonical artist (alphabetical) with their performances
    (oldest first). Powers the default library view. Canonicals with no
    recordings are omitted.
    """
    canonicals = (
        db.session.query(CanonicalArtist)
        .order_by(func.coalesce(CanonicalArtist.sort_name, CanonicalArtist.name))
        .all()
    )

    result = []
    for canonical in canonicals:
        performances = (
            db.session.query(Performance)
            .join(Artist,          Artist.id == Performance.artist_id)
            .join(ArtistCanonical, ArtistCanonical.artist_id == Artist.id)
            .filter(ArtistCanonical.canonical_artist_id == canonical.id)
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
                "performer_name": p.artist.name,
                "title":          p.title,
                "start_year":     p.start_year,
                "start_month":    p.start_month,
                "start_day":      p.start_day,
                "venue_name":     v.name    if v else None,
                "city":           v.city    if v else p.city,
                "state":          v.state   if v else p.state,
                "country":        v.country if v else p.country,
                "recordings":     [recording_summary(r) for r in p.recordings],
            })

        result.append({
            "artist_id":       canonical.id,
            "artist_name":     canonical.name,
            "performance_count": len(perf_list),
            "recording_count": sum(len(p["recordings"]) for p in perf_list),
            "performances":    perf_list,
        })

    return jsonify(result)


# ── GET /api/artists/performers — all performing artists (for picker) ─────────

@bp.route("/performers")
@login_required
def list_performers():
    """Return all performing artists with their linked canonical artist IDs."""
    rows = (
        db.session.query(Artist)
        .order_by(Artist.name)
        .all()
    )
    return jsonify([
        {
            "id":         a.id,
            "name":       a.name,
            "artist_ids": [link.canonical_artist_id for link in a.canonical_links],
        }
        for a in rows
    ])


# ── GET /api/artists/<id> ──────────────────────────────────────────────────────

@bp.route("/<int:artist_id>")
@login_required
def get_artist(artist_id):
    """`artist_id` here is a CanonicalArtist id."""
    c = db.session.get(CanonicalArtist, artist_id)
    if not c:
        return jsonify({"error": "Not found"}), 404

    # Linked performing artists (via the ArtistCanonical junction)
    linked = (
        db.session.query(Artist)
        .join(ArtistCanonical, ArtistCanonical.artist_id == Artist.id)
        .filter(ArtistCanonical.canonical_artist_id == artist_id)
        .order_by(Artist.name)
        .all()
    )

    return jsonify({
        "id":         c.id,
        "name":       c.name,
        "sort_name":  c.sort_name,
        "bio":        c.bio,
        "performers": [{"id": a.id, "name": a.name} for a in linked],
    })


# ── POST /api/artists/<id>/performers — link an artist to a canonical ─────────

@bp.route("/<int:artist_id>/performers", methods=["POST"])
@login_required
def link_performer(artist_id):
    """`artist_id` = canonical id; body `performer_id` = performing-artist id."""
    c = db.session.get(CanonicalArtist, artist_id)
    if not c:
        return jsonify({"error": "Not found"}), 404
    data         = request.get_json()
    performer_id = data.get("performer_id")
    a = db.session.get(Artist, performer_id)
    if not a:
        return jsonify({"error": "Artist not found"}), 404
    # Idempotent — skip if already linked
    exists = db.session.query(ArtistCanonical).filter_by(
        canonical_artist_id=artist_id, artist_id=performer_id
    ).first()
    if not exists:
        db.session.add(ArtistCanonical(canonical_artist_id=artist_id, artist_id=performer_id, order=0))
        db.session.commit()
    return jsonify({"ok": True, "performer": {"id": a.id, "name": a.name}}), 201


# ── DELETE /api/artists/<id>/performers/<performer_id> — unlink ───────────────

@bp.route("/<int:artist_id>/performers/<int:performer_id>", methods=["DELETE"])
@login_required
def unlink_performer(artist_id, performer_id):
    link = db.session.query(ArtistCanonical).filter_by(
        canonical_artist_id=artist_id, artist_id=performer_id
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
    Return all performances for this canonical artist with nested recordings.
    Powers the catalog browser — performances ordered newest first.
    """
    canonical = db.session.get(CanonicalArtist, artist_id)
    if not canonical:
        return jsonify({"error": "Not found"}), 404

    performances = (
        db.session.query(Performance)
        .join(Artist,          Artist.id == Performance.artist_id)
        .join(ArtistCanonical, ArtistCanonical.artist_id == Artist.id)
        .filter(ArtistCanonical.canonical_artist_id == artist_id)
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
            "performer_name":  p.artist.name,
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
            "recordings":      [recording_summary(r) for r in p.recordings],
        })
    return jsonify(out)


# ── POST /api/artists/ ─────────────────────────────────────────────────────────

@bp.route("/", methods=["POST"])
@login_required
def create_artist():
    """Create a canonical artist."""
    data = request.get_json()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    if db.session.query(CanonicalArtist).filter_by(name=name).first():
        return jsonify({"error": "Artist already exists"}), 409
    c = CanonicalArtist(name=name, sort_name=data.get("sort_name"), bio=data.get("bio"))
    db.session.add(c)
    db.session.commit()
    return jsonify({"id": c.id, "name": c.name}), 201


# ── PUT /api/artists/<id> ──────────────────────────────────────────────────────

@bp.route("/<int:artist_id>", methods=["PUT"])
@login_required
def update_artist(artist_id):
    """Update a canonical artist."""
    c = db.session.get(CanonicalArtist, artist_id)
    if not c:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json()
    for f in ["name", "sort_name", "bio"]:
        if f in data:
            setattr(c, f, data[f])
    db.session.commit()
    return jsonify({"id": c.id})
