"""Build API-free decoder responses from the current sample JCL for tests.

Integration tests need realistic step names, DD datasets, and control cards.
This helper derives them from TEST.jcl each time rather than maintaining a
second, manually edited decoded JSON fixture.
"""

import re
from pathlib import Path

from agent.output.diagram_generator import extract_files_from_dd
from agent.parser.jcl_splitter import load_jcl_from_file, split_jcl_into_steps
from agent.parser.utility_classifier import classify_steps


# Resolve fixtures from this file so tests work from any working directory.
ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "jcl/TEST.jcl"
CNTL = ROOT / "cntl"


def build_sample_steps():
    """Return parsed steps and minimal decoded responses for mocked API tests.

    The IDCAMS names come from its control cards; DD names come from the
    existing DD extractor. No saved output or manually maintained JSON is used.
    """
    # Use the same parser and classifier as production. CNTL points at the
    # real members referenced by the sample's external control cards.
    original = classify_steps(split_jcl_into_steps(
        load_jcl_from_file(str(SAMPLE)), cntl_folder=str(CNTL)
    ))
    decoded = []
    for step in original:
        # DD statements supply normal dataset edges. IDCAMS can name datasets
        # inside SYSIN instead, so extract those operations as well.
        inputs, outputs = extract_files_from_dd(step)
        sysin = step.get("sysin_content", "")
        if step["utility_type"] == "IDCAMS":
            outputs.extend(re.findall(
                r"DEFINE\s+CLUSTER[\s-]*\(NAME\(([^)]+)\)", sysin, re.I
            ))
            inputs.extend(re.findall(r"LISTCAT\s+ENTRIES\(([^)]+)\)", sysin, re.I))
        skipped = step["utility_type"] == "PROGRAM" or step["sysin_source"] == "dummy"
        # Keep only the fields consumed by the orchestration and output tests;
        # no API-generated business interpretation is being asserted here.
        decoded.append({
            "step_name": step["step_name"],
            "utility_type": step["utility_type"],
            "pgm": step.get("pgm", ""),
            "decode_status": "skipped" if skipped else "success",
            "business_rule": "Mocked decoder response for integration tests",
            "input_files": inputs,
            "output_files": outputs,
            "key_fields": [],
            "operations": [],
            "sysin_content": sysin,
        })
    return original, decoded
