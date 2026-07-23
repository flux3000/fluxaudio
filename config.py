"""
config.py — Flux Audio application configuration.

Reads from environment variables when present (via .env),
falls back to safe defaults for local development.
"""

import os
from pathlib import Path

# Base directory of this file
BASE_DIR = Path(__file__).parent.resolve()


def _env_flag(name, default=False):
    """
    Parse a boolean environment variable. Only the literal string "true"
    (case-insensitive) is truthy; anything else (including unset, "false",
    "0", "") is False. `default` controls what an *unset* variable resolves
    to. Factored out so the DEV_MODE/SERVER_MODE default behavior is unit
    testable without needing to reload the config module.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() == "true"


# Known-insecure placeholder SECRET_KEY. Fine for a single-user local
# instance; app._validate_server_mode refuses to boot with this value
# when SERVER_MODE is on. Defined once here so config.py and
# app/__init__.py can't drift out of sync on what "the default" is.
DEV_SECRET_DEFAULT = "dev-secret-change-me"


class Config:
    # ── Security ──────────────────────────────────────────────
    SECRET_KEY = os.environ.get("SECRET_KEY", DEV_SECRET_DEFAULT)

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

    # Allowlist of base directories the ingest-preview endpoint is permitted
    # to read from. `folder` query params are resolved and must fall inside
    # one of these roots — otherwise arbitrary filesystem reads would be
    # possible for any logged-in user. Override via IMPORT_ROOTS env var
    # (":"-separated list of paths).
    _import_roots_env = os.environ.get("IMPORT_ROOTS", "").strip()
    if _import_roots_env:
        IMPORT_ROOTS = [p for p in _import_roots_env.split(":") if p]
    else:
        IMPORT_ROOTS = [str(LIBRARY_ROOT), "/Volumes"]

    # ── App ───────────────────────────────────────────────────
    # Note: Flask's own debug reloader is always forced off under PyWebView
    # (see run.py), so no DEBUG flag is carried here.
    HOST  = "127.0.0.1"
    PORT  = 5757        # internal Flask port used by PyWebView

    # ── Dev mode ──────────────────────────────────────────────
    # When True, skips login entirely — auto-logs in the first admin user.
    # Defaults to FALSE (fail-closed): must explicitly opt in with
    # DEV_MODE=true for local development. Never enable alongside SERVER_MODE.
    DEV_MODE = _env_flag("DEV_MODE", default=False)

    # ── Server mode ───────────────────────────────────────────
    # When True, indicates the app is running on a shared/public box rather
    # than a single-user local machine. Tightens boot-time validation
    # (see app._validate_server_mode): refuses to start with DEV_MODE on or
    # with a default/blank SECRET_KEY.
    SERVER_MODE = _env_flag("SERVER_MODE", default=False)
