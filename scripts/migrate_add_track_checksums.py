"""
scripts/migrate_add_track_checksums.py
Add checksum verification columns to `track`:
  checksum_type          TEXT   -- ffp | md5 | st5
  expected_checksum      TEXT   -- hex digest recorded in the fingerprint file
  checksum_status        TEXT   -- match | mismatch | unverified
  checksum_verified_at   TIMESTAMP

Idempotent: skips any column that already exists.
Run once from the repo root:  python3 scripts/migrate_add_track_checksums.py
"""

import os
import sqlite3

DB = os.environ.get(
    "FLUX_DB",
    os.path.join(os.path.dirname(__file__), "..", "db", "fluxaudio.db"),
)

_NEW_COLUMNS = [
    ("checksum_type",        "TEXT"),
    ("expected_checksum",    "TEXT"),
    ("checksum_status",      "TEXT"),
    ("checksum_verified_at", "TIMESTAMP"),
]


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    existing = {r[1] for r in cur.execute("PRAGMA table_info(track)")}
    added = []
    for name, coltype in _NEW_COLUMNS:
        if name in existing:
            continue
        cur.execute(f"ALTER TABLE track ADD COLUMN {name} {coltype}")
        added.append(name)
    con.commit()
    con.close()
    if added:
        print("Added track columns: " + ", ".join(added))
    else:
        print("All checksum columns already present — nothing to do.")


if __name__ == "__main__":
    main()
