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

from flask import g, has_request_context

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


# ══════════════════════════════════════════════════════════════════════════════
# THE VISIBLE SET  (milestone 2, 2026-08-08)
# ══════════════════════════════════════════════════════════════════════════════
#
# Everything above answers "may this peer reach recording X?" — enough for
# milestone 1, whose whole job was streaming what was granted.
#
# Milestone 2 gives peers real entity pages (performer, venue, artist, genre,
# search, Browse). Those need the INVERSE question: "what is this peer's entire
# world?" — because a performer is only visible by virtue of a visible
# recording pointing at it, and every count rendered on such a page has to be
# computed over that world and nothing wider.
#
# THE RULE, and it is not negotiable: no share endpoint may query outside the
# set `peer_visible_recording_ids` returns. Not for a list, not for a count, not
# for a "years active" span. The one authorization path stays one path.
#
# Why counts specifically: the recording lists are the obvious leak and will be
# got right. The subtle leak is a genre chip reading "41" when the peer can see
# 3, which publishes the size of a collection Ryan never shared.


def peer_visible_recording_ids(peer):
    """
    Every recording id this peer may see. The root of all derived visibility.

    Memoized per request (`g`) because a single entity page calls this several
    times over — once for the recordings module, again for each aggregate. The
    memo is keyed by peer id so it cannot bleed between peers if a request ever
    resolves two (it doesn't today; the key costs nothing and removes the
    question).
    """
    if peer is None:
        return set()

    cache_key = f"_peer_visible_recs_{peer.id}"
    if has_request_context() and hasattr(g, cache_key):
        return getattr(g, cache_key)

    granted = peer_granted_collection_ids(peer)
    if not granted:
        result = set()
    else:
        rows = (db.session.query(CollectionRecording.recording_id)
                .filter(CollectionRecording.collection_id.in_(granted))
                .distinct().all())
        result = {rid for (rid,) in rows}

    if has_request_context():
        setattr(g, cache_key, result)
    return result


def peer_visible_performance_ids(peer):
    """Performances behind the visible recordings."""
    from app.models.recording import Recording
    visible = peer_visible_recording_ids(peer)
    if not visible:
        return set()
    rows = (db.session.query(Recording.performance_id)
            .filter(Recording.id.in_(visible)).distinct().all())
    return {pid for (pid,) in rows if pid is not None}


def peer_visible_performer_ids(peer):
    """Performers (acts) with at least one visible recording."""
    from app.models.performance import Performance
    perf_ids = peer_visible_performance_ids(peer)
    if not perf_ids:
        return set()
    rows = (db.session.query(Performance.performer_id)
            .filter(Performance.id.in_(perf_ids)).distinct().all())
    return {pid for (pid,) in rows if pid is not None}


def peer_visible_venue_ids(peer):
    """Venues where at least one visible recording was performed.

    Note the null-venue case is simply absent rather than special-cased: a
    performance with no venue contributes no venue id. Placeholder venues were
    eliminated as a concept on 2026-07-15, so there is no sentinel row to skip.
    """
    from app.models.performance import Performance
    perf_ids = peer_visible_performance_ids(peer)
    if not perf_ids:
        return set()
    rows = (db.session.query(Performance.venue_id)
            .filter(Performance.id.in_(perf_ids)).distinct().all())
    return {vid for (vid,) in rows if vid is not None}


def peer_visible_artist_ids(peer):
    """
    Artists (people) reachable from a visible performer via membership.

    Deliberately membership-based rather than per-show-personnel-resolved: a
    peer looking at an act should see its lineup, and narrowing that to only
    the people who played the specific shared nights would make the artist
    pages incoherent (a band with one visible member). Membership is catalog
    metadata about the act, not a holding.
    """
    from app.models.artist import Membership
    performer_ids = peer_visible_performer_ids(peer)
    if not performer_ids:
        return set()
    rows = (db.session.query(Membership.artist_id)
            .filter(Membership.performer_id.in_(performer_ids)).distinct().all())
    return {aid for (aid,) in rows if aid is not None}


def peer_visible_genre_ids(peer):
    """Genres carried by performers with at least one visible recording."""
    from app.models.performer import Performer
    performer_ids = peer_visible_performer_ids(peer)
    if not performer_ids:
        return set()
    rows = (db.session.query(Performer.genre_id)
            .filter(Performer.id.in_(performer_ids)).distinct().all())
    return {gid for (gid,) in rows if gid is not None}


# ── Entity access checks — all derived, none independent ──────────────────────

def peer_can_access_performer(peer, performer_id):
    return performer_id is not None and performer_id in peer_visible_performer_ids(peer)


def peer_can_access_venue(peer, venue_id):
    return venue_id is not None and venue_id in peer_visible_venue_ids(peer)


def peer_can_access_artist(peer, artist_id):
    return artist_id is not None and artist_id in peer_visible_artist_ids(peer)


def peer_can_access_genre(peer, genre_id):
    return genre_id is not None and genre_id in peer_visible_genre_ids(peer)
