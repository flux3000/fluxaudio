"""
tests/test_db_logic.py — serializer, tag builder, and cascade-prune behavior
against the seeded temp DB.

(2026-07-11 remodel: Performer = act; Artist = person; Membership = M2M.)
"""

import pytest

from app.extensions import db as _db
from app.models.recording import Recording
from app.models.performance import Performance
from app.models.performer import Performer
from app.models.artist import Artist, Membership
from app.models.venue import Venue
from app.models.track import Track
from app.models.track_analysis import TrackAnalysis
from app.models.play_log import PlayLog
from app.utils.serialize import recording_summary
from app.utils.ingest import build_recording_tags
from app.utils.pruning import prune_after_recording_delete, prune_performer_if_orphaned


@pytest.fixture()
def api(app):
    """A test client with auth disabled — exercises the JSON CRUD endpoints."""
    app.config["LOGIN_DISABLED"] = True
    return app.test_client()


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
    rec_id       = seeded_ids["recording_id"]
    perf_id      = seeded_ids["performance_id"]
    performer_id = seeded_ids["performer_id"]
    artist_id    = seeded_ids["artist_id"]      # the sole member (person)
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

    assert pruned == {"performances": [perf_id], "performers": [performer_id],
                      "artists": [artist_id]}
    assert _db.session.get(Performance, perf_id) is None
    assert _db.session.get(Performer, performer_id) is None
    assert _db.session.get(Artist, artist_id) is None      # orphaned person removed
    assert TrackAnalysis.query.count() == 0
    assert PlayLog.query.count() == 0


def test_prune_keeps_person_who_is_in_another_act(app, db, seeded_ids):
    performer_id = seeded_ids["performer_id"]
    artist_id    = seeded_ids["artist_id"]

    # Put the same person in a second performer, so they survive the prune.
    other = Performer(name="Bill Evans Trio")
    db.session.add(other); db.session.flush()
    db.session.add(Membership(performer_id=other.id, artist_id=artist_id, order=0))
    db.session.flush()

    # Orphan the original performer (remove its performance) and prune it.
    db.session.query(Performance).filter_by(performer_id=performer_id).delete(
        synchronize_session=False)
    db.session.flush()
    result = prune_performer_if_orphaned(performer_id)
    db.session.commit()

    assert result["performers"] == [performer_id]
    assert result["artists"] == []                          # person kept
    assert _db.session.get(Artist, artist_id) is not None
    assert _db.session.get(Performer, other.id) is not None


# ── CRUD endpoint guards (delete refuses while referenced) ─────────────────────

def test_delete_performer_refuses_with_recordings(api, seeded_ids):
    r = api.delete(f"/api/performers/{seeded_ids['performer_id']}")
    assert r.status_code == 409
    assert "performance" in r.get_json()["error"]
    assert _db.session.get(Performer, seeded_ids["performer_id"]) is not None


def test_delete_venue_refuses_with_performances(api, seeded_ids):
    perf = _db.session.get(Performance, seeded_ids["performance_id"])
    r = api.delete(f"/api/venues/{perf.venue_id}")
    assert r.status_code == 409
    assert _db.session.get(Venue, perf.venue_id) is not None


def test_delete_artist_refuses_while_member(api, seeded_ids):
    r = api.delete(f"/api/artists/{seeded_ids['artist_id']}")
    assert r.status_code == 409
    assert "member" in r.get_json()["error"]


def test_artist_edit_and_delete_when_orphan(api):
    created = api.post("/api/artists/", json={"name": "Sandip Burman"}).get_json()
    aid = created["id"]
    # Edit
    assert api.put(f"/api/artists/{aid}", json={"sort_name": "Burman, Sandip"}).status_code == 200
    assert _db.session.get(Artist, aid).sort_name == "Burman, Sandip"
    # Delete (no memberships) succeeds
    assert api.delete(f"/api/artists/{aid}").status_code == 200
    assert _db.session.get(Artist, aid) is None


def test_performer_and_venue_delete_when_empty(api):
    pid = api.post("/api/performers/", json={"name": "Temp Act"}).get_json()["id"]
    assert api.delete(f"/api/performers/{pid}").status_code == 200
    assert _db.session.get(Performer, pid) is None

    vid = api.post("/api/venues/", json={"name": "Temp Hall"}).get_json()["id"]
    assert api.delete(f"/api/venues/{vid}").status_code == 200
    assert _db.session.get(Venue, vid) is None
