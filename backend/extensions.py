"""
Shared Flask extension instances.

Kept in their own module (rather than instantiated in app.py) so that
models.py, routes/*.py etc. can all `from backend.extensions import db`
without circular imports back to app.py.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login_page"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "info"