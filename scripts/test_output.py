# =============================================================================
# scripts/test_output.py
#
# PROJECT : JUIA — JCL Utility Intelligence Agent
# PURPOSE : Phase 6 — assemble all previous phase outputs into a
#           Markdown migration specification and Mermaid data lineage diagram.
#
# WHAT THIS SCRIPT DOES:
#   1. Loads Phase 3 decoded results (business rules, key fields, operations)
#   2. Loads Phase 5 validation report (PASS/FAIL per step)
#   3. Re-runs Phase 2 parser (for original SYSIN and DD statements)
#   4. Generates the Mermaid data lineage diagram
#   5. Assembles the full Markdown migration specification
#   6. Saves both files to outputs/
#
# HOW TO RUN (from the juia project root):
#   source venv/bin/activate
#   python scripts/test_output.py
#
# OUTPUTS:
#   outputs/test_jcl_migration_spec.md   — full migration spec
#   outputs/test_jcl_lineage.mmd         — Mermaid diagram source
#
# HOW TO VIEW:
#   Open outputs/test_jcl_migration_spec.md on GitHub
#   or in VS Code with the Markdown Preview extension.
#   The Mermaid diagram renders automatically.
# =============================================================================


# =============================================================================
# SECTION 1 — IMPORTS AND PATH SETUP
# =============================================================================

import os
import sys
import json

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Phase 2 modules
from agent.parser.jcl_splitter       import split_jcl_into_steps, load_jcl_from_file
from agent.parser.utility_classifier import classify_steps

# Phase 6 modules
from agent.output.diagram_generator import generate_mermaid_diagram
from agent.output.md_writer          import assemble_migration_spec


# =============================================================================
# SECTION 2 — CONFIGURATION
# =============================================================================

JCL_FILE_PATH      = "jcl/TEST.jcl"
CNTL_FOLDER        = "cntl"
DECODED_JSON_PATH  = "outputs/test_jcl_decoded.json"
VALIDATION_PATH    = "outputs/validation_report.json"
PYSPARK_FOLDER     = "outputs/pyspark"
OUTPUT_FOLDER      = "outputs"
SPEC_FILENAME      = "test_jcl_migration_spec.md"
DIAGRAM_FILENAME   = "test_jcl_lineage.mmd"


# =============================================================================
# SECTION 3 — DATA LOADERS
# =============================================================================

def load_decoded_results(json_path):
    """
    Loads Phase 3 decoded results as a dict keyed by step_name.
    """

    if not os.path.exists(json_path):
        print(f"  [ERROR] Not found: {json_path}")
        print("  Run scripts/test_decoder.py first.")
        return {}, ""

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    job_name = data.get("job_name", "UNKNOWN")
    steps    = data.get("steps", [])

    result_map = {}
    for step in steps:
        name = step.get("step_name", "")
        if name:
            result_map[name] = step

    print(f"  Loaded {len(result_map)} decoded results (job: {job_name})")
    return result_map, job_name


def load_validation_results(json_path):
    """
    Loads Phase 5 validation report as a dict keyed by step_name.
    """

    if not os.path.exists(json_path):
        print(f"  [WARN] Validation report not found: {json_path}")
        print("  Proceeding without validation data.")
        return {}

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    steps = data.get("steps", [])

    result_map = {}
    for step in steps:
        name = step.get("step_name", "")
        if name:
            result_map[name] = step

    print(f"  Loaded {len(result_map)} validation results")
    return result_map


def load_original_steps(jcl_path, cntl_folder):
    """
    Re-runs Phase 2 parser and returns a dict keyed by step_name.
    """

    if not os.path.exists(jcl_path):
        print(f"  [ERROR] JCL file not found: {jcl_path}")
        return {}

    jcl_text = load_jcl_from_file(jcl_path)
    steps    = split_jcl_into_steps(jcl_text, cntl_folder=cntl_folder)
    steps    = classify_steps(steps)

    step_map = {}
    for step in steps:
        name = step.get("step_name", "")
        if name:
            step_map[name] = step

    print(f"  Parsed {len(step_map)} original steps from JCL")
    return step_map


def build_steps_data(decoded_map, original_map):
    """
    Combines decoded results and original steps into a unified list.

    Each entry contains both the decoded Phase 3 result and the
    original Phase 2 step, paired by step_name.

    The order follows the original JCL step order from original_map.

    Returns:
        A list of dicts: [{"decoded": ..., "original": ...}, ...]
    """

    # Use the order of original_map (which preserves JCL step order)
    combined = []

    for step_name, original_step in original_map.items():
        decoded_step = decoded_map.get(step_name, {})

        # If no decoded result, build a minimal placeholder
        if not decoded_step:
            decoded_step = {
                "step_name"    : step_name,
                "utility_type" : original_step.get("utility_type", "UNKNOWN"),
                "pgm"          : original_step.get("pgm", ""),
                "decode_status": "skipped",
                "sysin_source" : original_step.get("sysin_source", "none"),
                "business_rule": "(not decoded)",
                "key_fields"   : [],
                "operations"   : [],
                "input_files"  : [],
                "output_files" : [],
                "notes"        : ""
            }

        combined.append({
            "decoded"  : decoded_step,
            "original" : original_step
        })

    return combined


# =============================================================================
# SECTION 4 — FILE SAVERS
# =============================================================================

def save_file(content, folder, filename):
    """
    Saves a text string to a file in the given folder.

    Creates the folder if it doesn't exist.

    Returns:
        The full path of the saved file.
    """

    os.makedirs(folder, exist_ok=True)
    output_path = os.path.join(folder, filename)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    file_size_kb = os.path.getsize(output_path) / 1024
    print(f"  Saved: {output_path}  ({file_size_kb:.1f} KB)")

    return output_path


# =============================================================================
# SECTION 5 — MAIN
# =============================================================================

def main():

    print("=" * 65)
    print("  JUIA — Phase 6 Output Assembly")
    print("=" * 65)
    print()

    # -------------------------------------------------------------------------
    # Step 1 — Load all phase outputs
    # -------------------------------------------------------------------------
    print("[1/5] Loading Phase 3 decoded results...")
    decoded_map, job_name = load_decoded_results(DECODED_JSON_PATH)
    if not decoded_map:
        return
    print()

    print("[2/5] Loading Phase 5 validation report...")
    validation_map = load_validation_results(VALIDATION_PATH)
    print()

    print("[3/5] Re-running Phase 2 parser for original step data...")
    original_map = load_original_steps(JCL_FILE_PATH, CNTL_FOLDER)
    if not original_map:
        return
    print()

    # Combine into unified steps list
    all_steps_data = build_steps_data(decoded_map, original_map)
    print(f"  Combined: {len(all_steps_data)} steps ready for output assembly")
    print()

    # -------------------------------------------------------------------------
    # Step 2 — Generate Mermaid data lineage diagram
    # -------------------------------------------------------------------------
    print("[4/5] Generating data lineage diagram...")

    mermaid_source = generate_mermaid_diagram(
        job_name       = job_name,
        all_steps_data = all_steps_data
    )

    diagram_path = save_file(mermaid_source, OUTPUT_FOLDER, DIAGRAM_FILENAME)
    print()

    # -------------------------------------------------------------------------
    # Step 3 — Assemble the Markdown migration specification
    # -------------------------------------------------------------------------
    print("[5/5] Assembling migration specification...")

    jcl_filename = os.path.basename(JCL_FILE_PATH)

    markdown_content = assemble_migration_spec(
        job_name       = job_name,
        jcl_filename   = jcl_filename,
        all_steps_data = all_steps_data,
        validation_map = validation_map,
        mermaid_source = mermaid_source,
        pyspark_folder = PYSPARK_FOLDER
    )

    spec_path = save_file(markdown_content, OUTPUT_FOLDER, SPEC_FILENAME)
    print()

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------
    print("=" * 65)
    print("  Phase 6 complete — Output files created")
    print("=" * 65)
    print()
    print("  Migration Specification:")
    print(f"    {spec_path}")
    print()
    print("  Data Lineage Diagram:")
    print(f"    {diagram_path}")
    print()
    print("  To view the migration spec with diagram rendering:")
    print("  1. Push to GitHub and open the .md file in the browser")
    print("  2. Or open in VS Code with Markdown Preview")
    print()
    print("  Next: Phase 7 — Orchestrator (wire all phases into main.py)")


# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    main()
