"""Exhausted correction attempts retain review details without source backups."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.validator import self_correction_loop as loop


class CorrectionLoopTests(unittest.TestCase):
    def test_three_failed_corrections_require_review_without_backups(self):
        with tempfile.TemporaryDirectory() as temp:
            code_path = Path(temp) / "STEP070_DFSORT.py"
            code_path.write_text("original\n", encoding="utf-8")
            validations = []

            def validate(**kwargs):
                validations.append(code_path.read_text(encoding="utf-8"))
                return {
                    "step_name": "STEP070", "validation_status": "FAIL",
                    "validate_status": "success", "issues_found": ["Still disputed"],
                }

            def correct(**kwargs):
                return f"correction {kwargs['attempt_number']}\n"

            with patch.object(loop, "validate_generated_code", side_effect=validate), \
                    patch.object(loop, "request_correction", side_effect=correct):
                result = loop.run_correction_loop(
                    {"step_name": "STEP070", "utility_type": "DFSORT"},
                    {}, str(code_path), object(),
                )

            self.assertEqual(len(validations), 4)
            self.assertEqual(result["validation_status"], "REVIEW")
            self.assertEqual(result["last_validation_status"], "FAIL")
            self.assertTrue(result["review_required"])
            self.assertEqual(result["corrections_applied"], 3)
            self.assertEqual(len(result["correction_history"]), 3)
            self.assertEqual(code_path.read_text(encoding="utf-8"), "correction 3\n")
            self.assertEqual(list(Path(temp).glob("**/*.bak_*")), [])
            self.assertFalse((Path(temp) / "backups").exists())

    def test_failed_correction_request_remains_failed(self):
        with tempfile.TemporaryDirectory() as temp:
            code_path = Path(temp) / "STEP070_DFSORT.py"
            code_path.write_text("original\n", encoding="utf-8")
            with patch.object(loop, "validate_generated_code", return_value={
                    "validation_status": "FAIL", "validate_status": "success",
                    "issues_found": ["Issue"],
                }), patch.object(loop, "request_correction", return_value=None):
                result = loop.run_correction_loop(
                    {"step_name": "STEP070", "utility_type": "DFSORT"},
                    {}, str(code_path), object(),
                )
            self.assertEqual(result["validation_status"], "FAIL")
            self.assertEqual(result["corrections_applied"], 0)
            self.assertFalse(result.get("review_required", False))


if __name__ == "__main__":
    unittest.main()
