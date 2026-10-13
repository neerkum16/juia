"""Check the Phase 8 web boundary without an API key or vector database."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from web.app import app


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "outputs"
        self.client = TestClient(app)

    def test_upload_report_and_registered_files(self):
        seen = {}
        run_id = "a" * 32

        def fake_run(jcl_path, cntl_folder, output_folder):
            seen["jcl"] = jcl_path.read_text(encoding="utf-8")
            seen["cntl"] = (cntl_folder / "WIREEXT.jcl").read_text(encoding="utf-8")
            folder = Path(output_folder) / "DEMO" / run_id
            code_folder = folder / "pyspark"
            code_folder.mkdir(parents=True)
            code_path = code_folder / "STEP010_DFSORT.py"
            code_path.write_text("# generated test code\n", encoding="utf-8")
            spec_path = folder / "migration_spec.md"
            spec_path.write_text("# Migration spec\n", encoding="utf-8")
            result = {
                "run_id": run_id, "job_name": "DEMO", "status": "success",
                "steps": [{"step_name": "STEP010", "business_rule": "Sort records."}],
                "step_statuses": [{"step_name": "STEP010", "utility_type": "DFSORT",
                                   "decode_status": "success", "generation_status": "success",
                                   "validation_status": "PASS"}],
                "errors": [], "warnings": [], "review_steps": [], "review_required": False,
                "validation_results": [{"step_name": "STEP010", "validation_status": "PASS",
                                        "code_path": str(code_path)}],
                "artifacts": {"spec": str(spec_path), "result": str(folder / "result.json")},
                "code_paths": {"STEP010": str(code_path)},
            }
            (folder / "result.json").write_text(json.dumps(result), encoding="utf-8")
            return result

        with patch("web.app.OUTPUT_DIR", self.output), \
             patch("web.app.readiness", return_value={"ready": True, "issues": []}), \
             patch("web.app.run_job", side_effect=fake_run):
            response = self.client.post("/api/tasks", files=[
                ("jcl", ("DEMO.jcl", b"//DEMO JOB\n//STEP010 EXEC PGM=SORT\n", "text/plain")),
                ("cntl_files", ("WIREEXT.jcl", b"SORT FIELDS=COPY\n", "text/plain")),
            ])
            self.assertEqual(response.status_code, 202)
            task_id = response.json()["task_id"]
            for _ in range(100):
                task = self.client.get(f"/api/tasks/{task_id}").json()
                if task["state"] == "completed":
                    break
                time.sleep(.02)
            self.assertEqual(task["state"], "completed")
            self.assertEqual(task["run_id"], run_id)
            self.assertIn("//DEMO JOB", seen["jcl"])
            self.assertEqual(seen["cntl"], "SORT FIELDS=COPY\n")

            report = self.client.get(f"/api/runs/{run_id}")
            self.assertEqual(report.status_code, 200)
            body = report.json()
            self.assertEqual(body["artifacts"], ["spec", "result"])
            self.assertEqual(body["code_steps"], ["STEP010"])
            self.assertNotIn("code_path", body["validation_results"][0])
            self.assertNotIn(str(self.output), report.text)
            self.assertEqual(self.client.get(f"/runs/{run_id}/code/STEP010").text,
                             "# generated test code\n")
            download = self.client.get(f"/runs/{run_id}/code/STEP010?download=true")
            self.assertIn("attachment", download.headers["content-disposition"])
            self.assertEqual(self.client.get(f"/runs/{run_id}/artifacts/spec").status_code, 200)
            self.assertEqual(self.client.get(f"/runs/{run_id}/artifacts/unknown").status_code, 404)
            self.assertEqual(self.client.get("/api/runs").json()["runs"][0]["run_id"], run_id)

    def test_upload_validation_and_missing_readiness(self):
        with patch("web.app.readiness", return_value={"ready": False, "issues": ["Missing corpus"]}):
            response = self.client.post("/api/tasks", files={"jcl": ("DEMO.jcl", b"//DEMO JOB\n")})
            self.assertEqual(response.status_code, 503)
        with patch("web.app.readiness", return_value={"ready": True, "issues": []}):
            response = self.client.post("/api/tasks", files={"jcl": ("bad.txt", b"hello")})
            self.assertEqual(response.status_code, 400)
            response = self.client.post("/api/tasks", files={"jcl": ("../bad.jcl", b"hello")})
            self.assertEqual(response.status_code, 400)
            response = self.client.post("/api/tasks", files={"jcl": ("empty.jcl", b"")})
            self.assertEqual(response.status_code, 400)

    def test_old_result_shape_and_outside_artifact(self):
        run_id = "b" * 32
        folder = self.output / "OLD" / run_id
        folder.mkdir(parents=True)
        outside = Path(self.temp.name) / "secret.txt"
        outside.write_text("do not serve", encoding="utf-8")
        result = {
            "run_id": run_id, "job_name": "OLD", "status": "failed",
            "steps": [], "step_statuses": [], "errors": ["Input missing"],
            "artifacts": {"spec": str(outside), "result": str(folder / "result.json")},
            "code_paths": {},
        }
        (folder / "result.json").write_text(json.dumps(result), encoding="utf-8")
        with patch("web.app.OUTPUT_DIR", self.output):
            body = self.client.get(f"/api/runs/{run_id}").json()
            self.assertIsNone(body["runtime_verified"])
            self.assertEqual(body["artifacts"], ["result"])
            self.assertEqual(self.client.get(f"/runs/{run_id}/artifacts/spec").status_code, 404)
            self.assertEqual(self.client.get(f"/runs/{run_id}/artifacts/result").status_code, 200)
            self.assertEqual(self.client.get("/api/runs/not-a-run").status_code, 404)
            self.assertEqual(self.client.get(f"/api/runs/{run_id.upper()}").status_code, 200)


if __name__ == "__main__":
    unittest.main()
