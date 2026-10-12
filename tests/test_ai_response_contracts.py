"""Exercise model-response boundaries without API, Chroma, or Spark calls."""

import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.validator import code_validator, self_correction_loop
from agent.validator.python_static_checks import check_python_source


def load_ai_module(name):
    """Load an API-facing module while keeping this suite dependency-free."""

    anthropic_stub = types.ModuleType("anthropic")
    for exception_name in ("APIConnectionError", "RateLimitError", "APIStatusError"):
        setattr(anthropic_stub, exception_name, type(exception_name, (Exception,), {}))
    with patch.dict(sys.modules, {"anthropic": anthropic_stub}):
        return importlib.import_module(name)


def fake_client(response, stop_reason="end_turn"):
    """Return a small stand-in for the messages.create API surface."""

    message = types.SimpleNamespace(
        content=[types.SimpleNamespace(text=response)], stop_reason=stop_reason,
    )
    return types.SimpleNamespace(messages=types.SimpleNamespace(
        create=lambda **kwargs: message,
    ))


def valid_decoding():
    return {
        "business_rule": "Copy records", "input_files": [], "output_files": [],
        "key_fields": [], "operations": [{"type": "COPY", "description": "Copy all"}],
        "pyspark_hint": "df", "decode_confidence": "HIGH", "notes": "",
    }


def valid_report():
    checks = {
        name: {"status": "PASS", "detail": "Checked against SYSIN"}
        for name in code_validator.REQUIRED_CHECKS
    }
    return {
        "overall_status": "PASS", "checks": checks,
        "synthetic_data_summary": "Fifteen conceptual records",
        "issues_found": [], "correction_needed": False, "correction_hint": "",
    }


class DecodeContractTests(unittest.TestCase):
    def setUp(self):
        self.decoder = load_ai_module("agent.decoder.claude_decoder")
        self.step = {"step_name": "S1", "utility_type": "DFSORT", "pgm": "SORT"}

    def test_complete_response_succeeds(self):
        result = self.decoder.decode_with_claude(
            self.step, "", fake_client(json.dumps(valid_decoding())),
        )
        self.assertEqual(result["decode_status"], "success")

    def test_missing_fields_and_wrong_types_are_parse_errors(self):
        for response in ('{}', json.dumps({**valid_decoding(), "input_files": "bad"})):
            with self.subTest(response=response):
                result = self.decoder.decode_with_claude(
                    self.step, "", fake_client(response),
                )
                self.assertEqual(result["decode_status"], "parse_error")
                self.assertIn("Invalid decoded response", result["notes"])

    def test_array_response_is_not_sliced_into_object(self):
        self.assertIsNone(self.decoder.parse_json_response(
            json.dumps([valid_decoding()]),
        ))

    def test_truncated_response_does_not_decode(self):
        result = self.decoder.decode_with_claude(
            self.step, "", fake_client(json.dumps(valid_decoding()), "max_tokens"),
        )
        self.assertNotEqual(result["decode_status"], "success")


class GenerationContractTests(unittest.TestCase):
    def setUp(self):
        self.generator = load_ai_module("agent.generator.pyspark_generator")
        self.decoded = {"step_name": "S1", "utility_type": "DFSORT"}
        self.original = {"sysin_content": "SORT FIELDS=COPY", "dd_statements": []}

    def test_invalid_syntax_or_missing_sys_import_is_not_saved(self):
        for code in ("def broken(:\n", "print(sys.argv[1])\n"):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temp:
                path, status = self.generator.generate_pyspark_code(
                    self.decoded, self.original, fake_client(code), temp,
                )
                self.assertIsNone(path)
                self.assertEqual(status, "code_error")
                self.assertEqual(list(Path(temp).glob("*.py")), [])

    def test_truncated_code_does_not_get_saved(self):
        with tempfile.TemporaryDirectory() as temp:
            path, status = self.generator.generate_pyspark_code(
                self.decoded, self.original,
                fake_client("print('prefix')", "max_tokens"), temp,
            )
            self.assertIsNone(path)
            self.assertEqual(status, "api_error")


class ValidationContractTests(unittest.TestCase):
    def test_partial_pass_is_error_and_complete_pass_is_accepted(self):
        partial = code_validator.build_validation_result(
            "S1", "DFSORT", "example.py", {"overall_status": "PASS"},
        )
        self.assertEqual(partial["validation_status"], "ERROR")
        self.assertIn("Incomplete validation report", partial["issues_found"][0])
        complete = code_validator.build_validation_result(
            "S1", "DFSORT", "example.py", valid_report(),
        )
        self.assertEqual(complete["validation_status"], "PASS")

    def test_failed_check_overrides_overall_pass(self):
        report = valid_report()
        report["checks"]["sort_logic"]["status"] = "FAIL"
        result = code_validator.build_validation_result("S1", "DFSORT", "example.py", report)
        self.assertEqual(result["validation_status"], "FAIL")

    def test_truncated_validation_is_error(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "S1.py"
            path.write_text("print('valid')\n", encoding="utf-8")
            result = code_validator.validate_generated_code(
                {"step_name": "S1", "utility_type": "DFSORT"}, {}, str(path),
                fake_client(json.dumps(valid_report()), "max_tokens"),
            )
            self.assertEqual(result["validation_status"], "ERROR")
            self.assertIn("truncated", result["issues_found"][0])

    def test_invalid_python_stops_before_model_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "S1.py"
            path.write_text("print(sys.argv[1])\n", encoding="utf-8")
            client = types.SimpleNamespace(messages=types.SimpleNamespace(
                create=lambda **kwargs: self.fail("Invalid code must not reach Claude"),
            ))
            result = code_validator.validate_generated_code(
                {"step_name": "S1", "utility_type": "DFSORT"}, {}, str(path), client,
            )
            self.assertEqual(result["validation_status"], "ERROR")
            self.assertIn("without importing sys", result["issues_found"][0])

    def test_static_semantic_findings_override_model_pass_for_correction(self):
        cases = (
            (
                {"step_name": "S1", "utility_type": "DFSORT", "sysin_content": ""},
                "INPUT_PATH = 'path/to/input'\n",
                "unconfigured path",
            ),
            (
                {"step_name": "S1", "utility_type": "DFSORT",
                 "sysin_content": "SORT FIELDS=(1,3,CH,A)\nSUM FIELDS=(4,5,PD)"},
                "from pyspark.sql import functions as F\n"
                "def transform(df):\n    return df.groupBy('currency').agg(F.first('account'))\n",
                "not deterministic",
            ),
            (
                {"step_name": "S1", "utility_type": "IDCAMS",
                 "sysin_content": "DELETE WORK.KSDS\nIF LASTCC > 8 THEN SET MAXCC = 0"},
                "def _drop_table_if_exists(spark):\n"
                "    try:\n        spark.sql('DROP TABLE IF EXISTS work')\n"
                "    except Exception:\n        pass\n",
                "suppresses all DROP/DELETE exceptions",
            ),
        )
        for original, code, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "S1.py"
                path.write_text(code, encoding="utf-8")
                result = code_validator.validate_generated_code(
                    {"step_name": "S1", "utility_type": original["utility_type"]},
                    original, str(path), fake_client(json.dumps(valid_report())),
                )
                self.assertEqual(result["validation_status"], "FAIL")
                self.assertTrue(result["correction_needed"])
                self.assertTrue(any(expected in issue for issue in result["issues_found"]))

    def test_static_preflight_catches_missing_sys_import(self):
        self.assertIn("without importing sys", check_python_source("print(sys.argv[1])"))
        self.assertEqual(check_python_source("import sys\nprint(sys.argv[1])"), "")
        self.assertIn("Undefined global name(s): F", check_python_source(
            "def transform(df):\n    return df.filter(F.col('x') > 0)\n"
        ))


class CorrectionSafetyTests(unittest.TestCase):
    def test_truncated_correction_is_not_saved(self):
        result = self_correction_loop.request_correction(
            {"step_name": "S1", "utility_type": "DFSORT"}, {}, "print('old')",
            {"issues_found": ["Wrong output"]}, 1,
            fake_client("print('partial')", "max_tokens"),
        )
        self.assertIsNone(result)

    def test_invalid_correction_preserves_original_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "S1.py"
            path.write_text("print('original')\n", encoding="utf-8")
            self.assertFalse(self_correction_loop.save_corrected_code("def broken(:", str(path)))
            self.assertEqual(path.read_text(encoding="utf-8"), "print('original')\n")
            self.assertTrue(self_correction_loop.save_corrected_code("print('fixed')\n", str(path)))
            self.assertEqual(path.read_text(encoding="utf-8"), "print('fixed')\n")
            self.assertEqual(list(Path(temp).glob(".correction-*")), [])

    def test_revalidation_error_stops_retries(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "S1.py"
            path.write_text("print('original')\n", encoding="utf-8")
            failed = {"validation_status": "FAIL", "validate_status": "success",
                      "issues_found": ["Wrong filter"]}
            errored = {"validation_status": "ERROR", "validate_status": "error",
                       "issues_found": ["API unavailable"]}
            with patch.object(self_correction_loop, "validate_generated_code",
                              side_effect=[failed, errored]) as validate, \
                    patch.object(self_correction_loop, "request_correction",
                                 return_value="print('fixed')\n") as correct:
                result = self_correction_loop.run_correction_loop(
                    {"step_name": "S1", "utility_type": "DFSORT"}, {},
                    str(path), object(),
                )
            self.assertEqual(validate.call_count, 2)
            self.assertEqual(correct.call_count, 1)
            self.assertEqual(result["validation_status"], "ERROR")


if __name__ == "__main__":
    unittest.main()
