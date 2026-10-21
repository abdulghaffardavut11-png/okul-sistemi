import functools
import sqlite3

import click
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db

bp = Blueprint("auth", __name__, cli_group=None)
ROLES = (
    "super_admin",
    "admin",
    "manager",
    "teacher",
    "student",
    "parent",
    "health_officer",
    "boarding_master",
    "head_office",
    "board",
)


@bp.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        identity = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        users = get_db().execute(
            "SELECT u.id, u.username, u.email, u.password_hash, u.role, u.is_active "
            "FROM users u WHERE u.username = ? OR lower(u.email) = lower(?) "
            "OR (u.role IN ('student', 'parent') AND EXISTS ("
            "SELECT 1 FROM students s WHERE "
            "(s.id=u.student_id OR EXISTS (SELECT 1 FROM user_students us "
            "WHERE us.user_id=u.id AND us.student_id=s.id)) AND lower(s.email)=lower(?))) "
            "ORDER BY CASE WHEN u.username=? THEN 0 ELSE 1 END",
            (identity, identity, identity, identity),
        ).fetchall()
        user = next(
            (
                candidate for candidate in users
                if candidate["is_active"]
                and check_password_hash(candidate["password_hash"], password)
            ),
            None,
        )
        if user is None:
            flash("Invalid sign-in details or inactive account.", "error")
        else:
            language = session.get("language")
            session.clear()
            if language in {"en", "tr", "fr", "ar", "ha", "ak"}:
                session["language"] = language
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            return redirect(url_for("views.dashboard"))
    return render_template("login.html")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.before_app_request
def load_logged_in_user():
    user_id = session.get("user_id")
    if user_id is None:
        session.pop("username", None)
        session.pop("role", None)
        return
    user = get_db().execute(
        "SELECT id, username, role, is_active FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if user is None or not user["is_active"]:
        session.clear()
    else:
        session["username"] = user["username"]
        session["role"] = user["role"]


@bp.cli.command("create-admin")
def create_admin():
    """Create a super administrator with a hidden password prompt."""
    if get_db().execute("SELECT 1 FROM users WHERE role = 'super_admin'").fetchone():
        raise click.ClickException(
            "A super administrator already exists. Use the reset-admin command."
        )
    username = click.prompt("Administrator username").strip()
    if not username:
        raise click.ClickException("Username cannot be empty.")
    password = click.prompt(
        "Administrator password", hide_input=True, confirmation_prompt=True
    )
    if len(password) < 12:
        raise click.ClickException("Password must contain at least 12 characters.")
    try:
        get_db().execute(
            "INSERT INTO users (username, password_hash, role) "
            "VALUES (?, ?, 'super_admin')",
            (username, generate_password_hash(password, method="pbkdf2:sha256")),
        )
        get_db().commit()
    except sqlite3.IntegrityError as error:
        get_db().rollback()
        raise click.ClickException("That username is already in use.") from error
    click.echo("Super administrator account created.")


@bp.cli.command("reset-admin")
def reset_admin():
    """Reset an administrator password using hidden password prompts."""
    username = click.prompt("Administrator username").strip()
    user = get_db().execute(
        "SELECT id FROM users WHERE username = ? AND role = 'super_admin'",
        (username,),
    ).fetchone()
    if user is None:
        raise click.ClickException("No super administrator exists with that username.")
    password = click.prompt(
        "New administrator password", hide_input=True, confirmation_prompt=True
    )
    if len(password) < 12:
        raise click.ClickException("Password must contain at least 12 characters.")
    get_db().execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (generate_password_hash(password, method="pbkdf2:sha256"), user["id"]),
    )
    get_db().commit()
    click.echo("Administrator password updated.")


def login_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if session.get("user_id") is None:
            return redirect(url_for("auth.login"))
        return view(**kwargs)

    return wrapped_view


def roles_required(*roles):
    def decorator(view):
        @functools.wraps(view)
        @login_required
        def wrapped_view(**kwargs):
            if session.get("role") not in roles:
                abort(403)
            return view(**kwargs)

        return wrapped_view

    return decorator
