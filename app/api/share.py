"""
api/share.py — the peer-facing door (INBOUND sharing).

Everything here is authenticated by a peer Bearer token (@peer_required),
NOT a local login. This blueprint is deliberately READ-ONLY: it exposes
browse + stream over a peer's granted collections and nothing else. There is
no route here that mutates the library — a peer token is structurally
incapable of editing/deleting, because the only endpoints it can authenticate
to are the ones in this file. See "Peer Sharing — Design Spec v1".

Routes (url_prefix /api/share):
  POST /enroll                    invite-code handshake → mints a token (the
                                  only route NOT behind @peer_required; the
                                  invite code itself is the credential)
  GET  /me                        who am I / node + owner identity
  GET  /collections               collections granted to me
  GET  /collections/<id>          recordings in a granted collection
  GET  /recordings/<id>           full recording detail (read-only)
  GET  /stream/<track_id>         transcoded MP3 256k stream (access-checked)

Entity pages (milestone 2, 2026-08-08) — paths mirror the LOCAL API so the
consumer's frontend can reuse its existing render functions:
  GET  /performers/<id>              catalog metadata (bio, dossier, genre…)
  GET  /performers/<id>/recordings   holdings — filtered to the visible set
  GET  /performers/images/<image_id> performer photo, checked via its owner
  GET  /venues/<id>                  venue + its visible shows, visible counts
  GET  /artists/<id>                 person, visible acts + guest appearances
  GET  /genres/                      genres present in the visible set

Every one of those is filtered through peer_visible_recording_ids(). Counts
included — see the rule in the ENTITY PAGES banner below.
"""

from datetime import datetime, timezone
import json as _json

from flask import Blueprint, request, jsonify, g, current_app, abort

from app.extensions import db
from app.models.user import User
from app.models.peer import Peer, PeerInvite, PeerToken, PeerAccessLog
from app.models.collection import Collection
from app.models.recording import Recording
from app.models.track import Track
from app.utils.peer_auth import (
    peer_required, current_peer, hash_secret, generate_token,
)
from app.utils.peer_access import (
    peer_granted_collection_ids, peer_can_access_recording, peer_can_access_track,
    peer_visible_recording_ids, peer_visible_performance_ids,
    peer_visible_performer_ids, peer_can_access_performer, peer_can_access_venue,
    peer_can_access_artist,
)
from app.utils.serialize import recording_row
from app.utils.format import format_partial_date
from app.api.stream import _serve_file
from app.utils import transcode as tx

bp = Blueprint("share", __name__)


def _utcnow():
    return datetime.now(timezone.utc)


def _node_identity():
    """This instance's public identity, shown to peers on enroll/‘me’.
    Config-driven with sensible fallbacks; SHARE_NODE_NAME / SHARE_OWNER_NAME
    become real settings in the server-mode config milestone."""
    node = current_app.config.get("SHARE_NODE_NAME") or "Flux Library"
    owner = current_app.config.get("SHARE_OWNER_NAME")
    if not owner:
        admin = db.session.query(User).filter_by(role="admin", is_active=True).first()
        owner = admin.username if admin else "Unknown"
    return node, owner


# ── POST /api/share/enroll ────────────────────────────────────────────────────
# The one unauthenticated-by-token route. The invite code IS the credential:
# unguessable, single-use, expiring. TODO(server-mode): add rate limiting here
# as defense-in-depth before this is internet-exposed.

@bp.route("/enroll", methods=["POST"])
def enroll():
    data = request.get_json(silent=True) or {}
    code = (data.get("invite_code") or "").strip()
    device_label = (data.get("device_label") or "").strip() or None
    if not code:
        return jsonify({"error": "Missing invite code"}), 400

    invite = db.session.query(PeerInvite).filter_by(code_hash=hash_secret(code)).first()
    if invite is None or not invite.is_valid():
        return jsonify({"error": "Invalid or expired invite"}), 401

    peer = invite.peer
    if peer is None or not peer.is_active:
        return jsonify({"error": "This invite is no longer active"}), 403

    # Consume the invite (single use) and mint a durable token.
    invite.consumed_at = _utcnow()
    raw_token = generate_token()
    token = PeerToken(
        peer_id=peer.id,
        token_hash=hash_secret(raw_token),
        device_label=device_label,
    )
    db.session.add(token)
    db.session.commit()

    node_name, owner_name = _node_identity()
    return jsonify({
        "token":       raw_token,          # shown to the client ONCE — it stores this
        "node_name":   node_name,
        "owner_name":  owner_name,
        "peer_name":   peer.name,
    }), 201


# ── GET /api/share/me ─────────────────────────────────────────────────────────

@bp.route("/me")
@peer_required
def me():
    peer = current_peer()
    node_name, owner_name = _node_identity()
    return jsonify({
        "peer_name":         peer.name,
        "node_name":         node_name,
        "owner_name":        owner_name,
        "collection_count":  len(peer_granted_collection_ids(peer)),
    })


# ── GET /api/share/collections ────────────────────────────────────────────────

@bp.route("/collections")
@peer_required
def list_collections():
    peer = current_peer()
    granted_ids = peer_granted_collection_ids(peer)
    if not granted_ids:
        return jsonify([])
    collections = db.session.query(Collection).filter(Collection.id.in_(granted_ids)).all()
    collections.sort(key=lambda c: (c.name or "").lower())
    return jsonify([
        {
            "id":              c.id,
            "name":            c.name,
            "description":     c.description,
            "recording_count": len(c.recordings),
        }
        for c in collections
    ])


# ── GET /api/share/collections/<id> ───────────────────────────────────────────

@bp.route("/collections/<int:collection_id>")
@peer_required
def collection_detail(collection_id):
    peer = current_peer()
    if collection_id not in peer_granted_collection_ids(peer):
        abort(403)
    collection = db.session.get(Collection, collection_id)
    if collection is None:
        abort(404)
    # card=True (2026-08-08): adds genre, genre_color and image_id, which the
    # handbill Browse cards need. Without it a peer's collection renders as
    # colourless cards with initials where every photo should be.
    return jsonify({
        "id":          collection.id,
        "name":        collection.name,
        "description": collection.description,
        "recordings":  [recording_row(r, card=True) for r in collection.recordings],
    })


# ── GET /api/share/recordings/<id> ────────────────────────────────────────────
# Full metadata (decision #4: peers see everything) — MINUS the internal
# recording-event log, which is this instance's own edit history, not shared
# metadata. Track stream_urls point at the PEER stream endpoint (transcoded),
# never the local FLAC endpoint (which a peer token can't reach anyway).

@bp.route("/recordings/<int:recording_id>")
@peer_required
def recording_detail(recording_id):
    peer = current_peer()
    rec = db.session.get(Recording, recording_id)
    if rec is None:
        abort(404)
    if not peer_can_access_recording(peer, rec):
        abort(403)

    p = rec.performance
    v = p.venue if p else None

    def _analysis(ta):
        if ta is None:
            return None
        return {
            "sample_rate_hz":       ta.sample_rate_hz,
            "bit_depth":            ta.bit_depth,
            "bitrate_kbps":         ta.bitrate_kbps,
            "rms_db":               ta.rms_db,
            "peak_db":              ta.peak_db,
            "noise_floor_db":       ta.noise_floor_db,
            "dynamic_range_db":     ta.dynamic_range_db,
            "spectral_cutoff_hz":   ta.spectral_cutoff_hz,
            "bpm":                  ta.bpm,
            "waveform":             _json.loads(ta.waveform_json) if ta.waveform_json else [],
        }

    return jsonify({
        "id":               rec.id,
        # Show identity (self-contained, so the peer client needs no other call)
        "performer":        p.performer.name if (p and p.performer) else None,
        # Nav ids (2026-08-08). Milestone 1 sent names only, which was right
        # when a peer had nowhere to navigate TO. With entity pages, the
        # frontend builds #/performer/<id> and #/venue/<id> from exactly these
        # two fields — without them the pages exist but are unreachable.
        # Not a leak: both endpoints are access-checked, so an id for something
        # ungranted buys a 403 and nothing else.
        "performer_id":     p.performer_id if p else None,
        "date":             format_partial_date(p.start_year, p.start_month, p.start_day) if p else None,
        "venue":            v.name    if v else None,
        "venue_id":         v.id      if v else None,
        "city":             v.city    if v else (p.city    if p else None),
        "state":            v.state   if v else (p.state   if p else None),
        "country":          v.country if v else (p.country if p else None),
        # Archivist metadata (the whole payload, read-only)
        "source":           rec.source,
        "lineage":          rec.lineage,
        "quality":          rec.quality,
        "rating":           rec.rating,
        "is_complete":      rec.is_complete,
        "is_official":      bool(rec.is_official),
        "info_file_content": rec.info_file_content,
        "notes":            rec.notes,
        "tracks": [
            {
                "id":           t.id,
                "track_number": t.track_number,
                "title":        t.title,
                "set_number":   t.set_number,
                "duration":     t.duration,
                "is_official":  bool(t.is_official),
                "flags":        _json.loads(t.flags) if t.flags else [],
                "songwriter":   t.songwriter,
                "notes":        t.notes,
                "stream_url":   f"/api/share/stream/{t.id}",
                "analysis":     _analysis(t.analysis),
                "checksum": {
                    "type":        t.checksum_type,
                    "status":      t.checksum_status,
                } if t.checksum_type else None,
            }
            for t in rec.tracks
        ],
        "fingerprints": [
            {"type": fp.fingerprint_type, "filename": fp.filename}
            for fp in rec.fingerprints
        ],
    })


# ── GET /api/share/stream/<track_id> ──────────────────────────────────────────
# The one enforcement point that matters: same access check as browse, then a
# transcoded (never raw-FLAC) stream. Logs one access row per play-start.

@bp.route("/stream/<int:track_id>")
@peer_required
def stream(track_id):
    peer = current_peer()
    track = db.session.get(Track, track_id)
    if track is None:
        abort(404)
    if not peer_can_access_track(peer, track):
        abort(403)

    try:
        path = tx.get_or_create_transcode(track)
    except tx.SourceMissing:
        abort(404)
    except tx.FfmpegMissing:
        # Transcoder unavailable — the server box is missing ffmpeg.
        return jsonify({"error": "Transcoder unavailable"}), 503
    except RuntimeError as e:
        current_app.logger.warning("transcode failed for track %s: %s", track_id, e)
        return jsonify({"error": "Transcode failed"}), 500

    # Log one row per play-start: no Range header, or a Range that starts at 0.
    # Seeks (Range starting mid-file) don't re-log, so this counts plays, not
    # every chunk the player requests.
    range_header = request.headers.get("Range", "")
    is_play_start = (not range_header) or "=0-" in range_header.replace(" ", "")
    if is_play_start:
        db.session.add(PeerAccessLog(peer_id=peer.id, track_id=track.id))
        db.session.commit()

    return _serve_file(path, mimetype=tx.mimetype_for())


# ══════════════════════════════════════════════════════════════════════════════
# ENTITY PAGES  (milestone 2, 2026-08-08)
# ══════════════════════════════════════════════════════════════════════════════
#
# Ryan's requirement: a peer should get a HOLISTIC experience — learn about the
# performer, see the venue, see who played that night — not a bare list of
# streamable files. So these mirror the LOCAL endpoints' paths and payload
# shapes exactly, which is what lets the consumer's frontend reuse its existing
# render functions instead of growing a parallel set of peer-only pages.
#
# The distinction that makes this safe (Peer UX Design Spec v1 §2):
#
#   CATALOG METADATA — bio, dossier, photo, genre, lineup, venue name/city.
#       Reference data about the WORLD. Reveals nothing about what is held.
#   HOLDINGS — which recordings exist here. The only thing grants control.
#
# So a peer sees the full Allman Brothers page and only the 3 shows they were
# granted, never all 41.
#
# THE RULE: no query below may reach outside peer_visible_recording_ids(). That
# includes every COUNT — the recording lists are the obvious leak and get got
# right; the subtle one is a count computed over the whole library, which
# publishes the size of a collection that was never shared.

_SHARE_IMG_URL = "/api/share/performers/images"


def _visible_performances(peer, performances):
    """Filter a performance collection to the peer's world, preserving order."""
    visible = peer_visible_performance_ids(peer)
    return [p for p in performances if p.id in visible]


def _visible_recordings(peer, recordings):
    visible = peer_visible_recording_ids(peer)
    return [r for r in recordings if r.id in visible]


# ── GET /api/share/performers/<id> ────────────────────────────────────────────
# Pure catalog metadata. Note there is NOTHING to filter here: the local
# endpoint carries no holdings at all — holdings live in the separate
# /recordings sub-route below. The only change from the local payload is the
# image URL prefix, because a peer cannot reach /api/performers/images/<id>.

@bp.route("/performers/<int:performer_id>")
@peer_required
def performer_detail(performer_id):
    from app.models.performer import Performer
    from app.utils import entity_images as ei
    from app.api.performers import _serialize_roster

    peer = current_peer()
    if not peer_can_access_performer(peer, performer_id):
        abort(403)
    p = db.session.get(Performer, performer_id)
    if p is None:
        abort(404)

    return jsonify({
        "id":        p.id,
        "name":      p.name,
        "sort_name": p.sort_name,
        "bio":       p.bio,
        "default_personnel_mode": p.default_personnel_mode,
        "members":   _serialize_roster(p),
        "resources": [{"id": r.id, "label": r.label, "url": r.url} for r in p.resources],
        "has_image": bool(p.images),
        "images":    [ei.image_payload(i, _SHARE_IMG_URL) for i in p.images],
        "dossier":   _json.loads(p.dossier_json) if p.dossier_json else None,
        "genre":     {"id": p.genre.id, "name": p.genre.name,
                      "color": p.genre.color} if p.genre else None,
        "musicbrainz": {
            "mbid":           p.mbid,
            "status":         p.mb_status,
            "type":           p.mb_type,
            "area":           p.mb_area,
            "begin":          p.mb_begin,
            "end":            p.mb_end,
            "disambiguation": p.mb_disambiguation,
            "links":          _json.loads(p.mb_links_json) if p.mb_links_json else {},
            **(_json.loads(p.mb_extra_json) if p.mb_extra_json else {"related": []}),
            "checked_at":     p.mb_checked_at.isoformat() if p.mb_checked_at else None,
        },
    })


# ── GET /api/share/performers/<id>/recordings ─────────────────────────────────
# Holdings. Filtered twice over: performances the peer can't see are dropped
# entirely, and a visible performance's recordings are themselves filtered —
# two tapers of one night can land in different collections.

@bp.route("/performers/<int:performer_id>/recordings")
@peer_required
def performer_recordings(performer_id):
    from app.models.performance import Performance
    from app.utils.serialize import recording_summary

    peer = current_peer()
    if not peer_can_access_performer(peer, performer_id):
        abort(403)

    visible_perf_ids = peer_visible_performance_ids(peer)
    performances = (
        db.session.query(Performance)
        .filter(Performance.performer_id == performer_id,
                Performance.id.in_(visible_perf_ids))
        .order_by(
            Performance.start_year.desc().nullsfirst(),
            Performance.start_month.desc().nullsfirst(),
            Performance.start_day.desc().nullsfirst(),
        ).all()
    )

    out = []
    for perf in performances:
        v = perf.venue
        recs = _visible_recordings(peer, perf.recordings)
        if not recs:
            continue
        out.append({
            "performance_id": perf.id,
            "performer_name": perf.performer.name if perf.performer else None,
            "title":          perf.title,
            "stage":          perf.stage,
            "start_year":     perf.start_year,
            "start_month":    perf.start_month,
            "start_day":      perf.start_day,
            "end_year":       perf.end_year,
            "end_month":      perf.end_month,
            "end_day":        perf.end_day,
            "venue_name":     v.name    if v else None,
            "city":           v.city    if v else perf.city,
            "state":          v.state   if v else perf.state,
            "country":        v.country if v else perf.country,
            "recordings":     [recording_summary(r) for r in recs],
        })
    return jsonify(out)


# ── GET /api/share/performers/images/<image_id> ───────────────────────────────
# The photo route. Access is checked against the image's OWNING performer, not
# the image id — otherwise a peer could walk image ids and pull the face of
# every act in a library they were never granted.

@bp.route("/performers/images/<int:image_id>")
@peer_required
def performer_image(image_id):
    from app.models.performer_image import PerformerImage
    from app.utils import entity_images as ei
    from app.api.performers import _performer_images_dir

    peer = current_peer()
    img = db.session.get(PerformerImage, image_id)
    if not img:
        abort(404)
    if not peer_can_access_performer(peer, img.performer_id):
        abort(403)
    return ei.handle_serve(img, _performer_images_dir(img.performer))


# ── GET /api/share/venues/<id> ────────────────────────────────────────────────
# The count-leak endpoint. Local `get_venue` returns performance_count and
# recording_count over the venue's ENTIRE history; served unfiltered to a peer
# that publishes exactly how much of that venue Ryan holds. Both counts here
# are computed over the filtered lists.

@bp.route("/venues/<int:venue_id>")
@peer_required
def venue_detail(venue_id):
    from app.models.venue import Venue
    from app.utils import entity_images as ei

    peer = current_peer()
    if not peer_can_access_venue(peer, venue_id):
        abort(403)
    v = db.session.get(Venue, venue_id)
    if v is None:
        abort(404)

    perfs = sorted(
        _visible_performances(peer, v.performances),
        key=lambda p: (p.start_year or 0, p.start_month or 0, p.start_day or 0),
    )
    recordings = [recording_row(r, card=True)
                  for p in perfs for r in _visible_recordings(peer, p.recordings)]

    return jsonify({
        "id":                v.id,
        "name":              v.name,
        "city":              v.city,
        "state":             v.state,
        "country":           v.country,
        "bio":               v.bio,
        # Counts over the VISIBLE set — see the rule at the top of this section.
        "performance_count": len(perfs),
        "recording_count":   len(recordings),
        "recordings":        recordings,
        # Venue photos are not exposed to peers: venue_image has zero rows
        # (2026-08-08), so a peer image route for it would be untested code
        # guarding an empty table. Add it when venues actually have photos.
        "has_image":         False,
        "images":            [],
    })


# ── GET /api/share/artists/<id> ───────────────────────────────────────────────
# The people. `performers` is narrowed to acts the peer can see — an artist
# page listing bands whose shows aren't shared would name acts by the back
# door. Guest appearances are filtered to visible performances.

@bp.route("/artists/<int:artist_id>")
@peer_required
def artist_detail(artist_id):
    from app.models.artist import Artist
    from app.models.performance_personnel import PerformancePersonnel
    from app.utils.serialize import recording_summary

    peer = current_peer()
    if not peer_can_access_artist(peer, artist_id):
        abort(403)
    a = db.session.get(Artist, artist_id)
    if a is None:
        abort(404)

    visible_performer_ids = peer_visible_performer_ids(peer)
    performers = [m.performer for m in a.memberships
                  if m.performer is not None and m.performer.id in visible_performer_ids]
    performers.sort(key=lambda p: (p.sort_name or p.name).lower())
    member_performer_ids = {p.id for p in performers}

    visible_perf_ids = peer_visible_performance_ids(peer)
    guest_appearances = []
    for pp in db.session.query(PerformancePersonnel).filter_by(artist_id=artist_id).all():
        perf = pp.performance
        if not perf or perf.id not in visible_perf_ids:
            continue
        if perf.performer_id in member_performer_ids:
            continue
        recs = _visible_recordings(peer, perf.recordings)
        if not recs:
            continue
        v = perf.venue
        guest_appearances.append({
            "performance_id": perf.id,
            "performer_id":   perf.performer_id,
            "performer_name": perf.performer.name if perf.performer else None,
            "date":       format_partial_date(perf.start_year, perf.start_month, perf.start_day),
            "start_year": perf.start_year, "start_month": perf.start_month,
            "start_day":  perf.start_day,
            "venue_name": v.name    if v else None,
            "city":       v.city    if v else perf.city,
            "state":      v.state   if v else perf.state,
            "country":    v.country if v else perf.country,
            "instrument": pp.instrument,
            "is_guest":   pp.is_guest,
            "note":       pp.note,
            "recordings": [recording_summary(r) for r in recs],
        })
    guest_appearances.sort(
        key=lambda g: (g["start_year"] or 0, g["start_month"] or 0, g["start_day"] or 0))

    return jsonify({
        "id":        a.id,
        "name":      a.name,
        "sort_name": a.sort_name,
        "bio":       a.bio,
        "performers":        [{"id": p.id, "name": p.name} for p in performers],
        "guest_appearances": guest_appearances,
    })


# ── GET /api/share/genres/ ────────────────────────────────────────────────────
# Local list_genres computes performer_count and recording_count with a GROUP BY
# over the whole library. Reproduced here in Python over the visible set only,
# which is cheap at this scale and impossible to accidentally leave unfiltered.
# Genres with nothing visible are omitted entirely rather than shown as zero —
# a zero row still names a genre the peer has no access to.

@bp.route("/genres/")
@peer_required
def list_genres():
    from app.models.genre import Genre
    from app.models.performance import Performance
    from app.models.performer import Performer

    peer = current_peer()
    visible_recs = peer_visible_recording_ids(peer)
    if not visible_recs:
        return jsonify([])

    rows = (db.session.query(Performer.genre_id, Performer.id, Recording.id)
            .join(Performance, Performance.performer_id == Performer.id)
            .join(Recording, Recording.performance_id == Performance.id)
            .filter(Recording.id.in_(visible_recs),
                    Performer.genre_id.isnot(None))
            .all())

    performers_by_genre = {}
    recordings_by_genre = {}
    for genre_id, performer_id, recording_id in rows:
        performers_by_genre.setdefault(genre_id, set()).add(performer_id)
        recordings_by_genre.setdefault(genre_id, set()).add(recording_id)

    if not performers_by_genre:
        return jsonify([])

    genres = (db.session.query(Genre)
              .filter(Genre.id.in_(performers_by_genre.keys()))
              .order_by(Genre.name).all())
    return jsonify([
        {
            "id":              g.id,
            "name":            g.name,
            "description":     g.description,
            "color":           g.color,
            "performer_count": len(performers_by_genre.get(g.id, ())),
            "recording_count": len(recordings_by_genre.get(g.id, ())),
        }
        for g in genres
    ])
