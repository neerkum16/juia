"""Integration checks for the job-level phase handoffs, without API calls."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.main import run_job
from agent.memory.session_store import get_run
from tests.sample_steps import build_sample_steps


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "jcl/TEST.jcl"
_, DECODED = build_sample_steps()


class FakeDecoderSession:
    api_client = object()

    def is_ready(self):
        return True

    def decode_all_steps(self, steps):
        assert [step["step_name"] for step in steps] == [
            decoded["step_name"] for decoded in DECODED
        ]
        return DECODED


class OrchestratorTests(unittest.TestCase):
    def test_sample_runs_through_all_phases(self):
        with tempfile.TemporaryDirectory() as temp:
            generated = []
            validated = []
            legacy = Path(temp) / "outputs/pyspark"
            legacy.mkdir(parents=True)
            (legacy / "STEP030_DFSORT.validated").write_text("PASS", encoding="utf-8")
            (Path(temp) / "outputs/validation_report.json").write_text(
                '{"steps":[{"step_name":"STEP030","validation_status":"PASS"}]}',
                encoding="utf-8",
            )

            def generate(decoded, original, client, output_folder):
                self.assertEqual(decoded["step_name"], original["step_name"])
                code_folder = Path(output_folder)
                code_folder.mkdir(parents=True, exist_ok=True)
                path = code_folder / f'{decoded["step_name"]}_{decoded["utility_type"]}.py'
                path.write_text("# generated test code\n", encoding="utf-8")
                generated.append(decoded["step_name"])
                return str(path), "success"

            def validate(decoded, original, code_path, client):
                self.assertTrue(Path(code_path).is_file())
                validated.append(decoded["step_name"])
                return {"step_name": decoded["step_name"], "validation_status": "PASS"}

            result = run_job(
                SAMPLE, ROOT / "cntl", Path(temp) / "outputs",
                decoder_factory=FakeDecoderSession,
                generator=generate,
                validator=validate,
            )
            self.assertEqual(result["job_name"], "MYTEST")
            self.assertEqual(len(result["steps"]), 9)
            self.assertEqual(len(generated), 6)
            self.assertEqual(validated, generated)
            self.assertEqual(len(result["validation_results"]), 6)
            for artifact in result["artifacts"].values():
                self.assertTrue(Path(artifact).is_file())
            diagram = Path(result["artifacts"]["diagram"]).read_text(encoding="utf-8")
            self.assertIn("STEP030 --> DS_WIRE_WORK_EXTRT_SEQ", diagram)
            self.assertEqual(diagram.count(" --> "), 20)
            stored = get_run(result["run_id"], Path(temp) / "outputs/sessions.db")
            self.assertEqual(stored["status"], "success")
            self.assertEqual(stored["artifacts"], result["artifacts"])
            self.assertEqual(
                json.loads(Path(result["artifacts"]["result"]).read_text(encoding="utf-8")),
                result,
            )

            second = run_job(
                SAMPLE, ROOT / "cntl", Path(temp) / "outputs",
                decoder_factory=FakeDecoderSession,
                generator=generate,
                validator=validate,
            )
            self.assertNotEqual(result["run_id"], second["run_id"])
            self.assertNotEqual(result["artifacts"]["validation"], second["artifacts"]["validation"])
            self.assertNotEqual(result["code_paths"]["STEP030"], second["code_paths"]["STEP030"])
            self.assertEqual(len(generated), 12)
            self.assertEqual(len(validated), 12)
            self.assertEqual(len(second["validation_results"]), 6)
            self.assertTrue(Path(result["artifacts"]["validation"]).is_file())
            self.assertEqual(
                get_run(second["run_id"], Path(temp) / "outputs/sessions.db")["run_id"],
                second["run_id"],
            )
            self.assertIsNone(get_run("missing", Path(temp) / "outputs/sessions.db"))

    def test_jcl_job_name_controls_output_path(self):
        with tempfile.TemporaryDirectory() as temp:
            alternate = Path(temp) / "alternate.jcl"
            source = SAMPLE.read_text(encoding="utf-8")
            alternate.write_text(source.replace("//MYTEST  JOB", "//ALTJOB   JOB", 1), encoding="utf-8")
            result = run_job(
                alternate, ROOT / "cntl", Path(temp) / "outputs",
                decoder_factory=FakeDecoderSession,
                generator=lambda decoded, original, client, output_folder: (None, "api_error"),
                validator=lambda *args: self.fail("Failed code must not be validated"),
            )
            self.assertEqual(result["job_name"], "ALTJOB")
            self.assertIn("ALTJOB", result["artifacts"]["diagram"])
            self.assertNotIn("MYTEST", result["artifacts"]["diagram"])

    def test_generation_failure_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_job(
                SAMPLE, ROOT / "cntl", temp,
                decoder_factory=FakeDecoderSession,
                generator=lambda decoded, original, client, output_folder: (None, "api_error"),
                validator=lambda *args: self.fail("Failed code must not be validated"),
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(len(result["errors"]), 6)
            self.assertEqual(result["validation_results"], [])
            self.assertEqual(result["step_statuses"][0]["generation_status"], "api_error")
            self.assertEqual(
                get_run(result["run_id"], Path(temp) / "sessions.db")["status"],
                "failed",
            )

    def test_setup_failure_is_retrievable(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_job(
                Path(temp) / "missing.jcl", ROOT / "cntl", temp,
                decoder_factory=FakeDecoderSession,
                generator=lambda *args, **kwargs: self.fail("No job should run"),
                validator=lambda *args: self.fail("No job should run"),
            )
            self.assertEqual(result["status"], "failed")
            self.assertTrue(result["errors"])
            self.assertTrue(Path(result["artifacts"]["result"]).is_file())
            self.assertEqual(
                get_run(result["run_id"], Path(temp) / "sessions.db")["status"],
                "failed",
            )

    def test_overlapping_sort_and_sum_fields_fail_before_decoding(self):
        with tempfile.TemporaryDirectory() as temp:
            invalid = Path(temp) / "invalid.jcl"
            invalid.write_text(
                SAMPLE.read_text(encoding="utf-8").replace(
                    "SORT FIELDS=(63,3,CH,A)\n  SUM FIELDS=(17,9,PD)",
                    "SORT FIELDS=(63,3,CH,A,17,9,PD,D)\n  SUM FIELDS=(17,9,PD)",
                ),
                encoding="utf-8",
            )
            result = run_job(
                invalid, ROOT / "cntl", Path(temp) / "outputs",
                decoder_factory=lambda: self.fail("Invalid JCL must not be decoded"),
            )
            self.assertEqual(result["status"], "failed")
            self.assertIn("STEP070", result["errors"][0])
            self.assertIn("overlaps SUM field", result["errors"][0])
            self.assertTrue(Path(result["artifacts"]["result"]).is_file())

    def test_missing_decoder_dependency_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict("sys.modules", {"agent.decoder.step_decoder": None}):
                result = run_job(
                    SAMPLE, ROOT / "cntl", temp,
                    generator=lambda *args, **kwargs: self.fail("No job should run"),
                    validator=lambda *args: self.fail("No job should run"),
                )
            self.assertEqual(result["status"], "failed")
            self.assertIn("decoder", result["errors"][0].lower())
            self.assertEqual(
                get_run(result["run_id"], Path(temp) / "sessions.db")["status"],
                "failed",
            )

    def test_decoder_error_cannot_pass(self):
        class ErrorDecoder(FakeDecoderSession):
            def decode_all_steps(self, steps):
                results = [dict(decoded) for decoded in DECODED]
                results[0].update(decode_status="api_error", notes="API unavailable")
                return results

        with tempfile.TemporaryDirectory() as temp:
            result = run_job(
                SAMPLE, ROOT / "cntl", temp,
                decoder_factory=ErrorDecoder,
                generator=lambda decoded, original, client, output_folder: (None, "api_error"),
                validator=lambda *args: self.fail("Failed code must not be validated"),
            )
            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("STEP010: decode status api_error" in e for e in result["errors"]))
            self.assertEqual(result["step_statuses"][0]["generation_status"], "not_run")

    def test_failed_validation_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            def generate(decoded, original, client, output_folder):
                path = Path(output_folder) / f'{decoded["step_name"]}_{decoded["utility_type"]}.py'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# generated\n", encoding="utf-8")
                return str(path), "success"

            result = run_job(
                SAMPLE, ROOT / "cntl", temp,
                decoder_factory=FakeDecoderSession,
                generator=generate,
                validator=lambda decoded, original, path, client: {
                    "step_name": decoded["step_name"], "validation_status": "FAIL"
                },
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(len(result["validation_results"]), 6)
            self.assertEqual(len(result["errors"]), 6)

    def test_exhausted_validation_review_keeps_run_successful(self):
        with tempfile.TemporaryDirectory() as temp:
            def generate(decoded, original, client, output_folder):
                path = Path(output_folder) / f'{decoded["step_name"]}_{decoded["utility_type"]}.py'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# generated\n", encoding="utf-8")
                return str(path), "success"

            def validate(decoded, original, path, client):
                if decoded["step_name"] == "STEP070":
                    return {
                        "step_name": "STEP070", "validation_status": "REVIEW",
                        "last_validation_status": "FAIL", "review_required": True,
                        "corrections_applied": 3, "issues_found": ["Needs review"],
                    }
                return {"step_name": decoded["step_name"], "validation_status": "PASS"}

            result = run_job(
                SAMPLE, ROOT / "cntl", temp,
                decoder_factory=FakeDecoderSession,
                generator=generate,
                validator=validate,
            )
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["review_steps"], ["STEP070"])
            self.assertTrue(result["review_required"])
            self.assertEqual(result["errors"], [])
            self.assertTrue(any("STEP070" in item for item in result["warnings"]))
            self.assertEqual(result["step_statuses"][6]["validation_status"], "REVIEW")
            spec = Path(result["artifacts"]["spec"]).read_text(encoding="utf-8")
            self.assertIn("5/6 PASS, 1 REVIEW", spec)
            self.assertIn("Human review required", spec)
            self.assertEqual(
                get_run(result["run_id"], Path(temp) / "sessions.db")["review_steps"],
                ["STEP070"],
            )

    def test_unresolved_cntl_marks_run_incomplete(self):
        class MissingCntlDecoder(FakeDecoderSession):
            def decode_all_steps(self, steps):
                results = [dict(decoded) for decoded in DECODED]
                for step, decoded in zip(steps, results):
                    if step["sysin_source"] == "external_unresolved":
                        decoded.update(decode_status="skipped", notes="External SYSIN unresolved")
                return results

        with tempfile.TemporaryDirectory() as temp:
            def generate(decoded, original, client, output_folder):
                path = Path(output_folder) / f'{decoded["step_name"]}_{decoded["utility_type"]}.py'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# generated\n", encoding="utf-8")
                return str(path), "success"

            result = run_job(
                SAMPLE, Path(temp) / "empty_cntl", temp,
                decoder_factory=MissingCntlDecoder,
                generator=generate,
                validator=lambda decoded, original, path, client: {
                    "step_name": decoded["step_name"], "validation_status": "PASS"
                },
            )
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(len(result["warnings"]), 2)
            self.assertTrue(any("STEP030" in warning for warning in result["warnings"]))
            self.assertTrue(any("STEP040" in warning for warning in result["warnings"]))
            spec = Path(result["artifacts"]["spec"]).read_text(encoding="utf-8")
            self.assertIn("## STEP030 — DFSORT\n\n**Decode status:** skipped", spec)


if __name__ == "__main__":
    unittest.main()
