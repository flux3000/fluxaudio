"""
extensions.py — Shared Flask extension instances.

Instantiated here (without an app) so models can import `db`
without causing circular imports. The app factory calls db.init_app(app).
"""

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from sqlalchemy import event
from sqlalchemy.engine import Engine

db           = SQLAlchemy()
login_manager = LoginManager()


# SQLite ignores declared foreign keys unless PRAGMA foreign_keys=ON is issued
# on every connection (it is not persisted in the DB file, and is OFF by
# default). This app is SQLite-only; the dbapi_connection module check keeps
# this correct if that ever changes.
@event.listens_for(Engine, "connect")
def _sqlite_fk_pragma(dbapi_connection, _connection_record):
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
