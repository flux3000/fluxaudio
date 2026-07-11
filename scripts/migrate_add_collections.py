"""
scripts/migrate_add_collections.py — add the `collection` + `collection_recording`
tables (optional Recording groupings). Idempotent.

Run once from the repo root:  python3 scripts/migrate_add_collections.py
"""

import os
import sqlite3

DB = os.environ.get(
    "FLUX_DB",
    os.path.join(os.path.dirname(__file__), "..", "db", "fluxaudio.db"),
)


def _has(cur, name):
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    if not _has(cur, "collection"):
        cur.execute("""
            CREATE TABLE collection (
                id INTEGER PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                created_at DATETIME,
                updated_at DATETIME
            )""")
        print("created collection")
    else:
        print("collection already exists")
    if not _has(cur, "collection_recording"):
        cur.execute("""
            CREATE TABLE collection_recording (
                id INTEGER PRIMARY KEY,
                collection_id INTEGER NOT NULL,
                recording_id INTEGER NOT NULL,
                "order" INTEGER NOT NULL DEFAULT 0,
                added_at DATETIME
            )""")
        print("created collection_recording")
    else:
        print("collection_recording already exists")
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
