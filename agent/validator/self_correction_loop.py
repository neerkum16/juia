# =============================================================================
# agent/validator/self_correction_loop.py
#
# PROJECT : JUIA — JCL Utility Intelligence Agent
# PURPOSE : If validation fails, ask Claude to fix the specific issues
#           in the generated code, save the correction, and re-validate.
#           Repeat up to MAX_RETRIES times.
#
# WHAT THIS MODULE DOES (plain English):
#   This is the agentic loop in JUIA. When Phase 5 validation finds a
#   problem in the generated PySpark code, this module:
#     1. Takes the generated code and the validation report
#     2. Builds a correction prompt showing Claude exactly what is wrong
#     3. Asks Claude to produce a corrected version of the code
#     4. Saves the corrected code over the original file
#     5. Re-validates the corrected code
#     6. Repeats until PASS or MAX_RETRIES is reached
#
#   This loop makes JUIA genuinely agentic — it does not just flag errors
#   and stop, it attempts to fix them autonomously.
#
# LOOP FLOW:
#
#   validate → PASS → done, return result
#   validate → FAIL → build correction prompt
#                    → Claude produces corrected code
#                    → save corrected code
#                    → re-validate
#                    → repeat up to MAX_RETRIES times
#                    → if still FAIL after MAX_RETRIES → return REVIEW result
#                      with full correction history
#
# CORRECTION HISTORY:
#   Every correction attempt is recorded so the final report shows
#   exactly what was tried and what changed.
# =============================================================================


# =============================================================================
# SECTION 1 — IMPORTS
# =============================================================================

import re

from agent.validator.code_validator import (
    validate_generated_code,
    read_generated_code,
    STATUS_PASS,
    STATUS_FAIL,
    STATUS_REVIEW,
    STATUS_ERROR,
    CLAUDE_MODEL,
    MAX_TOKENS
)


# =============================================================================
# SECTION 2 — CONFIGURATION
# =============================================================================

# Maximum number of correction attempts before giving up
MAX_RETRIES = 3


# =============================================================================
# SECTION 3 — CORRECTION PROMPT BUILDER
# =============================================================================

def build_correction_prompt(decoded_step, original_step,
                              current_code, validation_result,
                              attempt_number):
    """
    Builds a prompt asking Claude to fix the specific issues found
    during validation.

    The prompt includes:
        - The original SYSIN (what the code SHOULD implement)
        - The current code (what was generated)
        - The validation report (what is wrong)
        - The specific correction hint from the validator
        - Clear instructions to fix ONLY the identified issues

    Parameters:
        decoded_step      : Phase 3 decoded result dict
        original_step     : Phase 2 parsed step dict
        current_code      : the current Python code as a string
        validation_result : the validation result from code_validator
        attempt_number    : which correction attempt this is (1, 2, or 3)

    Returns:
        A prompt string ready to send to Claude.
    """

    step_name       = decoded_step.get("step_name", "UNKNOWN")
    utility_type    = decoded_step.get("utility_type", "UNKNOWN")
    business_rule   = decoded_step.get("business_rule", "")
    sysin_content   = original_step.get("sysin_content", "")
    issues_found    = validation_result.get("issues_found", [])
    correction_hint = validation_result.get("correction_hint", "")

    # Format the validation checks that failed
    checks      = validation_result.get("checks", {})
    failed_checks_text = ""
    for check_name, check_result in checks.items():
        check_status = check_result.get("status", "")
        if check_status == "FAIL":
            check_detail = check_result.get("detail", "")
            failed_checks_text += f"  [{check_name}] FAIL: {check_detail}\n"

    if not failed_checks_text:
        failed_checks_text = "  (no specific failed checks listed)\n"

    # Format issues list
    issues_text = ""
    for i, issue in enumerate(issues_found, start=1):
        issues_text += f"  {i}. {issue}\n"
    if not issues_text:
        issues_text = "  (no specific issues listed)\n"

    prompt = f"""You are a senior PySpark engineer fixing a mainframe migration bug.

A PySpark function was generated to implement a mainframe JCL utility step.
Validation found issues that need to be corrected.
This is correction attempt {attempt_number} of {MAX_RETRIES}.

=== STEP INFORMATION ===
Step Name    : {step_name}
Utility Type : {utility_type}

=== BUSINESS RULE (what the code must implement) ===
{business_rule}

=== ORIGINAL MAINFRAME SYSIN (the ground truth) ===
{sysin_content if sysin_content else "(no SYSIN — catalog or copy operation)"}

=== FAILED VALIDATION CHECKS ===
{failed_checks_text}
=== SPECIFIC ISSUES FOUND ===
{issues_text}
=== CORRECTION HINT ===
{correction_hint if correction_hint else "(no specific hint provided)"}

=== CURRENT CODE (needs fixing) ===
{current_code}

=== INSTRUCTIONS ===
Produce a corrected version of the complete Python file that fixes ALL the
issues listed above.

Rules:
  1. Fix ONLY the identified issues — do not rewrite working parts
  2. Keep the same file structure, function names, and column constants
  3. Add a comment near each fix explaining what was changed and why
  4. Ensure the corrected code still correctly implements the SYSIN logic
  5. Return ONLY the complete corrected Python code
     No markdown, no explanation, just the Python file

The corrected code must be a complete, runnable Python file."""

    return prompt


# =============================================================================
# SECTION 4 — CORRECTION CALLER
# =============================================================================

def request_correction(decoded_step, original_step, current_code,
                        validation_result, attempt_number, api_client):
    """
    Calls Claude API to get a corrected version of the generated code.

    Parameters:
        decoded_step      : Phase 3 decoded result dict
        original_step     : Phase 2 parsed step dict
        current_code      : the current Python code string
        validation_result : the validation result from code_validator
        attempt_number    : which attempt this is (1, 2, or 3)
        api_client        : Anthropic client object

    Returns:
        The corrected Python code as a string, or None on failure.
    """

    prompt = build_correction_prompt(
        decoded_step      = decoded_step,
        original_step     = original_step,
        current_code      = current_code,
        validation_result = validation_result,
        attempt_number    = attempt_number
    )

    try:
        message = api_client.messages.create(
            model      = CLAUDE_MODEL,
            max_tokens = 16000,   # Same as generation — corrected code is full file
            messages   = [{"role": "user", "content": prompt}]
        )
        response_text = message.content[0].text

        # Strip markdown fences if present
        cleaned = response_text.strip()
        cleaned = re.sub(r'^```python\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'^```\s*$',      '', cleaned, flags=re.MULTILINE)
        return cleaned.strip()

    except Exception as error:
        print(f"  [ERROR] Correction API call failed: {error}")
        return None


# =============================================================================
# SECTION 5 — CODE FILE UPDATER
# =============================================================================

def save_corrected_code(code, code_path):
    """
    Overwrites the existing generated file with the corrected code.

    Each job run has its own output directory. The caller records correction
    history in its validation report, so source backups are not written.

    Parameters:
        code      : the corrected Python code string
        code_path : path to the .py file to overwrite

    Returns:
        True on success, False on failure.
    """

    try:
        # Write corrected code
        with open(code_path, "w", encoding="utf-8") as f:
            f.write(code)

        print(f"  Corrected code saved: {code_path}")
        return True

    except Exception as error:
        print(f"  [ERROR] Could not save corrected code: {error}")
        return False


# =============================================================================
# SECTION 6 — MAIN CORRECTION LOOP
# =============================================================================

def run_correction_loop(decoded_step, original_step,
                         code_path, api_client):
    """
    Runs the full validate → correct → re-validate loop for one step.

    This is the main function of this module and the agentic core of JUIA.

    LOOP LOGIC:
        1. Validate the current code
        2. If PASS → done, return result
        3. If FAIL and retries remain:
           a. Ask Claude to fix the specific issues
           b. Save the corrected code
           c. Re-validate
           d. Record what was attempted
           e. Repeat from step 2
        4. If FAIL after MAX_RETRIES → return REVIEW with the final issues
           and full correction history

    Parameters:
        decoded_step  : Phase 3 decoded result dict
        original_step : Phase 2 parsed step dict
        code_path     : path to the generated .py file
        api_client    : Anthropic client object

    Returns:
        The final validation result dictionary.
        Always contains "corrections_applied" and "correction_history".
    """

    step_name    = decoded_step.get("step_name", "UNKNOWN")
    utility_type = decoded_step.get("utility_type", "UNKNOWN")

    print(f"\n  Running correction loop for {step_name} ({utility_type})")
    print(f"  Max retries: {MAX_RETRIES}")

    # Track all correction attempts
    correction_history = []
    corrections_applied = 0

    # -------------------------------------------------------------------------
    # Initial validation — attempt 0
    # -------------------------------------------------------------------------
    print(f"\n  Attempt 0: Initial validation")
    validation_result = validate_generated_code(
        decoded_step  = decoded_step,
        original_step = original_step,
        code_path     = code_path,
        api_client    = api_client
    )

    # If initial validation passes, we are done
    if validation_result.get("validation_status") == STATUS_PASS:
        print(f"  Initial validation PASSED — no corrections needed")
        validation_result["corrections_applied"] = 0
        validation_result["correction_history"]  = []
        return validation_result

    # If the validation itself errored (not FAIL but ERROR), stop
    if validation_result.get("validate_status") == "error":
        print(f"  Validation errored — cannot proceed with correction")
        validation_result["corrections_applied"] = 0
        validation_result["correction_history"]  = []
        return validation_result

    # -------------------------------------------------------------------------
    # Correction loop — attempts 1 to MAX_RETRIES
    # -------------------------------------------------------------------------
    for attempt in range(1, MAX_RETRIES + 1):

        print(f"\n  Attempt {attempt}/{MAX_RETRIES}: Requesting correction from Claude")

        issues  = validation_result.get("issues_found", [])
        hint    = validation_result.get("correction_hint", "")
        print(f"  Issues to fix: {len(issues)}")
        if hint:
            print(f"  Hint: {hint[:100]}...")

        # Read the current version of the file
        current_code = read_generated_code(code_path)
        if not current_code:
            print(f"  [ERROR] Cannot read code file — stopping loop")
            break

        # Ask Claude to produce a corrected version
        corrected_code = request_correction(
            decoded_step      = decoded_step,
            original_step     = original_step,
            current_code      = current_code,
            validation_result = validation_result,
            attempt_number    = attempt,
            api_client        = api_client
        )

        if corrected_code is None:
            print(f"  [ERROR] Correction request failed — stopping loop")
            break

        if not corrected_code.strip():
            print(f"  [ERROR] Empty correction response — stopping loop")
            break

        # Save the corrected code
        saved = save_corrected_code(corrected_code, code_path)
        if not saved:
            print(f"  [ERROR] Could not save correction — stopping loop")
            break

        corrections_applied += 1

        # Record this attempt in history
        attempt_record = {
            "attempt"          : attempt,
            "issues_before"    : issues,
            "correction_hint"  : hint
        }
        correction_history.append(attempt_record)

        # Re-validate the corrected code
        print(f"  Re-validating after correction {attempt}...")
        validation_result = validate_generated_code(
            decoded_step  = decoded_step,
            original_step = original_step,
            code_path     = code_path,
            api_client    = api_client
        )

        # Update the attempt record with the result
        attempt_record["status_after"] = validation_result.get("validation_status")
        attempt_record["issues_after"] = validation_result.get("issues_found", [])

        if validation_result.get("validation_status") == STATUS_PASS:
            print(f"  Validation PASSED after {attempt} correction(s)")
            break
        else:
            remaining = MAX_RETRIES - attempt
            if remaining > 0:
                print(f"  Still FAILING — {remaining} attempt(s) remaining")
            else:
                print(f"  MAX_RETRIES reached — marking for human REVIEW")

    # -------------------------------------------------------------------------
    # Add correction metadata to the final result
    # -------------------------------------------------------------------------
    validation_result["corrections_applied"] = corrections_applied
    validation_result["correction_history"]  = correction_history
    if (validation_result.get("validation_status") == STATUS_FAIL
            and validation_result.get("validate_status") != "error"
            and corrections_applied == MAX_RETRIES):
        validation_result["last_validation_status"] = STATUS_FAIL
        validation_result["validation_status"] = STATUS_REVIEW
        validation_result["review_required"] = True

    return validation_result
