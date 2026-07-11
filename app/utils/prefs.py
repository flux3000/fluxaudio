"""
app/utils/prefs.py — user preferences + BYOK secret storage.

Non-secret prefs (ai_model, ingest_file_behavior) live in the user_preference
table. The Anthropic API key is a real secret, so it goes in the OS keychain via
the `keyring` package — the DB never stores it, only whether one is present.
"""

from app.extensions import db
from app.models.user_preference import UserPreference

try:
    import keyring
    _HAS_KEYRING = True
except ImportError:
    _HAS_KEYRING = False

_SERVICE = "flux_audio"


def get_pref(user_id, key, default=None):
    row = db.session.query(UserPreference).filter_by(user_id=user_id, key=key).first()
    return row.value if row else default


def set_pref(user_id, key, value):
    row = db.session.query(UserPreference).filter_by(user_id=user_id, key=key).first()
    if row:
        row.value = value
    else:
        db.session.add(UserPreference(user_id=user_id, key=key, value=value))
    db.session.commit()


# ── BYOK secret (OS keychain) ─────────────────────────────────────────────────

def _account(user_id):
    return "anthropic_api_key:%s" % user_id


def get_api_key(user_id):
    if not _HAS_KEYRING:
        return None
    try:
        return keyring.get_password(_SERVICE, _account(user_id))
    except Exception:
        return None


def set_api_key(user_id, key):
    if not _HAS_KEYRING:
        raise RuntimeError("OS keychain unavailable")
    keyring.set_password(_SERVICE, _account(user_id), key)


def delete_api_key(user_id):
    if not _HAS_KEYRING:
        return
    try:
        keyring.delete_password(_SERVICE, _account(user_id))
    except Exception:
        pass


def has_api_key(user_id):
    return bool(get_api_key(user_id))
