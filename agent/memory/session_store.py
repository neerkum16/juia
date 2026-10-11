"""Small SQLite index of completed and failed job results."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def save_run(result, database_path):
    """Persist a structured result under its unique run ID."""
    database = Path(database_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS runs (
                       run_id TEXT PRIMARY KEY,
                       job_name TEXT NOT NULL,
                       status TEXT NOT NULL,
                       result_json TEXT NOT NULL,
                       completed_at TEXT NOT NULL
                   )"""
            )
            connection.execute(
                """INSERT INTO runs (run_id, job_name, status, result_json, completed_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    result["run_id"], result["job_name"], result["status"],
                    json.dumps(result, ensure_ascii=False), timestamp,
                ),
            )


def get_run(run_id, database_path="outputs/sessions.db"):
    """Return one stored result, or None when the run ID is unknown."""
    database = Path(database_path)
    if not database.is_file():
        return None
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(
            "SELECT result_json FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    if not row:
        return None
    result = json.loads(row[0])
    run_folder = database.parent / result["job_name"] / result["run_id"]

    def local_path(stored_path, folder):
        # Saved results may have originated in another Codespaces checkout.
        candidate = folder / Path(stored_path).name
        return str(candidate) if candidate.is_file() else stored_path

    result["artifacts"] = {
        key: local_path(path, run_folder)
        for key, path in result.get("artifacts", {}).items()
    }
    result["code_paths"] = {
        key: local_path(path, run_folder / "pyspark")
        for key, path in result.get("code_paths", {}).items()
    }
    for validation in result.get("validation_results", []):
        if validation.get("code_path"):
            validation["code_path"] = local_path(
                validation["code_path"], run_folder / "pyspark"
            )
    return result
