"""Keep diagram and specification dataset facts consistent."""

import unittest

from agent.output.diagram_generator import generate_mermaid_diagram, get_step_files
from agent.output.md_writer import assemble_migration_spec, write_copy_step_section
from tests.sample_steps import build_sample_steps


class OutputDatasetContractTests(unittest.TestCase):
    def test_idcams_sysin_without_decoder_file_lists(self):
        original, decoded = build_sample_steps()
        self.assertEqual(decoded[0]["output_files"], [])
        self.assertEqual(decoded[-1]["input_files"], [])
        steps = [
            {"decoded": value, "original": source}
            for source, value in zip(original, decoded)
        ]
        datasets = ["WIRE.WORK.INPUT.KSDS", "WIRE.WORK.EXTRT.KSDS"]
        self.assertEqual(get_step_files(decoded[0], original[0])[1], datasets)
        self.assertEqual(get_step_files(decoded[-1], original[-1])[0], datasets)

        diagram = generate_mermaid_diagram("MYTEST", steps)
        self.assertEqual(diagram.count(" --> "), 20)
        self.assertIn("DS_WIRE_WORK_INPUT_KSDS --> STEP090", diagram)
        self.assertIn("DS_WIRE_WORK_EXTRT_KSDS --> STEP090", diagram)
        spec = assemble_migration_spec("MYTEST", "TEST.jcl", steps, {}, diagram, "missing")
        step_90 = spec.split("## STEP090 — IDCAMS", 1)[1]
        for dataset in datasets:
            self.assertIn(f"**Input:** `{dataset}`", step_90)

    def test_decoded_and_jcl_datasets_merge_once(self):
        original, decoded = build_sample_steps()
        decoded[-1]["input_files"] = ["WIRE.WORK.INPUT.KSDS"]
        inputs, outputs = get_step_files(decoded[-1], original[-1])
        self.assertEqual(inputs, ["WIRE.WORK.INPUT.KSDS", "WIRE.WORK.EXTRT.KSDS"])
        self.assertEqual(outputs, [])

    def test_documented_copy_is_not_reported_as_validated(self):
        original, decoded = build_sample_steps()
        copy_index = next(
            index for index, step in enumerate(decoded)
            if step["utility_type"] == "IEBGENER" and step["decode_status"] == "skipped"
        )
        section = write_copy_step_section({
            "decoded": decoded[copy_index], "original": original[copy_index]
        })
        self.assertIn("Not run (copy action documented)", section)
        self.assertNotIn("PASS (hardcoded)", section)


if __name__ == "__main__":
    unittest.main()
