"""
scripts/migrate_add_performer_image_dossier.py
Add performer.image_ext (TEXT) and performer.dossier_json (TEXT).

image_ext holds the uploaded profile picture's extension (e.g. ".jpg"), null
if none uploaded — the file itself lives on disk under
LIBRARY_ROOT/<sanitized performer name>/_images/, not in the DB.
dossier_json holds the latest AI Dossier research result (biography draft +
suggested resource links), same pattern as recording.ai_research_json.

Idempotent: skips any column that already exists.
Run once from the repo root:  python3 scripts/migrate_add_performer_image_dossier.py
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
    cols = [r[1] for r in cur.execute("PRAGMA table_info(performer)")]

    if "image_ext" in cols:
        print("performer.image_ext already present — nothing to do.")
    else:
        cur.execute("ALTER TABLE performer ADD COLUMN image_ext TEXT")
        print("Added performer.image_ext")

    if "dossier_json" in cols:
        print("performer.dossier_json already present — nothing to do.")
    else:
        cur.execute("ALTER TABLE performer ADD COLUMN dossier_json TEXT")
        print("Added performer.dossier_json")

    con.commit()
    con.close()


if __name__ == "__main__":
    main()
