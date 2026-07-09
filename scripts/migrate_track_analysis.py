"""
migrate_track_analysis.py — Create track_analysis table.

Run from ~/Workshop/dev:
    python3 scripts/migrate_track_analysis.py
"""

import sqlite3, os, sys

DB_PATH = os.path.join(os.path.dirname(__file__), "../db/fluxaudio.db")

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS track_analysis (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id             INTEGER NOT NULL UNIQUE REFERENCES track(id) ON DELETE CASCADE,
    rms_db               REAL,
    peak_db              REAL,
    noise_floor_db       REAL,
    dynamic_range_db     REAL,
    clipping_pct         REAL,
    dc_offset            REAL,
    spectral_centroid_hz REAL,
    bpm                  REAL,
    waveform_json        TEXT,
    analysis_version     TEXT NOT NULL DEFAULT '1',
    analyzed_at          DATETIME NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS ix_track_analysis_track_id ON track_analysis(track_id);
"""

def main():
    db_path = os.path.abspath(DB_PATH)
    if not os.path.exists(db_path):
        print(f"DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(CREATE_SQL)
        conn.execute(CREATE_INDEX)
        conn.commit()
        print("track_analysis table ready.")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
