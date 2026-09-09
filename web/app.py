"""
Attestra — web console for the cloud-auditing protocol.

Run:
    cd web
    pip install -r requirements.txt      (code/requirements.txt must also be installed)
    python3 app.py
Then open http://127.0.0.1:5000
"""

from flask import Flask

from settings import Config
from extensions import db, login_manager


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)

    from routes import api, auth, console, market, public

    app.register_blueprint(public.bp)
    app.register_blueprint(market.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(console.bp)
    app.register_blueprint(api.bp)

    with app.app_context():
        import models  # noqa: F401  (register tables)
        db.create_all()
        models.ensure_schema()
        models.ensure_administrator()

    @app.errorhandler(Exception)
    def _unhandled(exc):
        """The console speaks JSON; an unhandled fault must not answer in HTML.

        Werkzeug's own HTTP errors are passed through untouched — this is only
        for genuine faults, which are logged and reported as a refusal the UI
        can show rather than a page it cannot parse.
        """
        from werkzeug.exceptions import HTTPException
        from flask import jsonify, request

        if isinstance(exc, HTTPException):
            return exc
        app.logger.exception("unhandled error on %s", request.path)
        if request.path.startswith("/api/"):
            return jsonify({"ok": False,
                            "error": f"{type(exc).__name__}: {exc}"}), 500
        raise exc

    @app.errorhandler(413)
    def _too_large(_exc):
        """An oversized upload must answer in JSON — the console reads JSON."""
        from flask import jsonify, request

        limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        if request.path.startswith("/api/"):
            return jsonify({"ok": False,
                            "error": f"that file is larger than the "
                                     f"{limit_mb} MB upload limit"}), 413
        return f"Upload exceeds the {limit_mb} MB limit", 413

    # A request may authenticate with an access key instead of a session.
    from services import apiauth
    apiauth.install(login_manager)

    # Background jobs record themselves in the database, which needs the app.
    from services import jobs
    jobs.bind_app(app)

    # Warm the protocol hub at startup so first-request latency is honest.
    from services.protocol import ProtocolHub
    ProtocolHub.instance()

    # Continuous verification: audits datasets on their configured schedule.
    from services import scheduler
    scheduler.start(app)

    return app


app = create_app()

if __name__ == "__main__":
    import os

    # Port is overridable: macOS AirPlay Receiver squats on 5000 on some machines.
    port = int(os.environ.get("ATTESTRA_PORT", "5000"))
    # threaded=True: long protocol jobs run in worker threads while the UI polls
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
