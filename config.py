"""
config.py — Flux Audio application configuration.

Reads from environment variables when present (via .env),
falls back to safe defaults for local development.
"""

import os
from pathlib import Path

# Base directory of this file
BASE_DIR = Path(__file__).parent.resolve()

class Config:
    # ── Security ──────────────────────────────────────────────
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # ── Database ──────────────────────────────────────────────
    DB_PATH = BASE_DIR / "db" / "fluxaudio.db"
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{DB_PATH}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # SQLite: increase timeout for long-running analysis writes (default 5s is too short)
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"timeout": 60, "check_same_thread": False}
    }

    # ── Library ───────────────────────────────────────────────
    # Root directory where all audio recordings are stored.
    # Tracks are referenced by ID; this path never leaves the server.
    LIBRARY_ROOT = Path(os.environ.get(
        "LIBRARY_ROOT",
        "/Volumes/music/Flux Library"
    ))

    # ── App ───────────────────────────────────────────────────
    # Note: Flask's own debug reloader is always forced off under PyWebView
    # (see run.py), so no DEBUG flag is carried here.
    HOST  = "127.0.0.1"
    PORT  = 5757        # internal Flask port used by PyWebView

    # ── Dev mode ──────────────────────────────────────────────
    # When True, skips login entirely — auto-logs in the first admin user.
    # Set DEV_MODE=false in env to disable before shipping.
    DEV_MODE = os.environ.get("DEV_MODE", "true").lower() != "false"
