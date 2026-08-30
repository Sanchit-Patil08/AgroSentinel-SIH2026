import os
from app import app
from backend.extensions import db

with app.app_context():
    db.session.remove()
    db.engine.dispose()

    db_path = app.config.get("SQLALCHEMY_DATABASE_URI", "")

    if db_path.startswith("sqlite:///"):
        db_file = db_path.replace("sqlite:///", "", 1)

        if not os.path.isabs(db_file):
            db_file = os.path.abspath(db_file)

        print(f"Database: {db_file}")

        if os.path.exists(db_file):
            try:
                os.remove(db_file)
                print("Old database deleted.")
            except PermissionError:
                print("Database is still being used by another process.")
                print("Close any Python/Flask process using the project and run this again.")
                raise

    db.create_all()

    print("Database recreated successfully.")
    print("Current database schema is ready.")