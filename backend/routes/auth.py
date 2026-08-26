"""
Auth blueprint
--------------
Handles both the page routes (login/register forms) and the JSON API
endpoints those forms call. Session-based auth via Flask-Login: on
successful login/register we call login_user(), which stores the user id
in the signed Flask session cookie. @login_required (used in routes/fields.py)
then protects every farmer-only route, and current_user.id is used to
scope every field/analysis query so a farmer can only ever see their own
data.

Passwords are never stored in plaintext -- werkzeug's generate_password_hash
(PBKDF2-SHA256 with a per-password salt) is used via User.set_password /
User.check_password in backend/models.py.
"""

import re

from flask import Blueprint, jsonify, render_template, request, redirect, url_for
from flask_login import login_user, logout_user, login_required, current_user

from backend.extensions import db
from backend.models import User

auth_bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------- pages ---
@auth_bp.get("/register")
def register_page():
    if current_user.is_authenticated:
        return redirect(url_for("fields.dashboard_page"))
    return render_template("register.html")


@auth_bp.get("/login")
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for("fields.dashboard_page"))
    return render_template("login.html")


@auth_bp.get("/logout")
@login_required
def logout_page():
    logout_user()
    return redirect(url_for("landing"))


# --------------------------------------------------------------- API ------
@auth_bp.post("/api/auth/register")
def api_register():
    payload = request.get_json(force=True, silent=True) or {}
    name = (payload.get("name") or "").strip()
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""

    if not name or not email or not password:
        return jsonify({"error": "Name, email and password are all required."}), 400
    if not EMAIL_RE.match(email):
        return jsonify({"error": "Enter a valid email address."}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters."}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "An account with this email already exists."}), 409

    user = User(name=name, email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    login_user(user)
    return jsonify({"user": user.to_dict(), "redirect": url_for("fields.dashboard_page")})


@auth_bp.post("/api/auth/login")
def api_login():
    payload = request.get_json(force=True, silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({"error": "Invalid email or password."}), 401

    login_user(user)
    return jsonify({"user": user.to_dict(), "redirect": url_for("fields.dashboard_page")})


@auth_bp.post("/api/auth/logout")
@login_required
def api_logout():
    logout_user()
    return jsonify({"ok": True})


@auth_bp.get("/api/auth/me")
def api_me():
    if not current_user.is_authenticated:
        return jsonify({"error": "Not authenticated."}), 401
    return jsonify({"user": current_user.to_dict()})