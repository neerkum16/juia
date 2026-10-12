"""Regression checks for the three JCL inputs and ICETOOL control cards.

The repository holds three separate jobs. These tests ensure the parser keeps
their identities, passes TOOLIN to the decoder, and gives each orchestration
run its own artifacts without making API calls.
"""

import tempfile
import unittest
from pathlib import Path

from agent.main import get_job_name, run_job
from agent.output.diagram_generator import extract_files_from_dd
from agent.parser.jcl_splitter import split_jcl_into_steps
from agent.parser.utility_classifier import classify_steps


# Use the repository location, not the shell's working directory, for inputs.
ROOT = Path(__file__).resolve().parents[1]


class JclInputTests(unittest.TestCase):
    """Check the shipped JCL files and their integration boundaries."""

    def test_each_jcl_file_has_its_own_job_and_steps(self):
        """Read JOB cards and verify each file's expected step count."""
        expected = {
            "DATE.jcl": ("MYJOB2", 6),
            "IFTRAIL.jcl": ("MYJOB1", 3),
            "TEST.jcl": ("MYTEST", 9),
        }
        for filename, (job_name, count) in expected.items():
            with self.subTest(filename=filename):
                # Run the real parser and classifier against each JCL file.
                path = ROOT / "jcl" / filename
                content = path.read_text(encoding="utf-8")
                steps = classify_steps(split_jcl_into_steps(
                    content, cntl_folder=str(ROOT / "cntl")
                ))
                self.assertEqual(get_job_name(content, path), job_name)
                self.assertEqual(len(steps), count)
                # Step names must be unique within a job for result mapping.
                self.assertEqual(len({step["step_name"] for step in steps}), count)

    def test_icetool_inline_toolin_is_available_to_decoder(self):
        """Expose ICETOOL TOOLIN text through the established SYSIN fields."""
        content = (ROOT / "jcl" / "IFTRAIL.jcl").read_text(encoding="utf-8")
        steps = classify_steps(split_jcl_into_steps(content))
        icetool = next(step for step in steps if step["step_name"] == "COMB1")
        self.assertEqual(icetool["utility_type"], "ICETOOL")
        self.assertEqual(icetool["sysin_source"], "inline")
        self.assertEqual(
            icetool["sysin_content"].strip(), "RESIZE FROM(IN1) TO(OUT1) TOLEN(30)"
        )
        # Preserve the DD's own inline data as well as the common control
        # field consumed by the decoder.
        toolin = next(dd for dd in icetool["dd_statements"] if dd["ddname"] == "TOOLIN")
        self.assertTrue(toolin["is_inline"])
        self.assertEqual(toolin["inline_content"], icetool["sysin_content"])

    def test_icetool_external_toolin_uses_cntl_member(self):
        """Resolve an external TOOLIN member from the selected CNTL folder."""
        with tempfile.TemporaryDirectory() as folder:
            # An isolated member proves lookup uses the supplied folder.
            (Path(folder) / "OPS.cntl").write_text(
                "RESIZE FROM(IN1) TO(OUT1) TOLEN(30)\n", encoding="utf-8"
            )
            steps = split_jcl_into_steps(
                "//S1 EXEC PGM=ICETOOL\n"
                "//TOOLIN DD DSN=SYS2.CNTL(OPS),DISP=SHR\n",
                cntl_folder=folder,
            )
            self.assertEqual(steps[0]["sysin_source"], "external_resolved")
            self.assertEqual(steps[0]["sysin_dsn"], "SYS2.CNTL(OPS)")
            self.assertEqual(
                steps[0]["sysin_content"].strip(), "RESIZE FROM(IN1) TO(OUT1) TOLEN(30)"
            )

    def test_three_jobs_get_separate_results_without_api_calls(self):
        """Run all jobs with fake phases and inspect their output isolation."""
        class FakeDecoderSession:
            """Return one minimal decoded result per parsed step."""
            api_client = object()

            def is_ready(self):
                """Let orchestration proceed without external resources."""
                return True

            def decode_all_steps(self, steps):
                """Derive minimal decoded results from the real parsed steps."""
                decoded = []
                for step in steps:
                    # DD relationships come from the real parser; phase
                    # responses are fake so the test stays deterministic.
                    inputs, outputs = extract_files_from_dd(step)
                    skipped = step["utility_type"] == "PROGRAM" or (
                        step["utility_type"] == "IEBGENER"
                        and step["sysin_source"] == "dummy"
                    )
                    decoded.append({
                        "step_name": step["step_name"],
                        "utility_type": step["utility_type"],
                        "pgm": step.get("pgm", ""),
                        "decode_status": "skipped" if skipped else "success",
                        "business_rule": "Mocked decoder response",
                        "input_files": inputs,
                        "output_files": outputs,
                        "key_fields": [],
                        "operations": [],
                        "sysin_content": step["sysin_content"],
                    })
                return decoded

        def generate(decoded, original, client, output_folder):
            """Write a tiny file where the orchestrator requests it."""
            path = Path(output_folder) / f'{decoded["step_name"]}_{decoded["utility_type"]}.py'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# generated test code\n", encoding="utf-8")
            return str(path), "success"

        def validate(decoded, original, code_path, client):
            """Report PASS so this test isolates routing and persistence."""
            return {"step_name": decoded["step_name"], "validation_status": "PASS"}

        with tempfile.TemporaryDirectory() as folder:
            runs = []
            # A shared root output directory tests job/run nesting, while the
            # temporary folder ensures no artifacts remain after the test.
            for filename in ("DATE.jcl", "IFTRAIL.jcl", "TEST.jcl"):
                result = run_job(
                    ROOT / "jcl" / filename, ROOT / "cntl", folder,
                    decoder_factory=FakeDecoderSession,
                    generator=generate,
                    validator=validate,
                )
                self.assertEqual(result["status"], "success", result["errors"])
                self.assertTrue(Path(result["artifacts"]["result"]).is_file())
                runs.append(result)

            # ICETOOL COMB1 must make it through generation, and all three
            # jobs must have distinct identities and result files.
            self.assertEqual({run["job_name"] for run in runs}, {"MYJOB1", "MYJOB2", "MYTEST"})
            self.assertEqual(len({run["run_id"] for run in runs}), 3)
            self.assertEqual(len({run["artifacts"]["result"] for run in runs}), 3)
            self.assertIn("COMB1", runs[1]["code_paths"])


if __name__ == "__main__":
    unittest.main()
