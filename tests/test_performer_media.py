"""
tests/test_performer_media.py — Performer profile picture + Dossier
(2026-07-22): migration idempotency, image upload/serve/delete endpoints, and
the shared AI-cost-computation helper both this feature and ingest-side AI
Assist depend on.
"""

import sqlite3
from io import BytesIO

import pytest

from app.models.performer import Performer


@pytest.fixture()
def api(app):
    app.config["LOGIN_DISABLED"] = True
    return app.test_client()


# ── Migration ────────────────────────────────────────────────────────────────

def test_migrate_add_performer_image_dossier_idempotent(tmp_path):
    """Runs the migration script twice against a bare `performer` table (no
    image_ext/dossier_json yet) — first run adds both columns, second run is
    a no-op, neither run errors."""
    from scripts import migrate_add_performer_image_dossier as mod

    db_path = tmp_path / "legacy.db"
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE performer (id INTEGER PRIMARY KEY, name TEXT)")
    con.commit()
    con.close()

    original_db = mod.DB
    try:
        mod.DB = str(db_path)
        mod.main()
        mod.main()   # idempotent — must not raise (e.g. "duplicate column")
    finally:
        mod.DB = original_db

    con = sqlite3.connect(str(db_path))
    cols = [r[1] for r in con.execute("PRAGMA table_info(performer)")]
    con.close()
    assert "image_ext" in cols
    assert "dossier_json" in cols


# ── Profile picture upload/serve/delete ─────────────────────────────────────

def test_upload_get_delete_performer_image(api, app, seeded_ids, tmp_path):
    app.config["LIBRARY_ROOT"] = str(tmp_path)
    pid = seeded_ids["performer_id"]   # "Bill Evans", per conftest._seed()

    # No image yet.
    assert api.get(f"/api/performers/{pid}").get_json()["has_image"] is False
    assert api.get(f"/api/performers/{pid}/image").status_code == 404

    # Upload a jpg.
    r = api.post(f"/api/performers/{pid}/image",
                data={"image": (BytesIO(b"\xff\xd8\xff fake jpeg bytes"), "photo.jpg")},
                content_type="multipart/form-data")
    assert r.status_code == 200
    assert r.get_json()["image_ext"] == ".jpg"

    expected = tmp_path / "Bill Evans" / "_images" / "profile.jpg"
    assert expected.exists()
    assert api.get(f"/api/performers/{pid}").get_json()["has_image"] is True

    r = api.get(f"/api/performers/{pid}/image")
    assert r.status_code == 200
    assert r.mimetype == "image/jpeg"

    # Replace with a different extension — the old file must not linger.
    r = api.post(f"/api/performers/{pid}/image",
                data={"image": (BytesIO(b"fake png bytes"), "new.png")},
                content_type="multipart/form-data")
    assert r.status_code == 200
    assert not expected.exists()
    assert (tmp_path / "Bill Evans" / "_images" / "profile.png").exists()

    # Delete.
    r = api.delete(f"/api/performers/{pid}/image")
    assert r.status_code == 200
    assert not (tmp_path / "Bill Evans" / "_images" / "profile.png").exists()
    assert api.get(f"/api/performers/{pid}").get_json()["has_image"] is False
    assert api.get(f"/api/performers/{pid}/image").status_code == 404


def test_upload_rejects_unsupported_extension(api, app, seeded_ids, tmp_path):
    app.config["LIBRARY_ROOT"] = str(tmp_path)
    pid = seeded_ids["performer_id"]
    r = api.post(f"/api/performers/{pid}/image",
                data={"image": (BytesIO(b"nope"), "notes.txt")},
                content_type="multipart/form-data")
    assert r.status_code == 400
    assert _db_performer(pid).image_ext is None


def _db_performer(pid):
    from app.extensions import db as _db
    return _db.session.get(Performer, pid)


# ── Shared AI-cost helper (ai_assist.py, reused by performer_research.py) ───

def test_compute_cost_matches_pricing_table():
    from types import SimpleNamespace
    from app.utils.ai_assist import _compute_cost

    usage = SimpleNamespace(
        input_tokens=5000, output_tokens=1200,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
        server_tool_use=SimpleNamespace(web_search_requests=3),
    )
    sonnet = _compute_cost(usage, "claude-sonnet-5")
    assert sonnet["cost_cents"] == 5.2   # (5000/1e6*2 + 1200/1e6*10)*100 + 3

    haiku = _compute_cost(usage, "claude-haiku-4-5")
    assert haiku["cost_cents"] == 4.1

    assert _compute_cost(usage, "some-unknown-model") is None
