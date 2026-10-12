# =============================================================================
# agent/decoder/rag_retriever.py
#
# PROJECT : JUIA — JCL Utility Intelligence Agent
# PURPOSE : Query ChromaDB to find the most relevant chunks from the
#           IBM manuals and synthetic pattern files for a given
#           utility step.
#
# WHAT THIS MODULE DOES (plain English):
#   When the decoder needs to understand a DFSORT INCLUDE statement or
#   an IDCAMS REPRO command, it needs relevant reference documentation.
#   This module searches the ChromaDB vector database (built in Phase 1)
#   and returns the most relevant chunks for the specific step being decoded.
#
#   The search is:
#     1. TARGETED by utility type — DFSORT steps only search DFSORT manual
#        chunks; IDCAMS steps only search IDCAMS chunks. This prevents
#        irrelevant results from other manuals polluting the context.
#     2. QUERY-DRIVEN — we build a search query from the control card
#        keywords found in the SYSIN content.
#     3. SEMANTIC — ChromaDB finds chunks that are similar in meaning
#        to the query, not just exact keyword matches.
#
# DATA FLOW:
#   step (utility_type + sysin_content)
#        │
#        ▼
#   build_rag_query()  → search query string
#        │
#        ▼
#   ChromaDB.query()   → top N most similar chunks
#        │
#        ▼
#   formatted context string (passed to Claude API)
# =============================================================================


# =============================================================================
# SECTION 1 — IMPORTS
# =============================================================================

# os — built into Python.
# Used to: check if the ChromaDB folder exists before connecting.
import os
import re

# chromadb — our local vector database.
# Used to: connect and run similarity searches.
import chromadb

# SentenceTransformer — converts text to embeddings (numbers).
# The same model used during ingestion MUST be used during retrieval.
# Using a different model would produce incompatible embeddings.
from sentence_transformers import SentenceTransformer


# =============================================================================
# SECTION 2 — CONFIGURATION
# =============================================================================

# ChromaDB settings — must match what was used in ingest_corpus.py
VECTORDB_FOLDER  = "corpus/vectordb"
COLLECTION_NAME  = "juia_corpus"
EMBEDDING_MODEL  = "all-MiniLM-L6-v2"

# How many chunks to retrieve per query.
# 5 gives enough context without overwhelming the Claude API prompt.
TOP_K_RESULTS = 5

# =============================================================================
# SECTION 3 — SOURCE DOCUMENT MAPPING
#
# Each utility type has a list of ChromaDB source names that contain
# relevant documentation. We only search those sources for each utility.
# This keeps retrieved chunks focused and relevant.
# =============================================================================

# Maps utility type → list of ChromaDB source names to search
RAG_SOURCE_MAP = {
    "DFSORT"   : ["dfsort_apg", "dfsort_patterns"],
    "ICETOOL"  : ["dfsort_apg", "dfsort_patterns"],
    "IDCAMS"   : ["idcams", "idcams_patterns"],
    "IEBGENER" : ["dfsmdfp_utilities", "iebgener_patterns"],
    "IEBCOPY"  : ["dfsmdfp_utilities"],
    "PROGRAM"  : [],   # Non-utility — no RAG needed
}

# =============================================================================
# SECTION 4 — KEYWORDS FOR QUERY BUILDING
#
# These are the control card statement names for each utility.
# We look for these keywords in the SYSIN content to build a
# targeted search query.
# =============================================================================

# Statement keywords to look for in SYSIN — ordered by importance
DFSORT_KEYWORDS   = ["INCLUDE", "OMIT", "SORT", "OUTFIL", "OUTREC",
                      "INREC", "SUM", "JOINKEYS", "MERGE", "COPY",
                      "FIELDS", "COND", "IFTHEN"]

ICETOOL_KEYWORDS  = ["RESIZE", "TOLEN", "SELECT", "SORT", "COPY", "STATS", "SPLICE",
                      "UNIQUE", "DISPLAY", "COUNT", "RANGE", "OCCUR",
                      "FROM", "USING", "ON", "FIRST", "LAST"]

IDCAMS_KEYWORDS   = ["REPRO", "DEFINE", "DELETE", "LISTCAT", "BLDINDEX",
                      "EXPORT", "IMPORT", "PRINT", "VERIFY", "ALLOCATE",
                      "IF", "LASTCC", "CLUSTER", "INDEXED", "NONINDEXED"]

IEBGENER_KEYWORDS = ["GENERATE", "RECORD", "MEMBER", "FIELD",
                      "MAXFLDS", "MAXLITS", "EXITS"]

IEBCOPY_KEYWORDS  = ["COPY", "SELECT", "EXCLUDE", "INDD",
                      "OUTDD", "MEMBER", "COPYGRP", "COPYGROUP"]

# Maps utility type → its keyword list
UTILITY_KEYWORDS = {
    "DFSORT"   : DFSORT_KEYWORDS,
    "ICETOOL"  : ICETOOL_KEYWORDS,
    "IDCAMS"   : IDCAMS_KEYWORDS,
    "IEBGENER" : IEBGENER_KEYWORDS,
    "IEBCOPY"  : IEBCOPY_KEYWORDS,
}


# =============================================================================
# SECTION 5 — QUERY BUILDER
# =============================================================================

def build_rag_query(utility_type, sysin_content):
    """
    Builds a targeted search query for ChromaDB from the utility type
    and the SYSIN control card content.

    STRATEGY:
        Start with the utility type name as the base.
        Look through the SYSIN for known control card statement keywords.
        Add the first 4 found keywords to narrow the search.
        Append "syntax" to encourage retrieval of reference documentation.

    EXAMPLE:
        utility_type = "DFSORT"
        sysin_content = "  INCLUDE COND=(55,2,CH,EQ,C'PE',...)\n  SORT FIELDS=..."

        Finds keywords: INCLUDE, SORT, COND, FIELDS
        Builds query: "DFSORT INCLUDE SORT COND syntax"

    Parameters:
        utility_type  : string like "DFSORT", "IDCAMS", "IEBGENER"
        sysin_content : the full SYSIN text for this step

    Returns:
        A search query string suitable for ChromaDB similarity search.
    """

    # Start with the utility type name
    query_parts = [utility_type]

    # Get the keyword list for this utility type
    # .get() returns an empty list if the utility type is not in the map
    keywords_to_look_for = UTILITY_KEYWORDS.get(utility_type, [])

    # Search the SYSIN content for these keywords (case insensitive)
    sysin_upper  = sysin_content.upper()
    found_so_far = []

    for keyword in keywords_to_look_for:
        if re.search(rf"\b{re.escape(keyword.upper())}\b", sysin_upper):
            found_so_far.append(keyword)

        # Stop after finding 4 keywords — enough for a focused query
        if len(found_so_far) >= 4:
            break

    # Add the found keywords to the query
    query_parts.extend(found_so_far)

    # Add "syntax" to bias retrieval toward reference documentation
    query_parts.append("syntax")

    # Join into a single search string
    final_query = " ".join(query_parts)

    return final_query


# =============================================================================
# SECTION 6 — MAIN RETRIEVAL FUNCTION
# =============================================================================

def get_relevant_chunks(utility_type, sysin_content,
                         embedding_model, collection):
    """
    Retrieves the most relevant documentation chunks from ChromaDB
    for a given utility step.

    This is the main function of this module.

    HOW IT WORKS:
        1. Build a search query from utility type + SYSIN keywords
        2. Convert the query to an embedding using SentenceTransformer
        3. Query ChromaDB, filtered to only the relevant source documents
        4. Return the top chunks formatted as a single context string

    Parameters:
        utility_type    : string — the classified utility type (e.g. "DFSORT")
        sysin_content   : string — the SYSIN control card text
        embedding_model : loaded SentenceTransformer model object
        collection      : ChromaDB collection object

    Returns:
        A formatted string containing the retrieved chunks, ready to
        be included in the Claude API prompt as reference documentation.
        Returns an empty string if no relevant sources exist.
    """

    # Get the list of source documents to search for this utility
    sources_to_search = RAG_SOURCE_MAP.get(utility_type, [])

    if len(sources_to_search) == 0:
        # No RAG sources defined for this utility type
        return ""

    # Build the search query
    query = build_rag_query(utility_type, sysin_content)

    # Convert the query text to an embedding vector
    # .tolist() converts the numpy array to a plain Python list
    query_embedding = embedding_model.encode(query).tolist()

    # Build the ChromaDB filter to restrict search to relevant sources
    # $in operator means "source must be one of these values"
    where_filter = {
        "source": {"$in": sources_to_search}
    }

    # Query ChromaDB for the most similar chunks
    results = collection.query(
        query_embeddings = [query_embedding],
        n_results        = TOP_K_RESULTS,
        where            = where_filter,
        include          = ["documents", "metadatas", "distances"]
    )

    # Format the results as a readable context string for Claude
    context_string = format_chunks_for_prompt(results, query)

    return context_string


def format_chunks_for_prompt(query_results, query_used):
    """
    Formats ChromaDB query results into a readable string for the
    Claude API prompt.

    Each chunk is labeled with its source and relevance score so
    Claude can see where the information comes from.

    Parameters:
        query_results : the raw dictionary returned by ChromaDB
        query_used    : the query string that was used (shown in output)

    Returns:
        A formatted multi-line string containing all retrieved chunks.
    """

    # ChromaDB returns results as lists of lists.
    # [0] gets the results for our single query.
    documents = query_results["documents"][0]
    metadatas = query_results["metadatas"][0]
    distances = query_results["distances"][0]

    if len(documents) == 0:
        return "(No relevant documentation found)"

    lines = []
    lines.append(f"[RAG Query used: '{query_used}']")
    lines.append(f"[Retrieved {len(documents)} chunks]")
    lines.append("")

    for index in range(len(documents)):
        chunk_text = documents[index]
        source     = metadatas[index].get("source", "unknown")
        distance   = distances[index]

        # Add a separator and source label before each chunk
        lines.append(f"--- Source: {source} (relevance score: {distance:.4f}) ---")
        lines.append(chunk_text.strip())
        lines.append("")

    return "\n".join(lines)


# =============================================================================
# SECTION 7 — SETUP FUNCTIONS
# Functions to initialise the embedding model and ChromaDB connection.
# Called once at the start of a decoding session and reused across steps.
# =============================================================================

def load_embedding_model():
    """
    Loads the SentenceTransformer embedding model.

    This is the same model used during corpus ingestion.
    On first call after ingestion, the model is already cached locally
    so loading is fast (a few seconds, not a download).

    Returns:
        A loaded SentenceTransformer model object.
    """
    print(f"  Loading embedding model: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL)
    print("  Embedding model loaded.")
    return model


def connect_to_chromadb():
    """
    Connects to the ChromaDB vector database and returns the collection.

    Checks that the database folder and collection exist before connecting.
    Provides clear error messages if they are not found.

    Returns:
        A ChromaDB collection object, or None if connection fails.
    """

    # Check the database folder exists
    if not os.path.exists(VECTORDB_FOLDER):
        print(f"  [ERROR] ChromaDB folder not found: {VECTORDB_FOLDER}")
        print("  Run scripts/ingest_corpus.py first to build the database.")
        return None

    try:
        client = chromadb.PersistentClient(path=VECTORDB_FOLDER)

        # get_collection raises an exception if the collection doesn't exist
        collection = client.get_collection(name=COLLECTION_NAME)

        chunk_count = collection.count()
        print(f"  ChromaDB connected: {chunk_count:,} chunks in '{COLLECTION_NAME}'")
        return collection

    except Exception as error:
        print(f"  [ERROR] Could not connect to ChromaDB: {error}")
        print("  Run scripts/ingest_corpus.py first.")
        return None
