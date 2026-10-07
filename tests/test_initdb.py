import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app.py"
# Forced over the environment so a real value exported in the shell never reaches the subprocess.
DUMMIES = {
    key: "test"
    for key in (
        "TODOIST_FLASK_SECRET_KEY",
        "TODOIST_CLIENT_ID",
        "TODOIST_CLIENT_SECRET",
        "GOOGLE_MAP_API_KEY",
    )
}


def test_initdb_creates_the_schema_on_an_empty_database(tmp_path):
    """G1: the documented `python app.py initdb` must create the tables, unique
    constraint included. It ran create_all outside an app context and crashed."""
    db = tmp_path / "fresh.db"
    run = subprocess.run(
        [sys.executable, str(APP), "initdb"],
        cwd=APP.parent,
        env={**os.environ, **DUMMIES, "DATABASE_URL": f"sqlite:///{db}"},
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    with closing(sqlite3.connect(db)) as conn:
        (sql,) = conn.execute(
            "select sql from sqlite_master where name = 'location_label'"
        ).fetchone()
    assert "CONSTRAINT uq_location_label_user_label UNIQUE (user_id, label_id)" in sql
