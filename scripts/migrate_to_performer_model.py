"""
scripts/migrate_to_performer_model.py
Transform the old Artist/CanonicalArtist model into Performer/Artist(person)/Membership.

Old:  artist (=acts) · canonical_artist (grouping) · artist_canonical (junction)
      performance.artist_id → artist(act)
New:  performer (=acts, keeps old artist ids) · artist (=persons, fresh) · membership
      performance.performer_id → performer

Each act becomes a Performer (same id + name; sort_name pulled from a linked
canonical if present). Each Performer is seeded with ONE member Artist matching
its own name — real people are added later in the app. user_artist_permission
rows are cleared (the old canonical scope doesn't map cleanly; re-grant as needed).

Refuses to run if `performer` already exists. Run once from the repo root:
  python3 scripts/migrate_to_performer_model.py
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


def migrate(db_path):
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    if _has(cur, "performer"):
        print("performer table already exists — migration already applied.")
        con.close()
        return
    if not _has(cur, "artist"):
        print("no artist table — nothing to migrate.")
        con.close()
        return

    has_junction = _has(cur, "artist_canonical") and _has(cur, "canonical_artist")

    # 1. Performer (acts) — copy from old artist, keeping ids so the FK stays valid.
    cur.execute("""
        CREATE TABLE performer (
            id INTEGER PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            sort_name VARCHAR(255),
            bio TEXT,
            created_at DATETIME,
            updated_at DATETIME
        )""")
    if has_junction:
        cur.execute("""
            INSERT INTO performer (id, name, sort_name, bio, created_at, updated_at)
            SELECT a.id, a.name,
                   (SELECT c.sort_name FROM artist_canonical ac
                      JOIN canonical_artist c ON c.id = ac.canonical_artist_id
                     WHERE ac.artist_id = a.id LIMIT 1),
                   a.bio, a.created_at, a.updated_at
            FROM artist a""")
    else:
        cur.execute("""INSERT INTO performer (id, name, sort_name, bio, created_at, updated_at)
                       SELECT id, name, NULL, bio, created_at, updated_at FROM artist""")

    # 2. Rebuild `artist` as persons (old act rows move aside).
    cur.execute("ALTER TABLE artist RENAME TO _artist_acts_old")
    cur.execute("""
        CREATE TABLE artist (
            id INTEGER PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            sort_name VARCHAR(255),
            bio TEXT,
            created_at DATETIME,
            updated_at DATETIME
        )""")

    # 3. Membership + seed one person per performer (same name).
    cur.execute("""
        CREATE TABLE membership (
            id INTEGER PRIMARY KEY,
            performer_id INTEGER NOT NULL,
            artist_id INTEGER NOT NULL,
            "order" INTEGER NOT NULL DEFAULT 0
        )""")
    for pid, pname in cur.execute("SELECT id, name FROM performer").fetchall():
        cur.execute("INSERT INTO artist (name) VALUES (?)", (pname,))
        aid = cur.lastrowid
        cur.execute('INSERT INTO membership (performer_id, artist_id, "order") VALUES (?,?,0)',
                    (pid, aid))

    # 4. performance.artist_id → performer_id (values already equal performer ids).
    cur.execute("ALTER TABLE performance RENAME COLUMN artist_id TO performer_id")

    # 5. user_artist_permission: clear (old scope doesn't map) + repoint column.
    if _has(cur, "user_artist_permission"):
        cur.execute("DELETE FROM user_artist_permission")
        cols = [r[1] for r in cur.execute("PRAGMA table_info(user_artist_permission)")]
        if "canonical_artist_id" in cols:
            cur.execute("ALTER TABLE user_artist_permission "
                        "RENAME COLUMN canonical_artist_id TO performer_id")

    # 6. Drop old grouping tables.
    for t in ("artist_canonical", "canonical_artist", "_artist_acts_old"):
        if _has(cur, t):
            cur.execute("DROP TABLE %s" % t)

    con.commit()
    ok = cur.execute("PRAGMA integrity_check").fetchone()[0]
    print("integrity_check:", ok)
    print("performers:",  cur.execute("SELECT COUNT(*) FROM performer").fetchone()[0],
          "| artists:",   cur.execute("SELECT COUNT(*) FROM artist").fetchone()[0],
          "| memberships:", cur.execute("SELECT COUNT(*) FROM membership").fetchone()[0])
    con.close()


if __name__ == "__main__":
    migrate(DB)
