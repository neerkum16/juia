# =============================================================================
# agent/parser/jcl_splitter.py
#
# PROJECT : JUIA — JCL Utility Intelligence Agent
# PURPOSE : Read raw JCL text and split it into a structured list of
#           step objects that downstream components can work with.
#
# WHAT THIS MODULE DOES (plain English):
#   A JCL job is a text file with many different statement types mixed
#   together. Before the agent can decode anything, it needs to separate
#   the JCL into individual job steps and extract the key information
#   from each step:
#     - What is the step name?
#     - What program does it run (PGM=)?
#     - What files does it use (DD statements)?
#     - What control cards does it pass to the program (SYSIN content)?
#
# SYSIN VARIATIONS HANDLED:
#   Production JCL uses four different patterns for SYSIN:
#
#   1. INLINE — control cards coded directly in the JCL:
#        //SYSIN  DD *
#          SORT FIELDS=(1,9,CH,A)
#        /*
#
#   2. EXTERNAL PDS MEMBER — control cards stored in a CNTL library:
#        //SYSIN  DD DSN=SYS2.CNTL(WIRESORT),DISP=SHR
#      The parser extracts the member name and looks for a matching
#      file in the local cntl/ folder (e.g. cntl/WIRESORT.jcl).
#
#   3. EXTERNAL SEQUENTIAL — control cards in a sequential dataset:
#        //SYSIN  DD DSN=WIRE.SORT.CNTL,DISP=SHR
#      The parser looks for cntl/WIRE.SORT.CNTL.jcl locally.
#
#   4. DUMMY — no control cards (straight copy for IEBGENER etc.):
#        //SYSIN  DD DUMMY
#
# OUTPUT FORMAT — each step is a Python dictionary:
#   {
#     "step_name"      : "STEP030",
#     "pgm"            : "SORT",
#     "exec_statement" : "//STEP030  EXEC PGM=SORT",
#     "dd_statements"  : [ { "ddname", "parameters", "is_inline",
#                             "inline_content" }, ... ],
#     "sysin_content"  : "  INCLUDE COND=...\n  SORT FIELDS=...",
#     "sysin_source"   : "inline",          ← see SYSIN_SOURCE constants
#     "sysin_dsn"      : "",                ← DSN if external, else ""
#     "raw_lines"      : [ "//STEP030  EXEC PGM=SORT", ... ],
#     "utility_type"   : None               ← filled by utility_classifier
#   }
#
# SYSIN SOURCE VALUES:
#   "inline"               — DD * with content read from JCL
#   "dummy"                — DD DUMMY, no content
#   "external_resolved"    — DSN found in local cntl/ folder
#   "external_unresolved"  — DSN not found locally, content is empty
#   "none"                 — no SYSIN DD statement in this step
# =============================================================================


# =============================================================================
# SECTION 1 — IMPORTS
# =============================================================================

# os — built into Python.
# Used to: search for files in the local cntl/ folder, build paths.
import os

# re — built into Python.
# "re" stands for regular expressions — pattern matching in text.
# Used to: extract PGM= values and DSN= values from JCL parameters.
import re


# =============================================================================
# SECTION 2 — CONSTANTS
# =============================================================================

# Every JCL statement starts with // in columns 1-2
JCL_STATEMENT_PREFIX = "//"

# JCL comment lines start with //*
JCL_COMMENT_PREFIX = "//*"

# Inline data ends with /* on its own line
JCL_INLINE_END = "/*"

# Maximum useful column width of a JCL statement
# Columns 73-80 are a sequence field used by mainframe editors — ignore them
JCL_MAX_USEFUL_COLUMN = 72

# Local folder where external CNTL library members are stored
# Users upload their CNTL members here as .jcl or .txt files
CNTL_LOCAL_FOLDER = "cntl"

# File extensions to try when looking for a CNTL member locally
# .cntl is the project standard — tried first
CNTL_FILE_EXTENSIONS = [".cntl", ".jcl", ".txt", ""]

# SYSIN source label constants
# These are stored in every step so the decoder knows how SYSIN was obtained
SYSIN_SOURCE_INLINE              = "inline"
SYSIN_SOURCE_DUMMY               = "dummy"
SYSIN_SOURCE_EXTERNAL_RESOLVED   = "external_resolved"
SYSIN_SOURCE_EXTERNAL_UNRESOLVED = "external_unresolved"
SYSIN_SOURCE_NONE                = "none"


# =============================================================================
# SECTION 3 — LINE CLASSIFICATION FUNCTIONS
# =============================================================================

def is_comment_line(line):
    """
    Returns True if this line is a JCL comment (//*).
    Example: //* This is a comment
    """
    return line.startswith(JCL_COMMENT_PREFIX)


def is_inline_data_end(line):
    """
    Returns True if this line is the /* delimiter ending inline data.
    The /* must be at the start of the line and not part of //.
    Example: /*
    """
    return (line.startswith(JCL_INLINE_END) and
            not line.startswith(JCL_STATEMENT_PREFIX))


def is_jcl_statement(line):
    """
    Returns True if this is a JCL statement (starts with //, not //*).
    Examples:
        //STEP010  EXEC PGM=IDCAMS  → True
        //* comment                  → False
        REPRO INFILE(...)            → False (inline data)
    """
    return (line.startswith(JCL_STATEMENT_PREFIX) and
            not line.startswith(JCL_COMMENT_PREFIX))


def is_continuation_line(line):
    """
    Returns True if this JCL line continues a previous statement.

    A continuation line starts with // followed immediately by spaces.
    The blank name field tells JCL that this is not a new statement
    but a continuation of parameters from the line above.

    Example:
        //SORTOUT  DD DSN=WIRE.WORK.EXTRT.SEQ,    ← ends with comma
        //            DISP=(NEW,CATLG,DELETE),      ← continuation
        //            SPACE=(CYL,(10,5))            ← continuation
    """
    if not is_jcl_statement(line):
        return False

    # After //, check if the very next character is a space
    # which means the name field (columns 3+) is blank
    rest = line[2:]
    return len(rest) > 0 and rest[0] == ' '


# =============================================================================
# SECTION 4 — JCL PARSING HELPER FUNCTIONS
# =============================================================================

def parse_jcl_statement(line):
    """
    Breaks a JCL statement into (name, operation, parameters).

    JCL format:  //name  OPERATION  parameters
    Examples:
        "//STEP010  EXEC PGM=IDCAMS"
            → ("STEP010", "EXEC", "PGM=IDCAMS")
        "//SORTIN   DD DSN=WIRE.DAILY.INPUT.SEQ,DISP=SHR"
            → ("SORTIN", "DD", "DSN=WIRE.DAILY.INPUT.SEQ,DISP=SHR")
        "//SYSIN    DD DSN=SYS2.CNTL(WIREEXT),DISP=SHR"
            → ("SYSIN", "DD", "DSN=SYS2.CNTL(WIREEXT),DISP=SHR")

    Returns a tuple of three strings. Missing parts are empty strings.
    """

    # Remove leading // and trim to 72 columns
    content = line[2:JCL_MAX_USEFUL_COLUMN + 2]

    # Split on whitespace — handles any number of spaces between fields
    parts = content.split()

    if len(parts) == 0:
        return ("", "", "")
    elif len(parts) == 1:
        return (parts[0], "", "")
    elif len(parts) == 2:
        return (parts[0], parts[1], "")
    else:
        # Rejoin everything from index 2 onwards as the parameters
        return (parts[0], parts[1], " ".join(parts[2:]))


def extract_pgm_from_exec(exec_parameters):
    """
    Extracts the program name from EXEC statement parameters.

    Examples:
        "PGM=IDCAMS"              → "IDCAMS"
        "PGM=SORT"                → "SORT"
        "PGM=ACCTPAY,REGION=4M"   → "ACCTPAY"
        "PROC=MYPROCNAME"         → "PROC:MYPROCNAME"
        "MYPROCNAME"              → "PROC:MYPROCNAME"
    """

    # Search for PGM=something
    # [^,\s]+ means: one or more characters that are NOT a comma or space.
    # This stops the match at the comma before REGION=4M, COND=, etc.
    # Without this fix, PGM=ACCTPAY,REGION=4M would return "ACCTPAY,REGION=4M"
    pgm_match = re.search(r'PGM=([^,\s]+)', exec_parameters, re.IGNORECASE)
    if pgm_match:
        return pgm_match.group(1)

    # Search for PROC=something
    proc_match = re.search(r'PROC=([^,\s]+)', exec_parameters, re.IGNORECASE)
    if proc_match:
        return f"PROC:{proc_match.group(1)}"

    # If just a bare word with no = it is a proc name
    parts = exec_parameters.strip().split()
    if parts:
        first_parameter = parts[0].split(",", 1)[0]
        if first_parameter and '=' not in first_parameter:
            return f"PROC:{first_parameter}"

    return "UNKNOWN"


def is_inline_sysin(parameters):
    """
    Returns True if this DD introduces inline data (DD * or DD DATA).

    Examples that return True:
        "*"       → True
        "DATA"    → True

    Examples that return False:
        "DSN=SYS2.CNTL(WIREEXT),DISP=SHR"  → False (external)
        "DUMMY"                              → False (dummy)
        "SYSOUT=*"                           → False (sysout)
    """
    params_clean = parameters.strip().upper()
    # DD *,DLM=xx and DD DATA,DLM=xx introduce inline records too.
    return bool(re.match(r"^(?:\*|DATA)(?:\s*(?:,|$))", params_clean))


def is_dummy_sysin(parameters):
    """
    Returns True if this DD statement is DD DUMMY.
    Example: //SYSIN DD DUMMY
    """
    return parameters.strip().upper() == "DUMMY"


def is_external_sysin(parameters):
    """
    Returns True if SYSIN points to an external dataset (DSN=...).

    Examples that return True:
        "DSN=SYS2.CNTL(WIREEXT),DISP=SHR"   → True
        "DSN=WIRE.SORT.CNTL,DISP=SHR"        → True

    Examples that return False:
        "*"                                   → False (inline)
        "DUMMY"                               → False (dummy)
    """
    # JCL permits DISP before DSN; inspect every parameter, not only the first.
    return bool(re.search(r"(?:^|,)\s*DSN\s*=", parameters, re.IGNORECASE))


def inline_delimiter(parameters):
    """Return a DD's optional DLM marker, or the default ``/*`` marker."""
    match = re.search(r"(?:^|,)\s*DLM\s*=\s*([^,\s]+)", parameters, re.I)
    return match.group(1).strip("'\"") if match else JCL_INLINE_END


def detect_unsupported_jcl_constructs(jcl_text, job_cards=None):
    """Identify JCL features that require expansion before safe conversion.

    This is a guard, not an attempt to execute JCL. The orchestrator can use
    these messages to mark a run incomplete instead of silently skipping work.
    Inline data is skipped so its records are not mistaken for JCL control.
    When supplied, ``job_cards`` receives the names of real JOB statements;
    the orchestrator uses them for identity and multi-job validation.
    """
    issues = []
    job_count = 0
    in_inline = False
    delimiter = JCL_INLINE_END
    last_operation = ""
    for raw_line in jcl_text.splitlines():
        line = raw_line.rstrip()
        if in_inline:
            if is_continuation_line(line) and "DLM=" in line.upper():
                delimiter = inline_delimiter(line[2:])
                continue
            if line.strip() == delimiter:
                in_inline = False
            elif delimiter == JCL_INLINE_END and is_jcl_statement(line):
                in_inline = False
            else:
                continue
        if not is_jcl_statement(line):
            continue
        if is_continuation_line(line):
            unnamed_control = re.match(
                r"^//\s+(IF|ELSE|ENDIF|INCLUDE|SET|JCLLIB|PROC|PEND)\b",
                line, re.I,
            )
            if unnamed_control:
                issues.append(
                    f"Unsupported JCL {unnamed_control.group(1).upper()} statement: {line}"
                )
                continue
            if last_operation == "EXEC" and re.search(r"\bCOND\s*=", line, re.I):
                issues.append(f"Unsupported conditional EXEC: {line}")
            if last_operation == "EXEC" and re.search(r"\bPROC\s*=", line, re.I):
                issues.append(f"Unexpanded procedure call: {line}")
            symbols = re.findall(r"(?<!&)&(?!&)([A-Za-z@$#][A-Za-z0-9@$#]*)", line)
            if any(not symbol.upper().startswith("SYS") for symbol in symbols):
                issues.append(f"Unresolved JCL symbolic parameter: {line}")
            continue
        name, operation, parameters = parse_jcl_statement(line)
        op = operation.upper()
        # Unnamed control statements put the operation in the first token.
        if name.upper() in {"INCLUDE", "IF", "ELSE", "ENDIF", "PROC", "PEND", "SET", "JCLLIB"}:
            op = name.upper()
        last_operation = op
        if op == "JOB":
            job_count += 1
            if job_cards is not None:
                job_cards.append(name)
        if op in {"INCLUDE", "IF", "ELSE", "ENDIF", "PROC", "PEND", "SET", "JCLLIB"}:
            issues.append(f"Unsupported JCL {op} statement: {line}")
        if op == "EXEC" and extract_pgm_from_exec(parameters).startswith("PROC:"):
            issues.append(f"Unexpanded procedure call: {line}")
        if op == "EXEC" and re.search(r"\bCOND\s*=", parameters, re.I):
            issues.append(f"Unsupported conditional EXEC: {line}")
        # A single ampersand denotes a symbolic parameter; doubled
        # ampersands denote temporary datasets and remain supported.
        symbols = re.findall(r"(?<!&)&(?!&)([A-Za-z@$#][A-Za-z0-9@$#]*)", line)
        if any(not symbol.upper().startswith("SYS") for symbol in symbols):
            issues.append(f"Unresolved JCL symbolic parameter: {line}")
        if op == "DD" and is_inline_sysin(parameters):
            in_inline = True
            delimiter = inline_delimiter(parameters)
    if job_count > 1:
        issues.append(f"JCL contains {job_count} JOB cards; process one job per file")
    return issues


# =============================================================================
# SECTION 5 — EXTERNAL SYSIN RESOLUTION
# Functions to find and read control cards from the local cntl/ folder.
# =============================================================================

def extract_dsn_from_parameters(dd_parameters):
    """
    Extracts the dataset name from a DD statement's parameters.

    Examples:
        "DSN=SYS2.CNTL(WIREEXT),DISP=SHR"  → "SYS2.CNTL(WIREEXT)"
        "DSN=WIRE.SORT.CNTL,DISP=SHR"       → "WIRE.SORT.CNTL"
        "DSN=&&TEMPCNTL,DISP=SHR"            → "&&TEMPCNTL"

    Parameters:
        dd_parameters : the parameters string from a DD statement

    Returns:
        The dataset name string, or empty string if not found.
    """

    # Look for DSN=something — stop at the first comma or end of string
    # [^,]+ means "one or more characters that are not a comma"
    dsn_match = re.search(r'DSN=([^,\s]+)', dd_parameters, re.IGNORECASE)

    if dsn_match:
        return dsn_match.group(1).strip()

    return ""


def extract_member_from_dsn(dsn):
    """
    Extracts the PDS member name from a dataset name if present.

    Examples:
        "SYS2.CNTL(WIREEXT)"    → "WIREEXT"
        "PROD.CNTLLIB(WIRESPL)"  → "WIRESPL"
        "WIRE.SORT.CNTL"         → "" (no member — sequential dataset)

    Parameters:
        dsn : a dataset name string

    Returns:
        The member name string, or empty string if no member specified.
    """

    # Look for (membername) pattern in the DSN
    member_match = re.search(r'\(([^)]+)\)', dsn)

    if member_match:
        return member_match.group(1).strip()

    return ""


def _case_insensitive_file(directory, filename):
    """Find one filename in a directory regardless of host case rules."""
    if not directory or not os.path.isdir(directory):
        return None
    matches = [os.path.join(directory, entry) for entry in os.listdir(directory)
               if entry.casefold() == filename.casefold()
               and os.path.isfile(os.path.join(directory, entry))]
    return matches[0] if len(matches) == 1 else None


def _case_insensitive_directory(parent, name):
    """Find one child directory even on a case-sensitive filesystem."""
    if not os.path.isdir(parent):
        return None
    matches = [os.path.join(parent, entry) for entry in os.listdir(parent)
               if entry.casefold() == name.casefold()
               and os.path.isdir(os.path.join(parent, entry))]
    return matches[0] if len(matches) == 1 else None


def find_local_cntl_file(dsn, cntl_folder, allow_member_fallback=True):
    """
    Searches the local cntl/ folder for a file matching the given DSN.

    SEARCH STRATEGY:
        For a PDS member like SYS2.CNTL(WIREEXT):
            Tries: cntl/WIREEXT.jcl
                   cntl/WIREEXT.txt
                   cntl/WIREEXT.cntl
                   cntl/WIREEXT

        For a sequential dataset like WIRE.SORT.CNTL:
            Tries: cntl/WIRE.SORT.CNTL.jcl
                   cntl/WIRE.SORT.CNTL.txt
                   cntl/WIRE.SORT.CNTL.cntl
                   cntl/WIRE.SORT.CNTL

        Search is case-insensitive on the member name.

    Parameters:
        dsn         : the full dataset name from the DD statement
        cntl_folder : path to the local cntl/ folder

    Returns:
        The full path to the local file if found, or None if not found.
    """

    # A PDS member belongs to a library. Prefer names retaining both pieces
    # so two libraries may safely contain a member with the same name.
    member_name = extract_member_from_dsn(dsn)
    base_dsn = re.sub(r'\([^)]*\)', '', dsn).strip()

    if member_name:
        library_dir = _case_insensitive_directory(cntl_folder, base_dsn)
        for extension in CNTL_FILE_EXTENSIONS:
            for directory, filename in (
                (library_dir, member_name + extension),
                (cntl_folder, f"{base_dsn}({member_name}){extension}"),
                (cntl_folder, f"{base_dsn}.{member_name}{extension}"),
            ):
                match = _case_insensitive_file(directory, filename)
                if match:
                    return match
        # Existing single-library projects can retain bare member files.
        # A caller disables this fallback when a job references the same
        # member name from multiple PDS libraries.
        if allow_member_fallback:
            for extension in CNTL_FILE_EXTENSIONS:
                match = _case_insensitive_file(cntl_folder, member_name + extension)
                if match:
                    return match
        return None

    # Sequential datasets have no member; their full DSN is the identity.
    for extension in CNTL_FILE_EXTENSIONS:
        match = _case_insensitive_file(cntl_folder, base_dsn + extension)
        if match:
            return match

    # Not found
    return None


def resolve_external_sysin(dsn, cntl_folder, allow_member_fallback=True):
    """
    Attempts to read external SYSIN content from the local cntl/ folder.

    This is called when a SYSIN DD statement points to an external
    dataset (e.g. //SYSIN DD DSN=SYS2.CNTL(WIREEXT),DISP=SHR).

    Steps:
        1. Check if the cntl/ folder exists locally
        2. Search for a file matching the DSN or member name
        3. If found, read and return the file contents
        4. If not found, return empty content and a warning message

    Parameters:
        dsn         : the dataset name from the SYSIN DD statement
        cntl_folder : path to the local cntl/ folder

    Returns:
        A tuple of (content, source_label, warning_message):
            content       : the file contents as string (empty if not found)
            source_label  : SYSIN_SOURCE_EXTERNAL_RESOLVED or _UNRESOLVED
            warning       : a human-readable message about what happened
    """

    # Check if the local cntl folder exists at all
    if not os.path.exists(cntl_folder):
        warning = (f"cntl/ folder not found at '{cntl_folder}'. "
                   f"Create the folder and upload '{dsn}' as a .jcl file.")
        return ("", SYSIN_SOURCE_EXTERNAL_UNRESOLVED, warning)

    # Search for a matching local file
    local_file_path = find_local_cntl_file(
        dsn, cntl_folder, allow_member_fallback=allow_member_fallback
    )

    if local_file_path is None:
        # Provide a helpful message telling the user exactly what to do
        member_name = extract_member_from_dsn(dsn)
        filename_hint = dsn + ".cntl"
        warning = (f"External SYSIN not resolved: '{dsn}'. "
                   f"Upload the control cards as '{cntl_folder}/{filename_hint}'.")
        if member_name and not allow_member_fallback:
            warning += " Bare member filenames are ambiguous across libraries."
        return ("", SYSIN_SOURCE_EXTERNAL_UNRESOLVED, warning)

    # Found the file — read it
    try:
        with open(local_file_path, encoding="utf-8", errors="replace") as f:
            content = f.read()

        warning = f"Resolved from local file: {local_file_path}"
        return (content, SYSIN_SOURCE_EXTERNAL_RESOLVED, warning)

    except Exception as read_error:
        warning = f"Found '{local_file_path}' but could not read it: {read_error}"
        return ("", SYSIN_SOURCE_EXTERNAL_UNRESOLVED, warning)


# =============================================================================
# SECTION 6 — STEP OBJECT BUILDER
# =============================================================================

def create_new_step(step_name, pgm, exec_statement_line):
    """
    Creates a new empty step dictionary with all required fields.

    Parameters:
        step_name          : name of the step (e.g. "STEP010")
        pgm                : program name from EXEC (e.g. "IDCAMS")
        exec_statement_line: the full original EXEC JCL line

    Returns:
        A Python dictionary representing this step.
    """

    return {
        "step_name"       : step_name,
        "pgm"             : pgm,
        "exec_statement"  : exec_statement_line,
        "dd_statements"   : [],

        # Full control-card text read by the decoder. ICETOOL uses TOOLIN;
        # other supported utilities use SYSIN.
        "sysin_content"   : "",

        # How the SYSIN content was obtained — see SYSIN_SOURCE constants
        "sysin_source"    : SYSIN_SOURCE_NONE,

        # The original DSN from the SYSIN DD statement (if external).
        # Empty string if SYSIN was inline or DUMMY.
        "sysin_dsn"       : "",

        # Warning message when external SYSIN could not be resolved.
        # Empty string when all is well.
        "sysin_warning"   : "",

        "raw_lines"       : [exec_statement_line],
        "utility_type"    : None,
        "unsupported_constructs": []
    }


def create_new_dd_entry(ddname, parameters):
    """
    Creates a new DD statement dictionary.

    Parameters:
        ddname     : the DD name (e.g. "SORTIN", "SYSIN")
        parameters : everything after "DD" on the statement line

    Returns:
        A Python dictionary representing this DD statement.
    """

    return {
        "ddname"         : ddname,
        "parameters"     : parameters,
        "is_inline"      : False,
        "inline_content" : ""
    }


# =============================================================================
# SECTION 7 — INLINE DATA COLLECTOR
# =============================================================================

def finish_inline_data_collection(current_step, current_dd,
                                   buffer, ddname):
    """
    Stores collected inline data into the step and DD objects.
    Called when /* delimiter is found or next JCL statement begins.

    Parameters:
        current_step : the step dictionary being built
        current_dd   : the DD entry that triggered inline collection
        buffer       : list of collected inline data lines
        ddname       : name of the DD (usually "SYSIN")
    """

    if current_step is None:
        return

    inline_text = "\n".join(buffer)

    if current_dd is not None:
        current_dd["inline_content"] = inline_text

    if ddname.upper() in {"SYSIN", "TOOLIN"}:
        current_step["sysin_content"] = inline_text
        current_step["sysin_source"]  = SYSIN_SOURCE_INLINE


# =============================================================================
# SECTION 8 — MAIN SPLITTER FUNCTION
# =============================================================================

def split_jcl_into_steps(jcl_text, cntl_folder=None):
    """
    Parses a complete JCL job and returns a list of step dictionaries.

    This is the main function of this module.

    CONTROL-CARD HANDLING:
        SYSIN for most utilities and TOOLIN for ICETOOL use the same fields.
        Inline DD *        : content read directly from JCL
        External DD DSN=.. : content read from local cntl/ folder
        DD DUMMY           : sysin_content="" sysin_source="dummy"
        No control DD      : sysin_content="" sysin_source="none"

    Parameters:
        jcl_text    : string containing the full JCL job text
        cntl_folder : path to the local folder containing CNTL members.
                      Defaults to "cntl" relative to the working directory.
                      Set to None to use the default.

    Returns:
        A list of step dictionaries with all fields populated.

    Example:
        from agent.parser.jcl_splitter import split_jcl_into_steps

        jcl_text = open("jcl/TEST.jcl").read()
        steps    = split_jcl_into_steps(jcl_text)

        for step in steps:
            print(step["step_name"], step["pgm"], step["sysin_source"])
    """

    # Use the default cntl folder if none was provided
    if cntl_folder is None:
        cntl_folder = CNTL_LOCAL_FOLDER

    all_lines          = jcl_text.splitlines()
    completed_steps    = []
    current_step       = None
    current_dd         = None
    collecting_inline  = False
    inline_buffer      = []
    inline_ddname      = ""
    # A bare member filename cannot identify a library if this job uses the
    # same member name in more than one PDS. Require a qualified local path.
    member_libraries = {}
    for dsn in re.findall(r"\bDSN\s*=\s*([^,\s]+)", jcl_text, re.I):
        member = extract_member_from_dsn(dsn)
        if member:
            library = dsn.split("(", 1)[0].upper()
            member_libraries.setdefault(member.upper(), set()).add(library)
    ambiguous_members = {member for member, libraries in member_libraries.items()
                         if len(libraries) > 1}

    for raw_line in all_lines:

        # JCL statement whitespace can be trimmed, but inline records retain
        # every character, including blanks and trailing spaces.
        line = raw_line.rstrip()

        # =============================================================
        # STATE: collecting inline data (between DD * and /*)
        # =============================================================
        if collecting_inline:
            delimiter = inline_delimiter(current_dd["parameters"])
            # DLM may be supplied on a continuation of DD *, before data.
            if (not inline_buffer and is_continuation_line(line)
                    and "DLM=" in line.upper()):
                current_step["raw_lines"].append(line)
                current_dd["parameters"] += " " + line[2:].strip()
                continue

            if line.strip() == delimiter:
                # /* found — end of inline data
                finish_inline_data_collection(current_step, current_dd,
                                               inline_buffer, inline_ddname)
                collecting_inline = False
                inline_buffer     = []
                inline_ddname     = ""
                continue

            elif delimiter == JCL_INLINE_END and is_jcl_statement(line):
                # New // statement — inline data ends implicitly
                finish_inline_data_collection(current_step, current_dd,
                                               inline_buffer, inline_ddname)
                collecting_inline = False
                inline_buffer     = []
                inline_ddname     = ""
                # Fall through to process this // line

            else:
                # Regular inline data line — add to buffer
                inline_buffer.append(raw_line)
                continue

        # Blank lines outside an inline DD carry no program input.
        if len(line.strip()) == 0:
            continue

        # =============================================================
        # Skip comment lines
        # =============================================================
        if is_comment_line(line):
            if current_step is not None:
                current_step["raw_lines"].append(line)
            continue

        # =============================================================
        # Skip standalone /* delimiter
        # =============================================================
        if is_inline_data_end(line):
            continue

        # =============================================================
        # Process JCL statements
        # =============================================================
        if is_jcl_statement(line):

            # Continuation line — append to the current DD or EXEC statement.
            if is_continuation_line(line):
                if current_step is not None:
                    current_step["raw_lines"].append(line)
                    continuation = line[2:].strip()
                    if current_dd is not None:
                        current_dd["parameters"] += " " + continuation
                        ddname = current_dd["ddname"].upper()
                        is_control = ddname == "SYSIN" or (
                            ddname == "TOOLIN"
                            and current_step.get("pgm", "").upper() == "ICETOOL"
                        )
                        # A DSN or inline marker may appear after DISP on a
                        # continuation. Resolve it once the value is present.
                        if is_control and is_external_sysin(current_dd["parameters"]):
                            dsn = extract_dsn_from_parameters(current_dd["parameters"])
                            member = extract_member_from_dsn(dsn).upper()
                            content, source, warning = resolve_external_sysin(
                                dsn, cntl_folder,
                                allow_member_fallback=member not in ambiguous_members
                            )
                            current_step.update(sysin_dsn=dsn, sysin_content=content,
                                                sysin_source=source, sysin_warning=warning)
                            if source == SYSIN_SOURCE_EXTERNAL_RESOLVED:
                                current_dd["inline_content"] = content
                    else:
                        current_step["exec_statement"] += " " + continuation
                        current_step["pgm"] = extract_pgm_from_exec(
                            current_step["exec_statement"]
                        )
                        if current_step["pgm"].startswith("PROC:"):
                            message = f"Unexpanded procedure call in {current_step['step_name']}"
                            if message not in current_step["unsupported_constructs"]:
                                current_step["unsupported_constructs"].append(message)
                continue

            # Parse into name, operation, parameters
            name, operation, parameters = parse_jcl_statement(line)

            if not operation:
                if current_step is not None:
                    current_step["raw_lines"].append(line)
                continue

            operation_upper = operation.upper()

            # ---------------------------------------------------------
            # JOB card — skip, not a step
            # ---------------------------------------------------------
            if operation_upper == "JOB":
                continue

            # ---------------------------------------------------------
            # EXEC — new job step starts here
            # ---------------------------------------------------------
            elif operation_upper == "EXEC":

                # Save the step we were working on
                if current_step is not None:
                    completed_steps.append(current_step)

                pgm_name     = extract_pgm_from_exec(parameters)
                current_step = create_new_step(name, pgm_name, line)
                if pgm_name.startswith("PROC:"):
                    current_step["unsupported_constructs"].append(
                        f"Unexpanded procedure call in {name}: {line}"
                    )
                current_dd   = None

            # ---------------------------------------------------------
            # DD — dataset definition within current step
            # ---------------------------------------------------------
            elif operation_upper == "DD" and current_step is not None:

                new_dd = create_new_dd_entry(name, parameters)
                current_step["dd_statements"].append(new_dd)
                current_step["raw_lines"].append(line)
                current_dd = new_dd

                ddname_upper = name.upper()

                # ICETOOL reads operators from TOOLIN. Store them in the
                # established sysin_* step fields used by the decoder.
                is_control_dd = ddname_upper == "SYSIN" or (
                    ddname_upper == "TOOLIN"
                    and current_step.get("pgm", "").upper() == "ICETOOL"
                )
                if is_inline_sysin(parameters):
                    # DD * can hold input data (for example SORTIN), not only
                    # utility control cards. Preserve it on its DD entry.
                    new_dd["is_inline"] = True
                    collecting_inline = True
                    inline_buffer = []
                    inline_ddname = name

                if is_control_dd:

                    if is_dummy_sysin(parameters):
                        # DD DUMMY — no content, mark as dummy
                        current_step["sysin_source"] = SYSIN_SOURCE_DUMMY

                    elif is_external_sysin(parameters):
                        # DD DSN=... — resolve from local cntl/ folder
                        dsn = extract_dsn_from_parameters(parameters)
                        current_step["sysin_dsn"] = dsn

                        member = extract_member_from_dsn(dsn).upper()
                        content, source, warning = resolve_external_sysin(
                            dsn, cntl_folder,
                            allow_member_fallback=member not in ambiguous_members
                        )

                        current_step["sysin_content"] = content
                        current_step["sysin_source"]  = source
                        current_step["sysin_warning"] = warning

                        if source == SYSIN_SOURCE_EXTERNAL_RESOLVED:
                            new_dd["inline_content"] = content

            # ---------------------------------------------------------
            # Any other statement inside a step
            # ---------------------------------------------------------
            elif current_step is not None:
                current_step["raw_lines"].append(line)

    # End of file — save last step
    if current_step is not None:
        if collecting_inline:
            finish_inline_data_collection(current_step, current_dd,
                                           inline_buffer, inline_ddname)
        completed_steps.append(current_step)

    return completed_steps


# =============================================================================
# SECTION 9 — UTILITY FUNCTIONS
# =============================================================================

def load_jcl_from_file(file_path):
    """
    Reads a JCL file from disk and returns its contents as a string.

    Parameters:
        file_path : path to the JCL file

    Returns:
        The full JCL text as a single string.
    """
    with open(file_path, encoding="utf-8", errors="replace") as f:
        return f.read()


def get_step_summary(step):
    """
    Returns a one-line text summary of a step for display purposes.
    """
    sysin_lines  = len(step["sysin_content"].splitlines()) if step["sysin_content"] else 0
    utility_type = step["utility_type"] if step["utility_type"] else "unclassified"

    return (f"Step={step['step_name']:<10} "
            f"PGM={step['pgm']:<12} "
            f"Utility={utility_type:<10} "
            f"SYSINsrc={step['sysin_source']:<25} "
            f"SYSINlines={sysin_lines}")
