"""Persist and retrieve the structured result of each JCL conversion run.

The run folder holds its JSON and generated artifacts. A small SQLite index
maps the unique run ID to the final result so callers can retrieve successful,
incomplete, and failed runs without knowing their job names or folder paths.
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def save_run(result, database_path):
    """Persist a structured result under its unique run ID.

    Table creation and insertion share a transaction, so a caller never sees
    an indexed run without its JSON payload.
    """
    # The caller chooses the database location; create its parent when this
    # is the first run in a new output directory.
    database = Path(database_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    # closing() releases the connection, while the nested connection context
    # commits both SQL statements together or rolls them back on failure.
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            # The result JSON is the full API contract. Separate identity and
            # status columns make the run index easy to inspect or query.
            connection.execute(
                """CREATE TABLE IF NOT EXISTS runs (
                       run_id TEXT PRIMARY KEY,
                       job_name TEXT NOT NULL,
                       status TEXT NOT NULL,
                       result_json TEXT NOT NULL,
                       completed_at TEXT NOT NULL
                   )"""
            )
            # SQL placeholders keep run data out of the SQL statement itself.
            connection.execute(
                """INSERT INTO runs (run_id, job_name, status, result_json, completed_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    result["run_id"], result["job_name"], result["status"],
                    json.dumps(result, ensure_ascii=False), timestamp,
                ),
            )


def get_run(run_id, database_path="outputs/sessions.db"):
    """Return one stored result, or None when the run ID is unknown.

    Artifact paths are remapped to the current output directory if matching
    files exist there, which supports a checkout copied from Codespaces.
    """
    database = Path(database_path)
    # Looking up an unknown run must not create an empty SQLite database.
    if not database.is_file():
        return None
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(
            "SELECT result_json FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    if not row:
        return None
    # JSON restores the same nested result shape written by the orchestrator.
    result = json.loads(row[0])
    run_folder = database.parent / result["job_name"] / result["run_id"]

    def local_path(stored_path, folder):
        """Prefer a matching file in this checkout over a saved absolute path."""
        # Saved results may have originated in another Codespaces checkout.
        # Only replace paths for files that actually exist in this run folder.
        candidate = folder / Path(stored_path).name
        return str(candidate) if candidate.is_file() else stored_path

    # Rebase both top-level artifact paths and nested validation code paths;
    # the latter are used by clients that display a validator's source file.
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
