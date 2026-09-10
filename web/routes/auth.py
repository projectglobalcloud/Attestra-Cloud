"""Sign-up / sign-in / sign-out."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from extensions import db
from models import (MEMBERSHIP_TYPES, PARTY_TYPES, PARTY_TYPE_NAMES, LoginRow,
                    ensure_administrator,
                    User, record_event)
from services import registry

bp = Blueprint("auth", __name__)


def _record_login(user: User) -> None:
    db.session.add(LoginRow(
        user_id=user.id,
        ip=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        agent=(request.user_agent.string or "")[:200],
    ))
    db.session.commit()


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    """Create an account and, with it, the party it will trade as.

    An account that is not somebody on the register cannot hold a dataset or
    sign a deed, so the two are created together rather than leaving a new
    arrival to discover later that it has no legal identity here.
    """
    if current_user.is_authenticated:
        return redirect(url_for("console.dashboard"))
    form = {}
    if request.method == "POST":
        form = {k: (request.form.get(k) or "").strip()
                for k in ("name", "email", "organization", "party_type",
                          "legal_name", "registration_id", "jurisdiction")}
        name = form["name"]
        email = form["email"].lower()
        password = request.form.get("password", "")
        party_type = form["party_type"] if form["party_type"] in PARTY_TYPE_NAMES \
            else "company"
        register_name = form["organization"] or name

        if not name or not email or len(password) < 8:
            flash("Name, email and a password of at least 8 characters are required.", "error")
        elif not registry.valid_email(email):
            flash("That does not look like an email address.", "error")
        elif User.query.filter_by(email=email).first():
            flash("An account with that email already exists — sign in instead.", "error")
        elif registry.party_exists(register_name):
            flash(f"{register_name!r} is already on the ownership register. "
                  "Choose another name, or ask its owner to add you.", "error")
        else:
            party = registry.create_party(
                register_name, party_type,
                legal_name=form["legal_name"],
                registration_id=form["registration_id"],
                jurisdiction=form["jurisdiction"],
                contact_email=email)
            if party_type in MEMBERSHIP_TYPES:
                # Whoever registers a group owns it, exactly as with a shared
                # repository; the other members are admitted afterwards.
                registry.add_member(party, email, name, role="owner")
            user = User(name=name, email=email, organization=register_name,
                        party_id=party.id, role="member")
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            # A brand-new deployment has no operator: the startup check runs
            # against an empty table and finds nobody to promote. Whoever
            # registers first is therefore promoted here, at the moment there
            # is finally an account to promote — otherwise nobody could approve
            # a transfer or verify an identity until the service happened to
            # restart.
            ensure_administrator()
            record_event("signup",
                         f"{name} registered {party.display_name} "
                         f"({party.type_label}) as {party.party_ref}")
            login_user(user)
            _record_login(user)
            return redirect(url_for("console.dashboard"))
    return render_template("auth/signup.html", party_types=PARTY_TYPES, form=form)


@bp.route("/signin", methods=["GET", "POST"])
def signin():
    if current_user.is_authenticated:
        return redirect(url_for("console.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user, remember=bool(request.form.get("remember")))
            _record_login(user)
            return redirect(request.args.get("next") or url_for("console.dashboard"))
        flash("Incorrect email or password.", "error")
    return render_template("auth/signin.html")


@bp.get("/signout")
@login_required
def signout():
    logout_user()
    return redirect(url_for("public.landing"))


@bp.get("/dev/login")
def dev_login():
    """Auto-login for screenshots/demos. ONLY active with ATTESTRA_DEV_LOGIN=1.

    Never set that variable on a shared or exposed deployment.
    """
    import os

    if os.environ.get("ATTESTRA_DEV_LOGIN") != "1":
        return redirect(url_for("auth.signin"))
    user = User.query.first()
    if user is None:
        return redirect(url_for("auth.signup"))
    login_user(user)
    _record_login(user)
    return redirect(request.args.get("next") or url_for("console.dashboard"))
