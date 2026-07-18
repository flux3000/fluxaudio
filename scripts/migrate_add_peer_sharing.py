"""
scripts/migrate_add_peer_sharing.py — add the inbound peer-sharing tables:
  peer · collection_grant · peer_invite · peer_token · peer_access_log

Additive only — no existing table is touched, no data migrated. Idempotent:
safe to re-run. See "Peer Sharing — Design Spec v1" in the Drive Context
Library.

Run once from the repo root:  python3 scripts/migrate_add_peer_sharing.py
"""

import os
import sqlite3

DB = os.environ.get(
    "FLUX_DB",
    os.path.join(os.path.dirname(__file__), "..", "db", "fluxaudio.db"),
)


def _has_table(cur, name):
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _create(cur, name, ddl):
    if _has_table(cur, name):
        print(f"{name} already exists")
        return
    cur.execute(ddl)
    print(f"created {name}")


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()

    _create(cur, "peer", """
        CREATE TABLE peer (
            id INTEGER PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            contact_note TEXT,
            created_at DATETIME,
            last_seen_at DATETIME,
            revoked_at DATETIME
        )""")

    _create(cur, "collection_grant", """
        CREATE TABLE collection_grant (
            id INTEGER PRIMARY KEY,
            peer_id INTEGER NOT NULL REFERENCES peer(id),
            collection_id INTEGER NOT NULL REFERENCES collection(id),
            created_at DATETIME,
            revoked_at DATETIME
        )""")

    _create(cur, "peer_invite", """
        CREATE TABLE peer_invite (
            id INTEGER PRIMARY KEY,
            peer_id INTEGER NOT NULL REFERENCES peer(id),
            code_hash VARCHAR(64) NOT NULL UNIQUE,
            created_at DATETIME,
            expires_at DATETIME NOT NULL,
            consumed_at DATETIME
        )""")

    _create(cur, "peer_token", """
        CREATE TABLE peer_token (
            id INTEGER PRIMARY KEY,
            peer_id INTEGER NOT NULL REFERENCES peer(id),
            token_hash VARCHAR(64) NOT NULL UNIQUE,
            device_label VARCHAR(255),
            created_at DATETIME,
            last_used_at DATETIME,
            revoked_at DATETIME
        )""")

    _create(cur, "peer_access_log", """
        CREATE TABLE peer_access_log (
            id INTEGER PRIMARY KEY,
            peer_id INTEGER NOT NULL REFERENCES peer(id),
            track_id INTEGER NOT NULL REFERENCES track(id),
            occurred_at DATETIME
        )""")

    # Lookup indexes (match the index=True / unique=True declared on the models).
    # CREATE INDEX IF NOT EXISTS is itself idempotent.
    for stmt in [
        "CREATE INDEX IF NOT EXISTS ix_collection_grant_peer_id ON collection_grant(peer_id)",
        "CREATE INDEX IF NOT EXISTS ix_collection_grant_collection_id ON collection_grant(collection_id)",
        "CREATE INDEX IF NOT EXISTS ix_peer_invite_peer_id ON peer_invite(peer_id)",
        "CREATE INDEX IF NOT EXISTS ix_peer_invite_code_hash ON peer_invite(code_hash)",
        "CREATE INDEX IF NOT EXISTS ix_peer_token_peer_id ON peer_token(peer_id)",
        "CREATE INDEX IF NOT EXISTS ix_peer_token_token_hash ON peer_token(token_hash)",
        "CREATE INDEX IF NOT EXISTS ix_peer_access_log_peer_id ON peer_access_log(peer_id)",
        "CREATE INDEX IF NOT EXISTS ix_peer_access_log_track_id ON peer_access_log(track_id)",
        "CREATE INDEX IF NOT EXISTS ix_peer_access_log_occurred_at ON peer_access_log(occurred_at)",
    ]:
        cur.execute(stmt)
    print("indexes ensured")

    con.commit()
    con.close()
    print("done")


if __name__ == "__main__":
    main()
