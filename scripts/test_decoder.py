# =============================================================================
# scripts/test_decoder.py
#
# PROJECT : JUIA — JCL Utility Intelligence Agent
# PURPOSE : Run Phase 3 decoder on the sample JCL and display results.
#
# WHAT THIS SCRIPT DOES:
#   1. Loads the sample JCL (Phase 2)
#   2. Splits and classifies all steps (Phase 2)
#   3. Decodes every step using RAG + Claude (Phase 3)
#   4. Prints the decoded result for each step to the screen
#   5. Saves all results to outputs/test_jcl_decoded.json
#
# HOW TO RUN (from the juia project root):
#   source venv/bin/activate
#   python scripts/test_decoder.py
#
# PREREQUISITES:
#   - ANTHROPIC_API_KEY must be set (Codespaces secret or environment variable)
#   - corpus/vectordb/ must exist (run ingest_corpus.py first)
#   - jcl/TEST.jcl must exist (Phase 2)
#
# EXPECTED OUTPUT:
#   9 steps processed:
#     6 decoded via Claude API (IDCAMS x3, DFSORT x3)
#     2 skipped with hardcoded result (IEBGENER DUMMY x2)
#     1 skipped (PROGRAM step)
#   Results saved to outputs/test_jcl_decoded.json
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

# Phase 2 parser modules
from agent.parser.jcl_splitter       import split_jcl_into_steps, load_jcl_from_file
from agent.parser.utility_classifier import classify_steps, get_utility_breakdown

# Phase 3 decoder
from agent.decoder.step_decoder      import DecoderSession


# =============================================================================
# SECTION 2 — CONFIGURATION
# =============================================================================

JCL_FILE_PATH   = "jcl/TEST.jcl"
CNTL_FOLDER     = "cntl"
OUTPUT_FOLDER   = "outputs"
OUTPUT_FILENAME = "test_jcl_decoded.json"


# =============================================================================
# SECTION 3 — DISPLAY FUNCTIONS
# =============================================================================

def separator(char="─", width=65):
    """Print a consistent divider between decoder report sections."""
    print(char * width)


def print_decoded_step(result, index, total):
    """
    Prints the decoded result for one step in a readable format.
    """
    step_name    = result.get("step_name", "UNKNOWN")
    utility_type = result.get("utility_type", "UNKNOWN")
    status       = result.get("decode_status", "unknown")
    confidence   = result.get("decode_confidence", "N/A")

    separator("=")
    print(f"Result {index}/{total}: {step_name}  [{utility_type}]  "
          f"Status={status}  Confidence={confidence}")
    separator("=")

    # Business rule
    print()
    print("BUSINESS RULE:")
    business_rule = result.get("business_rule", "")
    # Word-wrap the business rule at 65 characters for readability
    words         = business_rule.split()
    current_line  = "  "
    for word in words:
        if len(current_line) + len(word) + 1 > 67:
            print(current_line)
            current_line = "  " + word
        else:
            current_line += (" " if current_line != "  " else "") + word
    if current_line.strip():
        print(current_line)

    # Files
    print()
    input_files  = result.get("input_files", [])
    output_files = result.get("output_files", [])
    if input_files:
        print(f"INPUT FILES  : {', '.join(input_files)}")
    if output_files:
        print(f"OUTPUT FILES : {', '.join(output_files)}")

    # Key fields
    key_fields = result.get("key_fields", [])
    if key_fields:
        print()
        print("KEY FIELDS:")
        for field in key_fields:
            offset  = field.get("offset", "?")
            length  = field.get("length", "?")
            fmt     = field.get("format", "?")
            meaning = field.get("meaning", "")
            print(f"  Offset {offset:<4} Len {length:<4} {fmt:<4} : {meaning}")

    # Operations
    operations = result.get("operations", [])
    if operations:
        print()
        print("OPERATIONS:")
        for op in operations:
            op_type = op.get("type", "")
            op_desc = op.get("description", "")
            print(f"  [{op_type:<12}] {op_desc}")

    # PySpark hint
    pyspark_hint = result.get("pyspark_hint", "")
    if pyspark_hint and pyspark_hint != "# Requires separate COBOL/program analysis":
        print()
        print("PYSPARK HINT:")
        for line in pyspark_hint.splitlines():
            print(f"  {line}")

    # Notes
    notes = result.get("notes", "")
    if notes and status == "success":
        print()
        print(f"NOTES: {notes}")

    # Warning for unresolved
    if status == "skipped" and "unresolved" in result.get("notes", "").lower():
        print()
        print(f"⚠  ACTION NEEDED: {result.get('notes', '')}")

    print()


def print_final_summary(all_results, breakdown):
    """
    Prints a summary table after all steps are decoded.
    """
    separator("=")
    print("  FINAL SUMMARY")
    separator("=")
    print()
    print(f"  {'#':<4} {'Step':<10} {'Utility':<12} {'Status':<16} {'Confidence'}")
    print(f"  {'─'*4} {'─'*10} {'─'*12} {'─'*16} {'─'*10}")

    for i, result in enumerate(all_results, start=1):
        step_name  = result.get("step_name", "")
        utility    = result.get("utility_type", "")
        status     = result.get("decode_status", "")
        confidence = result.get("decode_confidence", "N/A")
        print(f"  {i:<4} {step_name:<10} {utility:<12} {status:<16} {confidence}")

    # Count by status
    success_count  = sum(1 for r in all_results if r.get("decode_status") == "success")
    skipped_count  = sum(1 for r in all_results if r.get("decode_status") == "skipped")
    error_count    = sum(1 for r in all_results if r.get("decode_status") not in ("success", "skipped"))

    print()
    print(f"  Decoded (Claude API) : {success_count}")
    print(f"  Skipped              : {skipped_count}")
    print(f"  Errors               : {error_count}")
    print()
    separator("=")


# =============================================================================
# SECTION 4 — RESULT SAVER
# =============================================================================

def save_results_to_json(all_results, output_folder, output_filename):
    """
    Saves all decoded results to a JSON file in the outputs/ folder.

    Parameters:
        all_results    : list of decoded result dictionaries
        output_folder  : folder to save to (created if needed)
        output_filename: filename for the JSON output
    """

    os.makedirs(output_folder, exist_ok=True)
    output_path = os.path.join(output_folder, output_filename)

    # Build the output structure
    output_data = {
        "job_name"    : "MYTEST",
        "jcl_file"    : JCL_FILE_PATH,
        "total_steps" : len(all_results),
        "steps"       : all_results
    }

    with open(output_path, "w", encoding="utf-8") as f:
        # indent=2 makes the JSON human-readable with 2-space indentation
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print(f"  Results saved to: {output_path}")


# =============================================================================
# SECTION 5 — MAIN
# =============================================================================

def main():
    """Parse the sample job, decode each step, and save a phase report."""

    print("=" * 65)
    print("  JUIA — Phase 3 Decoder Test")
    print("=" * 65)
    print()

    # -------------------------------------------------------------------------
    # Phase 2 — Parse and classify JCL
    # -------------------------------------------------------------------------
    print("[ PHASE 2 ] Parsing JCL...")

    if not os.path.exists(JCL_FILE_PATH):
        print(f"  [ERROR] JCL file not found: {JCL_FILE_PATH}")
        return

    jcl_text = load_jcl_from_file(JCL_FILE_PATH)
    steps    = split_jcl_into_steps(jcl_text, cntl_folder=CNTL_FOLDER)
    steps    = classify_steps(steps)
    breakdown = get_utility_breakdown(steps)

    print(f"  {len(steps)} steps parsed. Breakdown: {breakdown}")
    print()

    # -------------------------------------------------------------------------
    # Phase 3 — Decode all steps
    # -------------------------------------------------------------------------
    print("[ PHASE 3 ] Starting decoder session...")
    print()

    session = DecoderSession()

    if not session.is_ready():
        print("[ERROR] Decoder session failed to initialise.")
        print("  Check ANTHROPIC_API_KEY and corpus/vectordb/ exist.")
        return

    all_results = session.decode_all_steps(steps)

    # -------------------------------------------------------------------------
    # Display results
    # -------------------------------------------------------------------------
    print()
    print("=" * 65)
    print("  DECODED RESULTS")
    print("=" * 65)
    print()

    for i, result in enumerate(all_results, start=1):
        print_decoded_step(result, i, len(all_results))

    # -------------------------------------------------------------------------
    # Summary and save
    # -------------------------------------------------------------------------
    print_final_summary(all_results, breakdown)

    print()
    print("Saving results...")
    save_results_to_json(all_results, OUTPUT_FOLDER, OUTPUT_FILENAME)
    print()
    print("Phase 3 complete. Next: Phase 4 — PySpark code generator.")


# =============================================================================
# SECTION 6 — ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    main()
