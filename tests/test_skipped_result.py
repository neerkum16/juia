"""Utility skips must not be described as custom program steps."""

import importlib
import sys
import types
import unittest
from unittest.mock import patch


class SkippedResultTests(unittest.TestCase):
    def test_unresolved_utility_is_not_program(self):
        with patch.dict(sys.modules, {"anthropic": types.ModuleType("anthropic")}):
            decoder = importlib.import_module("agent.decoder.claude_decoder")
        result = decoder.build_skipped_result(
            {
                "step_name": "STEP030",
                "pgm": "SORT",
                "utility_type": "DFSORT",
                "sysin_source": "external_unresolved",
                "sysin_dsn": "SYS2.CNTL(MISSING)",
            },
            "External SYSIN unresolved: SYS2.CNTL(MISSING)",
        )
        self.assertEqual(result["utility_type"], "DFSORT")
        self.assertEqual(result["decode_status"], "skipped")
        self.assertEqual(result["sysin_dsn"], "SYS2.CNTL(MISSING)")
        self.assertEqual(result["operations"], [])
        self.assertNotIn("Non-utility", result["business_rule"])


if __name__ == "__main__":
    unittest.main()
