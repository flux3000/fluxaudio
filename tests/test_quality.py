"""
tests/test_quality.py — Listening Quality scoring, staging and promotion.

Pure logic and DB behaviour only: no audio is decoded anywhere in this file.
That is possible because feature extraction and scoring are separate modules —
scoring is a pure function over a feature dict, so it can be tested against
hand-written fixtures.  If a change to the engine ever makes these tests need a
real FLAC, that separation has been broken and THAT is the bug.
"""

import unicodedata

import pytest

from app.extensions import db as _db
from app.models.performance import Performance
from app.models.performer import Performer
from app.models.recording import Recording
from app.utils import quality_store as qs
from app.utils.quality import score_recording, GROUP_WEIGHTS


# ═════════════════════════════════════════════════════════════════════════════
# Fixtures — feature dicts, not audio
# ═════════════════════════════════════════════════════════════════════════════
def _features(**over):
    """
    A clean, mid-range recording.  Values are real measurements taken from the
    Danny Gatton 1988-06-10 Birchmere tape (Ryan grades it an A), so the numbers
    are plausible rather than invented.
    """
    base = {
        "presence_balance_db":   2.79,
        "midrange_scoop_db":    -2.96,
        "spectral_tilt_db_oct": -4.24,
        "hf_energy_ratio_db":  -16.50,
        "mid_snr_db":           20.46,
        "hum_ratio_db":         13.33,
        "crest_factor_db":      18.22,
        "clipping_pct":          0.0,
        "channel_rms_min_db":  -20.07,
        "phase_correlation":     0.51,
        "dropout_count":         0.0,
        "analysis_version":     "1",
        "sampled": [{"track": "Track05.flac", "duration_s": 278.7,
                     "offsets": [91.2, 167.4]}],
    }
    base.update(over)
    return base


# ═════════════════════════════════════════════════════════════════════════════
# Scoring — the pure function
# ═════════════════════════════════════════════════════════════════════════════
def test_score_recording_shape():
    out = score_recording(_features())
    assert set(out) >= {"listening_quality", "score_tone", "score_noise",
                        "score_dynamics", "technical_issues",
                        "technical_deduction", "flags", "score_version"}
    assert 0 <= out["listening_quality"] <= 100


def test_clean_recording_trips_no_technical_issues():
    """Technical Issues are insurance, not discriminators — silent when clean."""
    out = score_recording(_features())
    assert out["technical_issues"] == []
    assert out["technical_deduction"] == 0.0


def test_group_weights_sum_to_one():
    assert sum(GROUP_WEIGHTS.values()) == pytest.approx(1.0)


def test_worst_group_gates_the_score():
    """
    Groups combine GEOMETRICALLY: listening is gated by the worst problem, not
    the average of all of them.  A recording that collapses on one dimension
    must score below the arithmetic mean of its groups, or the geometric
    combination has been silently replaced with an average.
    """
    out = score_recording(_features(crest_factor_db=5.0))   # squashed dynamics
    groups = [out["score_tone"], out["score_noise"], out["score_dynamics"]]
    arithmetic = sum(groups) / 3
    assert out["listening_quality"] < arithmetic


def test_dead_channel_deducts():
    out = score_recording(_features(channel_rms_min_db=-80.0))
    names = [i["issue"] for i in out["technical_issues"]]
    assert "Dead channel" in names
    assert out["technical_deduction"] >= 30.0


def test_missing_feature_does_not_crash():
    """A feature the extractor failed to produce must degrade, not explode."""
    f = _features()
    del f["crest_factor_db"]
    out = score_recording(f)
    assert out["score_dynamics"] is None
    assert out["listening_quality"] is not None


def test_scoring_is_pure_over_features():
    """Same input, same output — no hidden state, no filesystem, no clock."""
    f = _features()
    assert score_recording(f) == score_recording(f)


# ═════════════════════════════════════════════════════════════════════════════
# Staging store — NFC normalisation
# ═════════════════════════════════════════════════════════════════════════════
ACCENTED = "/Volumes/music/Workshop/Paco de Lucía - 1976-02-28"


def test_norm_path_composes_and_strips_slash():
    nfd = unicodedata.normalize("NFD", ACCENTED) + "/"
    out = qs.norm_path(nfd)
    assert out == unicodedata.normalize("NFC", ACCENTED)
    assert not out.endswith("/")


def test_nfd_and_nfc_paths_resolve_to_one_row(app):
    """
    The bug this prevents: macOS hands out decomposed filenames, the DB holds
    composed ones, so "Lucía" != "Lucía" byte-for-byte and every lookup silently
    misses.  This cost an afternoon on the Guitar Trio corpus (2026-07-28).
    """
    nfd = unicodedata.normalize("NFD", ACCENTED)
    nfc = unicodedata.normalize("NFC", ACCENTED)
    assert nfd != nfc                                   # genuinely different bytes

    qs.upsert_staging(nfd, source_dir="/Volumes/music/Workshop",
                      scored=score_recording(_features()), features=_features())

    assert qs.get_staging(nfd) is not None
    assert qs.get_staging(nfc) is not None
    assert qs.get_staging(nfd).id == qs.get_staging(nfc).id

    # And re-analysing via the other form must not create a second row.
    qs.upsert_staging(nfc, scored=score_recording(_features()),
                      features=_features())
    assert len(qs.list_staging("/Volumes/music/Workshop")) == 1


# ═════════════════════════════════════════════════════════════════════════════
# Triage
# ═════════════════════════════════════════════════════════════════════════════
def _stage(path="/src/show-a", source_dir="/src", **over):
    return qs.upsert_staging(path, source_dir=source_dir,
                             scored=score_recording(_features(**over)),
                             features=_features(**over))


def test_new_analysis_starts_pending(app):
    assert _stage().triage_status == qs.TRIAGE_PENDING


def test_triage_accept_and_reject(app):
    _stage()
    assert qs.set_triage("/src/show-a", qs.TRIAGE_ACCEPTED).triage_status == "accepted"
    assert qs.set_triage("/src/show-a", qs.TRIAGE_REJECTED).triage_status == "rejected"


def test_triage_rejects_unknown_status(app):
    _stage()
    with pytest.raises(ValueError):
        qs.set_triage("/src/show-a", "maybe")


def test_triage_on_unknown_folder_returns_none(app):
    assert qs.set_triage("/src/never-analysed", qs.TRIAGE_ACCEPTED) is None


def test_triage_survives_reanalysis(app):
    """
    The user rejected the RECORDING, not the measurement.  Re-analysing must not
    silently reset that judgement back to pending.
    """
    _stage()
    qs.set_triage("/src/show-a", qs.TRIAGE_REJECTED)
    _stage()                                            # re-analyse same folder
    assert qs.get_staging("/src/show-a").triage_status == qs.TRIAGE_REJECTED


def test_list_staging_orders_best_first_errors_last(app):
    _stage("/src/good", crest_factor_db=18.0)
    _stage("/src/poor", crest_factor_db=5.0)
    qs.upsert_staging("/src/broken", source_dir="/src", error="no audio found")

    names = [r.name for r in qs.list_staging("/src")]
    assert names[0] == "good"
    assert names[-1] == "broken"          # errored rows sort last, never hidden


def test_failed_analysis_records_error(app):
    row = qs.upsert_staging("/src/broken", source_dir="/src", error="boom")
    assert row.error == "boom"
    assert row.listening_quality is None


# ═════════════════════════════════════════════════════════════════════════════
# Promotion — staging → permanent
# ═════════════════════════════════════════════════════════════════════════════
@pytest.fixture()
def recording(app):
    p = Performer(name="Test Act")
    _db.session.add(p)
    _db.session.commit()
    perf = Performance(performer_id=p.id)
    _db.session.add(perf)
    _db.session.commit()
    rec = Recording(performance_id=perf.id, folder_path="lib/test", quality="A-")
    _db.session.add(rec)
    _db.session.commit()
    return rec


def test_promote_copies_scores(app, recording):
    staged = _stage()
    perm = qs.promote_to_recording("/src/show-a", recording.id)
    assert perm.listening_quality == staged.listening_quality
    assert perm.score_tone == staged.score_tone
    assert perm.features_json == staged.features_json
    assert perm.recording_id == recording.id


def test_promote_backlinks_staging_row(app, recording):
    _stage()
    qs.promote_to_recording("/src/show-a", recording.id)
    assert qs.get_staging("/src/show-a").recording_id == recording.id


def test_promote_unanalysed_folder_is_a_noop(app, recording):
    """Ingest must never fail because a folder was not analysed."""
    assert qs.promote_to_recording("/src/never-analysed", recording.id) is None


def test_promote_is_idempotent(app, recording):
    _stage()
    qs.promote_to_recording("/src/show-a", recording.id)
    qs.promote_to_recording("/src/show-a", recording.id)
    rows = (_db.session.query(type(qs.get_for_recording(recording.id)))
            .filter_by(recording_id=recording.id).all())
    assert len(rows) == 1


def test_letter_grade_and_automated_score_coexist(app, recording):
    """
    The two measurements are separate by design: the letter grade covers
    performance quality too and stays a human judgement.
    """
    _stage()
    qs.promote_to_recording("/src/show-a", recording.id)
    assert recording.quality == "A-"                      # manual, untouched
    assert recording.quality_score.listening_quality is not None


def test_deleting_recording_cascades_score_but_keeps_triage(app, recording):
    _stage()
    qs.promote_to_recording("/src/show-a", recording.id)
    rec_id = recording.id

    _db.session.delete(recording)
    _db.session.commit()

    assert qs.get_for_recording(rec_id) is None           # CASCADE
    assert qs.get_staging("/src/show-a") is not None      # SET NULL, decision kept


# ═════════════════════════════════════════════════════════════════════════════
# Serialisation
# ═════════════════════════════════════════════════════════════════════════════
def test_serialize_omits_features_unless_asked(app):
    _stage()
    row = qs.get_staging("/src/show-a")
    assert "features" not in qs.serialize(row)
    assert "features" in qs.serialize(row, include_features=True)


def test_serialize_staging_carries_triage_fields(app):
    _stage()
    out = qs.serialize(qs.get_staging("/src/show-a"))
    assert out["triage_status"] == qs.TRIAGE_PENDING
    assert out["folder_path"].endswith("show-a")
    assert out["sampled"]                                 # JSON round-tripped


def test_serialize_none_is_none():
    assert qs.serialize(None) is None
