"""Failure reporting and portable run lookup at the Phase 7 boundary."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.main import run_job
from agent.memory.session_store import get_run


class RunIntegrityTests(unittest.TestCase):
    def test_run_can_be_retrieved_from_committed_json_without_sqlite(self):
        with tempfile.TemporaryDirectory() as folder:
            run_id = "a" * 32
            run_folder = Path(folder) / "MYJOB" / run_id
            run_folder.mkdir(parents=True)
            source_path = "/workspaces/juia/outputs/MYJOB/" + run_id + "/result.json"
            result = {
                "run_id": run_id, "job_name": "MYJOB", "status": "failed",
                "artifacts": {"result": source_path}, "code_paths": {},
                "validation_results": [],
            }
            (run_folder / "result.json").write_text(json.dumps(result), encoding="utf-8")

            loaded = get_run(run_id, Path(folder) / "sessions.db")
            self.assertEqual(loaded["status"], "failed")
            self.assertEqual(loaded["artifacts"]["result"], str(run_folder / "result.json"))
            self.assertIsNone(get_run("../not-a-run", Path(folder) / "sessions.db"))

    def test_sqlite_failure_returns_failed_result_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("agent.main.save_run", side_effect=OSError("disk unavailable")):
                result = run_job(Path(folder) / "missing.jcl", output_folder=folder)
            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("SQLite" in error for error in result["errors"]))
            saved = json.loads(Path(result["artifacts"]["result"]).read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "failed")
            self.assertEqual(saved["errors"], result["errors"])

    def test_step_decode_exception_preserves_other_step_statuses(self):
        class Decoder:
            api_client = object()

            def is_ready(self):
                return True

            def decode_step(self, step):
                if step["step_name"] == "S1":
                    raise RuntimeError("retrieval unavailable")
                return {
                    "step_name": step["step_name"],
                    "utility_type": step["utility_type"],
                    "decode_status": "success", "input_files": [],
                    "output_files": [], "operations": [], "key_fields": [],
                }

        def generate(decoded, original, client, output_folder):
            path = Path(output_folder) / "S2_DFSORT.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("def transform():\n    return None\n", encoding="utf-8")
            return str(path), "success"

        with tempfile.TemporaryDirectory() as folder:
            jcl = Path(folder) / "two.jcl"
            jcl.write_text(
                "//JOBX JOB 1\n"
                "//S1 EXEC PGM=SORT\n//SYSIN DD *\n SORT FIELDS=COPY\n/*\n"
                "//S2 EXEC PGM=SORT\n//SYSIN DD *\n SORT FIELDS=COPY\n/*\n",
                encoding="utf-8",
            )
            result = run_job(
                jcl, output_folder=Path(folder) / "outputs",
                decoder_factory=Decoder, generator=generate,
                validator=lambda decoded, original, path, client: {
                    "step_name": decoded["step_name"], "validation_status": "PASS"
                },
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual([s["decode_status"] for s in result["step_statuses"]],
                             ["error", "success"])
            self.assertEqual(result["step_statuses"][1]["validation_status"], "PASS")

    def test_unimplemented_program_step_is_incomplete(self):
        class Decoder:
            api_client = object()

            def is_ready(self):
                return True

            def decode_step(self, step):
                return {
                    "step_name": step["step_name"], "utility_type": "PROGRAM",
                    "decode_status": "skipped", "input_files": [], "output_files": [],
                }

        with tempfile.TemporaryDirectory() as folder:
            jcl = Path(folder) / "program.jcl"
            jcl.write_text("//JOBX JOB 1\n//S1 EXEC PGM=COBOLAPP\n", encoding="utf-8")
            result = run_job(
                jcl, output_folder=Path(folder) / "outputs",
                decoder_factory=Decoder,
                generator=lambda *args, **kwargs: self.fail("No code generation expected"),
            )
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(result["manual_steps"], ["S1"])

    def test_multiple_job_cards_are_rejected_before_decoding(self):
        with tempfile.TemporaryDirectory() as folder:
            jcl = Path(folder) / "two_jobs.jcl"
            jcl.write_text(
                "//JOB1 JOB 1\n//S1 EXEC PGM=SORT\n"
                "//JOB2 JOB 1\n//S2 EXEC PGM=SORT\n", encoding="utf-8",
            )
            result = run_job(jcl, output_folder=Path(folder) / "outputs")
            self.assertEqual(result["status"], "failed")
            self.assertIn("multiple JOB cards", result["errors"][0])

    def test_review_only_run_preserves_requested_success_contract(self):
        class Decoder:
            api_client = object()

            def is_ready(self):
                return True

            def decode_step(self, step):
                return {
                    "step_name": step["step_name"], "utility_type": "DFSORT",
                    "decode_status": "success", "input_files": [], "output_files": [],
                    "operations": [], "key_fields": [],
                }

        def generate(decoded, original, client, output_folder):
            path = Path(output_folder) / "S1_DFSORT.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("def transform():\n    return None\n", encoding="utf-8")
            return str(path), "success"

        with tempfile.TemporaryDirectory() as folder:
            jcl = Path(folder) / "review.jcl"
            jcl.write_text(
                "//JOBX JOB 1\n//S1 EXEC PGM=SORT\n"
                "//SYSIN DD *\n SORT FIELDS=COPY\n/*\n", encoding="utf-8",
            )
            result = run_job(
                jcl, output_folder=Path(folder) / "outputs",
                decoder_factory=Decoder, generator=generate,
                validator=lambda decoded, original, path, client: {
                    "step_name": "S1", "validation_status": "REVIEW",
                    "corrections_applied": 3,
                },
            )
            self.assertEqual(result["status"], "success")
            self.assertTrue(result["review_required"])
            self.assertEqual(result["manual_steps"], [])

    def test_generator_cannot_reuse_a_script_from_another_run(self):
        class Decoder:
            api_client = object()

            def is_ready(self):
                return True

            def decode_step(self, step):
                return {
                    "step_name": "S1", "utility_type": "DFSORT",
                    "decode_status": "success", "input_files": [], "output_files": [],
                    "operations": [], "key_fields": [],
                }

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jcl = root / "input.jcl"
            jcl.write_text(
                "//JOBX JOB 1\n//S1 EXEC PGM=SORT\n"
                "//SYSIN DD *\n SORT FIELDS=COPY\n/*\n", encoding="utf-8",
            )
            stale = root / "other-run.py"
            stale.write_text("def old(): pass\n", encoding="utf-8")
            result = run_job(
                jcl, output_folder=root / "outputs", decoder_factory=Decoder,
                generator=lambda *args, **kwargs: (str(stale), "success"),
                validator=lambda *args: self.fail("A stale file must not be validated"),
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["step_statuses"][0]["generation_status"], "invalid_artifact")

    def test_unsupported_job_control_stops_before_api_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            jcl = Path(folder) / "conditional.jcl"
            jcl.write_text(
                "//JOBX JOB 1\n"
                "// IF (S0.RC = 0) THEN\n"
                "//S1 EXEC PGM=SORT\n"
                "//SYSIN DD *\n SORT FIELDS=COPY\n/*\n"
                "// ENDIF\n",
                encoding="utf-8",
            )
            result = run_job(
                jcl, output_folder=Path(folder) / "outputs",
                decoder_factory=lambda: self.fail("Unsupported JCL must not reach the API"),
            )
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(result["manual_steps"], ["S1"])
            self.assertEqual(result["step_statuses"][0]["decode_status"], "not_run")
            self.assertTrue(any("IF" in warning for warning in result["warnings"]))

    def test_cross_step_conflict_overrides_step_model_pass(self):
        class Decoder:
            api_client = object()

            def is_ready(self):
                return True

            def decode_step(self, step):
                return {
                    "step_name": step["step_name"], "utility_type": "DFSORT",
                    "decode_status": "success", "input_files": [], "output_files": [],
                    "operations": [], "key_fields": [],
                }

        def generate(decoded, original, client, output_folder):
            path = Path(output_folder) / "S1_DFSORT.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("def transform():\n    return None\n", encoding="utf-8")
            return str(path), "success"

        cross_step_result = {
            "status": "FAIL", "issues": [{
                "code": "CONTRACT_CONFLICT", "severity": "error",
                "dataset": "WORK.KSDS", "contract": "storage_path",
                "steps": ["S1"], "message": "Conflicting storage path for WORK.KSDS",
            }],
        }
        with tempfile.TemporaryDirectory() as folder:
            jcl = Path(folder) / "job.jcl"
            jcl.write_text(
                "//JOBX JOB 1\n//S1 EXEC PGM=SORT\n"
                "//SYSIN DD *\n SORT FIELDS=COPY\n/*\n",
                encoding="utf-8",
            )
            with patch("agent.main.check_cross_step_contracts", return_value=cross_step_result):
                result = run_job(
                    jcl, output_folder=Path(folder) / "outputs",
                    decoder_factory=Decoder, generator=generate,
                    validator=lambda decoded, original, path, client: {
                        "step_name": "S1", "validation_status": "PASS",
                    },
                )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["step_statuses"][0]["validation_status"], "PASS")
            self.assertEqual(result["cross_step_validation"], cross_step_result)
            self.assertIn(cross_step_result["issues"][0]["message"], result["errors"])
            validation_report = json.loads(
                Path(result["artifacts"]["validation"]).read_text(encoding="utf-8")
            )
            self.assertEqual(validation_report["cross_step_validation"], cross_step_result)



if __name__ == "__main__":
    unittest.main()
