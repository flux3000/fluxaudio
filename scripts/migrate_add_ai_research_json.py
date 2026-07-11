"""
scripts/migrate_add_ai_research_json.py
Add recording.ai_research_json (TEXT) — stores the latest AI research blob.

Idempotent: skips if the column already exists.
Run once from the repo root:  python3 scripts/migrate_add_ai_research_json.py
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
    if "ai_research_json" in cols:
        print("ai_research_json already present — nothing to do.")
    else:
        cur.execute("ALTER TABLE recording ADD COLUMN ai_research_json TEXT")
        con.commit()
        print("Added recording.ai_research_json")
    con.close()


if __name__ == "__main__":
    main()
