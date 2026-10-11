"""The production generator must honor a run-specific output folder."""

import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class GeneratorPathTests(unittest.TestCase):
    def test_code_is_written_to_selected_run_folder(self):
        with patch.dict(sys.modules, {"anthropic": types.ModuleType("anthropic")}):
            generator = importlib.import_module("agent.generator.pyspark_generator")

        class FakeMessages:
            def create(self, **kwargs):
                return types.SimpleNamespace(
                    content=[types.SimpleNamespace(text="```python\nprint('ok')\n```")]
                )

        client = types.SimpleNamespace(messages=FakeMessages())
        with tempfile.TemporaryDirectory() as temp:
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
