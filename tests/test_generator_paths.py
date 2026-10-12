"""Check that code generation writes only to the caller's run folder.

The fake Claude response avoids an API call while exercising the real prompt,
response cleanup, and file saver used by the orchestrator.
"""

import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class GeneratorPathTests(unittest.TestCase):
    """Guard the output path contract between orchestration and generation."""

    def test_code_is_written_to_selected_run_folder(self):
        """Save generated code at the path supplied by the active run."""
        # The generator imports anthropic lazily; a stub keeps this test
        # independent of whether that package is installed locally.
        with patch.dict(sys.modules, {"anthropic": types.ModuleType("anthropic")}):
            generator = importlib.import_module("agent.generator.pyspark_generator")

        class FakeMessages:
            """Mimic the messages endpoint used by code generation."""

            def create(self, **kwargs):
                """Return one fenced Python code block without an API call."""
                # Mimic Claude's fenced response so cleanup is tested too.
                return types.SimpleNamespace(
                    content=[types.SimpleNamespace(text="```python\nprint('ok')\n```")]
                )

        client = types.SimpleNamespace(messages=FakeMessages())
        with tempfile.TemporaryDirectory() as temp:
            # A nested folder catches accidental writes to shared outputs/.
            folder = Path(temp) / "one-run" / "pyspark"
            path, status = generator.generate_pyspark_code(
                {"step_name": "STEP030", "utility_type": "DFSORT", "business_rule": "Sort records"},
                {"sysin_content": "SORT FIELDS=COPY", "dd_statements": []},
                client,
                output_folder=str(folder),
            )
            self.assertEqual(status, "success")
            self.assertEqual(Path(path), folder / "STEP030_DFSORT.py")
            self.assertEqual(Path(path).read_text(encoding="utf-8"), "print('ok')")


if __name__ == "__main__":
    unittest.main()
