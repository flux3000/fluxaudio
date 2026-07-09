"""
Migration: add official release and track detail columns.

  recording.is_official   BOOLEAN NOT NULL DEFAULT 0
  track.is_official       BOOLEAN NOT NULL DEFAULT 0
  track.flags             TEXT  (JSON array, nullable)  e.g. '["banter","medley"]'
  track.songwriter        VARCHAR(255) (nullable)

track.notes already exists — no change needed.

Run from the project root:
    python3 scripts/migrate_official_release.py
"""
import sqlite3
import os
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "fluxaudio.db")
DB_PATH = os.path.realpath(DB_PATH)

if not os.path.exists(DB_PATH):
    sys.exit(f"DB not found: {DB_PATH}\nRun from the fluxaudio/ project root.")

con = sqlite3.connect(DB_PATH)
cur = con.cursor()


def existing_columns(table):
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


rec_cols   = existing_columns("recording")
track_cols = existing_columns("track")

migrations = []

if "is_official" not in rec_cols:
    migrations.append(
        "ALTER TABLE recording ADD COLUMN is_official BOOLEAN NOT NULL DEFAULT 0"
    )

if "is_official" not in track_cols:
    migrations.append(
        "ALTER TABLE track ADD COLUMN is_official BOOLEAN NOT NULL DEFAULT 0"
    )

if "flags" not in track_cols:
    migrations.append("ALTER TABLE track ADD COLUMN flags TEXT")

if "songwriter" not in track_cols:
    migrations.append("ALTER TABLE track ADD COLUMN songwriter VARCHAR(255)")

if not migrations:
    print("Nothing to do — all columns already present.")
    con.close()
    sys.exit(0)

print(f"Running {len(migrations)} migration(s) on {DB_PATH}:\n")
for sql in migrations:
    print(f"  {sql}")
    cur.execute(sql)

con.commit()
con.close()
print("\nMigration complete.")
