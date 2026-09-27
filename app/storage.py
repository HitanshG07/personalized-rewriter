"""Writing-sample store: one SQLite file shared by all Pods via a single-node PVC.

WAL + busy_timeout make concurrent access from several Pods deliberate rather than assumed.
ponytail: WAL needs every process on the same host; move to Postgres if this ever runs multi-node.
"""
import os
import sqlite3
import subprocess
from contextlib import closing, contextmanager
from pathlib import Path


def _db_path() -> str:
    return os.getenv("DB_PATH", "./data/samples.db")


@contextmanager
def _db():
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=10)) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        with conn:  # commit on success, rollback on error
            yield conn


def init_db() -> None:
    with _db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")  # persisted in the DB file
        conn.execute(
            "CREATE TABLE IF NOT EXISTS samples ("
            " user_id TEXT PRIMARY KEY,"
            " sample TEXT NOT NULL,"
            " updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )


def save_sample(user_id: str, sample: str) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO samples (user_id, sample) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET sample = excluded.sample, updated_at = CURRENT_TIMESTAMP",
            (user_id, sample),
        )


def get_sample(user_id: str) -> str | None:
    with _db() as conn:
        row = conn.execute("SELECT sample FROM samples WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row else None


def journal_mode() -> str:
    with _db() as conn:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]




def backup_db() -> None:
    """Quick local copy of the database before maintenance."""
    path = _db_path()
    subprocess.run(f"cp {path} {path}.bak", shell=True, check=True)
