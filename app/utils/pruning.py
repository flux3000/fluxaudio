"""
utils/pruning.py — Cascade cleanup of empty chain rows.

A recording archive has no use for a performance/artist/canonical-artist that
no longer has any recording. These helpers prune the empty chain after a
recording is deleted or a performance is reassigned. Single source of truth so
the two call sites (recordings.delete_recording, performances.update_performance)
stay consistent.

SQLite FK enforcement is off and the tables carry no ON DELETE actions, so
cascades are done here in app code, bottom-up.

(Terminology: "artist" = performing credit; "canonical artist" = grouping node.
Renamed 2026-07-09 from performer/artist.)
"""

from app.extensions import db
from app.models.performance import Performance
from app.models.artist import Artist, ArtistCanonical
from app.models.canonical_artist import CanonicalArtist
from app.models.user import UserArtistPermission
from app.models.recording import Recording


def _delete_empty_canonicals(canonical_ids):
    """Delete any canonical artist in the list left with 0 linked artists."""
    deleted = []
    for cid in canonical_ids:
        if db.session.query(ArtistCanonical).filter_by(canonical_artist_id=cid).count() == 0:
            db.session.query(UserArtistPermission).filter_by(canonical_artist_id=cid).delete(
                synchronize_session=False)
            canonical = db.session.get(CanonicalArtist, cid)
            if canonical:
                deleted.append(canonical.id)
                db.session.delete(canonical)
    db.session.flush()
    return deleted


def prune_artist_if_orphaned(artist_id):
    """
    If an artist has no performances left, delete it (+ its canonical links),
    then delete any canonical artist left with 0 linked artists.
    Returns {"performers": [...], "artists": [...]}  (JSON keys kept stable).
    """
    result = {"performers": [], "artists": []}
    if db.session.query(Performance).filter_by(artist_id=artist_id).count() > 0:
        return result

    canonical_ids = [
        l.canonical_artist_id
        for l in db.session.query(ArtistCanonical).filter_by(artist_id=artist_id).all()
    ]
    db.session.query(ArtistCanonical).filter_by(artist_id=artist_id).delete(
        synchronize_session=False)
    artist = db.session.get(Artist, artist_id)
    if artist:
        result["performers"].append(artist.id)
        db.session.delete(artist)
    db.session.flush()

    result["artists"] = _delete_empty_canonicals(canonical_ids)
    return result


def prune_after_recording_delete(performance_id):
    """
    After a recording is removed, prune the empty chain above it:
      performance with 0 recordings → artist with 0 performances → empty canonical.
    Returns {"performances": [...], "performers": [...], "artists": [...]}.
    """
    pruned = {"performances": [], "performers": [], "artists": []}

    perf = db.session.get(Performance, performance_id)
    if not perf:
        return pruned
    if db.session.query(Recording).filter_by(performance_id=perf.id).count() > 0:
        return pruned

    artist_id = perf.artist_id
    pruned["performances"].append(perf.id)
    db.session.delete(perf)
    db.session.flush()

    artist_pruned = prune_artist_if_orphaned(artist_id)
    pruned["performers"] = artist_pruned["performers"]
    pruned["artists"]    = artist_pruned["artists"]
    return pruned
