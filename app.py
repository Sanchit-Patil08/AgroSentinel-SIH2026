"""
AgroSentinel - Flask entry point.

Run locally with:
    python app.py
Then open http://localhost:5000

On first run this creates instance/agrosentinel.db (SQLite) automatically
via db.create_all() -- no manual database setup needed. Set DATABASE_URL to
point at PostgreSQL instead for a deployment (see backend/config.py and
README.md).
"""

import os

from flask import Flask, render_template
from flask_cors import CORS
from flask_login import current_user

from backend.config import Config
from backend.extensions import db, login_manager
from backend.routes.api import api_bp
from backend.routes.auth import auth_bp
from backend.routes.diagnosis import diagnosis_bp
from backend.routes.fields import fields_bp
from backend.routes.intervention import intervention_bp


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config)
    CORS(app)

    db.init_app(app)
    login_manager.init_app(app)

    from backend.models import User  # imported here to register with SQLAlchemy

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    app.register_blueprint(api_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(fields_bp)
    app.register_blueprint(diagnosis_bp)
    app.register_blueprint(intervention_bp)

    @app.get("/")
    def landing():
        return render_template("index.html")

    @app.get("/demo")
    def demo():
        return render_template("demo.html")

    # Make current_user available in every template (nav bar login state)
    @app.context_processor
    def inject_user():
        return {"current_user": current_user}

    with app.app_context():
        db.create_all()

    # Continuous weather monitoring: background job that periodically
    # refreshes weather for every saved field (see backend/services/
    # weather_scheduler.py). Guarded so Flask's debug reloader -- which
    # spawns a watcher process plus a child process -- only starts the
    # scheduler once, in the actual running child process.
    if not app.config.get("DEBUG") or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        from backend.services.weather_scheduler import start_weather_scheduler

        start_weather_scheduler(app)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=app.config.get("DEBUG", True))