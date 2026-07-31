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
    return jsonify({
        "id":          collection.id,
        "name":        collection.name,
        "description": collection.description,
        "recordings":  [recording_row(r) for r in collection.recordings],
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
        "date":             format_partial_date(p.start_year, p.start_month, p.start_day) if p else None,
        "venue":            v.name    if v else None,
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
