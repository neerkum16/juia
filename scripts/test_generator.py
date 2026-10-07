# =============================================================================
# scripts/test_generator.py
#
# PROJECT : JUIA — JCL Utility Intelligence Agent
# PURPOSE : Run Phase 4 PySpark code generator on all decoded steps
#           and save one .py file per decodable step.
#
# WHAT THIS SCRIPT DOES:
#   1. Re-runs Phase 2 parser on the sample JCL to get original step
#      objects (for SYSIN content and DD statements)
#   2. Loads the Phase 3 decoded results from JSON
#   3. Matches each decoded result to its original parsed step
#   4. For every step where decode_status = "success", calls the
#      PySpark generator to produce a complete Python file
#   5. Saves all files to outputs/pyspark/
#   6. Prints a summary of what was generated
#
# HOW TO RUN (from the juia project root):
#   source venv/bin/activate
#   python scripts/test_generator.py
#
# INPUTS:
#   sample_jcl/wire_payment_batch.jcl      (re-parsed for SYSIN content)
#   outputs/wire_payment_batch_decoded.json (Phase 3 results)
#   ANTHROPIC_API_KEY                       (Codespaces secret)
#
# OUTPUTS:
#   outputs/pyspark/STEP010_IDCAMS.py
#   outputs/pyspark/STEP020_IDCAMS.py
#   outputs/pyspark/STEP030_DFSORT.py
#   outputs/pyspark/STEP040_DFSORT.py
#   outputs/pyspark/STEP070_DFSORT.py
#   outputs/pyspark/STEP090_IDCAMS.py
#
# NOTE ON SKIPPED STEPS:
#   STEP050 and STEP080 are IEBGENER DUMMY (straight copy).
#   STEP060 is a PROGRAM step (COBOL).
#   None of these need PySpark generation — they are skipped.
# =============================================================================


# =============================================================================
# SECTION 1 — IMPORTS AND PATH SETUP
# =============================================================================

import os
import sys
import json

# Add project root to Python path so agent/ imports work
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Phase 2 modules — re-run to get original step objects
from agent.parser.jcl_splitter       import split_jcl_into_steps, load_jcl_from_file
from agent.parser.utility_classifier import classify_steps

# Phase 4 generator
from agent.generator.pyspark_generator import generate_pyspark_code

# Claude API client (same setup as Phase 3)
from agent.decoder.claude_decoder import get_api_client


# =============================================================================
# SECTION 2 — CONFIGURATION
# =============================================================================

JCL_FILE_PATH     = "sample_jcl/wire_payment_batch.jcl"
CNTL_FOLDER       = "cntl"
DECODED_JSON_PATH = "outputs/wire_payment_batch_decoded.json"
PYSPARK_OUTPUT    = "outputs/pyspark"


# =============================================================================
# SECTION 3 — DATA LOADING FUNCTIONS
# =============================================================================

def load_decoded_results(json_path):
    """
    Loads the Phase 3 decoded results from the JSON file.

    Parameters:
        json_path : path to the decoded JSON file

    Returns:
        A list of decoded step dictionaries, or empty list on failure.
    """

    if not os.path.exists(json_path):
        print(f"  [ERROR] Decoded JSON not found: {json_path}")
        print("  Run scripts/test_decoder.py first to produce Phase 3 output.")
        return []

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    # The JSON has a "steps" key containing the list of decoded results
    steps = data.get("steps", [])
    print(f"  Loaded {len(steps)} decoded results from {json_path}")
    return steps


def build_original_step_map(jcl_file_path, cntl_folder):
    """
    Re-runs the Phase 2 parser on the JCL file and returns a dictionary
    mapping step_name → original step dictionary.

    This gives us the SYSIN content and DD statements for each step,
    which are not stored in the Phase 3 JSON.

    Parameters:
        jcl_file_path : path to the JCL file
        cntl_folder   : path to the cntl/ folder for external SYSIN

    Returns:
        A dictionary: { "STEP030": step_dict, "STEP040": step_dict, ... }
    """

    if not os.path.exists(jcl_file_path):
        print(f"  [ERROR] JCL file not found: {jcl_file_path}")
        return {}

    jcl_text = load_jcl_from_file(jcl_file_path)
    steps    = split_jcl_into_steps(jcl_text, cntl_folder=cntl_folder)
    steps    = classify_steps(steps)

    # Build a lookup dictionary by step_name
    step_map = {}
    for step in steps:
        step_name = step.get("step_name", "")
        if step_name:
            step_map[step_name] = step

    print(f"  Parsed {len(step_map)} steps from {jcl_file_path}")
    return step_map


# =============================================================================
# SECTION 4 — MAIN FUNCTION
# =============================================================================

def main():

    print("=" * 65)
    print("  JUIA — Phase 4 PySpark Generator")
    print("=" * 65)
    print()

    # -------------------------------------------------------------------------
    # Step 1 — Set up Claude API client
    # -------------------------------------------------------------------------
    print("[1/4] Setting up Claude API client...")
    api_client = get_api_client()
    if api_client is None:
        print("  Cannot proceed without API client.")
        return
    print("  API client ready.")
    print()

    # -------------------------------------------------------------------------
    # Step 2 — Load Phase 3 decoded results
    # -------------------------------------------------------------------------
    print(f"[2/4] Loading Phase 3 decoded results...")
    decoded_results = load_decoded_results(DECODED_JSON_PATH)
    if not decoded_results:
        return
    print()

    # -------------------------------------------------------------------------
    # Step 3 — Re-run Phase 2 parser to get original step objects
    # -------------------------------------------------------------------------
    print("[3/4] Re-running Phase 2 parser for SYSIN content...")
    original_step_map = build_original_step_map(JCL_FILE_PATH, CNTL_FOLDER)
    if not original_step_map:
        return
    print()

    # -------------------------------------------------------------------------
    # Step 4 — Generate PySpark code for each successful decoded step
    # -------------------------------------------------------------------------
    print("[4/4] Generating PySpark files...")
    print()

    # Track results
    generated_files = []   # list of (step_name, file_path)
    skipped_steps   = []   # list of (step_name, reason)
    error_steps     = []   # list of (step_name, error)

    total = len(decoded_results)

    for index, decoded_step in enumerate(decoded_results, start=1):

        step_name    = decoded_step.get("step_name", "UNKNOWN")
        utility_type = decoded_step.get("utility_type", "UNKNOWN")
        status       = decoded_step.get("decode_status", "")

        print(f"--- Step {index}/{total}: {step_name} ({utility_type}) ---")

        # Skip steps that were not decoded by Claude in Phase 3
        if status != "success":
            reason = (
                "IEBGENER straight copy — no transformation needed"
                if utility_type == "IEBGENER"
                else f"Skipped in Phase 3 (status={status})"
            )
            print(f"  Skipping — {reason}")
            skipped_steps.append((step_name, reason))
            print()
            continue

        # Look up the original step for SYSIN content
        original_step = original_step_map.get(step_name)

        if original_step is None:
            print(f"  [WARN] No original step found for {step_name} — using empty")
            original_step = {"sysin_content": "", "dd_statements": []}

        # Generate the PySpark code
        file_path, gen_status = generate_pyspark_code(
            decoded_step  = decoded_step,
            original_step = original_step,
            api_client    = api_client
        )

        if gen_status == "success" and file_path:
            generated_files.append((step_name, file_path))
        else:
            error_steps.append((step_name, gen_status))

        print()

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------
    print("=" * 65)
    print("  GENERATION COMPLETE — SUMMARY")
    print("=" * 65)
    print()

    if generated_files:
        print(f"  Generated {len(generated_files)} PySpark file(s):")
        for step_name, file_path in generated_files:
            # Show just the filename, not the full path
            filename = os.path.basename(file_path)
            print(f"    ✓  {filename}")

    if skipped_steps:
        print()
        print(f"  Skipped {len(skipped_steps)} step(s):")
        for step_name, reason in skipped_steps:
            print(f"    ─  {step_name}: {reason}")

    if error_steps:
        print()
        print(f"  Errors on {len(error_steps)} step(s):")
        for step_name, error in error_steps:
            print(f"    ✗  {step_name}: {error}")

    print()
    print(f"  Output folder: {PYSPARK_OUTPUT}")
    print()

    # Show file sizes as a quick quality check
    if generated_files:
        print("  File sizes (larger = more complete code):")
        for step_name, file_path in generated_files:
            size_kb = os.path.getsize(file_path) / 1024
            filename = os.path.basename(file_path)
            print(f"    {filename:<30} {size_kb:.1f} KB")

    print()
    print("Phase 4 complete.")
    print("Review generated files in outputs/pyspark/ before proceeding.")
    print("Next: Phase 5 — sandbox validator.")
    print()


# =============================================================================
# SECTION 5 — ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    main()
