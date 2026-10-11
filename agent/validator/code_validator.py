# =============================================================================
# agent/validator/code_validator.py
#
# PROJECT : JUIA — JCL Utility Intelligence Agent
# PURPOSE : Validate the PySpark code generated in Phase 4 by asking
#           Claude to review it against the original mainframe logic.
#
# WHAT THIS MODULE DOES (plain English):
#   Phase 4 generated PySpark Python files from decoded JCL steps.
#   Phase 5 verifies those files are correct by asking Claude to:
#     1. Create synthetic test records appropriate for the step
#     2. Mentally trace the PySpark code against those records
#     3. Check each aspect of the logic against the original SYSIN
#     4. Return a structured PASS/FAIL report per check
#
# WHY NOT RUN PYSPARK DIRECTLY?
#   Running PySpark requires Java (JVM). Adding a Java dependency to the
#   Codespace is a significant setup step. Claude can perform logical
#   validation without execution and catches the same class of errors:
#   wrong filter conditions, wrong sort direction, wrong split threshold,
#   wrong aggregation grouping. The validation is thorough and honest
#   about what it is — a code review, not a runtime test.
#
# VALIDATION CHECKS PERFORMED:
#   filter_logic      — Do filter conditions match the SYSIN INCLUDE/OMIT?
#   sort_logic        — Are sort keys and directions correct?
#   column_names      — Are column names meaningful and consistent?
#   pd_handling       — Are packed decimal fields handled with correct notes?
#   output_structure  — Does the return type match downstream expectations?
#   split_logic       — (OUTFIL only) Are split conditions exhaustive?
#   aggregation       — (SUM only) Is GROUP BY granularity correct?
#
# OUTPUT — validation result dictionary:
#   {
#     "step_name"          : "STEP030",
#     "utility_type"       : "DFSORT",
#     "code_path"          : "outputs/WIREPAY/<run_id>/pyspark/STEP030_DFSORT.py",
#     "validation_status"  : "PASS",
#     "checks"             : { "filter_logic": {"status":"PASS","detail":"..."},
#                               ... },
#     "issues_found"       : [],
#     "correction_needed"  : false,
#     "correction_hint"    : "",
#     "validate_status"    : "success"
#   }
#
# CHANGE LOG (parse-failure fix):
#   1. build_validation_prompt: STEP A / STEP B reworded so Claude performs
#      them internally and does not write them out. A FINAL REMINDER is
#      appended as the last lines of the prompt.
#   2. parse_validation_response: no longer slices from the first "{" in the
#      text. It now tries every "{" as a start position using
#      json.JSONDecoder.raw_decode and returns the first dict that looks like
#      the validation report. Set literals such as {R01-R12, R14, R15} in
#      analysis text are skipped.
#   3. validate_generated_code: warns when the response was cut off by
#      max_tokens, since truncation also produces unparseable JSON.
# =============================================================================


# =============================================================================
# SECTION 1 — IMPORTS
# =============================================================================

import os
import re
import ast
import json


# =============================================================================
# SECTION 2 — CONFIGURATION
# =============================================================================

CLAUDE_MODEL  = "claude-sonnet-4-6"

# 8000 tokens gives Claude enough room to write the full validation JSON
# without truncating detail strings, which causes JSON parse errors.
# Phase 3 used 4000 which was sufficient for shorter JSON responses,
# but validation reports for complex steps (STEP040, STEP070) are longer.
MAX_TOKENS    = 8000

# Validation status labels
STATUS_PASS    = "PASS"
STATUS_FAIL    = "FAIL"
STATUS_REVIEW  = "REVIEW"
STATUS_NA      = "NA"
STATUS_ERROR   = "ERROR"

# Validate status labels (overall call outcome)
VALIDATE_OK    = "success"
VALIDATE_ERROR = "error"

# Keys used to recognise the validation report among any dicts found in the
# response text. A candidate must contain at least one of these.
REPORT_MARKER_KEYS = ("overall_status", "checks", "issues_found")


# =============================================================================
# SECTION 3 — FILE READER
# =============================================================================

def read_generated_code(code_path):
    """
    Reads the generated PySpark Python file from disk.

    Parameters:
        code_path : full path to the .py file

    Returns:
        The file contents as a string, or empty string if not found.
    """

    if not os.path.exists(code_path):
        print(f"  [ERROR] Generated file not found: {code_path}")
        return ""

    with open(code_path, encoding="utf-8") as f:
        return f.read()


# =============================================================================
# SECTION 4 — PROMPT BUILDER
# =============================================================================

def build_validation_prompt(decoded_step, original_step, generated_code):
    """
    Builds the complete validation prompt for Claude.

    The prompt asks Claude to do three things in one call:
        1. Generate 15 synthetic test records appropriate for this step
        2. Trace the PySpark code against those records
        3. Return a structured JSON validation report

    Steps 1 and 2 are performed internally. Only the JSON report is
    written out. A final reminder at the end of the prompt restates the
    output contract because it is the last instruction Claude reads.

    Parameters:
        decoded_step   : result dictionary from Phase 3 decoder
        original_step  : step dictionary from Phase 2 parser
        generated_code : the Python code string from Phase 4

    Returns:
        A complete prompt string ready to send to Claude.
    """

    step_name     = decoded_step.get("step_name", "UNKNOWN")
    utility_type  = decoded_step.get("utility_type", "UNKNOWN")
    pgm           = decoded_step.get("pgm", "UNKNOWN")
    business_rule = decoded_step.get("business_rule", "")
    key_fields    = decoded_step.get("key_fields", [])
    operations    = decoded_step.get("operations", [])
    input_files   = decoded_step.get("input_files", [])
    output_files  = decoded_step.get("output_files", [])

    sysin_content = original_step.get("sysin_content", "")

    # Format key fields for the prompt
    key_fields_text = ""
    for field in key_fields:
        key_fields_text += (
            f"  Offset {field.get('offset','?'):<5} "
            f"Len {field.get('length','?'):<4} "
            f"Format {field.get('format','?'):<4} : "
            f"{field.get('meaning','')}\n"
        )
    if not key_fields_text:
        key_fields_text = "  (no key fields identified)\n"

    # Format operations for the prompt
    operations_text = ""
    for op in operations:
        operations_text += f"  [{op.get('type','?')}] {op.get('description','')}\n"
    if not operations_text:
        operations_text = "  (no operations identified)\n"

    prompt = f"""You are a senior PySpark engineer validating a mainframe migration.

A JCL utility step has been decoded and converted to PySpark code.
Your job is to validate that the generated code correctly implements the original mainframe logic.

=== STEP INFORMATION ===
Step Name    : {step_name}
Program      : {pgm}
Utility Type : {utility_type}
Input Files  : {', '.join(input_files) if input_files else '(none)'}
Output Files : {', '.join(output_files) if output_files else '(none)'}

=== BUSINESS RULE (from decoder) ===
{business_rule}

=== KEY FIELDS (mainframe record layout) ===
{key_fields_text}
=== ORIGINAL MAINFRAME SYSIN ===
{sysin_content if sysin_content else "(no SYSIN — catalog or copy operation)"}

=== OPERATIONS TO VALIDATE ===
{operations_text}
=== GENERATED PYSPARK CODE ===
{generated_code}

=== YOUR TASK ===

Perform the validation in three steps. Steps A and B are INTERNAL REASONING
ONLY. Do NOT write out the test table, the trace, set notation, checkmarks,
or any other analysis text. Only Step C is written in your response.

STEP A — GENERATE SYNTHETIC TEST DATA (internal, do not output)
Think of 15 synthetic test records for this step.
Design the records to include:
  - Records that should PASS any filter conditions (include in output)
  - Records that should FAIL each filter condition (exclude from output)
  - Edge cases: boundary values, zero amounts, empty strings
Use realistic business values (wire transfer amounts, status codes, dates).

STEP B — TRACE THE CODE (internal, do not output)
Mentally execute the PySpark code against your synthetic records.
Track which rows survive each filter, the sort order, and any aggregation.
Note any discrepancies between what the code does and what the SYSIN specifies.
Treat the original SYSIN as authoritative when the decoded business rule or
operations disagree with it. For SUM, test two rows with the same SORT key
and different SUM-field values; they must collapse into one summed result.
Check that non-key fields retained from a group have an explicit selection
rule, and do not accept unsupported claims of original input ordering.
For OUTREC with fixed LRECL, check field order, widths, overflow handling,
and the final record length.

STEP C — PRODUCE VALIDATION REPORT (this is your entire response)
Return ONLY a valid JSON object (no markdown, no explanation).
Summarise the outcome of Steps A and B inside the JSON fields
"synthetic_data_summary", "checks" and "issues_found".
Use this structure:

{{
    "synthetic_data_summary": "Brief description of the test records generated and how many pass/fail each condition",
    "overall_status": "{STATUS_PASS} or {STATUS_FAIL}",
    "checks": {{
        "filter_logic": {{
            "status": "{STATUS_PASS} or {STATUS_FAIL} or {STATUS_NA}",
            "detail": "Do the filter conditions exactly match the SYSIN INCLUDE/OMIT? Note any differences."
        }},
        "sort_logic": {{
            "status": "{STATUS_PASS} or {STATUS_FAIL} or {STATUS_NA}",
            "detail": "Are sort keys correct? Are ascending/descending directions correct?"
        }},
        "column_names": {{
            "status": "{STATUS_PASS} or {STATUS_FAIL}",
            "detail": "Are column names meaningful and derived from field meanings? Are constants used?"
        }},
        "pd_handling": {{
            "status": "{STATUS_PASS} or {STATUS_FAIL} or {STATUS_NA}",
            "detail": "For PD fields: is there a clear note that pre-decoding is required?"
        }},
        "output_structure": {{
            "status": "{STATUS_PASS} or {STATUS_FAIL}",
            "detail": "Does the function return type match what downstream code would expect?"
        }},
        "split_logic": {{
            "status": "{STATUS_PASS} or {STATUS_FAIL} or {STATUS_NA}",
            "detail": "For OUTFIL: are split conditions mutually exclusive and cover all records?"
        }},
        "aggregation": {{
            "status": "{STATUS_PASS} or {STATUS_FAIL} or {STATUS_NA}",
            "detail": "For SUM: is the GROUP BY granularity correct per the SYSIN?"
        }}
    }},
    "issues_found": [
        "List each specific issue found. Empty list if none."
    ],
    "correction_needed": true or false,
    "correction_hint": "If correction_needed is true: describe exactly what needs to change. Empty string if false."
}}"""

    # Appended as a plain string (not part of the f-string above) so that
    # no brace escaping is needed. This is the last text Claude reads.
    prompt += """

FINAL REMINDER: Your response must contain ONLY the JSON object above.
Start your response with { and end with }.
No analysis text, no markdown, no checkmarks before or after the JSON."""

    return prompt


# =============================================================================
# SECTION 5 — RESPONSE PARSER
# =============================================================================

def _looks_like_report(candidate):
    """
    Returns True if a parsed object looks like the validation report
    rather than an unrelated or nested dictionary.
    """

    if not isinstance(candidate, dict):
        return False
    return any(key in candidate for key in REPORT_MARKER_KEYS)


def _balanced_slice(text, start):
    """
    Returns the substring of text beginning at text[start] == '{' and
    ending at the matching '}', ignoring braces inside quoted strings.
    Returns None if the braces never balance.
    """

    depth     = 0
    in_string = False
    quote     = ""
    escaped   = False

    for i in range(start, len(text)):
        char = text[i]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue

        if char in ('"', "'"):
            in_string = True
            quote     = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return None


def parse_validation_response(response_text):
    """
    Parses Claude's validation response into a Python dictionary.

    Handles:
        - JSON wrapped in markdown code fences
        - Analysis text before or after the JSON (including text that
          contains braces, such as set notation {R01-R12, R14, R15})
        - Python-style dictionaries with single quotes

    Strategy:
        Every '{' in the text is tried as a start position. The first
        candidate that parses and contains a report key (overall_status,
        checks or issues_found) is returned. Candidates that fail to
        parse, such as set literals, are skipped.

    Parameters:
        response_text : the raw text response from Claude

    Returns:
        A parsed dictionary, or None if parsing fails.
    """

    if not response_text:
        return None

    cleaned = response_text.strip()

    # Strip markdown fences
    cleaned = re.sub(r'^```json\s*', '', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'^```\s*$',    '', cleaned, flags=re.MULTILINE)
    cleaned = cleaned.strip()

    if cleaned.find('{') == -1:
        print("  [ERROR] No JSON found in validation response")
        print(f"  [DEBUG] Response start: {cleaned[:300]}")
        return None

    decoder = json.JSONDecoder()

    # Pass 1 — strict JSON, trying each '{' as a start position
    position = cleaned.find('{')
    while position != -1:
        try:
            candidate, end = decoder.raw_decode(cleaned, position)
            if _looks_like_report(candidate):
                return candidate
            # Valid JSON but not the report (nested or unrelated dict):
            # continue scanning after it.
            position = cleaned.find('{', end)
        except json.JSONDecodeError:
            position = cleaned.find('{', position + 1)

    # Pass 2 — Python dict syntax (single quotes), balanced-brace slices
    position = cleaned.find('{')
    while position != -1:
        snippet = _balanced_slice(cleaned, position)
        if snippet:
            try:
                candidate = ast.literal_eval(snippet)
                if _looks_like_report(candidate):
                    return candidate
            except Exception:
                pass
        position = cleaned.find('{', position + 1)

    print("  [ERROR] Could not parse validation JSON")
    print(f"  [DEBUG] Response start: {cleaned[:300]}")
    return None


# =============================================================================
# SECTION 6 — RESULT BUILDER
# =============================================================================

def build_validation_result(step_name, utility_type, code_path,
                             parsed_response):
    """
    Builds the final validation result dictionary from the parsed response.

    Adds step identification fields and a validate_status field
    to the raw response from Claude.

    Parameters:
        step_name       : step name string
        utility_type    : utility type string
        code_path       : path to the generated .py file
        parsed_response : dictionary parsed from Claude's JSON response

    Returns:
        A complete validation result dictionary.
    """

    checks = parsed_response.get("checks", {})
    if not isinstance(checks, dict):
        checks = {}
    failed_check = any(
        isinstance(check, dict) and check.get("status") == STATUS_FAIL
        for check in checks.values()
    )
    reported_status = parsed_response.get("overall_status", STATUS_FAIL)
    if reported_status not in (STATUS_PASS, STATUS_FAIL):
        reported_status = STATUS_FAIL
    if failed_check or parsed_response.get("correction_needed", False):
        reported_status = STATUS_FAIL

    result = {
        "step_name"         : step_name,
        "utility_type"      : utility_type,
        "code_path"         : code_path,
        "validation_status" : reported_status,
        "checks"            : checks,
        "synthetic_data_summary" : parsed_response.get("synthetic_data_summary", ""),
        "issues_found"      : parsed_response.get("issues_found", []),
        "correction_needed" : parsed_response.get("correction_needed", False),
        "correction_hint"   : parsed_response.get("correction_hint", ""),
        "corrections_applied" : 0,    # updated by self_correction_loop
        "correction_history"  : [],   # updated by self_correction_loop
        "validate_status"   : VALIDATE_OK
    }

    return result


def build_error_result(step_name, utility_type, code_path, error_message):
    """
    Builds a validation result for when the validation itself fails
    (API error, parse error, missing file).

    Parameters:
        step_name     : step name string
        utility_type  : utility type string
        code_path     : path to the generated .py file
        error_message : what went wrong

    Returns:
        A validation result dictionary with validate_status = "error".
    """

    return {
        "step_name"           : step_name,
        "utility_type"        : utility_type,
        "code_path"           : code_path,
        "validation_status"   : STATUS_ERROR,
        "checks"              : {},
        "synthetic_data_summary" : "",
        "issues_found"        : [error_message],
        "correction_needed"   : False,
        "correction_hint"     : "",
        "corrections_applied" : 0,
        "correction_history"  : [],
        "validate_status"     : VALIDATE_ERROR
    }


# =============================================================================
# SECTION 7 — MAIN VALIDATION FUNCTION
# =============================================================================

def validate_generated_code(decoded_step, original_step,
                              code_path, api_client):
    """
    Validates the generated PySpark code for one step using Claude.

    This is the main function of this module.

    Steps:
        1. Read the generated .py file from disk
        2. Build the validation prompt
        3. Call Claude API
        4. Parse the JSON response
        5. Return a structured validation result

    Parameters:
        decoded_step  : result dictionary from Phase 3 decoder
        original_step : step dictionary from Phase 2 parser
        code_path     : path to the generated .py file
        api_client    : Anthropic client object

    Returns:
        A validation result dictionary with validation_status PASS/FAIL/ERROR.
    """

    step_name    = decoded_step.get("step_name", "UNKNOWN")
    utility_type = decoded_step.get("utility_type", "UNKNOWN")

    print(f"  Validating: {step_name} ({utility_type})")

    # Step 1: Read the generated code
    generated_code = read_generated_code(code_path)
    if not generated_code:
        return build_error_result(
            step_name, utility_type, code_path,
            f"Generated file not found or empty: {code_path}"
        )

    # Step 2: Build the validation prompt
    prompt = build_validation_prompt(decoded_step, original_step, generated_code)

    # Step 3: Call Claude API
    print(f"  Calling Claude API for validation...")
    try:
        message = api_client.messages.create(
            model      = CLAUDE_MODEL,
            max_tokens = MAX_TOKENS,
            messages   = [{"role": "user", "content": prompt}]
        )
        response_text = message.content[0].text

        # A response cut off at max_tokens leaves unbalanced JSON
        if getattr(message, "stop_reason", None) == "max_tokens":
            print(f"  [WARN] Response truncated at {MAX_TOKENS} tokens "
                  f"for {step_name}; JSON may be incomplete")

    except Exception as error:
        print(f"  [ERROR] API call failed: {error}")
        return build_error_result(
            step_name, utility_type, code_path,
            f"Claude API call failed: {error}"
        )

    # Step 4: Parse the JSON response
    parsed = parse_validation_response(response_text)
    if parsed is None:
        return build_error_result(
            step_name, utility_type, code_path,
            "Could not parse Claude validation response as JSON"
        )

    # Step 5: Build and return the result
    result = build_validation_result(step_name, utility_type, code_path, parsed)

    overall = result["validation_status"]
    issues  = len(result["issues_found"])
    print(f"  Result: {overall}  |  Issues: {issues}")

    return result
