# =============================================================================
# scripts/test_parser.py
#
# PROJECT : JUIA — JCL Utility Intelligence Agent
# PURPOSE : Test the Phase 2 parser against the sample JCL and show
#           how all four SYSIN variations are handled.
#
# HOW TO RUN (from the juia project root folder):
#   source venv/bin/activate
#   python scripts/test_parser.py
#
# EXPECTED RESULTS:
#   STEP010 : IDCAMS   sysin_source=inline               (DD *)
#   STEP020 : IDCAMS   sysin_source=inline               (DD *)
#   STEP030 : SORT     sysin_source=external_resolved    (DSN=SYS2.CNTL(WIREEXT))
#   STEP040 : SORT     sysin_source=external_resolved    (DSN=SYS2.CNTL(WIRESPL))
#   STEP050 : IEBGENER sysin_source=dummy                (DD DUMMY)
#   STEP060 : ACCTPAY  sysin_source=none                 (no SYSIN)
#   STEP070 : SORT     sysin_source=inline               (DD *)
#   STEP080 : IEBGENER sysin_source=dummy                (DD DUMMY)
#   STEP090 : IDCAMS   sysin_source=inline               (DD *)
# =============================================================================


# =============================================================================
# SECTION 1 — IMPORTS AND PATH SETUP
# =============================================================================

import os
import sys

# Add the project root to Python's search path so we can import agent/
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from agent.parser.jcl_splitter       import (split_jcl_into_steps,
                                               load_jcl_from_file,
                                               SYSIN_SOURCE_NONE,
                                               SYSIN_SOURCE_DUMMY,
                                               SYSIN_SOURCE_INLINE,
                                               SYSIN_SOURCE_EXTERNAL_RESOLVED,
                                               SYSIN_SOURCE_EXTERNAL_UNRESOLVED)
from agent.parser.utility_classifier import (classify_steps,
                                               get_utility_breakdown,
                                               get_decodable_steps,
                                               UTILITY_PROGRAM)


# =============================================================================
# SECTION 2 — CONFIGURATION
# =============================================================================

JCL_FILE_PATH  = "jcl/TEST.jcl"
CNTL_FOLDER    = "cntl"
SYSIN_DISPLAY_LIMIT = 2000


# =============================================================================
# SECTION 3 — DISPLAY HELPERS
# =============================================================================

def separator(char="─", width=65):
    print(char * width)


def sysin_source_label(source):
    """
    Returns a human-readable label with an icon for each SYSIN source type.
    """
    labels = {
        SYSIN_SOURCE_INLINE              : "✓ inline       (DD * in JCL)",
        SYSIN_SOURCE_DUMMY               : "─ dummy        (DD DUMMY)",
        SYSIN_SOURCE_EXTERNAL_RESOLVED   : "✓ external     (DSN= resolved from cntl/)",
        SYSIN_SOURCE_EXTERNAL_UNRESOLVED : "✗ external     (DSN= NOT FOUND in cntl/)",
        SYSIN_SOURCE_NONE                : "─ none         (no SYSIN DD)",
    }
    return labels.get(source, f"? {source}")


def print_step(step, step_number, total_steps):
    """
    Prints full details for one step including SYSIN content.
    """
    separator()
    print(f"Step {step_number}/{total_steps}: {step['step_name']}")
    separator()

    print(f"  PGM           : {step['pgm']}")
    print(f"  Utility Type  : {step['utility_type']}")
    print(f"  SYSIN Source  : {sysin_source_label(step['sysin_source'])}")

    # Show DSN if external
    if step["sysin_dsn"]:
        print(f"  SYSIN DSN     : {step['sysin_dsn']}")

    # Show warning if external was not resolved
    if step["sysin_warning"]:
        print(f"  SYSIN Warning : {step['sysin_warning']}")

    # DD statements
    dd_count = len(step["dd_statements"])
    print(f"  DD Statements : {dd_count}")
    for dd in step["dd_statements"]:
        inline_tag = " [INLINE]" if dd["is_inline"] else ""
        print(f"    //{dd['ddname']:<10} {dd['parameters'][:55]}{inline_tag}")

    # SYSIN content
    print()
    sysin = step["sysin_content"]

    if sysin and len(sysin.strip()) > 0:
        sysin_lines = len(sysin.splitlines())
        print(f"  SYSIN Content ({sysin_lines} lines):")
        print()
        display_text = sysin if len(sysin) <= SYSIN_DISPLAY_LIMIT else sysin[:SYSIN_DISPLAY_LIMIT] + "\n  ...(truncated)"
        for line in display_text.splitlines():
            print(f"    {line}")
    else:
        if step["sysin_source"] == SYSIN_SOURCE_DUMMY:
            print("  SYSIN Content : DD DUMMY — straight copy, no control cards")
        elif step["sysin_source"] == SYSIN_SOURCE_NONE:
            print("  SYSIN Content : No SYSIN DD — non-utility program step")
        elif step["sysin_source"] == SYSIN_SOURCE_EXTERNAL_UNRESOLVED:
            print("  SYSIN Content : EMPTY — upload the control card file to cntl/")

    print()


def print_summary(steps, breakdown):
    """
    Prints the final summary table.
    """
    print("=" * 65)
    print("  SUMMARY")
    print("=" * 65)
    print()
    print(f"  {'#':<4} {'Step':<10} {'PGM':<12} {'Utility':<12} {'SYSIN Source'}")
    print(f"  {'─'*4} {'─'*10} {'─'*12} {'─'*12} {'─'*28}")

    for i, step in enumerate(steps, start=1):
        source_short = step["sysin_source"]
        warn_flag    = " ⚠" if step["sysin_source"] == SYSIN_SOURCE_EXTERNAL_UNRESOLVED else ""
        print(f"  {i:<4} {step['step_name']:<10} {step['pgm']:<12} "
              f"{step['utility_type']:<12} {source_short}{warn_flag}")

    print()
    print(f"  Utility breakdown : {breakdown}")
    print()

    decodable = get_decodable_steps(steps)

    # Count SYSIN sources across decodable steps
    resolved   = sum(1 for s in decodable if s["sysin_source"] in
                     (SYSIN_SOURCE_INLINE, SYSIN_SOURCE_EXTERNAL_RESOLVED))
    unresolved = sum(1 for s in decodable if s["sysin_source"] ==
                     SYSIN_SOURCE_EXTERNAL_UNRESOLVED)
    dummy      = sum(1 for s in decodable if s["sysin_source"] ==
                     SYSIN_SOURCE_DUMMY)

    print(f"  Decodable steps   : {len(decodable)}")
    print(f"    SYSIN available : {resolved}  (inline + external resolved)")
    print(f"    SYSIN is DUMMY  : {dummy}  (straight copy — no decode needed)")
    print(f"    SYSIN missing   : {unresolved}  (upload to cntl/ to fix)")

    if unresolved == 0:
        print()
        print("  All decodable steps have SYSIN content.")
        print("  Phase 3 decoder is ready to run.")
    else:
        print()
        print(f"  ACTION NEEDED: {unresolved} step(s) have unresolved external SYSIN.")
        print("  Upload the missing control card files to the cntl/ folder.")

    print("=" * 65)


# =============================================================================
# SECTION 4 — MAIN
# =============================================================================

def main():
    print("=" * 65)
    print("  JUIA — Phase 2 Parser Test (with external SYSIN support)")
    print("=" * 65)
    print()

    # Load JCL
    print(f"[1/3] Loading : {JCL_FILE_PATH}")
    if not os.path.exists(JCL_FILE_PATH):
        print(f"\n  [ERROR] File not found: {JCL_FILE_PATH}")
        print("  Run from the juia project root.")
        return

    jcl_text = load_jcl_from_file(JCL_FILE_PATH)
    print(f"  Loaded {len(jcl_text.splitlines())} lines.")
    print()

    # Check cntl folder
    if os.path.exists(CNTL_FOLDER):
        cntl_files = os.listdir(CNTL_FOLDER)
        print(f"[cntl/] Found {len(cntl_files)} file(s): {cntl_files}")
    else:
        print(f"[cntl/] Folder not found — external SYSIN will be unresolved.")
    print()

    # Split
    print("[2/3] Running JCL splitter...")
    steps = split_jcl_into_steps(jcl_text, cntl_folder=CNTL_FOLDER)
    print(f"  Found {len(steps)} steps.")
    print()

    # Classify
    print("[3/3] Running utility classifier...")
    steps     = classify_steps(steps)
    breakdown = get_utility_breakdown(steps)
    print(f"  Done. Breakdown: {breakdown}")
    print()

    # Detailed display
    print("=" * 65)
    print("  DETAILED STEP ANALYSIS")
    print("=" * 65)
    print()

    for i, step in enumerate(steps, start=1):
        print_step(step, i, len(steps))

    # Summary
    print_summary(steps, breakdown)


if __name__ == "__main__":
    main()
