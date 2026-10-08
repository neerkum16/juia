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
#     "code_path"          : "outputs/pyspark/STEP030_DFSORT.py",
#     "validation_status"  : "PASS",
#     "checks"             : { "filter_logic": {"status":"PASS","detail":"..."},
#                               ... },
#     "issues_found"       : [],
#     "correction_needed"  : false,
#     "correction_hint"    : "",
#     "validate_status"    : "success"
#   }
# =============================================================================


# =============================================================================
# SECTION 1 — IMPORTS
# =============================================================================

import os
import re
import json
import anthropic


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
STATUS_NA      = "NA"
STATUS_ERROR   = "ERROR"

# Validate status labels (overall call outcome)
VALIDATE_OK    = "success"
VALIDATE_ERROR = "error"


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

    Combining these into one prompt reduces API calls and gives Claude
    full context when evaluating the code.

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

Perform a three-step validation:

STEP A — GENERATE SYNTHETIC TEST DATA
Create a table of 15 synthetic test records for this step.
Design the records to include:
  - Records that should PASS any filter conditions (include in output)
  - Records that should FAIL each filter condition (exclude from output)
  - Edge cases: boundary values, zero amounts, empty strings
Use realistic business values (wire transfer amounts, status codes, dates).
Format as a pipe-separated table with column headers.

STEP B — TRACE THE CODE
Mentally execute the PySpark code against your synthetic records.
Track which rows survive each filter, the sort order, and any aggregation.
Note any discrepancies between what the code does and what the SYSIN specifies.

STEP C — PRODUCE VALIDATION REPORT
Return ONLY a valid JSON object (no markdown, no explanation).
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

    return prompt


# =============================================================================
# SECTION 5 — RESPONSE PARSER
# =============================================================================

def parse_validation_response(response_text):
    """
    Parses Claude's validation response into a Python dictionary.

    Handles cases where Claude wraps the JSON in markdown code fences.

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

    # Extract the JSON object
    first_brace = cleaned.find('{')
    last_brace  = cleaned.rfind('}')

    if first_brace == -1 or last_brace == -1:
        print("  [ERROR] No JSON found in validation response")
        return None

    # Slice from the first { to the last } as a starting point
    json_text = cleaned[first_brace : last_brace + 1]

    # First attempt — parse the full slice directly
    try:
        return json.loads(json_text)

    except json.JSONDecodeError:
        # Attempt 2 — Claude may have used single quotes (Python dict style)
        # ast.literal_eval handles {'key': 'value'} syntax correctly
        try:
            import ast
            result = ast.literal_eval(json_text)
            if isinstance(result, dict):
                return result
        except Exception:
            pass

        # Attempt 3 — "Extra data": Claude appended text after closing }.
        # Walk character by character tracking brace depth to find exactly
        # where the first complete top-level object ends.
        depth = 0
        for i, char in enumerate(json_text):
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(json_text[:i + 1])
                    except json.JSONDecodeError:
                        try:
                            import ast
                            result = ast.literal_eval(json_text[:i + 1])
                            if isinstance(result, dict):
                                return result
                        except Exception:
                            pass
                    print(f"  [ERROR] Could not parse validation JSON")
                    # Print first 300 chars so the raw response is visible
                    print(f"  [DEBUG] Response start: {json_text[:300]}")
                    return None

        print("  [ERROR] No complete JSON object found in validation response")
        print(f"  [DEBUG] Response start: {json_text[:300]}")
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

    result = {
        "step_name"         : step_name,
        "utility_type"      : utility_type,
        "code_path"         : code_path,
        "validation_status" : parsed_response.get("overall_status", STATUS_FAIL),
        "checks"            : parsed_response.get("checks", {}),
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
