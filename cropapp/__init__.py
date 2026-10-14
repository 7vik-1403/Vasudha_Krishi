import os

from dotenv import load_dotenv
from flask import Flask, render_template
from sqlalchemy import inspect, text

from cropapp.config import BASE_DIR, Config
from cropapp.extensions import db, init_extensions

# Columns added after the first release - patched into an existing sqlite file.
NEW_COLUMNS = {
    "farms": {
        "crop_category": "VARCHAR(40)",
        "water_source": "VARCHAR(40)",
        "water_days": "FLOAT",
        "water_note": "VARCHAR(255)",
    },
    "recommendations": {
        "crop_category": "VARCHAR(40)",
        "water_json": "TEXT",
        "spray_json": "TEXT",
        "excluded_json": "TEXT",
    },
}


def create_app(config_overrides=None):
    load_dotenv(os.path.join(BASE_DIR, ".env"))

    app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"), static_folder=os.path.join(BASE_DIR, "static"))
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    init_extensions(app)

    from cropapp.routes.api import api_bp
    from cropapp.routes.auth import auth_bp
    from cropapp.routes.main import main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)

    _register_error_handlers(app)
    _register_cli(app)
    _register_context(app)

    with app.app_context():
        db.create_all()
        _ensure_columns()

    from cropapp.ml import FEATURE_LABELS, FEATURE_LABELS_MR

    app.jinja_env.globals["FEATURE_LABELS"] = FEATURE_LABELS
    app.jinja_env.globals["FEATURE_LABELS_MR"] = FEATURE_LABELS_MR
    return app


def _register_context(app):
    """Expose month labels and the active language to every template."""

    @app.context_processor
    def _inject_globals():
        from flask import session

        from cropapp.routes.main import MONTH_LABELS, MONTH_LABELS_MR

        return {
            "month_labels": MONTH_LABELS,
            "month_labels_mr": MONTH_LABELS_MR,
            "lang": session.get("language", "en"),
        }


def _ensure_columns():
    """ALTER TABLE ADD COLUMN for any column missing from an older database file."""
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    for table, columns in NEW_COLUMNS.items():
        if table not in tables:
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, ddl in columns.items():
            if name not in existing:
                db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
    db.session.commit()


def _register_error_handlers(app):
    @app.errorhandler(400)
    def bad_request(error):
        return (
            render_template(
                "error.html",
                code=400,
                message=getattr(error, "description", None) or "That request could not be understood.",
            ),
            400,
        )

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", code=404, message="That page does not exist."), 404

    @app.errorhandler(500)
    def server_error(_error):
        return render_template("error.html", code=500, message="Something went wrong on our side."), 500


def _register_cli(app):
    @app.cli.command("init-db")
    def init_db():
        """Create database tables."""
        db.create_all()
        print("Database tables created.")
