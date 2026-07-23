"""
tests/test_foreign_keys.py — SQLite FK enforcement (punch-list P1 #4).

SQLite ignores declared foreign keys unless `PRAGMA foreign_keys=ON` is issued
on every connection (it is not persisted in the DB file). app/extensions.py
registers a SQLAlchemy Engine `connect` listener that issues the pragma on
every new DBAPI connection. These tests prove the listener is wired and that
it actually rejects a dangling FK insert.
"""

from sqlalchemy.exc import IntegrityError

from app.extensions import db as _db
from app.models.peer import Peer, CollectionGrant


def test_foreign_keys_pragma_is_on(app, db):
    """The listener fires on connections obtained through the app's db session."""
    result = db.session.execute(db.text("PRAGMA foreign_keys")).scalar()
    assert result == 1


def test_bogus_fk_insert_is_rejected(app, db):
    """Inserting a CollectionGrant pointing at a non-existent collection_id
    must raise IntegrityError now that FK enforcement is on (previously this
    would insert silently and create an orphan row)."""
    peer = Peer(name="Test Peer")
    db.session.add(peer)
    db.session.flush()

    bogus = CollectionGrant(peer_id=peer.id, collection_id=999999)
    db.session.add(bogus)
    try:
        db.session.flush()
        assert False, "expected IntegrityError for dangling collection_id FK"
    except IntegrityError:
        db.session.rollback()
