"""
app/utils/peer_access.py — peer AUTHORIZATION (what a peer may see).

Separate from peer_auth.py (which answers "who is this peer?"). Everything a
peer can reach is derived, on every request, from their live CollectionGrants
intersected with the collections a recording belongs to. There is no other
path to access — browse and stream both funnel through the same helpers here,
so they can never disagree.

Reminder (Design Spec v1): a recording visible in ANY granted collection is
accessible, even if it also sits in collections the peer wasn't granted —
sharing a collection shares every recording in it. That's intended.
"""

from app.extensions import db
from app.models.collection import CollectionRecording


def peer_granted_collection_ids(peer):
    """Set of collection IDs this peer currently holds a live grant to."""
    return {g.collection_id for g in peer.grants if g.is_active}


def recording_collection_ids(recording_id):
    """Set of collection IDs a recording belongs to."""
    rows = db.session.query(CollectionRecording.collection_id).filter_by(
        recording_id=recording_id).all()
    return {cid for (cid,) in rows}


def peer_can_access_recording_id(peer, recording_id):
    """True iff the recording sits in at least one collection the peer holds a
    live grant to."""
    if recording_id is None:
        return False
    granted = peer_granted_collection_ids(peer)
    if not granted:
        return False
    return bool(granted & recording_collection_ids(recording_id))


def peer_can_access_recording(peer, recording):
    return recording is not None and peer_can_access_recording_id(peer, recording.id)


def peer_can_access_track(peer, track):
    """Track access = access to its parent recording."""
    return track is not None and peer_can_access_recording_id(peer, track.recording_id)
