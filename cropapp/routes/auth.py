from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from cropapp.extensions import db
from cropapp.models import User
from cropapp.services import demo as demo_service

auth_bp = Blueprint("auth", __name__, url_prefix="/account")


def _valid_email(email):
    return "@" in email and "." in email.split("@")[-1] and len(email) <= 255


def _safe_next(target):
    """Only allow same-site relative paths; reject scheme and protocol-relative redirects."""
    if not target:
        return None
    target = target.strip()
    if not target.startswith("/") or target[1:2] in ("/", "\\"):
        return None
    return target


def _t(en_text, mr_text):
    return mr_text if session.get("language", "en") == "mr" else en_text


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        error = None
        if not name:
            error = _t("Please enter your name.", "कृपया तुमचे नाव टाका.")
        elif not _valid_email(email):
            error = _t("Please enter a valid email address.", "कृपया वैध ईमेल पत्ता टाका.")
        elif len(password) < 6:
            error = _t("Password must be at least 6 characters.", "पासवर्ड किमान ६ अक्षरांचा असावा.")
        elif password != confirm:
            error = _t("Passwords do not match.", "पासवर्ड जुळत नाहीत.")
        elif User.query.filter_by(email=email).first():
            error = _t("An account with this email already exists.", "या ईमेलसह खाते आधीच आहे.")
        if error:
            flash(error, "danger")
            return render_template("auth/register.html", form=request.form), 400
        user = User(email=email, name=name, language=session.get("language", "en"))
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash(
            _t(
                f"Welcome, {user.greeting}! Your farm account is ready.",
                f"स्वागत, {user.greeting}! तुमचे शेत खाते तयार आहे.",
            ),
            "success",
        )
        return redirect(url_for("main.index"))
    return render_template("auth/register.html", form={})


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()
        if user is None or not user.check_password(password):
            flash(_t("Incorrect email or password.", "ईमेल किंवा पासवर्ड चुकीचा."), "danger")
            return render_template("auth/login.html", form=request.form), 401
        login_user(user, remember=bool(request.form.get("remember")))
        if user.language in ("en", "mr"):
            session["language"] = user.language
        flash(_t(f"Welcome back, {user.greeting}.", f"पुन्हा स्वागत, {user.greeting}."), "success")
        target = _safe_next(request.args.get("next")) or url_for("main.index")
        return redirect(target)
    return render_template("auth/login.html", form={})


@auth_bp.route("/demo", methods=["GET", "POST"])
def demo_login():
    """Log into the shared, pre-seeded demo account (no credentials needed)."""
    if not current_app.config.get("DEMO_MODE_ENABLED", True):
        flash(_t("The demo is switched off on this deployment.", "या तैनातीवर डेमो बंद आहे."), "warning")
        return redirect(url_for("main.index"))
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    try:
        user, created = demo_service.get_or_create_demo_user()
        if created:
            demo_service.seed_demo(current_app, user)
        login_user(user)
        if user.language in ("en", "mr"):
            session["language"] = user.language
        flash(
            _t(
                "You are exploring the demo account. Feel free to click around - nothing here is your real data.",
                "तुम्ही डेमो खाते पाहत आहात. मोकळेपणाने फिरून पाहा - हा तुमचा खरा डेटा नाही.",
            ),
            "info",
        )
    except Exception:  # seeding must never take down the demo route
        current_app.logger.exception("Demo seeding failed")
        db.session.rollback()
        flash(_t("Could not prepare the demo right now. Please try again.", "आत्ता डेमो तयार करता आला नाही. पुन्हा प्रयत्न करा."), "danger")
        return redirect(url_for("main.index"))
    return redirect(url_for("main.index"))


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash(_t("You have been logged out.", "तुम्ही लॉग आउट झाला आहात."), "secondary")
    return redirect(url_for("main.index"))


@auth_bp.route("/language", methods=["POST"])
def language():
    choice = request.form.get("language") or "en"
    choice = choice if choice in ("en", "mr") else "en"
    session["language"] = choice
    if current_user.is_authenticated:
        current_user.language = choice
        db.session.commit()
    return redirect(request.referrer or url_for("main.index"))
