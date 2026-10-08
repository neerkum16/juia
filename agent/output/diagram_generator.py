# =============================================================================
# agent/output/diagram_generator.py
#
# PROJECT : JUIA — JCL Utility Intelligence Agent
# PURPOSE : Generate a Mermaid data lineage diagram showing how datasets
#           flow through each step of the JCL job.
#
# WHAT THIS MODULE DOES (plain English):
#   A mainframe JCL job processes data through a series of steps, each
#   reading from one or more datasets and writing to others. This module
#   reads the file names from each step and draws a flow diagram showing
#   the complete data lineage from the first input file to the last output.
#
#   The diagram uses the Mermaid diagramming language. Mermaid diagrams
#   render automatically in GitHub, GitLab, VS Code preview, and most
#   Markdown viewers — no software to install.
#
# WHAT MERMAID IS:
#   Mermaid is a text-based diagramming language. Instead of drawing boxes
#   and arrows manually, you write a text description like:
#       A --> B --> C
#   and the Mermaid renderer draws the diagram automatically.
#
#   graph LR means "draw a graph flowing Left to Right".
#   Other options: TD (top-down), BT (bottom-top), RL (right-left).
#
# NODE SHAPES USED:
#   Datasets : [("DATASET.NAME")]   — cylinder shape (looks like a drum)
#   Steps    : ["STEP\n[UTILITY]"]  — rectangle
#   Color-coded by utility type via classDef.
#
# OUTPUT:
#   A string containing the full Mermaid diagram source.
#   Saved as outputs/wire_payment_batch_lineage.mmd
#   Also embedded inside the Markdown migration spec.
# =============================================================================


# =============================================================================
# SECTION 1 — IMPORTS
# =============================================================================

# re — built into Python.
# Used to: sanitize dataset names for use as Mermaid node IDs.
# Mermaid node IDs cannot contain dots, spaces, or special characters.
import re


# =============================================================================
# SECTION 2 — UTILITY TYPE COLOURS
#
# Each utility type gets a distinct background colour in the diagram.
# This makes it easy to see at a glance which steps are DFSORT vs IDCAMS.
# The values are CSS hex colours.
# =============================================================================

UTILITY_COLOURS = {
    "DFSORT"   : {"fill": "#27ae60", "stroke": "#1e8449", "text": "#ffffff"},
    "ICETOOL"  : {"fill": "#2ecc71", "stroke": "#27ae60", "text": "#ffffff"},
    "IDCAMS"   : {"fill": "#2980b9", "stroke": "#1a6fa3", "text": "#ffffff"},
    "IEBGENER" : {"fill": "#e67e22", "stroke": "#ca6f1e", "text": "#ffffff"},
    "IEBCOPY"  : {"fill": "#e74c3c", "stroke": "#c0392b", "text": "#ffffff"},
    "PROGRAM"  : {"fill": "#95a5a6", "stroke": "#717d7e", "text": "#ffffff"},
}

# Colour for dataset (file) nodes
DATASET_COLOUR = {"fill": "#f8f9fa", "stroke": "#6c757d", "text": "#212529"}


# =============================================================================
# SECTION 3 — NODE ID SANITIZER
# =============================================================================

def sanitize_node_id(name):
    """
    Converts a dataset name or step name into a valid Mermaid node ID.

    Mermaid node IDs can only contain letters, numbers, and underscores.
    Any other character must be removed or replaced.

    Examples:
        "WIRE.DAILY.INPUT.SEQ"  → "WIRE_DAILY_INPUT_SEQ"
        "WIRE.WORK.HIGH.SEQ"    → "WIRE_WORK_HIGH_SEQ"
        "STEP010"               → "STEP010"

    Parameters:
        name : the original name string

    Returns:
        A sanitized string safe for use as a Mermaid node ID.
    """

    # Replace dots, spaces, hyphens, and slashes with underscores
    sanitized = re.sub(r'[.\s\-/]', '_', name)

    # Remove any remaining characters that are not alphanumeric or underscore
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '', sanitized)

    # Ensure the ID does not start with a number (Mermaid requirement)
    if sanitized and sanitized[0].isdigit():
        sanitized = "N_" + sanitized

    return sanitized


# =============================================================================
# SECTION 4 — FILE EXTRACTOR
# Extracts input and output file names from decoded and original step data.
# =============================================================================

def get_step_files(decoded_step, original_step):
    """
    Returns the input and output dataset names for one step.

    For decoded steps (Phase 3 success), uses the input_files and
    output_files lists from the decoded result.

    For skipped steps (IEBGENER DUMMY, PROGRAM), reads the DD statements
    from the original Phase 2 parsed step.

    Parameters:
        decoded_step  : decoded result dict from Phase 3 (may have empty files)
        original_step : original step dict from Phase 2 parser

    Returns:
        A tuple of (input_files, output_files) — both lists of strings.
    """

    utility_type = decoded_step.get("utility_type", "")
    decode_status = decoded_step.get("decode_status", "")

    # For successfully decoded steps, use Phase 3 results directly
    input_files  = decoded_step.get("input_files", [])
    output_files = decoded_step.get("output_files", [])

    # If Phase 3 did not populate file lists, extract from DD statements
    if not input_files and not output_files and original_step:
        input_files, output_files = extract_files_from_dd(original_step)

    # Filter out non-dataset entries like SYSOUT=* or SYSPRINT
    input_files  = [f for f in input_files  if not f.upper().startswith("SYSOUT")
                    and not f.upper().startswith("SYSPRINT")]
    output_files = [f for f in output_files if not f.upper().startswith("SYSOUT")
                    and not f.upper().startswith("SYSPRINT")]

    return input_files, output_files


def extract_files_from_dd(original_step):
    """
    Extracts input and output dataset names from a step's DD statements.

    Uses heuristics to classify each DD as input or output:
        - DISP=SHR or DISP=(,PASS) → input (reading existing dataset)
        - DISP=(NEW,... → output (creating new dataset)
        - SYSUT1 → input (IEBGENER convention)
        - SYSUT2 → output (IEBGENER convention)
        - SORTIN  → input (DFSORT convention)
        - SORTOUT → output (DFSORT convention)

    Parameters:
        original_step : step dictionary from Phase 2 parser

    Returns:
        A tuple of (input_files, output_files) — both lists of strings.
    """

    input_files  = []
    output_files = []

    skip_ddnames = {"SYSPRINT", "SYSOUT", "SYSABEND", "SYSIN",
                    "STEPLIB", "JOBLIB", "SYSUDUMP"}

    for dd in original_step.get("dd_statements", []):

        ddname     = dd.get("ddname", "").upper()
        parameters = dd.get("parameters", "").upper()

        if ddname in skip_ddnames:
            continue

        # Extract DSN value
        dsn_match = re.search(r'DSN=([^,\s]+)', parameters, re.IGNORECASE)
        if not dsn_match:
            continue

        dsn = dsn_match.group(1).strip()

        # Skip temporary datasets (&&name) and special datasets
        if dsn.startswith("&&") or dsn.startswith("SYS"):
            continue

        # Classify as input or output based on DISP and ddname conventions
        if (ddname in {"SYSUT1", "SORTIN", "SEQIN", "VSAMIN", "INPUT"}
                or "DISP=SHR" in parameters
                or "DISP=(SHR" in parameters):
            if dsn not in input_files:
                input_files.append(dsn)

        elif (ddname in {"SYSUT2", "SORTOUT", "SEQOUT", "VSAMOUT",
                          "OUTPUT", "HIGHVAL", "LOWVAL"}
              or "DISP=(NEW" in parameters):
            if dsn not in output_files:
                output_files.append(dsn)

    return input_files, output_files


# =============================================================================
# SECTION 5 — DIAGRAM BUILDER
# =============================================================================

def generate_mermaid_diagram(job_name, all_steps_data):
    """
    Generates the complete Mermaid data lineage diagram source.

    The diagram shows every step as a rectangle and every dataset as a
    cylinder (database drum shape). Arrows connect datasets to the steps
    that read them, and steps to the datasets they produce.

    Colour coding:
        Green  → DFSORT/ICETOOL steps
        Blue   → IDCAMS steps
        Orange → IEBGENER/IEBCOPY steps
        Grey   → PROGRAM steps
        White  → Dataset nodes

    Parameters:
        job_name      : name of the JCL job (for the diagram title)
        all_steps_data: list of dicts, each containing:
                          "decoded_step"  : Phase 3 decoded result
                          "original_step" : Phase 2 parsed step

    Returns:
        The complete Mermaid diagram as a string.
    """

    # We build the diagram in four parts:
    #   1. classDef colour definitions
    #   2. Dataset node definitions
    #   3. Step node definitions
    #   4. Edge definitions (arrows)
    #   5. Class assignments (apply colours)

    # Track which datasets and steps we have already defined
    # to avoid duplicate node definitions in the Mermaid source
    defined_datasets = {}   # dataset_name → sanitized_id
    step_utilities   = {}   # step_name → utility_type

    all_edges        = []   # list of (from_id, to_id) tuples

    # -------------------------------------------------------------------------
    # Pass 1: collect all nodes and edges
    # -------------------------------------------------------------------------
    for step_data in all_steps_data:

        decoded_step  = step_data.get("decoded_step", {})
        original_step = step_data.get("original_step", {})

        step_name    = decoded_step.get("step_name", "UNKNOWN")
        utility_type = decoded_step.get("utility_type", "PROGRAM")
        step_id      = sanitize_node_id(step_name)

        step_utilities[step_name] = utility_type

        # Get input and output files for this step
        input_files, output_files = get_step_files(decoded_step, original_step)

        # Register input dataset nodes
        for dataset_name in input_files:
            ds_id = sanitize_node_id(dataset_name)
            if dataset_name not in defined_datasets:
                defined_datasets[dataset_name] = ds_id

            # Edge: dataset → step
            all_edges.append((ds_id, step_id, ""))

        # Register output dataset nodes
        for dataset_name in output_files:
            ds_id = sanitize_node_id(dataset_name)
            if dataset_name not in defined_datasets:
                defined_datasets[dataset_name] = ds_id

            # Edge: step → dataset
            all_edges.append((step_id, ds_id, ""))

    # -------------------------------------------------------------------------
    # Pass 2: build the Mermaid source string
    # -------------------------------------------------------------------------
    lines = []

    # Diagram type and direction
    lines.append(f"---")
    lines.append(f"title: {job_name} — Data Lineage")
    lines.append(f"---")
    lines.append("graph LR")
    lines.append("")

    # Class definitions (colour coding by utility type)
    lines.append("    %% Colour coding by utility type")
    for utility, colours in UTILITY_COLOURS.items():
        class_name = utility.lower()
        lines.append(
            f"    classDef {class_name} "
            f"fill:{colours['fill']},"
            f"stroke:{colours['stroke']},"
            f"color:{colours['text']}"
        )
    lines.append(
        f"    classDef dataset "
        f"fill:{DATASET_COLOUR['fill']},"
        f"stroke:{DATASET_COLOUR['stroke']},"
        f"color:{DATASET_COLOUR['text']}"
    )
    lines.append("")

    # Dataset node definitions — cylinder shape using [(" ")]
    lines.append("    %% Dataset nodes (cylinders)")
    for dataset_name, ds_id in sorted(defined_datasets.items()):
        # Truncate long dataset names to keep diagram readable
        display_name = dataset_name
        lines.append(f'    {ds_id}[("{display_name}")]')
    lines.append("")

    # Step node definitions — rectangle using [" "]
    lines.append("    %% Step nodes (rectangles)")
    for step_data in all_steps_data:
        decoded_step  = step_data.get("decoded_step", {})
        step_name     = decoded_step.get("step_name", "UNKNOWN")
        utility_type  = decoded_step.get("utility_type", "PROGRAM")
        pgm           = decoded_step.get("pgm", "")
        step_id       = sanitize_node_id(step_name)

        # Two-line label: step name on top, utility/pgm on bottom
        label = f"{step_name}\\n{utility_type}"
        lines.append(f'    {step_id}["{label}"]')
    lines.append("")

    # Edge definitions — arrows connecting nodes
    lines.append("    %% Data flow edges")

    # Deduplicate edges (same edge may appear multiple times if a dataset
    # is listed more than once as input or output)
    seen_edges = set()
    for from_id, to_id, label in all_edges:
        edge_key = (from_id, to_id)
        if edge_key not in seen_edges:
            seen_edges.add(edge_key)
            if label:
                lines.append(f"    {from_id} -->|{label}| {to_id}")
            else:
                lines.append(f"    {from_id} --> {to_id}")
    lines.append("")

    # Apply colour classes to dataset nodes
    lines.append("    %% Apply colour classes")
    if defined_datasets:
        dataset_ids = " ".join(defined_datasets.values())
        lines.append(f"    class {','.join(defined_datasets.values())} dataset")

    # Apply colour classes to step nodes
    for step_data in all_steps_data:
        decoded_step = step_data.get("decoded_step", {})
        step_name    = decoded_step.get("step_name", "UNKNOWN")
        utility_type = decoded_step.get("utility_type", "PROGRAM")
        step_id      = sanitize_node_id(step_name)
        class_name   = utility_type.lower()
        lines.append(f"    class {step_id} {class_name}")

    return "\n".join(lines)
