"""Regression checks for the sample job's output-to-diagram contract."""

import re
import unittest
from pathlib import Path

from agent.output.diagram_generator import generate_mermaid_diagram
from scripts.test_output import build_steps_data, load_decoded_results, load_original_steps


ROOT = Path(__file__).resolve().parents[1]


class LineageContractTests(unittest.TestCase):
    def test_sample_steps_and_dataset_edges(self):
        decoded, job_name = load_decoded_results(
            str(ROOT / "outputs/wire_payment_batch_decoded.json")
        )
        original = load_original_steps(
            str(ROOT / "sample_jcl/wire_payment_batch.jcl"),
            str(ROOT / "cntl"),
        )
        steps_data = build_steps_data(decoded, original)

        self.assertEqual(len(steps_data), 9)
        diagram = generate_mermaid_diagram(job_name, steps_data)
        step_nodes = re.findall(r'^\s*(STEP\d{3})\["', diagram, re.MULTILINE)
        self.assertEqual(step_nodes, [f"STEP{i:03d}" for i in range(10, 100, 10)])
        self.assertNotIn("UNKNOWN", diagram)
        self.assertEqual(diagram.count(" --> "), 20)
        for edge in (
            "DS_WIRE_DAILY_INPUT_SEQ --> STEP030",
            "STEP030 --> DS_WIRE_WORK_EXTRT_SEQ",
            "DS_WIRE_WORK_EXTRT_SEQ --> STEP040",
            "STEP040 --> DS_WIRE_WORK_HIGH_SEQ",
            "DS_WIRE_WORK_HIGH_SEQ --> STEP060",
            "STEP060 --> DS_WIRE_PROCESSED_SEQ",
            "DS_WIRE_PROCESSED_SEQ --> STEP070",
            "STEP070 --> DS_WIRE_SUMMARY_SEQ",
        ):
            with self.subTest(edge=edge):
                self.assertIn(edge, diagram)


if __name__ == "__main__":
    unittest.main()
