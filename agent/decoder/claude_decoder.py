# =============================================================================
# agent/decoder/claude_decoder.py
#
# PROJECT : JUIA — JCL Utility Intelligence Agent
# PURPOSE : Build a prompt from a JCL step and RAG context chunks,
#           call the Claude API, and parse the structured JSON response.
#
# WHAT THIS MODULE DOES (plain English):
#   This is where the actual AI decoding happens. For each utility step,
#   this module:
#     1. Assembles a detailed prompt containing:
#          - The step name, program, utility type
#          - All DD statements (input and output files)
#          - The SYSIN control card content
#          - Relevant documentation chunks from ChromaDB (RAG context)
#          - Clear instructions telling Claude what JSON to return
#     2. Calls the Claude API (claude-sonnet-4-6) with that prompt
#     3. Parses the JSON response into a Python dictionary
#     4. Handles errors gracefully (API failures, JSON parse errors)
#
# INTERACTION WITH CLAUDE API:
#   User message → Anthropic API → Claude model → response text → JSON parse
#
#   The prompt asks Claude to respond ONLY with a JSON object.
#   No markdown, no explanation, just the JSON. We then parse it with
#   Python's built-in json.loads() function.
#
# OUTPUT FORMAT — the decoded result dictionary:
#   {
#     "step_name"        : "STEP030",
#     "utility_type"     : "DFSORT",
#     "business_rule"    : "Extract all pending wire transactions...",
#     "input_files"      : ["WIRE.DAILY.INPUT.SEQ"],
#     "output_files"     : ["WIRE.WORK.EXTRT.SEQ"],
#     "key_fields"       : [{"offset":55, "length":2, "format":"CH",
#                             "meaning":"Transaction status"}],
#     "operations"       : [{"type":"FILTER","description":"..."},
#                            {"type":"SORT","description":"..."}],
#     "pyspark_hint"     : "df.filter(...).orderBy(...)",
#     "decode_confidence": "HIGH",
#     "notes"            : "...",
#     "decode_status"    : "success"
#   }
# =============================================================================


# =============================================================================
# SECTION 1 — IMPORTS
# =============================================================================

# json — built into Python.
# Used to: parse the JSON response from Claude into a Python dictionary.
import json

# re — built into Python.
# Used to: strip any accidental markdown code fences from Claude's response
# before parsing it as JSON.
import re

# os — built into Python.
# Used to: read the ANTHROPIC_API_KEY from environment variables.
import os

# anthropic — install: pip install anthropic
# Used to: call the Claude API.
# The Anthropic class creates an API client.
# client.messages.create() sends a prompt and returns a response.
import anthropic


# =============================================================================
# SECTION 2 — CONFIGURATION
# =============================================================================

# The Claude model to use for all decoding.
# claude-sonnet-4-6 gives the right balance of quality and speed.
CLAUDE_MODEL = "claude-sonnet-4-6"

# Maximum tokens Claude can return in its response.
# 2000 is enough for a detailed JSON response for any single utility step.
MAX_TOKENS = 2000

# Decode status labels — stored in every decoded result
DECODE_STATUS_SUCCESS      = "success"
DECODE_STATUS_API_ERROR    = "api_error"
DECODE_STATUS_PARSE_ERROR  = "parse_error"
DECODE_STATUS_SKIPPED      = "skipped"


# =============================================================================
# SECTION 3 — API CLIENT SETUP
# =============================================================================

def get_api_client():
    """
    Creates and returns an Anthropic API client.

    Reads the API key from the ANTHROPIC_API_KEY environment variable.
    In GitHub Codespaces, this is automatically injected from the
    repository secret you set up earlier.

    For local development, set it in your terminal:
        export ANTHROPIC_API_KEY=your_key_here

    Returns:
        An anthropic.Anthropic client object, or None if key is missing.
    """

    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not api_key:
        print("  [ERROR] ANTHROPIC_API_KEY environment variable not set.")
        print("  In Codespaces: check your repository secrets.")
        print("  Locally: export ANTHROPIC_API_KEY=your_key_here")
        return None

    # Create the client — it uses the key automatically
    client = anthropic.Anthropic(api_key=api_key)
    return client


# =============================================================================
# SECTION 4 — DD STATEMENT FORMATTER
# Formats the DD statements from a step into a readable table for the prompt.
# =============================================================================

def format_dd_statements_for_prompt(dd_statements):
    """
    Formats the list of DD statements into a readable table string
    that can be included in the Claude prompt.

    We skip SYSPRINT since it is always SYSOUT=* and carries no
    business logic.

    Example output:
        SORTIN   : DSN=WIRE.DAILY.INPUT.SEQ,DISP=SHR
        SORTOUT  : DSN=WIRE.WORK.EXTRT.SEQ,DISP=(NEW,CATLG,DELETE),...
        SYSIN    : DD * (inline control cards — shown in SYSIN section)

    Parameters:
        dd_statements : list of DD dictionaries from the step object

    Returns:
        A formatted string showing all relevant DD statements.
    """

    # DD names to skip — they carry no business logic
    skip_ddnames = {"SYSPRINT", "SYSOUT", "SYSABEND", "SYSUDUMP"}

    lines = []

    for dd in dd_statements:
        ddname = dd.get("ddname", "").upper()

        # Skip diagnostic output DDs
        if ddname in skip_ddnames:
            continue

        parameters = dd.get("parameters", "")

        # Shorten the SYSIN entry — the full content is shown separately
        if ddname == "SYSIN":
            if dd.get("is_inline"):
                parameters = "DD * (see SYSIN CONTROL CARDS section below)"
            elif "DUMMY" in parameters.upper():
                parameters = "DD DUMMY"
            # For external SYSIN, show the DSN as-is

        lines.append(f"  {ddname:<12} : {parameters}")

    if len(lines) == 0:
        return "  (no DD statements)"

    return "\n".join(lines)


# =============================================================================
# SECTION 5 — PROMPT BUILDER
# Assembles the full prompt for Claude from step information and RAG context.
# =============================================================================

def build_decode_prompt(step, rag_context):
    """
    Builds the complete prompt to send to Claude for decoding one step.

    The prompt contains four sections:
        1. STEP INFORMATION — name, program, utility type
        2. DD STATEMENTS — all input and output files
        3. SYSIN CONTROL CARDS — the actual logic to decode
        4. REFERENCE DOCUMENTATION — relevant chunks from IBM manuals
           (provided by the RAG retriever)

    Then a clear instruction section telling Claude exactly what
    JSON structure to return.

    Parameters:
        step        : the step dictionary from jcl_splitter
        rag_context : formatted string of relevant documentation chunks

    Returns:
        A complete prompt string ready to send to Claude.
    """

    step_name    = step.get("step_name", "UNKNOWN")
    pgm          = step.get("pgm", "UNKNOWN")
    utility_type = step.get("utility_type", "UNKNOWN")
    sysin        = step.get("sysin_content", "").strip()
    sysin_source = step.get("sysin_source", "none")
    sysin_dsn    = step.get("sysin_dsn", "")

    # Format DD statements for display
    dd_table = format_dd_statements_for_prompt(step.get("dd_statements", []))

    # Build the SYSIN section label
    if sysin_source == "external_resolved":
        sysin_label = f"(read from external control card: {sysin_dsn})"
    elif sysin_source == "inline":
        sysin_label = "(inline in JCL)"
    else:
        sysin_label = ""

    # If there is no SYSIN content, note that
    if not sysin:
        sysin_display = "(no control cards — straight copy or program step)"
    else:
        sysin_display = sysin

    # Build the full prompt
    prompt = f"""You are an expert IBM mainframe engineer specialising in JCL utility migration to modern data platforms.

Your task is to analyse the following JCL utility step and decode the business logic embedded in its control cards.

=== STEP INFORMATION ===
Step Name    : {step_name}
Program      : {pgm}
Utility Type : {utility_type}

=== DD STATEMENTS (Input and Output Files) ===
{dd_table}

=== SYSIN CONTROL CARDS {sysin_label} ===
{sysin_display}

=== REFERENCE DOCUMENTATION ===
The following excerpts from IBM manuals are provided to help you correctly
interpret the control card syntax, field format codes (CH, ZD, PD, BI),
comparison operators, and statement parameters:

{rag_context if rag_context else "(No reference documentation available)"}

=== INSTRUCTIONS ===
Analyse the SYSIN control cards using the reference documentation above.
Identify every business rule, filter condition, sort key, output split,
field transformation, and data operation.

Respond ONLY with a valid JSON object. No markdown formatting, no code
fences, no explanation outside the JSON. Return exactly this structure:

{{
    "business_rule": "A clear plain English description of what this step does and what business purpose it serves",
    "input_files": ["list of input dataset names from SORTIN/SYSUT1/INFILE/SEQIN DD statements"],
    "output_files": ["list of output dataset names from SORTOUT/SYSUT2/OUTFILE/OUTFIL DD statements"],
    "key_fields": [
        {{
            "offset": <integer — 1-based byte offset in the record>,
            "length": <integer — number of bytes>,
            "format": "<CH or ZD or PD or BI or FS>",
            "meaning": "what this field represents in business terms"
        }}
    ],
    "operations": [
        {{
            "type": "<FILTER or SORT or SPLIT or AGGREGATE or COPY or LOAD or EXTRACT or DELETE or DEFINE or VERIFY or REFORMAT or JOIN>",
            "description": "plain English description of this specific operation"
        }}
    ],
    "pyspark_hint": "A one or two line PySpark or Python equivalent showing the core logic",
    "decode_confidence": "<HIGH or MEDIUM or LOW>",
    "notes": "Any important migration observations, caveats, or warnings about this step"
}}"""

    return prompt


# =============================================================================
# SECTION 6 — CLAUDE API CALLER
# =============================================================================

def call_claude_api(prompt, api_client):
    """
    Sends the prompt to Claude and returns the raw response text.

    Parameters:
        prompt     : the complete prompt string to send
        api_client : the Anthropic client object from get_api_client()

    Returns:
        The response text from Claude as a string.
        Returns None if the API call fails.
    """

    try:
        # Create a message with a single user turn
        # The messages list format: [{"role": "user", "content": "..."}]
        message = api_client.messages.create(
            model     = CLAUDE_MODEL,
            max_tokens = MAX_TOKENS,
            messages  = [
                {
                    "role"    : "user",
                    "content" : prompt
                }
            ]
        )

        # message.content is a list of content blocks.
        # For a text response, we want the first block's text.
        response_text = message.content[0].text
        return response_text

    except anthropic.APIConnectionError as error:
        print(f"  [ERROR] Could not connect to Claude API: {error}")
        return None

    except anthropic.RateLimitError as error:
        print(f"  [ERROR] Claude API rate limit reached: {error}")
        return None

    except anthropic.APIStatusError as error:
        print(f"  [ERROR] Claude API returned an error: {error.status_code} — {error.message}")
        return None

    except Exception as error:
        print(f"  [ERROR] Unexpected error calling Claude API: {error}")
        return None


# =============================================================================
# SECTION 7 — JSON RESPONSE PARSER
# =============================================================================

def parse_json_response(response_text):
    """
    Parses Claude's response text into a Python dictionary.

    Claude is instructed to return only JSON, but occasionally it wraps
    the JSON in markdown code fences (```json ... ```) even when told
    not to. This function strips any such formatting before parsing.

    Parameters:
        response_text : the raw text response from Claude

    Returns:
        A Python dictionary parsed from the JSON.
        Returns None if parsing fails.
    """

    if not response_text:
        return None

    # Strip leading and trailing whitespace
    cleaned = response_text.strip()

    # Remove markdown code fences if present
    # Pattern: ```json at start, ``` at end (with optional newlines)
    cleaned = re.sub(r'^```json\s*', '', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'^```\s*',     '', cleaned, flags=re.MULTILINE)
    cleaned = cleaned.strip()

    # Find the first { and last } to extract just the JSON object
    # This handles cases where Claude adds text before or after the JSON
    first_brace = cleaned.find('{')
    last_brace  = cleaned.rfind('}')

    if first_brace == -1 or last_brace == -1:
        print("  [ERROR] No JSON object found in Claude's response.")
        print(f"  Response preview: {cleaned[:200]}")
        return None

    json_text = cleaned[first_brace : last_brace + 1]

    try:
        parsed = json.loads(json_text)
        return parsed

    except json.JSONDecodeError as error:
        print(f"  [ERROR] Could not parse Claude's JSON response: {error}")
        print(f"  JSON text preview: {json_text[:300]}")
        return None


# =============================================================================
# SECTION 8 — MAIN DECODE FUNCTION
# =============================================================================

def decode_with_claude(step, rag_context, api_client):
    """
    Decodes one JCL utility step using Claude API and RAG context.

    This is the main function of this module. It orchestrates:
        1. Build prompt from step + RAG context
        2. Call Claude API
        3. Parse JSON response
        4. Return structured decoded result

    Parameters:
        step        : step dictionary from jcl_splitter (Phase 2)
        rag_context : formatted string from rag_retriever (Phase 3)
        api_client  : Anthropic client from get_api_client()

    Returns:
        A dictionary with all decoded fields plus a decode_status field.
        On success  : decode_status = "success", all fields populated
        On failure  : decode_status = "api_error" or "parse_error",
                      other fields may be empty
    """

    step_name    = step.get("step_name", "UNKNOWN")
    utility_type = step.get("utility_type", "UNKNOWN")

    # --- Build the prompt ---
    prompt = build_decode_prompt(step, rag_context)

    # --- Call Claude API ---
    print(f"  Calling Claude API for {step_name} ({utility_type})...")
    response_text = call_claude_api(prompt, api_client)

    if response_text is None:
        # API call failed — return an error result
        return build_error_result(step, DECODE_STATUS_API_ERROR,
                                   "Claude API call failed")

    # --- Parse the JSON response ---
    decoded_data = parse_json_response(response_text)

    if decoded_data is None:
        # JSON parsing failed — return an error result with raw response
        return build_error_result(step, DECODE_STATUS_PARSE_ERROR,
                                   "Could not parse Claude's JSON response",
                                   raw_response=response_text)

    # --- Add step identification fields to the decoded result ---
    decoded_data["step_name"]     = step_name
    decoded_data["utility_type"]  = utility_type
    decoded_data["pgm"]           = step.get("pgm", "")
    decoded_data["sysin_source"]  = step.get("sysin_source", "")
    decoded_data["sysin_dsn"]     = step.get("sysin_dsn", "")
    decoded_data["decode_status"] = DECODE_STATUS_SUCCESS
    decoded_data["sysin_content"] = step.get("sysin_content", "")
    decoded_data["dd_statements"] = [
        {"ddname": dd.get("ddname", ""), "parameters": dd.get("parameters", "")}
        for dd in step.get("dd_statements", [])
    ]

    print(f"  Decoded successfully. Confidence: "
          f"{decoded_data.get('decode_confidence', 'unknown')}")

    return decoded_data


# =============================================================================
# SECTION 9 — HELPER FUNCTIONS
# =============================================================================

def build_error_result(step, status, error_message, raw_response=None):
    """
    Builds a standardised error result dictionary when decoding fails.

    Parameters:
        step          : the step that failed to decode
        status        : the error status string
        error_message : human-readable description of what went wrong
        raw_response  : the raw Claude response text if available

    Returns:
        A dictionary with all expected fields set to empty/error values.
    """

    result = {
        "step_name"        : step.get("step_name", "UNKNOWN"),
        "utility_type"     : step.get("utility_type", "UNKNOWN"),
        "pgm"              : step.get("pgm", ""),
        "sysin_source"     : step.get("sysin_source", ""),
        "sysin_dsn"        : step.get("sysin_dsn", ""),
        "business_rule"    : f"[DECODE FAILED: {error_message}]",
        "input_files"      : [],
        "output_files"     : [],
        "key_fields"       : [],
        "operations"       : [],
        "pyspark_hint"     : "",
        "decode_confidence": "LOW",
        "notes"            : error_message,
        "decode_status"    : status,
    }

    if raw_response:
        # Store the first 500 chars of the raw response for debugging
        result["raw_response_preview"] = raw_response[:500]

    return result


def build_skipped_result(step, reason):
    """
    Builds a result dictionary for steps that are intentionally skipped.

    Used for IEBGENER DUMMY copies, non-utility programs, and utility
    steps whose control cards are unavailable.

    Parameters:
        step   : the step dictionary
        reason : why this step was skipped

    Returns:
        A result dictionary with decode_status = "skipped".
    """

    step_name    = step.get("step_name", "UNKNOWN")
    utility_type = step.get("utility_type", "UNKNOWN")
    sysin_source = step.get("sysin_source", "none")

    # For IEBGENER DUMMY steps, we can provide a complete result
    # without calling Claude — it's always a straight copy
    if utility_type == "IEBGENER" and sysin_source == "dummy":

        # Extract file names from SYSUT1 and SYSUT2 DD statements
        sysut1 = extract_dsn_from_dd(step, "SYSUT1")
        sysut2 = extract_dsn_from_dd(step, "SYSUT2")

        return {
            "step_name"        : step_name,
            "utility_type"     : utility_type,
            "pgm"              : step.get("pgm", ""),
            "sysin_source"     : sysin_source,
            "sysin_dsn"        : "",
            "business_rule"    : (
                f"Straight copy of {sysut1 or 'SYSUT1'} to "
                f"{sysut2 or 'SYSUT2'}. No record transformation applied. "
                "Used for backup or audit copy creation."
            ),
            "input_files"      : [sysut1] if sysut1 else [],
            "output_files"     : [sysut2] if sysut2 else [],
            "key_fields"       : [],
            "operations"       : [
                {
                    "type"        : "COPY",
                    "description" : (
                        "Direct record-by-record copy of entire input file "
                        "to output. SYSIN DD DUMMY means no control cards — "
                        "all records copied unchanged."
                    )
                }
            ],
            "pyspark_hint"     : (
                "import shutil\n"
                f"shutil.copy2('{sysut1 or 'input.dat'}', "
                f"'{sysut2 or 'output.dat'}')"
            ),
            "decode_confidence": "HIGH",
            "notes"            : (
                "IEBGENER with SYSIN DD DUMMY is always a straight copy. "
                "Consider ICEGENER (DFSORT-based) for files > 1M records."
            ),
            "decode_status"    : DECODE_STATUS_SKIPPED,
        }

    if utility_type != "PROGRAM":
        return {
            "step_name": step_name,
            "utility_type": utility_type,
            "pgm": step.get("pgm", ""),
            "sysin_source": sysin_source,
            "sysin_dsn": step.get("sysin_dsn", ""),
            "business_rule": f"Utility step could not be decoded: {reason}",
            "input_files": [],
            "output_files": [],
            "key_fields": [],
            "operations": [],
            "pyspark_hint": "",
            "decode_confidence": "LOW",
            "notes": reason,
            "decode_status": DECODE_STATUS_SKIPPED,
        }

    # For PROGRAM steps
    return {
        "step_name"        : step_name,
        "utility_type"     : utility_type,
        "pgm"              : step.get("pgm", ""),
        "sysin_source"     : sysin_source,
        "sysin_dsn"        : "",
        "business_rule"    : (
            f"Non-utility program step. PGM={step.get('pgm', 'UNKNOWN')} "
            "is a custom application program (COBOL, Assembler, or PL/I). "
            "JUIA does not decode non-utility steps — review source code separately."
        ),
        "input_files"      : [],
        "output_files"     : [],
        "key_fields"       : [],
        "operations"       : [
            {
                "type"        : "PROGRAM",
                "description" : "Custom application logic — see program source"
            }
        ],
        "pyspark_hint"     : "# Requires separate COBOL/program analysis",
        "decode_confidence": "N/A",
        "notes"            : reason,
        "decode_status"    : DECODE_STATUS_SKIPPED,
    }


def extract_dsn_from_dd(step, ddname_to_find):
    """
    Finds a specific DD statement in a step and returns its DSN value.

    Used to extract SYSUT1 and SYSUT2 dataset names for IEBGENER steps.

    Parameters:
        step           : the step dictionary
        ddname_to_find : the DD name to search for (e.g. "SYSUT1")

    Returns:
        The DSN value string, or empty string if not found.
    """

    for dd in step.get("dd_statements", []):
        if dd.get("ddname", "").upper() == ddname_to_find.upper():
            parameters = dd.get("parameters", "")

            # Extract DSN= value from parameters
            import re
            dsn_match = re.search(r'DSN=([^,\s]+)', parameters, re.IGNORECASE)
            if dsn_match:
                return dsn_match.group(1)

    return ""
