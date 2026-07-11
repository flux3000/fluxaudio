"""
scripts/migrate_drop_ai_research_json.py
Drop recording.ai_research_json — AI research is now session-only, never persisted.

Idempotent: skips if the column is already gone. Requires SQLite >= 3.35
(DROP COLUMN support). Run once from the repo root:
    python3 scripts/migrate_drop_ai_research_json.py
"""

import os
import sqlite3

DB = os.environ.get(
    "FLUX_DB",
    os.path.join(os.path.dirname(__file__), "..", "db", "fluxaudio.db"),
)


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(recording)")]
    if "ai_research_json" not in cols:
        print("ai_research_json already gone — nothing to do.")
    else:
        cur.execute("ALTER TABLE recording DROP COLUMN ai_research_json")
        con.commit()
        print("Dropped recording.ai_research_json")
    con.close()


if __name__ == "__main__":
    main()
