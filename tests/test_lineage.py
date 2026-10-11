"""Regression checks for the sample job's output-to-diagram contract."""

import re
import unittest

from agent.output.diagram_generator import generate_mermaid_diagram
from tests.sample_steps import build_sample_steps


class LineageContractTests(unittest.TestCase):
    def test_sample_steps_and_dataset_edges(self):
        original, decoded = build_sample_steps()
        steps_data = [
            {"decoded": decoded_step, "original": original_step}
            for original_step, decoded_step in zip(original, decoded)
        ]

        self.assertEqual(len(steps_data), 9)
        diagram = generate_mermaid_diagram("WIREPAY", steps_data)
        step_nodes = re.findall(r'^\s*(STEP\d{3})\["', diagram, re.MULTILINE)
        self.assertEqual(step_nodes, [f"STEP{i:03d}" for i in range(10, 100, 10)])
        self.assertNotIn("UNKNOWN", diagram)
        self.assertEqual(diagram.count(" --> "), 20)
        self.assertIn('WIRE.PARMLIB(WIRECNTL)', diagram)
        self.assertNotIn('WIRE.PARMLIB(WIRECNTL"', diagram)
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
