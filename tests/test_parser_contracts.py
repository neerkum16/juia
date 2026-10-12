"""Check parser handoffs that can silently change a JCL job's meaning.

The tests use small JCL fragments and temporary CNTL folders. They verify
the parsed values consumed by decoding without using a model or API key.
"""

import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.parser.jcl_splitter import (
    detect_unsupported_jcl_constructs,
    split_jcl_into_steps,
)
from agent.parser.sort_control_validator import validate_sort_sum_layout


class ParserContractTests(unittest.TestCase):
    """Verify input data, control-card lookup, and unsupported JCL handling."""

    def test_inline_sortin_keeps_blank_records_and_trailing_spaces(self):
        # SORTIN contains data records, while SYSIN contains the sort cards.
        # Both DDs must retain their own text; a blank record is real input.
        jcl = (
            "//BATCH JOB CLASS=A\n"
            "//S1 EXEC PGM=SORT\n"
            "//SORTIN DD *\n"
            "ABC  \n"
            "\n"
            "/*\n"
            "//SYSIN DD *\n"
            " SORT FIELDS=COPY\n"
            "/*\n"
        )
        step = split_jcl_into_steps(jcl)[0]
        sortin, sysin = step["dd_statements"]
        self.assertTrue(sortin["is_inline"])
        self.assertEqual(sortin["inline_content"], "ABC  \n")
        self.assertEqual(sysin["inline_content"], " SORT FIELDS=COPY")
        self.assertEqual(step["sysin_content"], sysin["inline_content"])

    def test_custom_delimiter_keeps_double_slash_data(self):
        # With DLM, a record beginning // is data until the chosen marker.
        jcl = (
            "//S1 EXEC PGM=SORT\n"
            "//SORTIN DD *,DLM=@@\n"
            "//THIS IS AN INPUT RECORD\n"
            "@@\n"
            "//SYSIN DD *\n"
            " SORT FIELDS=COPY\n"
            "/*\n"
        )
        step = split_jcl_into_steps(jcl)[0]
        self.assertEqual(step["dd_statements"][0]["inline_content"],
                         "//THIS IS AN INPUT RECORD")
        self.assertEqual(step["sysin_content"], " SORT FIELDS=COPY")

    def test_external_dsn_after_disp_and_on_continuation(self):
        # A qualified library/member path keeps member identity even on
        # case-sensitive Codespaces filesystems.
        with tempfile.TemporaryDirectory() as folder:
            library = Path(folder) / "lib.cntl"
            library.mkdir()
            (library / "one.cntl").write_text(" SORT FIELDS=COPY\n", encoding="utf-8")
            jcl = (
                "//S1 EXEC PGM=SORT\n"
                "//SYSIN DD DISP=SHR,\n"
                "//  DSN=LIB.CNTL(ONE)\n"
            )
            step = split_jcl_into_steps(jcl, cntl_folder=folder)[0]
            self.assertEqual(step["sysin_source"], "external_resolved")
            self.assertEqual(step["sysin_dsn"], "LIB.CNTL(ONE)")
            self.assertEqual(step["sysin_content"], " SORT FIELDS=COPY\n")

    def test_exec_continuation_supplies_program(self):
        # PGM on a continuation belongs to the existing EXEC, not a new DD.
        jcl = "//S1 EXEC REGION=4M,\n//  PGM=IDCAMS\n//SYSIN DD DUMMY\n"
        step = split_jcl_into_steps(jcl)[0]
        self.assertEqual(step["pgm"], "IDCAMS")
        self.assertEqual(step["unsupported_constructs"], [])

    def test_duplicate_member_names_require_qualified_files(self):
        # A single bare ONE.cntl must not stand for two distinct PDS members.
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "ONE.cntl").write_text("WRONG\n", encoding="utf-8")
            jcl = (
                "//S1 EXEC PGM=SORT\n"
                "//SYSIN DD DSN=LIBA.CNTL(ONE)\n"
                "//S2 EXEC PGM=SORT\n"
                "//SYSIN DD DSN=LIBB.CNTL(ONE)\n"
            )
            steps = split_jcl_into_steps(jcl, cntl_folder=folder)
            self.assertEqual([s["sysin_source"] for s in steps],
                             ["external_unresolved", "external_unresolved"])
            self.assertIn("ambiguous", steps[0]["sysin_warning"])

            # Qualified files restore unambiguous resolution for each library.
            (Path(folder) / "LIBA.CNTL(ONE).cntl").write_text("A\n", encoding="utf-8")
            (Path(folder) / "LIBB.CNTL(ONE).cntl").write_text("B\n", encoding="utf-8")
            steps = split_jcl_into_steps(jcl, cntl_folder=folder)
            self.assertEqual([s["sysin_content"] for s in steps], ["A\n", "B\n"])

    def test_unsupported_job_control_is_explicit(self):
        # The splitter cannot expand a PROC or evaluate JCL conditionals.
        # Return an explicit guard for the orchestrator to mark incomplete.
        jcl = (
            "//ONE JOB CLASS=A\n"
            "//S1 EXEC PROC=PAYPROC,COND=(4,LT)\n"
            "// INCLUDE MEMBER=EXTRA\n"
            "// IF (S1.RC = 0) THEN\n"
            "//S2 EXEC PGM=SORT\n"
            "//SYSIN DD DSN=&CNTL\n"
            "//TWO JOB CLASS=A\n"
        )
        issues = detect_unsupported_jcl_constructs(jcl)
        self.assertTrue(any("multiple JOB" in item or "2 JOB" in item for item in issues))
        self.assertTrue(any("procedure" in item for item in issues))
        self.assertTrue(any("INCLUDE" in item for item in issues))
        self.assertTrue(any("IF statement" in item for item in issues))
        self.assertTrue(any("symbolic" in item for item in issues))

    def test_system_symbol_and_temp_dataset_are_supported(self):
        # &SYSUID is a system substitution; &&WORK is a temporary dataset.
        jcl = "//ONE JOB NOTIFY=&SYSUID\n//S1 EXEC PGM=SORT\n//SORTIN DD DSN=&&WORK\n"
        self.assertEqual(detect_unsupported_jcl_constructs(jcl), [])

    def test_job_cards_inside_inline_data_do_not_change_job_identity(self):
        jcl = (
            "//REALJOB JOB 1\n"
            "//S1 EXEC PGM=SORT\n"
            "//SORTIN DD *,DLM=@@\n"
            "//FAKE JOB THIS IS AN INPUT RECORD\n"
            "@@\n"
            "//SYSIN DD *\n SORT FIELDS=COPY\n/*\n"
        )
        job_cards = []
        self.assertEqual(detect_unsupported_jcl_constructs(jcl, job_cards), [])
        self.assertEqual(job_cards, ["REALJOB"])

    def test_unparsed_sort_sum_card_requires_review(self):
        # A complex list must not be treated as an empty sort-key list.
        step = {
            "step_name": "S1", "utility_type": "DFSORT",
            "sysin_content": "SORT FIELDS=(1,5,CH,A,FORMAT=CH)\nSUM FIELDS=(1,5,ZD)",
        }
        validate_sort_sum_layout([step])
        self.assertIn("could not be prevalidated", step["unsupported_constructs"][0])

    def test_simple_sort_sum_overlap_is_rejected(self):
        step = {
            "step_name": "S1", "utility_type": "DFSORT",
            "sysin_content": "SORT FIELDS=(1,5,CH,A)\nSUM FIELDS=(1,5,ZD)",
        }
        with self.assertRaisesRegex(ValueError, "overlaps SUM field"):
            validate_sort_sum_layout([step])

    def test_icetool_resize_query_uses_specific_terms(self):
        # Stub optional libraries: query construction itself needs neither
        # ChromaDB nor an embedding model installed in the test environment.
        fake_transformers = types.SimpleNamespace(SentenceTransformer=object)
        with patch.dict(sys.modules, {
            "chromadb": types.SimpleNamespace(),
            "sentence_transformers": fake_transformers,
        }):
            retriever = importlib.import_module("agent.decoder.rag_retriever")
        query = retriever.build_rag_query(
            "ICETOOL", "RESIZE FROM(IN1) TO(OUT1) TOLEN(30)"
        )
        self.assertIn("RESIZE", query)
        self.assertIn("TOLEN", query)


if __name__ == "__main__":
    unittest.main()
