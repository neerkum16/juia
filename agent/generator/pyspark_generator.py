# =============================================================================
# agent/generator/pyspark_generator.py
#
# PROJECT : JUIA — JCL Utility Intelligence Agent
# PURPOSE : Generate complete, production-quality PySpark Python functions
#           from the decoded step information produced by Phase 3.
#
# WHAT THIS MODULE DOES (plain English):
#   Phase 3 decoded the business rules from each JCL utility step and
#   produced a one-line PySpark hint. Phase 4 expands that into a
#   complete Python file containing:
#     - All necessary imports
#     - Column name constants derived from the key field meanings
#     - A fully documented function implementing the business logic
#     - Inline comments referencing the original SYSIN control cards
#     - An example main() block showing how to call the function
#
#   The generated files are saved to outputs/pyspark/ and are
#   immediately usable by a developer as a starting point for the
#   modern migration.
#
# INPUT:
#   - decoded_step : result dictionary from Phase 3 (business_rule,
#                    key_fields, operations, pyspark_hint)
#   - original_step: step dictionary from Phase 2 (sysin_content,
#                    dd_statements)
#
# OUTPUT:
#   outputs/pyspark/
#     STEP010_IDCAMS.py
#     STEP020_IDCAMS.py
#     STEP030_DFSORT.py
#     STEP040_DFSORT.py
#     STEP070_DFSORT.py
#     STEP090_IDCAMS.py
#
# DATA FLOW:
#   decoded_step (Phase 3 JSON)  ──┐
#                                   ├──► build_generation_prompt()
#   original_step (Phase 2 parse) ──┘          │
#                                               ▼
#                                        Claude API call
#                                               │
#                                               ▼
#                                        Python code string
#                                               │
#                                               ▼
#                                        outputs/pyspark/*.py
# =============================================================================


# =============================================================================
# SECTION 1 — IMPORTS
# =============================================================================

# os — built into Python.
# Used to: create output folders, build file paths.
import os

# re — built into Python.
# Used to: strip markdown code fences from Claude's response if present.
import re

# anthropic — the Claude API client.
# Used to: send the generation prompt and receive Python code back.
import anthropic


# =============================================================================
# SECTION 2 — CONFIGURATION
# =============================================================================

# Claude model for code generation
# sonnet-4-6 produces high-quality Python/PySpark code
CLAUDE_MODEL = "claude-sonnet-4-6"

# Maximum tokens Claude can return for code generation.
# Each generated file contains:
#   - File header comment block
#   - All import statements
#   - Column name constants with mainframe offset annotations
#   - StructType schema definition
#   - Main business logic function with full docstring
#   - Internal validation helper function
#   - Example main() block with SparkSession setup and read/write
#
# A complete file runs approximately 14KB which is roughly 3,500 tokens
# of Python code. With the generation prompt itself consuming additional
# tokens on top of that, 4000 was right at the edge and caused the
# main() example block to be truncated mid-sentence on longer files.
# 8000 gives comfortable headroom for all utility types without hitting
# the limit again.
MAX_TOKENS = 16000

# Where generated Python files are saved
PYSPARK_OUTPUT_FOLDER = "outputs/pyspark"


# =============================================================================
# SECTION 3 — HELPER FUNCTIONS
# =============================================================================

def build_filename(step_name, utility_type):
    """
    Builds the output filename for a generated PySpark file.

    Format: STEP030_DFSORT.py

    Parameters:
        step_name    : step name from JCL (e.g. "STEP030")
        utility_type : classified utility type (e.g. "DFSORT")

    Returns:
        A filename string ending in .py
    """
    return f"{step_name}_{utility_type}.py"


def format_key_fields_for_prompt(key_fields):
    """
    Formats the key_fields list from Phase 3 into a readable table
    for inclusion in the generation prompt.

    Parameters:
        key_fields : list of field dictionaries from decoded result

    Returns:
        A formatted multi-line string.

    Example output:
        Offset 17  Len 9  Format PD : Transaction amount in cents
        Offset 44  Len 8  Format CH : Value date YYYYMMDD
        Offset 55  Len 2  Format CH : Transaction status (PE=Pending)
    """

    if not key_fields:
        return "  (no key fields identified)"

    lines = []
    for field in key_fields:
        offset  = field.get("offset", "?")
        length  = field.get("length", "?")
        fmt     = field.get("format", "?")
        meaning = field.get("meaning", "")
        lines.append(f"  Offset {offset:<5} Len {length:<4} Format {fmt:<4} : {meaning}")

    return "\n".join(lines)


def format_operations_for_prompt(operations):
    """
    Formats the operations list from Phase 3 for the generation prompt.

    Parameters:
        operations : list of operation dictionaries from decoded result

    Returns:
        A formatted multi-line string.
    """

    if not operations:
        return "  (no operations identified)"

    lines = []
    for op in operations:
        op_type = op.get("type", "UNKNOWN")
        op_desc = op.get("description", "")
        lines.append(f"  [{op_type}] {op_desc}")

    return "\n".join(lines)


def format_dd_statements_for_prompt(dd_statements):
    """
    Formats DD statements from the original Phase 2 step for the prompt.
    Skips SYSPRINT and SYSOUT — only shows business data files.

    Parameters:
        dd_statements : list of DD dictionaries from Phase 2 step

    Returns:
        A formatted multi-line string.
    """

    skip_ddnames = {"SYSPRINT", "SYSOUT", "SYSABEND", "SYSUDUMP"}
    lines = []

    for dd in dd_statements:
        ddname = dd.get("ddname", "").upper()
        if ddname in skip_ddnames:
            continue
        parameters = dd.get("parameters", "")
        if ddname == "SYSIN":
            parameters = "(control cards — shown in SYSIN section)"
        lines.append(f"  //{ddname:<12} {parameters}")

    return "\n".join(lines) if lines else "  (no DD statements)"


def clean_code_response(response_text):
    """
    Cleans Claude's response to extract pure Python code.

    Claude occasionally wraps its response in markdown code fences
    (```python ... ```) even when instructed not to. This function
    strips those fences so we get clean Python code.

    It also strips any leading explanation text before the first
    import statement or comment, in case Claude added a preamble.

    Parameters:
        response_text : the raw response string from Claude

    Returns:
        Clean Python code as a string.
    """

    if not response_text:
        return ""

    cleaned = response_text.strip()

    # Remove markdown python code fence at start
    cleaned = re.sub(r'^```python\s*', '', cleaned, flags=re.MULTILINE)

    # Remove markdown code fence at end
    cleaned = re.sub(r'^```\s*$', '', cleaned, flags=re.MULTILINE)

    cleaned = cleaned.strip()

    return cleaned


# =============================================================================
# SECTION 4 — PROMPT BUILDER
# =============================================================================

def build_generation_prompt(decoded_step, original_step):
    """
    Assembles the complete prompt for Claude to generate a PySpark function.

    The prompt contains all context Claude needs:
        - What the step does (business rule from Phase 3)
        - What fields are involved (key fields with offsets and meanings)
        - What operations to implement (decoded operations list)
        - What the files are (DD statements)
        - The original mainframe control cards (SYSIN content)
        - A one-line PySpark hint as a starting point
        - Detailed instructions on exactly what code to produce

    Parameters:
        decoded_step  : result dictionary from Phase 3 decoder
        original_step : step dictionary from Phase 2 parser

    Returns:
        A complete prompt string ready to send to Claude.
    """

    step_name    = decoded_step.get("step_name", "UNKNOWN")
    utility_type = decoded_step.get("utility_type", "UNKNOWN")
    pgm          = decoded_step.get("pgm", "UNKNOWN")
    business_rule = decoded_step.get("business_rule", "")
    pyspark_hint  = decoded_step.get("pyspark_hint", "")
    input_files   = decoded_step.get("input_files", [])
    output_files  = decoded_step.get("output_files", [])
    key_fields    = decoded_step.get("key_fields", [])
    operations    = decoded_step.get("operations", [])
    notes         = decoded_step.get("notes", "")

    # Get original SYSIN from Phase 2 step
    sysin_content = original_step.get("sysin_content", "")
    dd_statements = original_step.get("dd_statements", [])

    # Format sections for the prompt
    key_fields_text  = format_key_fields_for_prompt(key_fields)
    operations_text  = format_operations_for_prompt(operations)
    dd_text          = format_dd_statements_for_prompt(dd_statements)
    input_files_text  = ", ".join(input_files) if input_files else "(none)"
    output_files_text = ", ".join(output_files) if output_files else "(none)"

    # Build the output filename for reference in the prompt
    output_filename = build_filename(step_name, utility_type)

    prompt = f"""You are an expert IBM mainframe migration engineer converting JCL utility steps to Apache PySpark.

Generate a complete, production-quality Python file implementing the following JCL utility step as a PySpark function.

=== STEP INFORMATION ===
Step Name    : {step_name}
Program      : {pgm}
Utility Type : {utility_type}
Output file  : {output_filename}

=== BUSINESS RULE (from decoder analysis) ===
{business_rule}

=== KEY FIELDS (mainframe record layout — offsets are 1-based) ===
{key_fields_text}

=== OPERATIONS TO IMPLEMENT ===
{operations_text}

=== DD STATEMENTS (input and output files) ===
{dd_text}
Input files  : {input_files_text}
Output files : {output_files_text}

=== ORIGINAL MAINFRAME SYSIN CONTROL CARDS ===
{sysin_content if sysin_content else "(no control cards — catalog operation or copy)"}

=== PYSPARK HINT (one-liner from decoder — expand this into full code) ===
{pyspark_hint if pyspark_hint else "(no hint available)"}

=== MIGRATION NOTES ===
{notes if notes else "(none)"}

=== INSTRUCTIONS — Generate exactly this structure ===

Produce a single Python file containing:

1. FILE HEADER — a comment block with:
   - Step name, program, utility type
   - Job name (wire_payment_batch.jcl)
   - The business rule in plain English
   - A "Generated by JUIA" notice

2. IMPORTS — all required PySpark imports:
   from pyspark.sql import DataFrame, SparkSession
   from pyspark.sql import functions as F
   from pyspark.sql.types import StructType, StructField, StringType, LongType, etc.
   (only import what is actually used)

3. COLUMN NAME CONSTANTS — define Python constants for every column name:
   TRANSACTION_STATUS = "transaction_status"   # offset 55, len 2, CH
   AMOUNT             = "amount"               # offset 17, len 9, PD
   (one constant per key field, derived from the field meaning)
   Add a comment showing the mainframe offset/length/format for each.

4. MAIN FUNCTION — implement the business logic:
   - Use a meaningful function name derived from the step purpose
     (e.g. extract_pending_wires, split_by_value, summarise_by_branch)
   - Accept input_df: DataFrame as the first parameter
   - For steps with multiple outputs (OUTFIL splits), return a dict:
     {{"HIGH": df_high, "LOW": df_low}}
   - For single-output steps, return a DataFrame
   - Add a detailed docstring with:
       * Business rule description
       * Parameter descriptions and expected column schema
       * Return value description
       * Key field mapping table (mainframe offset → column name)
       * The original SYSIN pasted verbatim as a code comment
   - Add an inline comment before each operation referencing the
     mainframe SYSIN statement it implements. For example:
       # Mainframe: INCLUDE COND=(55,2,CH,EQ,C'PE',AND,17,9,PD,GT,+0)
       result = input_df.filter(...)
   - For packed decimal (PD) fields: add a comment noting that the
     column must be pre-decoded from PD format before this function runs
   - Use only the PySpark DataFrame API — no spark.sql() string queries

5. EXAMPLE MAIN BLOCK — show how to call the function:
   if __name__ == "__main__":
       spark = SparkSession.builder.appName("{step_name}").getOrCreate()
       # Load input (replace with actual data source)
       input_df = spark.read.parquet("path/to/{(input_files[0] if input_files else 'input').lower().replace('.', '_')}")
       result = function_name(input_df)
       # Write output
       result.write.mode("overwrite").parquet("path/to/output")
       spark.stop()

IMPORTANT RULES:
- Return ONLY valid Python code. No markdown, no code fences, no explanation.
- Every column reference must use the COLUMN NAME CONSTANTS, not string literals.
- PD (packed decimal) fields need a comment: the column must hold the decoded
  integer value, not raw bytes. The PD decoding step is handled separately.
- EBCDIC character fields that contain codes like 'PE': add a comment noting
  these are EBCDIC values that must be transcoded to UTF-8 before filtering.
- If the utility type is IDCAMS and the operation is DELETE/DEFINE (no data
  movement), generate Python code using Delta Lake table operations or
  file system operations as the equivalent.
- If the utility type is IDCAMS LISTCAT, generate a metadata verification
  function that checks table/dataset existence.
- Make the generated code something a senior developer would be proud of."""

    return prompt


# =============================================================================
# SECTION 5 — CLAUDE API CALLER FOR CODE GENERATION
# =============================================================================

def call_claude_for_code(prompt, api_client):
    """
    Calls Claude API to generate the PySpark code.

    This is similar to the Phase 3 decoder API call but:
    - Uses a higher MAX_TOKENS limit (code is longer than JSON)
    - Expects raw Python code back, not JSON
    - Uses the same model (claude-sonnet-4-6)

    Parameters:
        prompt     : the complete generation prompt string
        api_client : Anthropic client object

    Returns:
        The generated Python code as a string, or None on failure.
    """

    try:
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

        return message.content[0].text

    except anthropic.APIConnectionError as error:
        print(f"  [ERROR] Connection error: {error}")
        return None

    except anthropic.RateLimitError as error:
        print(f"  [ERROR] Rate limit: {error}")
        return None

    except anthropic.APIStatusError as error:
        print(f"  [ERROR] API error {error.status_code}: {error.message}")
        return None

    except Exception as error:
        print(f"  [ERROR] Unexpected error: {error}")
        return None


# =============================================================================
# SECTION 6 — FILE SAVER
# =============================================================================

def save_generated_code(code, step_name, utility_type, output_folder):
    """
    Saves the generated Python code to a file in the output folder.

    Creates the output folder if it does not exist.

    Parameters:
        code          : the generated Python code string
        step_name     : step name for the filename (e.g. "STEP030")
        utility_type  : utility type for the filename (e.g. "DFSORT")
        output_folder : path to the folder to save in

    Returns:
        The full path of the saved file, or None if saving failed.
    """

    os.makedirs(output_folder, exist_ok=True)

    filename    = build_filename(step_name, utility_type)
    output_path = os.path.join(output_folder, filename)

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(code)
        return output_path

    except Exception as error:
        print(f"  [ERROR] Could not save file {output_path}: {error}")
        return None


# =============================================================================
# SECTION 7 — MAIN GENERATION FUNCTION
# =============================================================================

def generate_pyspark_code(decoded_step, original_step, api_client):
    """
    Generates a complete PySpark Python file for one JCL step.

    This is the main function of this module. It orchestrates:
        1. Build the generation prompt
        2. Call Claude API
        3. Clean the code response
        4. Save to outputs/pyspark/

    Parameters:
        decoded_step  : result dictionary from Phase 3
                        (business_rule, key_fields, operations, pyspark_hint)
        original_step : step dictionary from Phase 2
                        (sysin_content, dd_statements)
        api_client    : Anthropic client object

    Returns:
        A tuple of (file_path, status):
            file_path : where the file was saved (or None on failure)
            status    : "success", "api_error", or "save_error"
    """

    step_name    = decoded_step.get("step_name", "UNKNOWN")
    utility_type = decoded_step.get("utility_type", "UNKNOWN")

    print(f"  Generating PySpark code for {step_name} ({utility_type})...")

    # Step 1: Build the prompt
    prompt = build_generation_prompt(decoded_step, original_step)

    # Step 2: Call Claude API
    raw_response = call_claude_for_code(prompt, api_client)

    if raw_response is None:
        print(f"  [ERROR] API call failed for {step_name}")
        return (None, "api_error")

    # Step 3: Clean the response (strip markdown fences if present)
    clean_code = clean_code_response(raw_response)

    if not clean_code.strip():
        print(f"  [ERROR] Empty response for {step_name}")
        return (None, "api_error")

    # Step 4: Save to file
    file_path = save_generated_code(
        code          = clean_code,
        step_name     = step_name,
        utility_type  = utility_type,
        output_folder = PYSPARK_OUTPUT_FOLDER
    )

    if file_path is None:
        return (None, "save_error")

    print(f"  Saved: {file_path}")
    return (file_path, "success")
