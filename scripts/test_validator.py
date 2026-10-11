# =============================================================================
# scripts/test_validator.py
#
# PROJECT : JUIA — JCL Utility Intelligence Agent
# PURPOSE : Run Phase 5 validation on all generated PySpark files.
#
# WHAT THIS SCRIPT DOES:
#   1. Loads the Phase 3 decoded results (for business rules and key fields)
#   2. Re-runs Phase 2 parser (for original SYSIN content)
#   3. For each .py file in outputs/pyspark/:
#      a. Runs initial validation via Claude
#      b. If FAIL: runs self-correction loop (up to 3 retries)
#      c. Saves the final validation status
#   4. Merges this run's results into outputs/validation_report.json
#      (results from previous runs are preserved; same step name is
#      overwritten by the newest result)
#   5. Prints a summary table for this run
#
# HOW TO RUN:
#   source venv/bin/activate
#   python scripts/test_validator.py
#
# EXPECTED RESULTS:
#   6 files validated.
#   Most should PASS on first attempt.
#   Any FAIL triggers automatic correction and re-validation.
#
# OUTPUT FILES:
#   outputs/validation_report.json   — accumulated validation results per step
#   outputs/pyspark/*.validated      — marker written after a PASS
# =============================================================================


# =============================================================================
# SECTION 1 — IMPORTS AND PATH SETUP
# =============================================================================

import os
import sys
import json
import glob

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Phase 2 modules
from agent.parser.jcl_splitter       import split_jcl_into_steps, load_jcl_from_file
from agent.parser.utility_classifier import classify_steps

# Phase 5 modules
from agent.validator.self_correction_loop import run_correction_loop

# API client
from agent.decoder.claude_decoder import get_api_client


# =============================================================================
# SECTION 2 — CONFIGURATION
# =============================================================================

JCL_FILE_PATH      = "jcl/TEST.jcl"
CNTL_FOLDER        = "cntl"
DECODED_JSON_PATH  = "outputs/test_jcl_decoded.json"
PYSPARK_FOLDER     = "outputs/pyspark"
REPORT_OUTPUT_PATH = "outputs/validation_report.json"


# =============================================================================
# SECTION 3 — DATA LOADERS
# =============================================================================

def load_decoded_results(json_path):
    """
    Loads Phase 3 decoded results and returns a dict keyed by step_name.
    """

    if not os.path.exists(json_path):
        print(f"  [ERROR] {json_path} not found. Run test_decoder.py first.")
        return {}

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    steps = data.get("steps", [])
    result_map = {}
    for step in steps:
        name = step.get("step_name", "")
        if name:
            result_map[name] = step

    print(f"  Loaded {len(result_map)} decoded results")
    return result_map


def load_original_steps(jcl_path, cntl_folder):
    """
    Re-runs Phase 2 parser and returns a dict keyed by step_name.
    """

    if not os.path.exists(jcl_path):
        print(f"  [ERROR] {jcl_path} not found.")
        return {}

    jcl_text = load_jcl_from_file(jcl_path)
    steps    = split_jcl_into_steps(jcl_text, cntl_folder=cntl_folder)
    steps    = classify_steps(steps)

    step_map = {}
    for step in steps:
        name = step.get("step_name", "")
        if name:
            step_map[name] = step

    print(f"  Parsed {len(step_map)} original steps")
    return step_map


def discover_generated_files(pyspark_folder):
    """
    Finds all .py files in the pyspark output folder.
    Excludes .bak files (correction backups).

    Returns a list of file paths sorted by filename.
    """

    pattern   = os.path.join(pyspark_folder, "*.py")
    all_files = glob.glob(pattern)

    # Exclude backup files
    py_files  = [f for f in all_files if ".bak_" not in f]
    py_files.sort()

    return py_files


def extract_step_name_from_path(file_path):
    """
    Derives the step name from a generated file path.

    Example: "outputs/pyspark/STEP030_DFSORT.py" → "STEP030"
    """
    filename  = os.path.basename(file_path)        # STEP030_DFSORT.py
    stem      = filename.replace(".py", "")         # STEP030_DFSORT
    step_name = stem.split("_")[0]                  # STEP030
    return step_name


# =============================================================================
# SECTION 4 — DISPLAY FUNCTIONS
# =============================================================================

def print_validation_result(result, index, total):
    """
    Prints the validation result for one step in a readable format.
    """

    step_name     = result.get("step_name", "UNKNOWN")
    utility_type  = result.get("utility_type", "UNKNOWN")
    status        = result.get("validation_status", "UNKNOWN")
    corrections   = result.get("corrections_applied", 0)
    issues        = result.get("issues_found", [])
    checks        = result.get("checks", {})
    data_summary  = result.get("synthetic_data_summary", "")

    print()
    print("=" * 65)
    print(f"Result {index}/{total}: {step_name} [{utility_type}]  "
          f"Status={status}  Corrections={corrections}")
    print("=" * 65)

    # Synthetic data summary
    if data_summary:
        print(f"\nTest Data: {data_summary}")

    # Check results table
    print("\nChecks:")
    print(f"  {'Check':<20} {'Status':<8} Detail")
    print(f"  {'─'*20} {'─'*8} {'─'*30}")

    for check_name, check_result in checks.items():
        check_status = check_result.get("status", "?")
        check_detail = check_result.get("detail", "")[:55]
        icon = "✓" if check_status == "PASS" else ("─" if check_status == "NA" else "✗")
        print(f"  {check_name:<20} {icon} {check_status:<6} {check_detail}")

    # Issues
    if issues:
        print(f"\nIssues Found ({len(issues)}):")
        for issue in issues:
            print(f"  • {issue}")

    # Correction history
    history = result.get("correction_history", [])
    if history:
        print(f"\nCorrection History:")
        for attempt in history:
            num    = attempt.get("attempt", "?")
            before = len(attempt.get("issues_before", []))
            after  = len(attempt.get("issues_after", []))
            status_after = attempt.get("status_after", "?")
            print(f"  Attempt {num}: {before} issues → "
                  f"{after} issues  [{status_after}]")


def print_summary_table(all_results):
    """
    Prints a compact summary table of the validation results from this run.
    """

    print()
    print("=" * 65)
    print("  VALIDATION SUMMARY (this run)")
    print("=" * 65)
    print()
    print(f"  {'#':<4} {'Step':<12} {'Utility':<12} "
          f"{'Status':<8} {'Corrections':<12} {'Issues'}")
    print(f"  {'─'*4} {'─'*12} {'─'*12} "
          f"{'─'*8} {'─'*12} {'─'*6}")

    pass_count  = 0
    fail_count  = 0
    error_count = 0
    total_corrections = 0

    for i, result in enumerate(all_results, start=1):
        step_name   = result.get("step_name", "")
        utility     = result.get("utility_type", "")
        status      = result.get("validation_status", "")
        corrections = result.get("corrections_applied", 0)
        issues      = len(result.get("issues_found", []))

        icon = "✓" if status == "PASS" else ("✗" if status == "FAIL" else "?")

        print(f"  {i:<4} {step_name:<12} {utility:<12} "
              f"{icon} {status:<6} {corrections:<12} {issues}")

        if status == "PASS":
            pass_count += 1
        elif status == "FAIL":
            fail_count += 1
        else:
            error_count += 1

        total_corrections += corrections

    print()
    print(f"  {'─' * 60}")
    print(f"  PASS   : {pass_count}")
    print(f"  FAIL   : {fail_count}")
    print(f"  ERRORS : {error_count}")
    print(f"  Total corrections applied : {total_corrections}")
    print()
    print("=" * 65)


# =============================================================================
# SECTION 5 — REPORT SAVER
# =============================================================================

def merge_with_existing_report(new_results, report_path):
    """
    Merges this run's results into any existing report on disk.

    Results from previous runs are preserved. If a step appears in both
    the existing report and the new results, the new result replaces it.
    The merged list is sorted by step name for a stable report layout.

    Parameters:
        new_results : list of validation result dicts from this run
        report_path : path of the existing JSON report (may not exist)

    Returns:
        The merged list of validation result dicts.
    """

    merged = {}

    if os.path.exists(report_path):
        try:
            with open(report_path, encoding="utf-8") as f:
                existing_data = json.load(f)
            for step in existing_data.get("steps", []):
                name = step.get("step_name", "")
                if name:
                    merged[name] = step
        except Exception as error:
            print(f"  [WARN] Could not read existing report "
                  f"({error}) — starting a new one")

    # New results overwrite existing results for the same step name
    for result in new_results:
        name = result.get("step_name", "")
        if name:
            merged[name] = result

    return [merged[name] for name in sorted(merged)]


def save_validation_report(all_results, report_path):
    """
    Saves the validation results to a JSON report file.

    Results are merged with any existing report so the report accumulates
    across runs. This keeps the report in sync with the .validated marker
    files, which cause already-passed steps to be skipped on later runs.

    Parameters:
        all_results : list of validation result dicts from this run
        report_path : path where the JSON report is saved
    """

    os.makedirs(os.path.dirname(report_path), exist_ok=True)

    # Merge BEFORE computing totals so the counts cover the whole report
    merged_results = merge_with_existing_report(all_results, report_path)

    pass_count  = sum(1 for r in merged_results if r.get("validation_status") == "PASS")
    fail_count  = sum(1 for r in merged_results if r.get("validation_status") == "FAIL")
    total_fixes = sum(r.get("corrections_applied", 0) for r in merged_results)

    report = {
        "job_name"            : "WIREPAY",
        "total_steps_validated": len(merged_results),
        "pass_count"          : pass_count,
        "fail_count"          : fail_count,
        "total_corrections"   : total_fixes,
        "steps"               : merged_results
    }

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"  Validation report saved: {report_path}")
    print(f"  Report now covers {len(merged_results)} step(s) "
          f"({len(all_results)} from this run)")


# =============================================================================
# SECTION 6 — MAIN
# =============================================================================

def main():

    print("=" * 65)
    print("  JUIA — Phase 5 Code Validator")
    print("=" * 65)
    print()

    # -------------------------------------------------------------------------
    # Setup
    # -------------------------------------------------------------------------
    print("[1/4] Setting up Claude API client...")
    api_client = get_api_client()
    if api_client is None:
        return
    print("  API client ready.")
    print()

    print("[2/4] Loading Phase 3 decoded results and Phase 2 original steps...")
    decoded_map  = load_decoded_results(DECODED_JSON_PATH)
    original_map = load_original_steps(JCL_FILE_PATH, CNTL_FOLDER)
    if not decoded_map or not original_map:
        return
    print()

    print("[3/4] Discovering generated PySpark files...")
    py_files = discover_generated_files(PYSPARK_FOLDER)
    print(f"  Found {len(py_files)} file(s) in {PYSPARK_FOLDER}/")
    for f in py_files:
        print(f"    {os.path.basename(f)}")
    print()

    if not py_files:
        print("  No files to validate. Run test_generator.py first.")
        return

    # -------------------------------------------------------------------------
    # Validation loop
    # -------------------------------------------------------------------------
    print("[4/4] Running validation and self-correction...")

    all_results = []
    total       = len(py_files)

    for index, code_path in enumerate(py_files, start=1):

        filename  = os.path.basename(code_path)
        step_name = extract_step_name_from_path(code_path)

        print(f"\n{'─' * 65}")
        print(f"File {index}/{total}: {filename}")
        print(f"{'─' * 65}")

        # Look up context for this step
        decoded_step  = decoded_map.get(step_name)
        original_step = original_map.get(step_name)

        if decoded_step is None:
            print(f"  [WARN] No decoded result found for {step_name} — skipping")
            continue

        if original_step is None:
            print(f"  [WARN] No original step found for {step_name} — using empty")
            original_step = {"sysin_content": "", "dd_statements": []}

        # Skip files that already passed a previous validation run.
        # A .validated marker file is written next to the .py file
        # after a successful PASS. Delete the marker to force re-validation.
        marker_path = code_path.replace(".py", ".validated")
        if os.path.exists(marker_path):
            print(f"  Already validated — skipping ({filename})")
            continue

        # Run validation + correction loop
        result = run_correction_loop(
            decoded_step  = decoded_step,
            original_step = original_step,
            code_path     = code_path,
            api_client    = api_client
        )

        all_results.append(result)

        # Write a .validated marker so this file is skipped on the next run
        if result.get("validation_status") == "PASS":
            marker_path = code_path.replace(".py", ".validated")
            with open(marker_path, "w") as f:
                f.write("PASS")
            print(f"  Marker written: {os.path.basename(marker_path)}")

        # Show result inline
        print_validation_result(result, index, total)

    # -------------------------------------------------------------------------
    # Summary and save
    # -------------------------------------------------------------------------
    print_summary_table(all_results)

    print("Saving validation report...")
    save_validation_report(all_results, REPORT_OUTPUT_PATH)
    print()
    print("Phase 5 complete.")
    print("Next: Phase 6 — output assembly (.md migration spec + Mermaid diagram).")


# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    main()
