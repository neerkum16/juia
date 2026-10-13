"""FastAPI interface around the existing one-job JUIA orchestrator."""

from __future__ import annotations

import json
import importlib.util
import os
import re
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from agent.main import run_job
from agent.memory.session_store import get_run


ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).parent / "static"
OUTPUT_DIR = ROOT / "outputs"
DEFAULT_CNTL = ROOT / "cntl"
VECTOR_DB = ROOT / "corpus" / "vectordb"
MAX_JCL_BYTES = 5 * 1024 * 1024
MAX_CNTL_BYTES = 2 * 1024 * 1024
MAX_CNTL_FILES = 20
RUN_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
TASK_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
UPLOAD_NAME_PATTERN = re.compile(r"^[A-Za-z0-9@#$_.()\-]+$")
ARTIFACT_NAMES = {
    "spec": "migration_spec.md",
    "diagram": "lineage.mmd",
    "decoded": "decoded.json",
    "validation": "validation.json",
    "result": "result.json",
}

app = FastAPI(title="JUIA", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class TaskManager:
    """Serialize conversions and retain short-lived browser task states."""

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="juia-run")
        self.lock = Lock()
        self.tasks: dict[str, dict] = {}

    def submit(self, jcl_path: Path, cntl_folder: Path, staging: tempfile.TemporaryDirectory):
        task_id = uuid4().hex
        task = {
            "task_id": task_id,
            "state": "queued",
            "filename": jcl_path.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "run_id": None,
            "error": None,
        }
        with self.lock:
            self.tasks[task_id] = task
        self.executor.submit(self._execute, task_id, jcl_path, cntl_folder, staging)
        return task.copy()

    def _execute(self, task_id: str, jcl_path: Path, cntl_folder: Path,
                 staging: tempfile.TemporaryDirectory):
        with self.lock:
            self.tasks[task_id]["state"] = "running"
        try:
            result = run_job(jcl_path, cntl_folder=cntl_folder, output_folder=OUTPUT_DIR)
            with self.lock:
                self.tasks[task_id]["state"] = "completed"
                self.tasks[task_id]["run_id"] = result["run_id"]
        except Exception as error:
            # run_job normally returns a saved failed result. This catches only
            # unexpected failures before it can return its result contract.
            with self.lock:
                self.tasks[task_id]["state"] = "failed"
                self.tasks[task_id]["error"] = f"Conversion could not finish: {type(error).__name__}"
        finally:
            staging.cleanup()

    def get(self, task_id: str):
        with self.lock:
            task = self.tasks.get(task_id)
            return task.copy() if task else None


tasks = TaskManager()


def readiness():
    """Report actionable local prerequisites without exposing a secret."""
    missing = []
    if not os.environ.get("ANTHROPIC_API_KEY"):
        missing.append("Set ANTHROPIC_API_KEY in the server environment.")
    if any(importlib.util.find_spec(package) is None
           for package in ("chromadb", "sentence_transformers", "anthropic")):
        missing.append("Install the Python packages in requirements.txt.")
    if not VECTOR_DB.is_dir() or not any(VECTOR_DB.iterdir()):
        missing.append("Ingest the local RAG corpus into corpus/vectordb/.")
    return {"ready": not missing, "issues": missing}


def validate_name(filename: str | None, allowed_extensions: set[str]):
    if not filename or not UPLOAD_NAME_PATTERN.fullmatch(filename):
        raise HTTPException(400, "Use a plain filename with no directories or spaces.")
    if filename in {".", ".."} or Path(filename).suffix.lower() not in allowed_extensions:
        raise HTTPException(400, f"Unsupported file type: {filename}")
    return filename


async def save_upload(upload: UploadFile, destination: Path, limit: int):
    """Copy a bounded upload before the request closes its file handle."""
    size = 0
    with destination.open("wb") as target:
        while chunk := await upload.read(64 * 1024):
            size += len(chunk)
            if size > limit:
                destination.unlink(missing_ok=True)
                raise HTTPException(413, f"File is too large: {upload.filename}")
            target.write(chunk)
    if size == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, f"File is empty: {upload.filename}")


def require_run(run_id: str):
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise HTTPException(404, "Run not found")
    result = get_run(run_id.lower(), OUTPUT_DIR / "sessions.db")
    if result is None:
        raise HTTPException(404, "Run not found")
    return result


def run_folder(result: dict):
    folder = (OUTPUT_DIR / result["job_name"] / result["run_id"]).resolve()
    if not folder.is_relative_to(OUTPUT_DIR.resolve()):
        raise HTTPException(404, "Run not found")
    return folder


def registered_file(result: dict, stored_path: str | None,
                    expected_name: str | None = None, subfolder: str = ""):
    """Allow only a file inside the selected run, including copied checkouts."""
    if not stored_path:
        return None
    folder = run_folder(result) / subfolder
    stored_name = str(stored_path).replace("\\", "/").rsplit("/", 1)[-1]
    if expected_name and stored_name != expected_name:
        return None
    candidate = folder / stored_name
    if candidate.is_file() and candidate.resolve().is_relative_to(folder):
        return candidate
    return None


def browser_result(result: dict):
    """Expose the report fields without persisted absolute artifact paths."""
    artifact_keys = [
        key for key, filename in ARTIFACT_NAMES.items()
        if registered_file(result, result.get("artifacts", {}).get(key), filename)
    ]
    code_steps = [
        step for step, path in result.get("code_paths", {}).items()
        if registered_file(result, path, subfolder="pyspark")
    ]
    validations = [
        {key: value for key, value in item.items() if key != "code_path"}
        for item in result.get("validation_results", [])
    ]
    return {
        "run_id": result["run_id"],
        "job_name": result["job_name"],
        "status": result.get("status", "failed"),
        "steps": result.get("steps", []),
        "step_statuses": result.get("step_statuses", []),
        "errors": result.get("errors", []),
        "warnings": result.get("warnings", []),
        "review_steps": result.get("review_steps", []),
        "review_required": result.get("review_required", False),
        "manual_steps": result.get("manual_steps", []),
        "validation_method": result.get("validation_method"),
        "runtime_verified": result.get("runtime_verified"),
        "cross_step_validation": result.get("cross_step_validation"),
        "validation_results": validations,
        "artifacts": artifact_keys,
        "code_steps": code_steps,
    }


def recent_runs(limit: int = 30):
    """Read the local index, with saved result folders as a portable fallback."""
    database = OUTPUT_DIR / "sessions.db"
    summaries = []
    if database.is_file():
        try:
            with closing(sqlite3.connect(database)) as connection:
                rows = connection.execute(
                    "SELECT run_id, job_name, status, completed_at, result_json "
                    "FROM runs ORDER BY completed_at DESC LIMIT ?", (limit,)
                ).fetchall()
            for run_id, job_name, status, completed_at, result_json in rows:
                try:
                    saved = json.loads(result_json)
                except (ValueError, TypeError):
                    saved = {}
                summaries.append({
                    "run_id": run_id,
                    "job_name": job_name,
                    "status": status,
                    "completed_at": completed_at,
                    "review_required": bool(saved.get("review_required")),
                })
            return summaries
        except sqlite3.Error:
            pass
    for result_path in OUTPUT_DIR.glob("*/*/result.json"):
        try:
            saved = json.loads(result_path.read_text(encoding="utf-8"))
            run_id = saved["run_id"]
            if not RUN_ID_PATTERN.fullmatch(run_id):
                continue
            if saved["job_name"] != result_path.parent.parent.name:
                continue
            summaries.append({
                "run_id": run_id,
                "job_name": saved["job_name"],
                "status": saved.get("status", "failed"),
                "completed_at": datetime.fromtimestamp(
                    result_path.stat().st_mtime, timezone.utc
                ).isoformat(),
                "review_required": bool(saved.get("review_required")),
            })
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return sorted(summaries, key=lambda item: item["completed_at"], reverse=True)[:limit]


@app.get("/", include_in_schema=False)
@app.get("/tasks/{task_id}", include_in_schema=False)
@app.get("/runs/{run_id}", include_in_schema=False)
def page():
    return FileResponse(STATIC / "index.html", media_type="text/html")


@app.get("/api/health")
def health():
    return readiness()


@app.post("/api/tasks", status_code=202)
async def create_task(jcl: UploadFile = File(...),
                      cntl_files: list[UploadFile] | None = File(None)):
    current = readiness()
    if not current["ready"]:
        raise HTTPException(503, {"issues": current["issues"]})
    jcl_name = validate_name(jcl.filename, {".jcl"})
    members = cntl_files or []
    if len(members) > MAX_CNTL_FILES:
        raise HTTPException(400, f"Attach at most {MAX_CNTL_FILES} CNTL files.")
    names = set()
    for member in members:
        name = validate_name(member.filename, {".jcl", ".txt", ".cntl"})
        if name.casefold() in names:
            raise HTTPException(400, f"Duplicate CNTL filename: {name}")
        names.add(name.casefold())
    staging = tempfile.TemporaryDirectory(prefix="juia-upload-")
    stage_path = Path(staging.name)
    try:
        source = stage_path / jcl_name
        await save_upload(jcl, source, MAX_JCL_BYTES)
        cntl_folder = DEFAULT_CNTL
        if members:
            cntl_folder = stage_path / "cntl"
            cntl_folder.mkdir()
            for member in members:
                await save_upload(member, cntl_folder / member.filename, MAX_CNTL_BYTES)
        return tasks.submit(source, cntl_folder, staging)
    except Exception:
        staging.cleanup()
        raise


@app.get("/api/tasks/{task_id}")
def task_state(task_id: str):
    if not TASK_ID_PATTERN.fullmatch(task_id):
        raise HTTPException(404, "Task not found")
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    return task


@app.get("/api/runs")
def runs():
    return {"runs": recent_runs()}


@app.get("/api/runs/{run_id}")
def run_result(run_id: str):
    return browser_result(require_run(run_id))


@app.get("/runs/{run_id}/artifacts/{kind}")
def artifact(run_id: str, kind: str):
    expected = ARTIFACT_NAMES.get(kind)
    if expected is None:
        raise HTTPException(404, "Artifact not found")
    result = require_run(run_id)
    path = registered_file(result, result.get("artifacts", {}).get(kind), expected)
    if path is None:
        raise HTTPException(404, "Artifact not found")
    return FileResponse(path, filename=expected)


@app.get("/runs/{run_id}/code/{step_name}")
def code(run_id: str, step_name: str, download: bool = False):
    result = require_run(run_id)
    path = registered_file(result, result.get("code_paths", {}).get(step_name),
                           subfolder="pyspark")
    if path is None or path.suffix.lower() != ".py" or not re.fullmatch(r"[A-Za-z0-9_]+", step_name):
        raise HTTPException(404, "Code file not found")
    if download:
        return FileResponse(path, filename=path.name, media_type="text/x-python")
    return PlainTextResponse(path.read_text(encoding="utf-8"))
