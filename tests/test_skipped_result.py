"""Keep unresolved utility steps distinguishable from custom programs.

The decoder's skipped-result contract preserves utility type and missing CNTL
information so the orchestrator can report an incomplete conversion.
"""

import importlib
import sys
import types
import unittest
from unittest.mock import patch


class SkippedResultTests(unittest.TestCase):
    """Check metadata returned when a utility cannot be decoded."""

    def test_unresolved_utility_is_not_program(self):
        """An unresolved DFSORT member stays a DFSORT step in the result."""
        # Stub the optional dependency because no API call is needed here.
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
        # These fields drive the user's incomplete-run warning and review UI.
        self.assertEqual(result["utility_type"], "DFSORT")
        self.assertEqual(result["decode_status"], "skipped")
        self.assertEqual(result["sysin_dsn"], "SYS2.CNTL(MISSING)")
        self.assertEqual(result["operations"], [])
        self.assertNotIn("Non-utility", result["business_rule"])


if __name__ == "__main__":
    unittest.main()
