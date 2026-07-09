"""
extensions.py — Shared Flask extension instances.

Instantiated here (without an app) so models can import `db`
without causing circular imports. The app factory calls db.init_app(app).
"""

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db           = SQLAlchemy()
login_manager = LoginManager()
