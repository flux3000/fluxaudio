"""
tests/test_db_logic.py — serializer, tag builder, and cascade-prune behavior
against the seeded temp DB.

Terminology (post 2026-07-09 rename): Artist = performing credit;
CanonicalArtist = grouping node; ArtistCanonical = junction.
"""

from app.extensions import db as _db
from app.models.recording import Recording
from app.models.performance import Performance
from app.models.artist import Artist, ArtistCanonical
from app.models.canonical_artist import CanonicalArtist
from app.models.track import Track
from app.models.track_analysis import TrackAnalysis
from app.models.play_log import PlayLog
from app.utils.serialize import recording_summary
from app.utils.ingest import build_recording_tags
from app.utils.pruning import prune_after_recording_delete, prune_artist_if_orphaned


def test_recording_summary_shape(app, seeded_ids):
    rec = _db.session.get(Recording, seeded_ids["recording_id"])
    s = recording_summary(rec)
    assert s["source"] == "AUD"
    assert s["quality"] == "B+"
    assert s["track_count"] == 2
    assert s["duration_sec"] == 360        # 300 + 60
    assert set(s.keys()) == {"id", "source", "source_modifier", "quality",
                             "rating", "is_complete", "is_official",
                             "track_count", "duration_sec"}


def test_build_recording_tags(app, seeded_ids):
    rec = _db.session.get(Recording, seeded_ids["recording_id"])
    tags, total = build_recording_tags(rec)
    assert tags["ARTIST"] == "Bill Evans"
    assert tags["CONCERTDATE"] == "1980-02-22"
    assert tags["CONCERTVENUE"] == "Sprague Memorial Hall"
    assert tags["CONCERTLOCATION"] == "New Haven, CT, US"
    assert total == "2"


def test_prune_after_delete_removes_full_chain(app, db, seeded_ids):
    rec_id = seeded_ids["recording_id"]
    perf_id = seeded_ids["performance_id"]
    artist_id = seeded_ids["performer_id"]      # performing Artist id
    canonical_id = seeded_ids["canonical_id"]
    track_ids = [t.id for t in Track.query.filter_by(recording_id=rec_id).all()]

    # Simulate delete_recording's child cleanup, then prune.
    db.session.query(TrackAnalysis).filter(TrackAnalysis.track_id.in_(track_ids)).delete(
        synchronize_session=False)
    db.session.query(PlayLog).filter(PlayLog.track_id.in_(track_ids)).delete(
        synchronize_session=False)
    db.session.query(Track).filter(Track.id.in_(track_ids)).delete(synchronize_session=False)
    db.session.query(Recording).filter_by(id=rec_id).delete(synchronize_session=False)
    db.session.flush()

    pruned = prune_after_recording_delete(perf_id)
    db.session.commit()

    assert pruned == {"performances": [perf_id], "performers": [artist_id],
                      "artists": [canonical_id]}
    # Everything is gone, including the dependent analysis/play_log rows.
    assert _db.session.get(Performance, perf_id) is None
    assert _db.session.get(Artist, artist_id) is None
    assert _db.session.get(CanonicalArtist, canonical_id) is None
    assert TrackAnalysis.query.count() == 0
    assert PlayLog.query.count() == 0


def test_prune_keeps_canonical_with_other_artists(app, db, seeded_ids):
    canonical_id = seeded_ids["canonical_id"]
    artist_id = seeded_ids["performer_id"]

    # Add a second performing artist under the same canonical so it survives.
    a2 = Artist(name="Bill Evans Trio")
    db.session.add(a2); db.session.flush()
    db.session.add(ArtistCanonical(artist_id=a2.id, canonical_artist_id=canonical_id, order=0))
    db.session.flush()

    # Orphan the original artist (remove its performance) and prune it.
    db.session.query(Performance).filter_by(artist_id=artist_id).delete(
        synchronize_session=False)
    db.session.flush()
    result = prune_artist_if_orphaned(artist_id)
    db.session.commit()

    assert result["performers"] == [artist_id]
    assert result["artists"] == []                 # canonical kept
    assert _db.session.get(CanonicalArtist, canonical_id) is not None
