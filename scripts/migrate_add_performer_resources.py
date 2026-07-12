"""
scripts/migrate_add_performer_resources.py

Create the performer_resource table (external reference links stored at the
Performer/act level). Idempotent. Optionally seeds the Pat Metheny Group PMDB.

    python3 scripts/migrate_add_performer_resources.py
"""

import os
import sqlite3

DB = os.environ.get(
    "FLUX_DB",
    os.path.join(os.path.dirname(__file__), "..", "db", "fluxaudio.db"),
)

PMDB_URL = "http://marc.morvan.free.fr/pmdb/"


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()

    existing = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "performer_resource" in existing:
        print("performer_resource already exists — nothing to do.")
    else:
        cur.execute("""
            CREATE TABLE performer_resource (
                id           INTEGER PRIMARY KEY,
                performer_id INTEGER NOT NULL REFERENCES performer(id),
                label        VARCHAR(255),
                url          VARCHAR(1024) NOT NULL,
                "order"      INTEGER NOT NULL DEFAULT 0,
                created_at   DATETIME
            )""")
        con.commit()
        print("Created performer_resource table.")

    # Seed the PMDB for Pat Metheny Group if that performer exists and doesn't
    # already have the link.
    row = cur.execute(
        "SELECT id FROM performer WHERE lower(name)=lower(?)",
        ("Pat Metheny Group",)).fetchone()
    if row:
        pid = row[0]
        has = cur.execute(
            "SELECT 1 FROM performer_resource WHERE performer_id=? AND url=?",
            (pid, PMDB_URL)).fetchone()
        if has:
            print("PMDB already linked to Pat Metheny Group.")
        else:
            cur.execute(
                'INSERT INTO performer_resource (performer_id, label, url, "order", created_at) '
                "VALUES (?, ?, ?, 0, datetime('now'))",
                (pid, "PMDB — Pat Metheny Database", PMDB_URL))
            con.commit()
            print("Seeded PMDB link for Pat Metheny Group.")
    else:
        print("No 'Pat Metheny Group' performer yet — add the PMDB link from the "
              "Performer page once it exists.")
    con.close()


if __name__ == "__main__":
    main()
