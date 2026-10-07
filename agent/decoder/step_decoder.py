# =============================================================================
# agent/decoder/step_decoder.py
#
# PROJECT : JUIA — JCL Utility Intelligence Agent
# PURPOSE : Orchestrate the complete decoding pipeline for each JCL step.
#           This is the main entry point for Phase 3.
#
# WHAT THIS MODULE DOES (plain English):
#   This module ties together the RAG retriever and Claude decoder.
#   For each step from Phase 2, it decides what to do:
#
#     IEBGENER DUMMY     → skip Claude, return hardcoded copy result
#     PROGRAM step       → skip, note it needs separate analysis
#     SYSIN unresolved   → warn, skip Claude (nothing to decode)
#     Everything else    → retrieve RAG chunks → call Claude → return result
#
#   It also manages the shared resources (embedding model, ChromaDB
#   connection, API client) so they are loaded once and reused across
#   all steps rather than being reloaded for every step.
#
# HOW TO USE:
#   from agent.decoder.step_decoder import DecoderSession
#
#   session = DecoderSession()         # loads model, DB, API client
#   result  = session.decode_step(step)  # decode one step
#   session.decode_all_steps(steps)    # decode all steps
# =============================================================================


# =============================================================================
# SECTION 1 — IMPORTS
# =============================================================================

# Our own modules from this project
from agent.decoder.rag_retriever import (
    get_relevant_chunks,
    load_embedding_model,
    connect_to_chromadb
)

from agent.decoder.claude_decoder import (
    get_api_client,
    decode_with_claude,
    build_skipped_result,
    DECODE_STATUS_SKIPPED
)

# SYSIN source constants from Phase 2 parser
# We import these so we can check what type of SYSIN each step has
from agent.parser.jcl_splitter import (
    SYSIN_SOURCE_INLINE,
    SYSIN_SOURCE_DUMMY,
    SYSIN_SOURCE_EXTERNAL_RESOLVED,
    SYSIN_SOURCE_EXTERNAL_UNRESOLVED,
    SYSIN_SOURCE_NONE
)

from agent.parser.utility_classifier import UTILITY_PROGRAM


# =============================================================================
# SECTION 2 — DECODER SESSION CLASS
#
# A "class" in Python is a blueprint for creating objects that bundle
# related data and functions together.
#
# Here we use a class to hold the shared resources (embedding model,
# ChromaDB connection, API client) that are expensive to load and should
# be reused across all steps.
#
# HOW CLASSES WORK (simplified):
#   class MyClass:
#       def __init__(self):    ← called when you create an instance
#           self.data = ...    ← self.data is stored on the object
#       def my_function(self): ← a function that belongs to the class
#           use self.data      ← access the stored data
#
#   obj = MyClass()            ← creates an instance, calls __init__
#   obj.my_function()          ← calls the function on that instance
# =============================================================================

class DecoderSession:
    """
    Manages a decoding session — holds shared resources and decodes steps.

    Resources loaded once when the session starts:
        - SentenceTransformer embedding model (for RAG queries)
        - ChromaDB collection (the vector database)
        - Anthropic API client (for Claude calls)

    These are stored on the session object (self.model etc.) and reused
    for every step, avoiding the overhead of reloading them each time.

    Usage:
        session = DecoderSession()
        if session.is_ready():
            result = session.decode_step(one_step)
            results = session.decode_all_steps(all_steps)
    """

    def __init__(self):
        """
        Initialises the decoder session by loading all shared resources.

        __init__ is Python's special constructor method — it runs
        automatically when you write DecoderSession().
        """

        print("Initialising JUIA Decoder Session...")
        print()

        # Track whether all resources loaded successfully
        self._ready = True

        # Load the SentenceTransformer embedding model
        # (same model used in ingest_corpus.py)
        print("[1/3] Loading embedding model for RAG queries...")
        self.embedding_model = load_embedding_model()
        print()

        # Connect to ChromaDB
        print("[2/3] Connecting to ChromaDB...")
        self.chroma_collection = connect_to_chromadb()
        if self.chroma_collection is None:
            self._ready = False
            print("  RAG retrieval will not be available.")
        print()

        # Set up the Claude API client
        print("[3/3] Setting up Claude API client...")
        self.api_client = get_api_client()
        if self.api_client is None:
            self._ready = False
            print("  Claude decoding will not be available.")
        else:
            print("  Claude API client ready.")
        print()

        if self._ready:
            print("Decoder session ready. All resources loaded.")
        else:
            print("Decoder session partially ready — some features unavailable.")
        print()


    def is_ready(self):
        """
        Returns True if all required resources loaded successfully.

        Call this before starting to decode steps. If False, check the
        error messages printed during __init__ for what is missing.
        """
        return self._ready


    def decode_step(self, step):
        """
        Decodes a single JCL step and returns the decoded result.

        DECISION LOGIC:
            1. If PROGRAM step    → skip (no control cards to decode)
            2. If SYSIN = DUMMY   → return hardcoded copy result (no API call)
            3. If SYSIN = none    → skip (non-utility step)
            4. If SYSIN unresolved → warn and skip (upload to cntl/ folder first)
            5. Otherwise          → RAG retrieval → Claude API → decoded result

        Parameters:
            step : a step dictionary from jcl_splitter + utility_classifier

        Returns:
            A decoded result dictionary. See claude_decoder.py for the
            full structure. Always contains at minimum:
                step_name, utility_type, pgm, decode_status
        """

        step_name    = step.get("step_name", "UNKNOWN")
        utility_type = step.get("utility_type", "UNKNOWN")
        sysin_source = step.get("sysin_source", SYSIN_SOURCE_NONE)

        print(f"Decoding step: {step_name} ({utility_type})")

        # -----------------------------------------------------------------
        # Decision 1: PROGRAM step — skip
        # -----------------------------------------------------------------
        if utility_type == UTILITY_PROGRAM:
            print(f"  Skipping — non-utility PROGRAM step (PGM={step.get('pgm')})")
            return build_skipped_result(step, "Non-utility program step")

        # -----------------------------------------------------------------
        # Decision 2: IEBGENER with DUMMY — hardcoded copy result
        # -----------------------------------------------------------------
        if sysin_source == SYSIN_SOURCE_DUMMY:
            print(f"  SYSIN DD DUMMY — straight copy, no API call needed")
            return build_skipped_result(step, "SYSIN DD DUMMY — straight copy")

        # -----------------------------------------------------------------
        # Decision 3: No SYSIN at all
        # -----------------------------------------------------------------
        if sysin_source == SYSIN_SOURCE_NONE:
            print(f"  Skipping — no SYSIN DD statement found in this step")
            return build_skipped_result(step, "No SYSIN DD statement")

        # -----------------------------------------------------------------
        # Decision 4: External SYSIN not resolved
        # -----------------------------------------------------------------
        if sysin_source == SYSIN_SOURCE_EXTERNAL_UNRESOLVED:
            dsn = step.get("sysin_dsn", "unknown DSN")
            member = dsn.split("(")[-1].rstrip(")") if "(" in dsn else dsn
            print(f"  WARNING: External SYSIN not found in cntl/ folder")
            print(f"  Upload '{member}.cntl' to the cntl/ folder and re-run")
            return build_skipped_result(
                step,
                f"External SYSIN unresolved: {dsn}. "
                f"Upload to cntl/{member}.cntl and re-run."
            )

        # -----------------------------------------------------------------
        # Decision 5: Has SYSIN content — run RAG + Claude
        # -----------------------------------------------------------------
        sysin_content = step.get("sysin_content", "")

        # Step 5a: Retrieve relevant documentation from ChromaDB
        rag_context = ""
        if self.chroma_collection is not None:
            print(f"  Retrieving RAG context from ChromaDB...")
            rag_context = get_relevant_chunks(
                utility_type    = utility_type,
                sysin_content   = sysin_content,
                embedding_model = self.embedding_model,
                collection      = self.chroma_collection
            )
            chunk_count = rag_context.count("--- Source:")
            print(f"  Retrieved {chunk_count} relevant chunks")
        else:
            print("  ChromaDB unavailable — decoding without RAG context")

        # Step 5b: Call Claude API to decode the step
        if self.api_client is None:
            print("  Claude API unavailable — cannot decode")
            from agent.decoder.claude_decoder import build_error_result, DECODE_STATUS_API_ERROR
            return build_error_result(
                step, DECODE_STATUS_API_ERROR,
                "Claude API client not initialised"
            )

        result = decode_with_claude(
            step       = step,
            rag_context = rag_context,
            api_client = self.api_client
        )

        return result


    def decode_all_steps(self, steps):
        """
        Decodes all steps in the list and returns a list of results.

        Processes every step, including PROGRAM and DUMMY steps
        (which return skipped results without API calls).

        Parameters:
            steps : list of step dictionaries from Phase 2

        Returns:
            A list of decoded result dictionaries, one per step,
            in the same order as the input list.
        """

        all_results   = []
        total         = len(steps)
        api_calls     = 0
        skipped       = 0
        errors        = 0

        print("=" * 65)
        print(f"  Decoding {total} steps...")
        print("=" * 65)
        print()

        for index, step in enumerate(steps, start=1):

            step_name = step.get("step_name", "UNKNOWN")
            print(f"--- Step {index}/{total}: {step_name} ---")

            result = self.decode_step(step)
            all_results.append(result)

            # Track statistics
            status = result.get("decode_status", "")
            if status == "success":
                api_calls += 1
            elif status == "skipped":
                skipped += 1
            else:
                errors += 1

            print()

        # Print session summary
        print("=" * 65)
        print("  Decoding session complete")
        print("=" * 65)
        print(f"  Total steps    : {total}")
        print(f"  API calls made : {api_calls}")
        print(f"  Skipped        : {skipped}  "
              f"(DUMMY copies + PROGRAM steps)")
        print(f"  Errors         : {errors}")
        print()

        return all_results
