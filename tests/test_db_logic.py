"""
tests/test_db_logic.py — serializer, tag builder, and cascade-prune behavior
against the seeded temp DB.

(2026-07-11 remodel: Performer = act; Artist = person; Membership = M2M.)
"""

import json as _json
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
from app.utils.ingest import build_recording_tags, scan_folder, compute_audio_rename_map
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
    assert set(s.keys()) == {"id", "source", "quality",
                             "rating", "is_complete", "is_official",
                             "track_count", "duration_sec", "created_at"}


def test_build_recording_tags(app, seeded_ids):
    rec = _db.session.get(Recording, seeded_ids["recording_id"])
    tags, total = build_recording_tags(rec)
    assert tags["ARTIST"] == "Bill Evans"
    assert tags["CONCERTDATE"] == "1980-02-22"
    assert tags["CONCERTVENUE"] == "Sprague Memorial Hall"
    assert tags["CONCERTLOCATION"] == "New Haven, CT, US"
    assert total == "2"


def test_performance_event_association(api, seeded_ids):
    """GET /api/performances/<id> surfaces event_id/event_name, and PUT accepts
    event_id (2026-07-13 addition — needed so the recording page can link a
    show to a Festival/Event the same way Add Recording already does)."""
    perf_id = seeded_ids["performance_id"]

    resp = api.get(f"/api/performances/{perf_id}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["event_id"] is None
    assert body["event_name"] is None

    created = api.post("/api/events/", json={"name": "Bonnaroo 2009"})
    assert created.status_code == 201
    event_id = created.get_json()["id"]

    updated = api.put(f"/api/performances/{perf_id}", json={"event_id": event_id})
    assert updated.status_code == 200

    resp = api.get(f"/api/performances/{perf_id}")
    body = resp.get_json()
    assert body["event_id"] == event_id
    assert body["event_name"] == "Bonnaroo 2009"


def test_recording_ai_research_serialization(api, seeded_ids):
    """GET /api/recordings/<id> surfaces the persisted AI Assist blob (2026-07-13
    revival of ai_research_json — null until a research pass has run, then the
    parsed JSON of the latest run)."""
    rec_id = seeded_ids["recording_id"]

    resp = api.get(f"/api/recordings/{rec_id}")
    assert resp.status_code == 200
    assert resp.get_json()["ai_research"] is None

    rec = _db.session.get(Recording, rec_id)
    rec.ai_research_json = '{"thinking": "test", "proposals": []}'
    _db.session.commit()

    resp = api.get(f"/api/recordings/{rec_id}")
    assert resp.get_json()["ai_research"] == {"thinking": "test", "proposals": []}


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


def test_do_confirm_copies_files_and_reports_progress(app, db, tmp_path):
    """The background ingest worker copies the folder, creates the chain, and
    reports copy progress ending at 100%. Audio lands flattened + renamed
    ("NN - Title.ext") per the 2026-07-14 always-flatten-and-rename policy;
    non-audio extras (cover.jpg) keep their original name."""
    from app.api.ingest import _do_confirm
    from app.models.user import User

    src = tmp_path / "src_show"; src.mkdir()
    (src / "t01.flac").write_bytes(b"x" * 2000)
    (src / "cover.jpg").write_bytes(b"y" * 1000)   # non-audio extra, copied too
    lib = tmp_path / "lib"; lib.mkdir()
    app.config["LIBRARY_ROOT"] = str(lib)
    uid = db.session.query(User).first().id

    progress = []
    data = {
        "source_folder_path": str(src),
        "artist_name": "Progress Test Act",
        "start_year": 2020, "start_month": 1, "start_day": 2,
        "source": "AUD",
        "tracks": [{"track_number": 1, "title": "One", "duration": 100, "filename": "t01.flac"}],
    }
    result = _do_confirm(data, uid, lambda c, t: progress.append((c, t)))

    assert result["recording_id"]
    rec = _db.session.get(Recording, result["recording_id"])
    track = rec.tracks[0]
    assert track.file_path == "01 - One.flac"
    assert (lib / rec.folder_path / "01 - One.flac").exists()
    assert (lib / rec.folder_path / "cover.jpg").exists()
    assert progress and progress[-1][0] == progress[-1][1]   # finished at 100%


def test_do_confirm_persists_pre_save_ai_result(app, db, tmp_path):
    """A recording ingested after running AI Assist pre-confirm (Add
    Recording's own button, before the row exists) should land with
    ai_research_json already populated — it was previously dropped on the
    floor because the confirm payload never carried it (Ryan, 2026-07-14:
    'the result was not saved with the database submission')."""
    from app.api.ingest import _do_confirm
    from app.models.user import User

    src = tmp_path / "src_show_ai"; src.mkdir()
    (src / "t01.flac").write_bytes(b"x" * 2000)
    lib = tmp_path / "lib_ai"; lib.mkdir()
    app.config["LIBRARY_ROOT"] = str(lib)
    uid = db.session.query(User).first().id

    ai_result = {
        "thinking": "Date correction is high-confidence...",
        "proposals": [{"field": "date", "proposed": "1974-07-03",
                        "confidence": "high", "source": "web"}],
        "track_titles": [], "verify_items": [], "provenance_notes": [], "sources": [],
    }
    data = {
        "source_folder_path": str(src),
        "artist_name": "Pre-Save AI Act",
        "start_year": 1974, "start_month": 8, "start_day": 13,
        "source": "FM",
        "tracks": [{"track_number": 1, "title": "One", "duration": 100, "filename": "t01.flac"}],
        "ai_result": ai_result,
    }
    result = _do_confirm(data, uid, None)
    rec = _db.session.get(Recording, result["recording_id"])
    assert rec.ai_research_json is not None
    saved = _json.loads(rec.ai_research_json)
    assert saved["proposals"][0]["proposed"] == "1974-07-03"


def test_do_confirm_without_ai_result_leaves_ai_research_json_null(app, db, tmp_path):
    """No AI Assist run pre-confirm — should stay null, not error or default
    to some empty-but-truthy blob."""
    from app.api.ingest import _do_confirm
    from app.models.user import User

    src = tmp_path / "src_show_no_ai"; src.mkdir()
    (src / "t01.flac").write_bytes(b"x" * 2000)
    lib = tmp_path / "lib_no_ai"; lib.mkdir()
    app.config["LIBRARY_ROOT"] = str(lib)
    uid = db.session.query(User).first().id

    data = {
        "source_folder_path": str(src),
        "artist_name": "No AI Act",
        "start_year": 1980, "start_month": 1, "start_day": 1,
        "source": "AUD",
        "tracks": [{"track_number": 1, "title": "One", "duration": 100, "filename": "t01.flac"}],
    }
    result = _do_confirm(data, uid, None)
    rec = _db.session.get(Recording, result["recording_id"])
    assert rec.ai_research_json is None


def test_check_existing_no_performer_match(api):
    """Artist name that doesn't exist yet — nothing to warn about."""
    resp = api.get("/api/ingest/check-existing?artist_name=Nobody+At+All&year=1999")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["performer_found"] is False
    assert body["performances"] == []


def test_check_existing_finds_recording_for_performer_and_date(app, db, api, tmp_path):
    """The core case: a recording already exists for this performer+date —
    Add Recording's duplicate-warning check should surface it (non-blocking,
    per Ryan's 2026-07-14 call — this only warns, never blocks Confirm)."""
    from app.api.ingest import _do_confirm
    from app.models.user import User

    src = tmp_path / "src_dup1"; src.mkdir()
    (src / "t01.flac").write_bytes(b"x" * 2000)
    lib = tmp_path / "lib_dup1"; lib.mkdir()
    app.config["LIBRARY_ROOT"] = str(lib)
    uid = db.session.query(User).first().id

    data = {
        "source_folder_path": str(src),
        "artist_name": "Duplicate Check Trio",
        "start_year": 1974, "start_month": 7, "start_day": 3,
        "source": "FM", "quality": "A",
        "tracks": [{"track_number": 1, "title": "One", "duration": 100, "filename": "t01.flac"}],
    }
    _do_confirm(data, uid, None)

    resp = api.get(
        "/api/ingest/check-existing"
        "?artist_name=Duplicate+Check+Trio&year=1974&month=7&day=3"
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["performer_found"] is True
    assert len(body["performances"]) == 1
    recs = body["performances"][0]["recordings"]
    assert len(recs) == 1
    assert recs[0]["source"] == "FM"
    assert recs[0]["quality"] == "A"
    assert recs[0]["track_count"] == 1

    # A different day for the same performer/year should NOT match — the
    # whole point is to narrow to the same show, not just the same act/year.
    resp2 = api.get(
        "/api/ingest/check-existing"
        "?artist_name=Duplicate+Check+Trio&year=1974&month=7&day=4"
    )
    assert resp2.get_json()["performances"] == []


def test_check_existing_excludes_performance_with_no_recordings(db, api):
    """A bare Performance row with zero Recordings isn't a duplicate risk —
    nothing to accidentally re-ingest yet."""
    from app.utils.performers import resolve_or_create_performer
    from app.models.performance import Performance

    performer = resolve_or_create_performer("Empty Performance Act")
    perf = Performance(performer_id=performer.id, start_year=2001, start_month=5, start_day=5)
    _db.session.add(perf)
    _db.session.commit()

    resp = api.get("/api/ingest/check-existing?artist_name=Empty+Performance+Act&year=2001")
    body = resp.get_json()
    assert body["performer_found"] is True
    assert body["performances"] == []


def test_do_confirm_verifies_checksums_end_to_end(app, db, tmp_path):
    """A .md5 fingerprint file sitting alongside the audio gets archived,
    parsed, matched to the track by filename, and auto-verified as part of
    confirm — 2026-07-13 checksum feature."""
    import hashlib
    from app.api.ingest import _do_confirm
    from app.models.user import User
    from app.models.track import Track

    src = tmp_path / "src_show2"; src.mkdir()
    audio_bytes = b"fake flac bytes for whole-file md5 test" * 10
    (src / "t01.flac").write_bytes(audio_bytes)
    real_md5 = hashlib.md5(audio_bytes).hexdigest()
    (src / "checksum.md5").write_text(f"{real_md5} *t01.flac\n")
    lib = tmp_path / "lib2"; lib.mkdir()
    app.config["LIBRARY_ROOT"] = str(lib)
    uid = db.session.query(User).first().id

    data = {
        "source_folder_path": str(src),
        "artist_name": "Checksum Test Act",
        "start_year": 2021, "start_month": 3, "start_day": 4,
        "source": "SBD",
        "tracks": [{"track_number": 1, "title": "One", "duration": 100, "filename": "t01.flac"}],
        "fingerprints": [{"type": "md5", "filename": "checksum.md5",
                          "rel_path": "checksum.md5", "path": str(src / "checksum.md5")}],
    }
    result = _do_confirm(data, uid, None)
    rec = _db.session.get(Recording, result["recording_id"])
    track = rec.tracks[0]

    assert rec.fingerprints[0].content == f"{real_md5} *t01.flac\n"
    assert track.checksum_type == "md5"
    assert track.expected_checksum == real_md5
    assert track.checksum_status == "match"
    assert track.checksum_verified_at is not None


def test_do_confirm_flags_checksum_mismatch(app, db, tmp_path):
    from app.api.ingest import _do_confirm
    from app.models.user import User

    src = tmp_path / "src_show3"; src.mkdir()
    (src / "t01.flac").write_bytes(b"some audio bytes")
    (src / "checksum.md5").write_text("0" * 32 + " *t01.flac\n")  # deliberately wrong
    lib = tmp_path / "lib3"; lib.mkdir()
    app.config["LIBRARY_ROOT"] = str(lib)
    uid = db.session.query(User).first().id

    data = {
        "source_folder_path": str(src),
        "artist_name": "Checksum Mismatch Act",
        "start_year": 2021, "start_month": 3, "start_day": 4,
        "source": "SBD",
        "tracks": [{"track_number": 1, "title": "One", "duration": 100, "filename": "t01.flac"}],
        "fingerprints": [{"type": "md5", "filename": "checksum.md5",
                          "rel_path": "checksum.md5", "path": str(src / "checksum.md5")}],
    }
    result = _do_confirm(data, uid, None)
    rec = _db.session.get(Recording, result["recording_id"])
    assert rec.tracks[0].checksum_status == "mismatch"


def test_verify_checksums_discovers_and_verifies_backfill(app, db, api, tmp_path):
    """A fingerprint file that wasn't sent at confirm time gets discovered
    from the library folder, parsed, and verified by the endpoint alone —
    covers Ryan's 'go back and re-process' ask, 2026-07-13.

    The checksum file is written against the track's post-ingest filename
    (its stored file_path) rather than the pre-ingest one — a real backfill
    checksum file is generated by re-running shntool/flac against what's
    actually sitting in the library folder today, and since 2026-07-14 that's
    always the flattened+renamed name (see compute_audio_rename_map), not
    whatever the original source folder called it."""
    import hashlib
    from app.api.ingest import _do_confirm
    from app.models.user import User

    src = tmp_path / "src_show4"; src.mkdir()
    audio_bytes = b"more fake flac bytes" * 10
    (src / "t01.flac").write_bytes(audio_bytes)
    lib = tmp_path / "lib4"; lib.mkdir()
    app.config["LIBRARY_ROOT"] = str(lib)
    uid = db.session.query(User).first().id

    data = {
        "source_folder_path": str(src),
        "artist_name": "Backfill Test Act",
        "start_year": 2022, "start_month": 5, "start_day": 6,
        "source": "AUD",
        "tracks": [{"track_number": 1, "title": "One", "duration": 100, "filename": "t01.flac"}],
        # deliberately no "fingerprints" — simulates a checksum file the
        # archivist forgot to include (or generated afterward) at confirm time
    }
    result = _do_confirm(data, uid, None)
    rec = _db.session.get(Recording, result["recording_id"])
    assert rec.tracks[0].checksum_status is None   # nothing to verify yet
    assert rec.tracks[0].file_path == "01 - One.flac"   # flattened + renamed on ingest

    # Drop a checksum file straight into the library folder, generated
    # against what's actually there now (the renamed filename).
    real_md5 = hashlib.md5(audio_bytes).hexdigest()
    (lib / rec.folder_path / "checksum.md5").write_text(f"{real_md5} *01 - One.flac\n")

    resp = api.post(f"/api/recordings/{rec.id}/verify-checksums")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["checked"] == 1
    assert body["tracks"][0]["checksum_status"] == "match"

    # Re-fetch from DB to confirm it actually persisted, not just the response.
    _db.session.refresh(rec.tracks[0])
    assert rec.tracks[0].checksum_type == "md5"


def test_save_info_file_overwrites_existing(api, tmp_path):
    """Editing the info file on the Add Recording form and saving it should
    write straight back to the real file scan_folder() found — independent
    of Confirm, per Ryan's 2026-07-13 ask (so a corrected file can drive a
    fresh AI Assist run before the show is ever added)."""
    src = tmp_path / "src_show5"; src.mkdir()
    (src / "info.txt").write_text("Original content\n")

    resp = api.post("/api/ingest/save-info-file", json={
        "folder_path": str(src), "filename": "info.txt", "content": "Corrected content\n",
    })
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert (src / "info.txt").read_text() == "Corrected content\n"


def test_save_info_file_creates_new_when_none_exists(api, tmp_path):
    """Folder had no info file — the archivist typed one in from scratch."""
    src = tmp_path / "src_show6"; src.mkdir()

    resp = api.post("/api/ingest/save-info-file", json={
        "folder_path": str(src), "filename": "info.txt", "content": "Typed from scratch\n",
    })
    assert resp.status_code == 200
    assert (src / "info.txt").read_text() == "Typed from scratch\n"


def test_save_info_file_rejects_path_traversal(api, tmp_path):
    src = tmp_path / "src_show7"; src.mkdir()
    outside = tmp_path / "escaped.txt"

    resp = api.post("/api/ingest/save-info-file", json={
        "folder_path": str(src), "filename": "../escaped.txt", "content": "nope",
    })
    assert resp.status_code == 400
    assert not outside.exists()


def test_performer_and_venue_delete_when_empty(api):
    pid = api.post("/api/performers/", json={"name": "Temp Act"}).get_json()["id"]
    assert api.delete(f"/api/performers/{pid}").status_code == 200
    assert _db.session.get(Performer, pid) is None

    vid = api.post("/api/venues/", json={"name": "Temp Hall"}).get_json()["id"]
    assert api.delete(f"/api/venues/{vid}").status_code == 200
    assert _db.session.get(Venue, vid) is None


# ── Multi-disc flatten + rename (2026-07-14) ────────────────────────────────
# Ryan's report: a CD1/CD2 source ingested with duplicate/interleaved track
# numbers (01,01,02,02,03... then 06-10 unduplicated) because each disc's
# FLAC files independently reset TRACKNUMBER. Root cause: multi-set/disc
# detection existed in scan_folder() but was fully dead code. Fix: wire up
# detection (below) + always flatten+rename audio into the library folder
# root on ingest (compute_audio_rename_map / move_to_library) so DB
# track_number is driven by the scan's own continuous index, never a
# per-disc tag that can collide.

def test_scan_folder_detects_and_orders_multi_disc(tmp_path):
    """CD1 (3 tracks) + CD2 (2 tracks), plus a non-set Art/ dir and a loose
    info file — sets_detected True, audio_files in continuous CD1-then-CD2
    order with correct 'set' labels and rel_path subdir prefixes intact
    (rel_path is only used to locate the ORIGINAL file pre-flatten)."""
    root = tmp_path / "multidisc_src"; root.mkdir()
    cd1 = root / "CD1"; cd1.mkdir()
    cd2 = root / "CD2"; cd2.mkdir()
    art = root / "Art"; art.mkdir()
    for n in (1, 2, 3):
        (cd1 / f"{n:02d}.flac").write_bytes(b"x")
    for n in (1, 2):
        (cd2 / f"{n:02d}.flac").write_bytes(b"x")
    (art / "cover.jpg").write_bytes(b"y")
    (root / "info.txt").write_text("Some show notes\n")

    result = scan_folder(str(root))

    assert result["sets_detected"] is True
    assert len(result["audio_files"]) == 5
    indices = [f["index"] for f in result["audio_files"]]
    assert indices == [1, 2, 3, 4, 5]                     # continuous, no reset
    assert [f["set"] for f in result["audio_files"]] == ["CD 1", "CD 1", "CD 1", "CD 2", "CD 2"]
    assert result["audio_files"][0]["rel_path"] in ("CD1/01.flac", "CD1\\01.flac")
    assert result["audio_files"][3]["rel_path"] in ("CD2/01.flac", "CD2\\01.flac")
    assert [o["filename"] for o in result["other_files"]] == ["cover.jpg"]
    assert [t["filename"] for t in result["text_files"]] == ["info.txt"]


def test_scan_folder_single_disc_subdir_not_flagged_as_set(tmp_path):
    """A single 'CD1' folder alone isn't multi-anything (nothing to flatten
    against) — sets_detected should stay False so a normal single-disc show
    with one 'flac/'-style subdir isn't needlessly treated as a multi-set."""
    root = tmp_path / "single_disc_src"; root.mkdir()
    cd1 = root / "CD1"; cd1.mkdir()
    (cd1 / "01.flac").write_bytes(b"x")

    result = scan_folder(str(root))
    assert result["sets_detected"] is False


def test_compute_audio_rename_map_pads_and_dedupes():
    """Zero-padding scales to the highest track number (2-digit minimum),
    and a naming collision (two tracks that would produce the same flat
    filename) gets a distinguishing suffix rather than one silently
    overwriting the other on disk."""
    tracks = [
        {"track_number": 1,  "title": "Improv",           "filename": "CD1/01.flac"},
        {"track_number": 2,  "title": "Improv",           "filename": "CD1/02.flac"},  # collides with #1
        {"track_number": 11, "title": "Set Break Jam",    "filename": "CD2/03.flac"},
    ]
    m = compute_audio_rename_map(tracks)

    assert m["CD1/01.flac"] == "01 - Improv.flac"
    assert m["CD1/02.flac"] == "02 - Improv.flac"     # track number, not title, disambiguates
    assert m["CD2/03.flac"] == "11 - Set Break Jam.flac"
    assert len(set(m.values())) == 3                  # no two tracks share a filename

    # True same-number collision (defensive case — shouldn't happen from a
    # real scan, but the map must never drop a track silently).
    dup_num = [
        {"track_number": 1, "title": "Jam", "filename": "a.flac"},
        {"track_number": 1, "title": "Jam", "filename": "b.flac"},
    ]
    m2 = compute_audio_rename_map(dup_num)
    assert m2["a.flac"] == "01 - Jam.flac"
    assert m2["b.flac"] == "01 - Jam (2).flac"


def test_compute_audio_rename_map_sanitizes_illegal_characters():
    tracks = [{"track_number": 1, "title": 'Dark Star -> Truckin\' / "Live"',
               "filename": "01.flac"}]
    m = compute_audio_rename_map(tracks)
    name = m["01.flac"]
    assert "/" not in name and ":" not in name and '"' not in name
    assert name.startswith("01 - ")


def test_do_confirm_flattens_and_renames_multi_disc_with_checksums(app, db, tmp_path):
    """End-to-end: a CD1/CD2 source, each disc with its OWN checksum file
    listing bare local filenames ("01.flac", "02.flac", ...) as most
    real-world per-disc checksum files do. Confirms audio flattens to the
    recording folder root with continuous cross-disc numbering, non-audio
    content (the checksum files themselves) keeps its original nested
    location, and checksums still correctly match + verify despite the
    rename — proving both the confirm-time proxy match
    (app.api.ingest._ChecksumMatchProxy) AND the per-fingerprint-file
    directory scoping (needed because "01.flac" is not unique across discs
    once matching falls back to original names) work end to end."""
    import hashlib
    from app.api.ingest import _do_confirm
    from app.models.user import User

    src = tmp_path / "src_multidisc"; src.mkdir()
    cd1 = src / "CD1"; cd1.mkdir()
    cd2 = src / "CD2"; cd2.mkdir()

    b1 = b"disc one track one audio bytes" * 5
    b2 = b"disc one track two audio bytes" * 5
    b3 = b"disc two track one audio bytes" * 5     # same local name "01.flac" as b1
    (cd1 / "01.flac").write_bytes(b1)
    (cd1 / "02.flac").write_bytes(b2)
    (cd2 / "01.flac").write_bytes(b3)

    # Each disc ships its OWN checksum file, scoped to its own bare
    # filenames — CD1's "01.flac" and CD2's "01.flac" are unrelated files
    # with unrelated hashes, exactly the ambiguity flattening creates if
    # matching isn't scoped per fingerprint file's original directory.
    md5_1, md5_2, md5_3 = (hashlib.md5(b).hexdigest() for b in (b1, b2, b3))
    (cd1 / "checksum.md5").write_text(f"{md5_1} *01.flac\n{md5_2} *02.flac\n")
    (cd2 / "checksum.md5").write_text(f"{md5_3} *01.flac\n")

    lib = tmp_path / "lib_multidisc"; lib.mkdir()
    app.config["LIBRARY_ROOT"] = str(lib)
    uid = db.session.query(User).first().id

    scan = scan_folder(str(src))
    assert scan["sets_detected"] is True
    assert len(scan["fingerprints"]) == 2   # both discs' checksum files found

    tracks_payload = [
        {"track_number": i + 1, "title": f"Track {i + 1}",
         "duration": 100, "filename": af["rel_path"], "set": af["set"]}
        for i, af in enumerate(scan["audio_files"])
    ]
    data = {
        "source_folder_path": str(src),
        "artist_name": "Multi Disc Test Trio",
        "start_year": 1979, "start_month": 12, "start_day": 12,
        "source": "SBD",
        "tracks": tracks_payload,
        "fingerprints": [
            {"type": fp["type"], "filename": fp["filename"], "rel_path": fp["rel_path"]}
            for fp in scan["fingerprints"]
        ],
    }
    result = _do_confirm(data, uid, None)
    rec = _db.session.get(Recording, result["recording_id"])
    tracks = sorted(rec.tracks, key=lambda t: t.track_number)

    # Continuous numbering across discs, no reset/duplication.
    assert [t.track_number for t in tracks] == [1, 2, 3]
    # Flattened: no subdir prefix in the stored path, and files actually
    # live at the folder root on disk — even though the checksum files that
    # shipped alongside them (non-audio) keep their original CD1/CD2 nesting.
    assert [t.file_path for t in tracks] == [
        "01 - Track 1.flac", "02 - Track 2.flac", "03 - Track 3.flac",
    ]
    for t in tracks:
        assert (lib / rec.folder_path / t.file_path).exists()
    assert not (lib / rec.folder_path / "CD1" / "01.flac").exists()   # audio moved out
    assert (lib / rec.folder_path / "CD1" / "checksum.md5").exists()  # non-audio stayed put
    assert (lib / rec.folder_path / "CD2" / "checksum.md5").exists()

    # Checksums matched against the ORIGINAL filenames each disc's own
    # fingerprint file listed, scoped so CD2's "01.flac" never gets matched
    # to CD1's track of the same original name, and verified against the
    # real (renamed) audio bytes.
    assert [t.checksum_status for t in tracks] == ["match", "match", "match"]
    assert tracks[0].expected_checksum == md5_1   # CD1/01.flac → Track 1
    assert tracks[1].expected_checksum == md5_2   # CD1/02.flac → Track 2
    assert tracks[2].expected_checksum == md5_3   # CD2/01.flac → Track 3 (not CD1's md5_1)
    assert tracks[2].expected_checksum == md5_3
