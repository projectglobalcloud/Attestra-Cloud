"""
Authenticating a request with an access key instead of a session.

The console signs a person in with a cookie. A pipeline has no browser, so it
presents an access key pair instead — the same credentials the Access Keys page
issues. Flask-Login's request loader is the right hook for this: it runs only
when there is no session user, and whatever it returns becomes `current_user`
for that request. Everything downstream — `services/access.py`, every party
scope, every refusal — then applies unchanged, because a key acts as the
account that owns it and can never do more than that account could.

Two forms are accepted, both ordinary for cloud APIs:

    Authorization: Bearer AKAT…:<secret>
    X-Attestra-Key: AKAT…   +   X-Attestra-Secret: <secret>

The secret is compared against a hash; it is never stored and never logged.
"""

from __future__ import annotations

from models import AccessKey, User, utcnow


def _split_credentials(request) -> tuple[str, str] | None:
    """Pull (key id, secret) out of the request, in either accepted form."""
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        if ":" in token:
            key_id, secret = token.split(":", 1)
            return key_id.strip(), secret.strip()
        return None
    key_id = request.headers.get("X-Attestra-Key", "").strip()
    secret = request.headers.get("X-Attestra-Secret", "").strip()
    if key_id and secret:
        return key_id, secret
    return None


def user_from_request(request) -> User | None:
    """The account this request is presenting a key for, if any."""
    from extensions import db

    credentials = _split_credentials(request)
    if credentials is None:
        return None
    key_id, secret = credentials
    row = AccessKey.query.filter_by(key_id=key_id).first()
    if row is None or row.status != "active":
        return None
    from werkzeug.security import check_password_hash

    if not check_password_hash(row.secret_hash, secret):
        return None

    user = db.session.get(User, row.user_id)
    if user is None:
        return None
    # "Last used" on the keys page is a real observation, so it is written
    # here rather than left as a column nothing ever fills in.
    row.last_used_at = utcnow()
    db.session.commit()
    return user


def install(login_manager) -> None:
    @login_manager.request_loader
    def _load_from_key(request):          # noqa: ANN001 — Flask-Login's signature
        return user_from_request(request)

    @login_manager.unauthorized_handler
    def _unauthorized():
        """A machine caller gets JSON; a person gets the sign-in page."""
        from flask import redirect, request, url_for
        from flask import jsonify

        if request.path.startswith("/api/"):
            return jsonify({
                "ok": False,
                "error": "authentication required — sign in, or present an "
                         "access key as 'Authorization: Bearer <KEY_ID>:<SECRET>'",
            }), 401
        return redirect(url_for("auth.signin", next=request.path))
