"""
app/__init__.py — Flask application factory.

Usage:
    from app import create_app
    app = create_app()
"""

from flask import Flask
from config import Config
from app.extensions import db, login_manager


def create_app(config_class=Config):
    app = Flask(__name__, static_folder="static")
    app.config.from_object(config_class)

    # ── Initialize extensions ──────────────────────────────────
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    # ── Register blueprints ────────────────────────────────────
    from app.api.auth         import bp as auth_bp
    from app.api.artists      import bp as artists_bp
    from app.api.performances import bp as performances_bp
    from app.api.recordings   import bp as recordings_bp
    from app.api.tracks       import bp as tracks_bp
    from app.api.stream       import bp as stream_bp
    from app.api.ingest       import bp as ingest_bp
    from app.api.venues       import bp as venues_bp
    from app.api.events       import bp as events_bp
    from app.api.debug        import bp as debug_bp

    app.register_blueprint(auth_bp,         url_prefix="/api/auth")
    app.register_blueprint(artists_bp,      url_prefix="/api/artists")
    app.register_blueprint(performances_bp, url_prefix="/api/performances")
    app.register_blueprint(recordings_bp,   url_prefix="/api/recordings")
    app.register_blueprint(tracks_bp,       url_prefix="/api/tracks")
    app.register_blueprint(stream_bp,       url_prefix="/api/stream")
    app.register_blueprint(ingest_bp,       url_prefix="/api/ingest")
    app.register_blueprint(venues_bp,       url_prefix="/api/venues")
    app.register_blueprint(events_bp,       url_prefix="/api/events")
    app.register_blueprint(debug_bp,        url_prefix="/api/debug")

    # ── Dev mode: auto-login as first admin ───────────────────
    if app.config.get("DEV_MODE"):
        from flask_login import login_user, current_user
        from flask import request as _req

        @app.before_request
        def dev_auto_login():
            if current_user.is_authenticated:
                return
            # Skip for static assets
            if _req.path.startswith("/css") or _req.path.startswith("/js"):
                return
            admin = db.session.query(User).filter_by(role="admin", is_active=True).first()
            if admin:
                login_user(admin, remember=True)

    # ── Serve the frontend SPA ─────────────────────────────────
    from flask import send_from_directory
    import os

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def serve_frontend(path):
        static_dir = os.path.join(app.root_path, "static")
        if path and os.path.exists(os.path.join(static_dir, path)):
            return send_from_directory(static_dir, path)
        return send_from_directory(static_dir, "index.html")

    return app


# Flask-Login user loader
from app.extensions import login_manager
from app.models.user import User
from app.models.track_analysis import TrackAnalysis  # noqa: F401 — ensures table is created

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))
