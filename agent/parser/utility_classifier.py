# =============================================================================
# agent/parser/utility_classifier.py
#
# PROJECT : JUIA — JCL Utility Intelligence Agent
# PURPOSE : Look at the PGM= name of each parsed JCL step and assign
#           a utility type that the decoder will use to choose the
#           right decoding logic.
#
# WHAT THIS MODULE DOES (plain English):
#   After jcl_splitter.py breaks the JCL into step objects, each step
#   has a "pgm" field (e.g. "SORT", "IDCAMS", "IEBGENER"). But program
#   names in production JCL are not always the canonical IBM names.
#   Sites sometimes use aliases, internal names, or vendor sort products
#   (e.g. SYNCSORT instead of SORT).
#
#   This module maps all known program name variations to a small set
#   of standard utility type labels:
#
#     DFSORT   — IBM DFSORT or any compatible sort product
#     ICETOOL  — IBM ICETOOL (DFSORT's operator-based companion)
#     IDCAMS   — IBM Access Method Services (catalog and VSAM operations)
#     IEBGENER — IBM sequential copy and generate utility
#     IEBCOPY  — IBM partitioned dataset copy utility
#     PROGRAM  — A non-utility step (COBOL, Assembler, PL/I program etc.)
#
# INPUT:
#   A list of step dictionaries from jcl_splitter.split_jcl_into_steps()
#
# OUTPUT:
#   The same list of step dictionaries, with "utility_type" field populated
#   in every step.
#
# INTERACTION WITH OTHER MODULES:
#   This module is always called after jcl_splitter.py:
#
#   jcl_splitter.py             utility_classifier.py
#   -------------------         -------------------------
#   raw JCL text                step list with pgm names
#        │                               │
#        ▼                               ▼
#   step list (utility_type=None) → step list (utility_type filled)
#        │                               │
#        └───────────────────────────────┘
#                                        ▼
#                               passed to decoder modules
# =============================================================================


# =============================================================================
# SECTION 1 — UTILITY TYPE LABELS
# These are the standard labels used throughout JUIA.
# Changing a label here changes it everywhere in the project.
# =============================================================================

UTILITY_DFSORT   = "DFSORT"
UTILITY_ICETOOL  = "ICETOOL"
UTILITY_IDCAMS   = "IDCAMS"
UTILITY_IEBGENER = "IEBGENER"
UTILITY_IEBCOPY  = "IEBCOPY"
UTILITY_PROGRAM  = "PROGRAM"   # Non-utility step — COBOL, Assembler, etc.


# =============================================================================
# SECTION 2 — PGM NAME TO UTILITY TYPE MAPPING
#
# This dictionary maps known program names (and their variants)
# to a standard utility type label.
#
# Key   : the program name as it appears after PGM= in JCL (uppercase)
# Value : the standard utility type label from Section 1
#
# Why so many variants?
#   IBM has renamed and aliased programs over the decades.
#   Sites also install their own aliases in system libraries.
#   SORT is the same as DFSORT. SYNCSORT is a competing product
#   with compatible control card syntax.
# =============================================================================

PGM_TO_UTILITY_MAP = {

    # --- DFSORT and compatible sort products ---
    # IBM's primary sort product. PGM=SORT and PGM=DFSORT are equivalent.
    "SORT"        : UTILITY_DFSORT,
    "DFSORT"      : UTILITY_DFSORT,
    "ICEMAN"      : UTILITY_DFSORT,   # Historical IBM sort alias
    "SYNCSORT"    : UTILITY_DFSORT,   # Broadcom (formerly Syncsort) product
    "SORT2000"    : UTILITY_DFSORT,   # Another sort alias used at some sites

    # --- ICETOOL ---
    # IBM's operator-based companion to DFSORT.
    # Uses COPY/SELECT/SORT/STATS operators instead of raw DFSORT statements.
    "ICETOOL"     : UTILITY_ICETOOL,

    # --- ICEGENER ---
    # DFSORT-based replacement for IEBGENER (faster for large files).
    # Compatible with IEBGENER syntax — treated as IEBGENER for decoding.
    "ICEGENER"    : UTILITY_IEBGENER,

    # --- IDCAMS ---
    # IBM Access Method Services. Handles VSAM dataset management,
    # REPRO (copy), DEFINE, DELETE, LISTCAT, and catalog operations.
    "IDCAMS"      : UTILITY_IDCAMS,

    # --- IEBGENER ---
    # IBM sequential copy and generate utility.
    # Copies datasets, creates PDS members, reformats records.
    "IEBGENER"    : UTILITY_IEBGENER,

    # --- IEBCOPY ---
    # IBM partitioned dataset copy utility.
    # Copies, merges, and compresses PDS and PDSE libraries.
    "IEBCOPY"     : UTILITY_IEBCOPY,

}


# =============================================================================
# SECTION 3 — SYSIN CONTENT DETECTION
# For ambiguous cases, we look at the SYSIN content to confirm the type.
# =============================================================================

# Keywords that strongly indicate DFSORT control cards in SYSIN
DFSORT_SYSIN_KEYWORDS = [
    "SORT FIELDS",
    "INCLUDE COND",
    "OMIT COND",
    "OUTFIL",
    "OUTREC",
    "INREC",
    "SUM FIELDS",
    "MERGE FIELDS",
    "JOINKEYS",
]

# Keywords that strongly indicate ICETOOL operators in SYSIN
ICETOOL_SYSIN_KEYWORDS = [
    "COPY FROM",
    "SORT FROM",
    "SELECT FROM",
    "STATS FROM",
    "SPLICE FROM",
    "UNIQUE FROM",
    "DISPLAY FROM",
    "COUNT FROM",
    "RANGE FROM",
    "OCCUR FROM",
]

# Keywords that strongly indicate IDCAMS commands in SYSIN
IDCAMS_SYSIN_KEYWORDS = [
    "REPRO ",
    "DEFINE CLUSTER",
    "DEFINE ALIAS",
    "DELETE ",
    "LISTCAT",
    "BLDINDEX",
    "EXPORT ",
    "IMPORT ",
    "PRINT ",
    "VERIFY ",
    "IF LASTCC",
    "IF MAXCC",
]

# Keywords that strongly indicate IEBGENER control cards in SYSIN
IEBGENER_SYSIN_KEYWORDS = [
    "GENERATE ",
    "RECORD ",
    "MEMBER ",
]

# Keywords that strongly indicate IEBCOPY control cards in SYSIN
IEBCOPY_SYSIN_KEYWORDS = [
    "COPY OUTDD",
    "SELECT MEMBER",
    "EXCLUDE MEMBER",
    "INDD=",
    "COPYGRP",
]


# =============================================================================
# SECTION 4 — CLASSIFICATION FUNCTIONS
# =============================================================================

def classify_pgm_name(pgm_name):
    """
    Looks up a program name in the PGM_TO_UTILITY_MAP and returns
    the corresponding utility type label.

    The lookup is case-insensitive — production JCL uses uppercase
    but we normalise just in case.

    Parameters:
        pgm_name : the program name from the EXEC PGM= parameter (string)

    Returns:
        A utility type string, or None if the name is not in the map.
        None means the step is likely a non-utility program (COBOL etc.)
    """

    # Normalise to uppercase for comparison
    pgm_upper = pgm_name.upper().strip()

    # Remove any leading "PROC:" prefix added by the splitter
    if pgm_upper.startswith("PROC:"):
        # Procedure calls are treated as PROGRAM — we cannot decode them
        return UTILITY_PROGRAM

    # Look up in the map
    if pgm_upper in PGM_TO_UTILITY_MAP:
        return PGM_TO_UTILITY_MAP[pgm_upper]

    # Not found in map — it's a user program
    return None


def classify_by_sysin_content(sysin_content):
    """
    Examines the SYSIN content of a step to determine utility type.

    This is used as a fallback when the PGM name alone is ambiguous,
    or as a confirmation step for known utilities.

    The function checks the SYSIN text for characteristic keywords
    from each utility's control card syntax.

    Parameters:
        sysin_content : the full SYSIN text for a step (string)

    Returns:
        A utility type string, or None if no keywords were found.
    """

    if not sysin_content or len(sysin_content.strip()) == 0:
        return None

    # Convert to uppercase for case-insensitive keyword matching
    sysin_upper = sysin_content.upper()

    # Check each utility's keyword list and count matches.
    # More matches = more likely to be that utility type.
    # We use a scoring approach rather than first-match to handle
    # cases where one utility's keywords appear in another's content.

    scores = {
        UTILITY_DFSORT   : 0,
        UTILITY_ICETOOL  : 0,
        UTILITY_IDCAMS   : 0,
        UTILITY_IEBGENER : 0,
        UTILITY_IEBCOPY  : 0,
    }

    # Count keyword matches for each utility type
    for keyword in DFSORT_SYSIN_KEYWORDS:
        if keyword.upper() in sysin_upper:
            scores[UTILITY_DFSORT] += 1

    for keyword in ICETOOL_SYSIN_KEYWORDS:
        if keyword.upper() in sysin_upper:
            scores[UTILITY_ICETOOL] += 1

    for keyword in IDCAMS_SYSIN_KEYWORDS:
        if keyword.upper() in sysin_upper:
            scores[UTILITY_IDCAMS] += 1

    for keyword in IEBGENER_SYSIN_KEYWORDS:
        if keyword.upper() in sysin_upper:
            scores[UTILITY_IEBGENER] += 1

    for keyword in IEBCOPY_SYSIN_KEYWORDS:
        if keyword.upper() in sysin_upper:
            scores[UTILITY_IEBCOPY] += 1

    # Find the utility type with the highest score
    best_utility = None
    best_score   = 0

    for utility_type, score in scores.items():
        if score > best_score:
            best_score   = score
            best_utility = utility_type

    # Only return a result if at least one keyword matched
    if best_score > 0:
        return best_utility

    return None


def classify_one_step(step):
    """
    Determines the utility type for a single step dictionary and
    sets the "utility_type" field in that dictionary.

    Classification logic (in order of priority):
        1. Look up PGM name in the known utility map (most reliable)
        2. If PGM name is unknown, examine SYSIN content for keywords
        3. If neither gives a result, classify as PROGRAM

    Parameters:
        step : a step dictionary from jcl_splitter (modified in place)

    Returns:
        The utility type string that was assigned.

    Note on "in place" modification:
        Python dictionaries are passed by reference. When this function
        sets step["utility_type"] = something, it changes the original
        dictionary — the caller sees the change without needing a return
        value. We return the value too for convenience.
    """

    pgm_name = step.get("pgm", "UNKNOWN")

    # --- Step 1: Try PGM name lookup ---
    utility_from_pgm = classify_pgm_name(pgm_name)

    if utility_from_pgm is not None and utility_from_pgm != UTILITY_PROGRAM:
        # Known utility found by name — assign it
        step["utility_type"] = utility_from_pgm
        return utility_from_pgm

    # --- Step 2: Try SYSIN content analysis ---
    # This handles cases where PGM name wasn't recognised
    sysin_content = step.get("sysin_content", "")
    utility_from_sysin = classify_by_sysin_content(sysin_content)

    if utility_from_sysin is not None:
        step["utility_type"] = utility_from_sysin
        return utility_from_sysin

    # --- Step 3: Default to PROGRAM ---
    # Either the PGM name lookup returned PROGRAM explicitly (procedure call)
    # or the name was unknown and SYSIN gave no clues.
    # Either way this is a non-utility step — COBOL, Assembler, PL/I etc.
    step["utility_type"] = UTILITY_PROGRAM
    return UTILITY_PROGRAM


def classify_steps(steps):
    """
    Classifies all steps in the list, setting utility_type on each.

    This is the main function of this module. Call it after
    jcl_splitter.split_jcl_into_steps() to get fully classified steps.

    Parameters:
        steps : list of step dictionaries from the JCL splitter

    Returns:
        The same list with utility_type populated on every step.
        (The list is modified in place — the return value is the same
        object, returned for convenience so callers can chain calls.)

    Example:
        from agent.parser.jcl_splitter   import split_jcl_into_steps
        from agent.parser.utility_classifier import classify_steps

        jcl_text = open("jcl/TEST.jcl").read()
        steps    = split_jcl_into_steps(jcl_text)
        steps    = classify_steps(steps)

        for step in steps:
            print(step["step_name"], step["utility_type"])
    """

    for step in steps:
        classify_one_step(step)

    return steps


# =============================================================================
# SECTION 5 — REPORTING FUNCTIONS
# =============================================================================

def get_utility_breakdown(steps):
    """
    Returns a summary dictionary showing how many steps of each
    utility type are in the job.

    Parameters:
        steps : classified list of step dictionaries

    Returns:
        A dictionary mapping utility type to count.
        Example: {"DFSORT": 3, "IDCAMS": 2, "IEBGENER": 2, "PROGRAM": 1}
    """

    breakdown = {}

    for step in steps:
        utility = step.get("utility_type", "UNKNOWN")

        # If we haven't seen this utility type before, add it with count 0
        if utility not in breakdown:
            breakdown[utility] = 0

        breakdown[utility] += 1

    return breakdown


def get_decodable_steps(steps):
    """
    Returns only the steps that JUIA can decode (utility steps).
    Filters out PROGRAM steps since they have no control cards.

    Parameters:
        steps : classified list of step dictionaries

    Returns:
        A list containing only steps where utility_type is not PROGRAM.
    """

    decodable = []

    for step in steps:
        if step.get("utility_type") != UTILITY_PROGRAM:
            decodable.append(step)

    return decodable
