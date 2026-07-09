"""
api/auth.py — Authentication endpoints.

Routes:
  POST /api/auth/login   — validate credentials, create session
  POST /api/auth/logout  — clear session
  GET  /api/auth/me      — return current user info
"""

from flask import Blueprint, request, jsonify
from flask_login import login_user, logout_user, current_user, login_required
import bcrypt
from app.extensions import db
from app.models.user import User

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["POST"])
def login():
    data     = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "")

    user = db.session.query(User).filter_by(username=username, is_active=True).first()
    if not user or not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        return jsonify({"error": "Invalid credentials"}), 401

    login_user(user)
    return jsonify({"id": user.id, "username": user.username, "role": user.role})


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return jsonify({"ok": True})


@bp.route("/me")
@login_required
def me():
    return jsonify({
        "id":          current_user.id,
        "username":    current_user.username,
        "role":        current_user.role,
        "all_artists": current_user.all_artists,
    })
