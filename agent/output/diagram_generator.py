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
#   Steps    : ["STEP<br/>UTILITY"] — rectangle
#   Color-coded by utility type via classDef.
#
# OUTPUT:
#   A string containing the full Mermaid diagram source.
#   The caller writes it to lineage.mmd in the job's run folder.
#   Also embedded inside the Markdown migration spec.
#
# CHANGE LOG (lineage completeness fix):
#   1. get_step_files: DD-statement datasets are now merged with the Phase 3
#      lists on each side (union). Previously the DD statements were used
#      only when BOTH input_files and output_files were empty, so a step
#      with partial Phase 3 data lost datasets, including temporary
#      datasets (&&NAME) that link one step to the next.
#   2. extract_files_from_dd: DISP is now parsed properly.
#        - DISP=(,PASS) and DISP=(,CATLG) are NEW (output). The old
#          docstring listed (,PASS) as input, which was wrong.
#        - DISP=OLD is input, DISP=MOD is output. Both were dropped before.
#        - DD name conventions are checked BEFORE DISP, so SORTOUT with
#          DISP=SHR is an output, not an input.
#        - Temporary datasets (&&NAME) are kept. They carry the lineage
#          between steps and were dropped before, which broke the chain.
#        - Only SYS1. and SYS2. datasets are skipped, not every DSN that
#          starts with SYS.
#        - The DD parameter text is read from several possible key names
#          (parameters, params, raw, dsn, disp) in case the Phase 2 parser
#          uses a different key than "parameters".
#   3. generate_mermaid_diagram:
#        - dataset node IDs get a DS_ prefix and collisions are resolved,
#          so a dataset can never clash with a step ID or another dataset.
#        - step labels use <br/> instead of \n.
#        - unknown utility types fall back to the PROGRAM colour class.
#        - a warning lists steps for which no datasets were found, and
#          prints the DD keys of the first such step to help diagnosis.
# =============================================================================


# =============================================================================
# SECTION 1 — IMPORTS
# =============================================================================

# re — built into Python.
# Used to: sanitize dataset names for use as Mermaid node IDs, and to parse
# DSN and DISP values out of DD statement text.
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


def dataset_node_id(dataset_name):
    """
    Builds the base Mermaid node ID for a dataset.

    The DS_ prefix keeps dataset IDs separate from step IDs and avoids
    Mermaid reserved words (for example a dataset named END).
    Temporary datasets (&&NAME) get a DS_TMP_ prefix.

    Parameters:
        dataset_name : dataset name as found in the DD statement

    Returns:
        A sanitized node ID string.
    """

    if dataset_name.startswith("&&"):
        return "DS_TMP_" + sanitize_node_id(dataset_name[2:])
    return "DS_" + sanitize_node_id(dataset_name)


def dataset_display_name(dataset_name):
    """
    Returns the label shown inside the dataset cylinder.

    Temporary datasets are shown as "(temp) NAME". Double quotes are
    replaced because they would terminate the Mermaid label string.
    """

    if dataset_name.startswith("&&"):
        label = f"(temp) {dataset_name[2:]}"
    else:
        label = dataset_name
    return label.replace('"', "'")


# =============================================================================
# SECTION 4 — FILE EXTRACTOR
# Extracts input and output file names from decoded and original step data.
# =============================================================================

# DD names that never carry a dataset relevant to lineage
SKIP_DDNAMES = {
    "SYSPRINT", "SYSOUT", "SYSABEND", "SYSUDUMP", "SYSIN",
    "STEPLIB", "JOBLIB", "SYSTSPRT", "SYSTSIN",
    "TOOLIN", "TOOLMSG", "DFSMSG", "SYMNAMES", "SYMNOUT",
    "SYSCNTL", "CEEOPTS", "SYSMDUMP",
}

# DD name conventions. Checked before DISP.
INPUT_DD_PATTERN = re.compile(
    r'^(SYSUT1|SORTIN\d*|SEQIN|VSAMIN|INDD|INPUT\d*|INFILE\d*|IN\d*)$'
)
OUTPUT_DD_PATTERN = re.compile(
    r'^(SYSUT2|SORTOUT|SORTOF\d*|SEQOUT|VSAMOUT|OUTDD|OUTPUT\d*|OUTFILE\d*|'
    r'OUT\d*|HIGHVAL|LOWVAL)$'
)

# Datasets that are system libraries, not business data
SYSTEM_DSN_PREFIXES = ("SYS1.", "SYS2.")

# Keys the Phase 2 parser may use for the DD parameter text
PARAMETER_KEYS = ("parameters", "params", "parm_string", "raw",
                  "text", "statement", "line")


def get_step_files(decoded_step, original_step):
    """
    Returns the input and output dataset names for one step.

    For decoded steps (Phase 3 success), uses the input_files and
    output_files lists from the decoded result.

    The DD statements from the original Phase 2 parsed step are then
    merged in on each side: any dataset found in a DD statement that is
    not already in the Phase 3 list is added. This matters for temporary
    datasets (&&NAME) and for steps where Phase 3 listed only some of the
    files, because a missing dataset breaks the lineage chain between
    steps.

    Parameters:
        decoded_step  : decoded result dict from Phase 3 (may have empty files)
        original_step : original step dict from Phase 2 parser

    Returns:
        A tuple of (input_files, output_files) — both lists of strings.
    """

    input_files  = list(decoded_step.get("input_files", []) or [])
    output_files = list(decoded_step.get("output_files", []) or [])

    # Merge in datasets found in the DD statements (union, Phase 3 first)
    if original_step:
        dd_inputs, dd_outputs = extract_files_from_dd(original_step)
        for name in dd_inputs:
            if name not in input_files:
                input_files.append(name)
        for name in dd_outputs:
            if name not in output_files:
                output_files.append(name)

    # Filter out non-dataset entries like SYSOUT=*, SYSPRINT, DUMMY
    def is_dataset(name):
        """Keep real dataset names and discard print/dummy DD targets."""
        upper = str(name).upper().strip()
        return bool(upper) and not (
            upper.startswith("SYSOUT")
            or upper.startswith("SYSPRINT")
            or upper in {"DUMMY", "NULLFILE"}
        )

    input_files  = [f for f in input_files  if is_dataset(f)]
    output_files = [f for f in output_files if is_dataset(f)]

    return input_files, output_files


def read_dd_fields(dd):
    """
    Returns (ddname, parameter_text) for one DD statement dictionary.

    The parameter text is upper-cased. If the Phase 2 parser stored the
    DSN or DISP under their own keys instead of inside one parameter
    string, they are added to the text so the same regexes work.
    """

    ddname = str(dd.get("ddname") or dd.get("dd_name") or dd.get("name") or "")
    ddname = ddname.upper().strip()

    text = ""
    for key in PARAMETER_KEYS:
        value = dd.get(key)
        if isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value)
        if isinstance(value, str) and value.strip():
            text = value
            break
    text = text.upper()

    if "DSN=" not in text and "DSNAME=" not in text:
        for key in ("dsn", "dsname", "dataset"):
            if dd.get(key):
                text += f",DSN={str(dd[key]).upper()}"
                break

    if "DISP=" not in text and dd.get("disp"):
        text += f",DISP={str(dd['disp']).upper()}"

    return ddname, text


def parse_disp_status(parameter_text):
    """
    Returns the DISP status (first sub-parameter) from DD parameter text.

    Examples:
        DISP=SHR                       → "SHR"
        DISP=(OLD,KEEP)                → "OLD"
        DISP=(NEW,CATLG,DELETE)        → "NEW"
        DISP=(,PASS)                   → "NEW"   (omitted status defaults to NEW)
        DISP=(MOD,CATLG)               → "MOD"
        (no DISP at all)               → "NEW"   (JCL default)
    """

    match = re.search(r'DISP=(\([^)]*\)|[A-Z]+)', parameter_text)
    if not match:
        return "NEW"

    inner = match.group(1).strip("()")
    first = inner.split(",")[0].strip()
    return first if first else "NEW"


def classify_dd(ddname, parameter_text):
    """
    Classifies one DD as "input", "output", or None (cannot tell).

    Order of checks:
        1. DD name conventions (SORTIN, SYSUT1, SORTOUT, SYSUT2, ...)
        2. DISP status: SHR/OLD → input, NEW/MOD → output
    """

    if INPUT_DD_PATTERN.match(ddname):
        return "input"
    if OUTPUT_DD_PATTERN.match(ddname):
        return "output"

    status = parse_disp_status(parameter_text)
    if status in ("SHR", "OLD"):
        return "input"
    if status in ("NEW", "MOD"):
        return "output"

    return None


def extract_files_from_dd(original_step):
    """
    Extracts input and output dataset names from a step's DD statements.

    Classification (see classify_dd):
        - DD name conventions first: SYSUT1/SORTIN → input,
          SYSUT2/SORTOUT/OUTFIL DDs → output.
        - Then DISP status: SHR or OLD → input, NEW or MOD → output.
        - DISP=(,PASS) means NEW,PASS, so it is an output passed on to a
          later step. A later DD with DISP=(OLD,...) on the same &&name
          reads it, which links the two steps in the diagram.

    Temporary datasets (&&NAME) are kept so lineage continues across steps.

    Parameters:
        original_step : step dictionary from Phase 2 parser

    Returns:
        A tuple of (input_files, output_files) — both lists of strings.
    """

    input_files  = []
    output_files = []

    for dd in original_step.get("dd_statements", []):

        ddname, parameters = read_dd_fields(dd)

        if ddname in SKIP_DDNAMES:
            continue

        # Extract DSN value
        dsn_match = re.search(r'DSN(?:AME)?=([^,\s(]+(?:\([^)]*\))?)',
                              parameters)
        if not dsn_match:
            continue

        dsn = dsn_match.group(1).strip()

        # Skip system libraries
        if dsn.startswith(SYSTEM_DSN_PREFIXES):
            continue

        direction = classify_dd(ddname, parameters)

        if direction == "input":
            if dsn not in input_files:
                input_files.append(dsn)
        elif direction == "output":
            if dsn not in output_files:
                output_files.append(dsn)

    return input_files, output_files


# =============================================================================
# SECTION 5 — DIAGRAM BUILDER
# =============================================================================

def register_dataset(dataset_name, defined_datasets, used_ids):
    """
    Returns the node ID for a dataset, creating it on first use.

    If two different dataset names sanitize to the same ID, a numeric
    suffix is added so they remain separate nodes.
    """

    if dataset_name in defined_datasets:
        return defined_datasets[dataset_name]

    base_id = dataset_node_id(dataset_name)
    node_id = base_id
    counter = 2
    while node_id in used_ids:
        node_id = f"{base_id}_{counter}"
        counter += 1

    used_ids.add(node_id)
    defined_datasets[dataset_name] = node_id
    return node_id


def utility_class_name(utility_type):
    """
    Returns the Mermaid class name for a utility type. Unknown types use
    the PROGRAM class so the class reference always exists.
    """

    if utility_type in UTILITY_COLOURS:
        return utility_type.lower()
    return "program"


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
                          "decoded"  : Phase 3 decoded result
                          "original" : Phase 2 parsed step

    Returns:
        The complete Mermaid diagram as a string.
    """

    # We build the diagram in these parts:
    #   1. classDef colour definitions
    #   2. Dataset node definitions
    #   3. Step node definitions
    #   4. Edge definitions (arrows)
    #   5. Class assignments (apply colours)

    defined_datasets = {}   # dataset_name → node id
    used_ids         = set()
    all_edges        = []   # list of (from_id, to_id, label) tuples

    steps_without_files = []   # step names that produced no datasets
    first_empty_original = None

    # -------------------------------------------------------------------------
    # Pass 1: collect all nodes and edges
    # -------------------------------------------------------------------------
    for step_data in all_steps_data:

        decoded_step  = step_data.get("decoded", {})
        original_step = step_data.get("original", {})

        step_name = decoded_step.get("step_name", "UNKNOWN")
        step_id   = sanitize_node_id(step_name)

        # Get input and output files for this step
        input_files, output_files = get_step_files(decoded_step, original_step)

        if not input_files and not output_files:
            steps_without_files.append(step_name)
            if first_empty_original is None:
                first_empty_original = original_step

        # Input datasets: dataset → step
        for dataset_name in input_files:
            ds_id = register_dataset(dataset_name, defined_datasets, used_ids)
            all_edges.append((ds_id, step_id, ""))

        # Output datasets: step → dataset
        for dataset_name in output_files:
            ds_id = register_dataset(dataset_name, defined_datasets, used_ids)
            all_edges.append((step_id, ds_id, ""))

    # Diagnostics: tell the user which steps have no lineage and why
    if steps_without_files:
        print(f"  [WARN] No datasets found for {len(steps_without_files)} "
              f"step(s): {', '.join(steps_without_files)}")
        dd_list = (first_empty_original or {}).get("dd_statements", [])
        if not dd_list:
            print("  [WARN] The Phase 2 step has no 'dd_statements' entries. "
                  "Check the key name in the parser output.")
        else:
            print(f"  [WARN] DD keys available in the first such step: "
                  f"{sorted(dd_list[0].keys())}")

    # -------------------------------------------------------------------------
    # Pass 2: build the Mermaid source string
    # -------------------------------------------------------------------------
    lines = []

    # Diagram type and direction
    lines.append("---")
    lines.append(f'title: "{job_name} — Data Lineage"')
    lines.append("---")
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
        display_name = dataset_display_name(dataset_name)
        lines.append(f'    {ds_id}[("{display_name}")]')
    lines.append("")

    # Step node definitions — rectangle using [" "]
    lines.append("    %% Step nodes (rectangles)")
    for step_data in all_steps_data:
        decoded_step = step_data.get("decoded", {})
        step_name    = decoded_step.get("step_name", "UNKNOWN")
        utility_type = decoded_step.get("utility_type", "PROGRAM")
        pgm          = decoded_step.get("pgm", "")
        step_id      = sanitize_node_id(step_name)

        # Two-line label: step name on top, utility (or program name) below
        second_line = utility_type
        if utility_type == "PROGRAM" and pgm:
            second_line = pgm
        label = f"{step_name}<br/>{second_line}".replace('"', "'")
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
        lines.append(f"    class {','.join(defined_datasets.values())} dataset")

    # Apply colour classes to step nodes
    for step_data in all_steps_data:
        decoded_step = step_data.get("decoded", {})
        step_name    = decoded_step.get("step_name", "UNKNOWN")
        utility_type = decoded_step.get("utility_type", "PROGRAM")
        step_id      = sanitize_node_id(step_name)
        lines.append(f"    class {step_id} {utility_class_name(utility_type)}")

    return "\n".join(lines)
