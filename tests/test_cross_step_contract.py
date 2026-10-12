"""Check explicit cross-step contracts without importing generated Spark code."""

import tempfile
import unittest
from pathlib import Path

from agent.output.cross_step_contract import check_cross_step_contracts
from tests.sample_steps import build_sample_steps


class CrossStepContractTests(unittest.TestCase):
    def _check(self, first_code, second_code):
        original, decoded = build_sample_steps()
        steps = [
            {"original": original[index], "decoded": decoded[index]}
            for index in (0, 1)
        ]
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "STEP010_IDCAMS.py"
            second = Path(folder) / "STEP020_IDCAMS.py"
            first.write_text(first_code, encoding="utf-8")
            second.write_text(second_code, encoding="utf-8")
            return check_cross_step_contracts(
                steps, {"STEP010": str(first), "STEP020": str(second)}
            )

    def test_step010_step020_path_and_key_conflicts_fail(self):
        first = '''KEY_COLUMN = "wire_transfer_key"
INPUT_CLUSTER = Cluster(cluster_name="WIRE.WORK.INPUT.KSDS",
                        table_name="wire_work_input_ksds",
                        table_path="/mnt/delta/wire/work/input_ksds")
'''
        second = '''WIRE_RECORD_KEY = "wire_record_key"
KSDS_PRIMARY_KEY = [WIRE_RECORD_KEY]
TARGET_TABLE = "wire_work_input_ksds"
TARGET_PATH = "s3://bucket/wire/work/input/ksds"
'''
        report = self._check(first, second)
        self.assertEqual(report["status"], "FAIL")
        conflicts = [item for item in report["issues"]
                     if item["code"] == "CONTRACT_CONFLICT"]
        self.assertEqual(
            {(item["dataset"], item["contract"]) for item in conflicts},
            {("WIRE.WORK.INPUT.KSDS", "storage_path"),
             ("WIRE.WORK.INPUT.KSDS", "primary_key")},
        )

    def test_matching_explicit_contracts_have_no_conflict(self):
        first = '''KEY_COLUMN = "wire_transfer_key"
INPUT_CLUSTER = Cluster(cluster_name="WIRE.WORK.INPUT.KSDS",
                        table_name="wire_work_input_ksds",
                        table_path="/data/wire/work/input_ksds")
'''
        second = '''KSDS_PRIMARY_KEY = ["wire_transfer_key"]
TARGET_TABLE = "wire_work_input_ksds"
TARGET_PATH = "/data/wire/work/input_ksds/"
'''
        report = self._check(first, second)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["issues"], [])

    def test_missing_contracts_are_unverified_not_pass(self):
        report = self._check("pass\n", "pass\n")
        self.assertEqual(report["status"], "UNVERIFIED")
        self.assertTrue(any(item["code"] == "CONTRACT_UNVERIFIED"
                            for item in report["issues"]))

    def test_listcat_metadata_inspection_needs_no_physical_path(self):
        original, decoded = build_sample_steps()
        steps = [{"original": original[index], "decoded": decoded[index]}
                 for index in (0, 8)]
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "STEP010_IDCAMS.py"
            last = Path(folder) / "STEP090_IDCAMS.py"
            first.write_text(
                'INPUT_CLUSTER = Cluster(cluster_name="WIRE.WORK.INPUT.KSDS", '
                'table_path="/data/wire/work/input_ksds")\n',
                encoding="utf-8",
            )
            last.write_text("pass\n", encoding="utf-8")
            report = check_cross_step_contracts(
                steps, {"STEP010": str(first), "STEP090": str(last)}
            )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["issues"], [])

    def test_local_code_placeholders_are_detected_but_docstrings_ignored(self):
        original, decoded = build_sample_steps()
        steps = [{"original": original[3], "decoded": decoded[3]}]
        with tempfile.TemporaryDirectory() as folder:
            code_path = Path(folder) / "STEP040_DFSORT.py"
            code_path.write_text(
                '"""path/to/documentation-example"""\n'
                '# path/to/comment-example\n'
                'def main():\n'
                '    """path/to/function-example"""\n'
                '    INPUT_PATH = "path/to/wire_work_extrt_seq"\n',
                encoding="utf-8",
            )
            report = check_cross_step_contracts(
                steps, {"STEP040": str(code_path)}
            )
        self.assertEqual(report["status"], "FAIL")
        placeholders = [item for item in report["issues"]
                        if item["code"] == "PLACEHOLDER_PATH"]
        self.assertEqual(len(placeholders), 1)
        self.assertIn("path/to/wire_work_extrt_seq", placeholders[0]["message"])

    def test_dfsort_sum_cannot_carry_arbitrary_first_row(self):
        original, decoded = build_sample_steps()
        steps = [{"original": original[6], "decoded": decoded[6]}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "STEP070_DFSORT.py"
            path.write_text(
                'result = df.groupBy("currency").agg('
                'F.sum("amount"), F.first("account"))\n',
                encoding="utf-8",
            )
            report = check_cross_step_contracts(steps, {"STEP070": str(path)})
            self.assertEqual(report["status"], "FAIL")
            self.assertIn("NONDETERMINISTIC_CARRY",
                          {item["code"] for item in report["issues"]})

            path.write_text(
                'result = df.groupBy("currency").agg(F.sum("amount"))\n',
                encoding="utf-8",
            )
            clean = check_cross_step_contracts(steps, {"STEP070": str(path)})
            self.assertEqual(clean["status"], "PASS")

    def test_broad_drop_exception_after_idcams_condition_is_unverified(self):
        original, decoded = build_sample_steps()
        steps = [{"original": original[0], "decoded": decoded[0]}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "STEP010_IDCAMS.py"
            path.write_text(
                'def _drop_table_if_exists(spark):\n'
                '    try:\n'
                '        spark.sql("DROP TABLE IF EXISTS work")\n'
                '    except Exception:\n'
                '        pass\n',
                encoding="utf-8",
            )
            report = check_cross_step_contracts(steps, {"STEP010": str(path)})
            self.assertEqual(report["status"], "UNVERIFIED")
            self.assertIn("CONTROL_FLOW_UNVERIFIED",
                          {item["code"] for item in report["issues"]})

            path.write_text(
                'def _drop_table_if_exists(spark):\n'
                '    try:\n'
                '        spark.sql("DROP TABLE IF EXISTS work")\n'
                '    except Exception:\n'
                '        raise\n',
                encoding="utf-8",
            )
            clean = check_cross_step_contracts(steps, {"STEP010": str(path)})
            self.assertEqual(clean["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
