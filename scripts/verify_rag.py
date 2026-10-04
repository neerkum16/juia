# =============================================================================
# scripts/verify_rag.py
#
# PROJECT : JUIA — JCL Utility Intelligence Agent
# PURPOSE : Verify that ChromaDB was loaded correctly and is returning
#           relevant chunks for real-world queries.
#
# WHAT THIS SCRIPT DOES:
#   After ingest_corpus.py loads all documents into ChromaDB, we need to
#   confirm that the database actually works — that when the JUIA agent
#   asks "how does DFSORT INCLUDE work?" it gets back relevant manual pages
#   and not random noise.
#
#   This script runs 18 test queries, one for each major topic the agent
#   will need to decode JCL utility steps. For each query it shows:
#     - Which document each result came from
#     - A short snippet of the returned text
#     - A relevance score (how closely the chunk matches the query)
#     - A PASS / WARN / FAIL rating
#
#   At the end it saves a full report to:
#     outputs/rag_verification_report.txt
#
# HOW TO RUN (from the juia project root folder):
#   source venv/Scripts/activate
#   python scripts/verify_rag.py
#
# WHAT TO LOOK FOR:
#   A healthy result shows the returned chunk is clearly about the topic
#   in the query, and the score is EXCELLENT or GOOD.
#   If results look random or scores are POOR, the ingestion may need
#   to be re-run with adjusted CHUNK_SIZE settings.
#
# HOW RELEVANCE SCORING WORKS:
#   ChromaDB uses "cosine distance" to measure similarity.
#   This compares the mathematical angle between two embedding vectors.
#   Lower distance = more similar = better match.
#
#   Distance ranges used in this script:
#     0.00 – 0.30  →  EXCELLENT  (chunk is clearly about this topic)
#     0.30 – 0.50  →  GOOD       (chunk is relevant)
#     0.50 – 0.70  →  MODERATE   (chunk has some relevant content)
#     0.70 – 1.00  →  POOR       (chunk is probably not relevant)
# =============================================================================


# =============================================================================
# SECTION 1 — IMPORTS
# =============================================================================

# os — built into Python.
# Used to: check folder paths exist, create output folder.
import os

# datetime — built into Python.
# Used to: add a timestamp to the verification report.
from datetime import datetime

# chromadb — our local vector database.
# Used to: open the collection and run similarity queries.
import chromadb

# SentenceTransformer — converts text queries into embeddings.
# The same model used during ingestion must be used during querying.
# If the model is already cached from the ingestion run, this is instant.
from sentence_transformers import SentenceTransformer


# =============================================================================
# SECTION 2 — CONFIGURATION
# =============================================================================

# These paths must match exactly what was used in ingest_corpus.py
VECTORDB_FOLDER  = "corpus/vectordb"
COLLECTION_NAME  = "juia_corpus"
EMBEDDING_MODEL  = "all-MiniLM-L6-v2"

# Output folder for the verification report
OUTPUT_FOLDER    = "outputs"
REPORT_FILENAME  = "rag_verification_report.txt"

# How many results to return per query.
# 3 gives a good view of result quality without being overwhelming.
TOP_K_RESULTS = 3

# How many characters of each chunk to show in the preview.
# Enough to judge relevance without flooding the screen.
SNIPPET_LENGTH = 250

# Relevance score thresholds (cosine distance — lower is better).
THRESHOLD_EXCELLENT = 0.30
THRESHOLD_GOOD      = 0.50
THRESHOLD_MODERATE  = 0.70
# Anything above THRESHOLD_MODERATE is rated POOR


# =============================================================================
# SECTION 3 — TEST QUERIES
#
# These are the 18 queries that cover every source document in our corpus.
# Each query is written the way a real user or the agent would phrase it,
# NOT as exact words copied from the manuals.
# Semantic search finds meaning — not keyword matches.
#
# Each query is a dictionary with:
#   "query"       : the question or topic to search for
#   "expect_from" : which source document should ideally appear in results
#   "description" : what we are testing
# =============================================================================

TEST_QUERIES = [

    # --- DFSORT APG queries (4 queries) ---
    {
        "query"       : "DFSORT INCLUDE COND syntax ZD field comparison greater than zero",
        "expect_from" : "dfsort_apg",
        "description" : "DFSORT — INCLUDE statement with Zoned Decimal comparison"
    },
    {
        "query"       : "OUTFIL FNAMES split records into multiple output files based on field value",
        "expect_from" : "dfsort_apg",
        "description" : "DFSORT — OUTFIL multi-output splitting"
    },
    {
        "query"       : "SORT FIELDS packed decimal descending INREC OUTREC field reformatting",
        "expect_from" : "dfsort_apg",
        "description" : "DFSORT — SORT and record reformatting"
    },
    {
        "query"       : "ICETOOL SELECT FROM operator FIRST records top N by field value",
        "expect_from" : "dfsort_apg",
        "description" : "DFSORT — ICETOOL SELECT operator"
    },

    # --- IDCAMS queries (4 queries) ---
    {
        "query"       : "REPRO INFILE OUTFILE copy records VSAM sequential dataset",
        "expect_from" : "idcams",
        "description" : "IDCAMS — REPRO command for file copy and load"
    },
    {
        "query"       : "DEFINE CLUSTER INDEXED KEYS RECORDSIZE CYLINDERS VSAM KSDS",
        "expect_from" : "idcams",
        "description" : "IDCAMS — DEFINE CLUSTER for KSDS creation"
    },
    {
        "query"       : "DELETE cluster PURGE SCRATCH remove dataset catalog entry",
        "expect_from" : "idcams",
        "description" : "IDCAMS — DELETE command"
    },
    {
        "query"       : "IF LASTCC modal command THEN ELSE condition code IDCAMS",
        "expect_from" : "idcams",
        "description" : "IDCAMS — Modal IF-THEN-ELSE control flow"
    },

    # --- DFSMSdfp Utilities queries (2 queries) ---
    {
        "query"       : "IEBGENER GENERATE MAXFLDS RECORD FIELD input output offset length",
        "expect_from" : "dfsmdfp_utilities",
        "description" : "IEBGENER — GENERATE and RECORD control statements"
    },
    {
        "query"       : "IEBCOPY SELECT EXCLUDE members copy merge partitioned dataset PDS",
        "expect_from" : "dfsmdfp_utilities",
        "description" : "IEBCOPY — member selection and copy operations"
    },

    # --- MVS JCL Reference queries (3 queries) ---
    {
        "query"       : "DD statement DISP parameter NEW OLD SHR MOD dataset disposition",
        "expect_from" : "mvs_jcl_reference",
        "description" : "JCL — DD statement DISP parameter"
    },
    {
        "query"       : "EXEC statement COND parameter step condition code comparison",
        "expect_from" : "mvs_jcl_reference",
        "description" : "JCL — EXEC statement COND parameter"
    },
    {
        "query"       : "DCB parameter RECFM LRECL BLKSIZE fixed blocked record format",
        "expect_from" : "mvs_jcl_reference",
        "description" : "JCL — DCB sub-parameters for dataset attributes"
    },

    # --- Synthetic DFSORT patterns (2 queries) ---
    {
        "query"       : "filter active account status non-zero balance PySpark equivalent",
        "expect_from" : "dfsort_patterns",
        "description" : "Synthetic — DFSORT account filter pattern with PySpark"
    },
    {
        "query"       : "wire transfer high value amount threshold pending status exception",
        "expect_from" : "dfsort_patterns",
        "description" : "Synthetic — DFSORT high-value transaction extraction"
    },

    # --- Synthetic IDCAMS patterns (2 queries) ---
    {
        "query"       : "REPRO REPLACE duplicate key load update existing VSAM records",
        "expect_from" : "idcams_patterns",
        "description" : "Synthetic — IDCAMS REPRO with REPLACE parameter"
    },
    {
        "query"       : "DELETE then DEFINE CLUSTER reset VSAM dataset before daily batch load",
        "expect_from" : "idcams_patterns",
        "description" : "Synthetic — IDCAMS delete and redefine pattern"
    },

    # --- Synthetic IEBGENER patterns (1 query) ---
    {
        "query"       : "IEBGENER SYSIN DUMMY straight copy backup no transformation",
        "expect_from" : "iebgener_patterns",
        "description" : "Synthetic — IEBGENER plain copy pattern"
    },

]


# =============================================================================
# SECTION 4 — HELPER FUNCTIONS
# =============================================================================

def rate_score(distance):
    """
    Converts a cosine distance number into a human-readable rating.

    Parameters:
        distance : float — the cosine distance from ChromaDB (lower = better)

    Returns:
        A string: "EXCELLENT", "GOOD", "MODERATE", or "POOR"

    Note:
        Cosine distance ranges from 0.0 (identical) to 2.0 (opposite).
        For text similarity, most values fall between 0.0 and 1.0.
        Lower is always better — it means the chunk is closer in meaning
        to the query.
    """
    if distance <= THRESHOLD_EXCELLENT:
        return "EXCELLENT"
    elif distance <= THRESHOLD_GOOD:
        return "GOOD"
    elif distance <= THRESHOLD_MODERATE:
        return "MODERATE"
    else:
        return "POOR"


def make_snippet(chunk_text, max_length):
    """
    Returns a short preview of a chunk for display purposes.

    Takes the first max_length characters of the chunk text,
    then truncates at the last space so we don't cut a word in half.
    Adds "..." at the end to show the text continues.

    Parameters:
        chunk_text : the full text of the chunk (string)
        max_length : maximum number of characters to show (integer)

    Returns:
        A shortened string ending with "..." if truncated.
    """

    # Remove leading/trailing whitespace first
    cleaned = chunk_text.strip()

    # Replace internal newlines with a space so the snippet reads
    # as a single paragraph rather than scattered lines
    cleaned = cleaned.replace("\n", " ")

    # If the text is already short enough, return it as-is
    if len(cleaned) <= max_length:
        return cleaned

    # Truncate to max_length
    truncated = cleaned[:max_length]

    # Find the last space in the truncated text and cut there,
    # so we don't show half a word at the end.
    # rfind() searches from the right and returns the index of the last space.
    last_space = truncated.rfind(" ")

    if last_space > 0:
        truncated = truncated[:last_space]

    return truncated + "..."


def run_one_query(collection, embedding_model, query_text, top_k):
    """
    Runs one search query against ChromaDB and returns the results.

    HOW SEMANTIC SEARCH WORKS:
        1. embedding_model.encode() converts the query text into a
           384-number embedding vector.
        2. ChromaDB compares that vector against every stored chunk's
           embedding using cosine distance.
        3. The chunks with the smallest distance are most similar
           in meaning to the query.
        4. ChromaDB returns the top_k closest chunks.

    Parameters:
        collection      : ChromaDB collection object
        embedding_model : loaded SentenceTransformer model
        query_text      : the search query string
        top_k           : how many results to return

    Returns:
        A dictionary with keys: "ids", "documents", "metadatas", "distances"
        Each key maps to a list of lists (outer list = one query,
        inner list = results for that query).
    """

    # Step 1: Convert the query text to an embedding
    # encode() returns a numpy array; we convert to a plain Python list
    # because ChromaDB's query_embeddings parameter expects a list.
    query_embedding = embedding_model.encode(query_text).tolist()

    # Step 2: Query ChromaDB for the most similar chunks
    # query_embeddings : the numerical representation of our query
    # n_results        : how many chunks to return
    # include          : what data to include in the response
    results = collection.query(
        query_embeddings = [query_embedding],
        n_results        = top_k,
        include          = ["documents", "metadatas", "distances"]
    )

    return results


# =============================================================================
# SECTION 5 — DISPLAY FUNCTIONS
# Functions for printing results to the screen.
# =============================================================================

def print_query_results(query_info, results, query_number, total_queries):
    """
    Prints the results of one query in a readable format.

    Parameters:
        query_info    : the dictionary from TEST_QUERIES for this query
        results       : the result dictionary returned by ChromaDB
        query_number  : which query this is (for display)
        total_queries : total number of queries (for display)
    """

    print(f"\n{'─' * 65}")
    print(f"Query {query_number}/{total_queries}: {query_info['description']}")
    print(f"{'─' * 65}")
    print(f"Search text : {query_info['query']}")
    print(f"Expect from : {query_info['expect_from']}")
    print()

    # ChromaDB returns results as lists of lists.
    # The outer list has one entry per query (we only sent one).
    # The inner list has one entry per result (up to top_k).
    documents  = results["documents"][0]
    metadatas  = results["metadatas"][0]
    distances  = results["distances"][0]

    for result_index in range(len(documents)):

        chunk_text = documents[result_index]
        metadata   = metadatas[result_index]
        distance   = distances[result_index]
        rating     = rate_score(distance)
        source     = metadata.get("source", "unknown")
        doc_type   = metadata.get("doc_type", "unknown")
        snippet    = make_snippet(chunk_text, SNIPPET_LENGTH)

        # Flag if this result is from the expected source
        source_match = "✓" if source == query_info["expect_from"] else "✗"

        print(f"  Result {result_index + 1}: [{rating}]  "
              f"distance={distance:.4f}  "
              f"source={source} {source_match}  "
              f"type={doc_type}")
        print(f"  Snippet: {snippet}")
        print()


def print_summary(test_results):
    """
    Prints a final summary table after all queries have run.

    Parameters:
        test_results : list of result dictionaries collected during the run
                       Each entry has: description, best_rating, best_distance,
                       source_match, expected_source, actual_top_source
    """

    print("\n" + "=" * 65)
    print("  VERIFICATION SUMMARY")
    print("=" * 65)

    # Count results by rating
    count_excellent = 0
    count_good      = 0
    count_moderate  = 0
    count_poor      = 0
    count_match     = 0

    print(f"\n  {'#':<4} {'Description':<38} {'Rating':<10} {'Src?':<6} {'Dist':>6}")
    print(f"  {'─'*4} {'─'*38} {'─'*10} {'─'*6} {'─'*6}")

    for i, result in enumerate(test_results):
        num         = i + 1
        description = result["description"][:37]
        rating      = result["best_rating"]
        src_match   = "YES" if result["source_match"] else "no"
        distance    = result["best_distance"]

        print(f"  {num:<4} {description:<38} {rating:<10} {src_match:<6} {distance:>6.4f}")

        if rating == "EXCELLENT":
            count_excellent += 1
        elif rating == "GOOD":
            count_good += 1
        elif rating == "MODERATE":
            count_moderate += 1
        else:
            count_poor += 1

        if result["source_match"]:
            count_match += 1

    total = len(test_results)

    print(f"\n  {'─' * 65}")
    print(f"  Total queries       : {total}")
    print(f"  EXCELLENT results   : {count_excellent}")
    print(f"  GOOD results        : {count_good}")
    print(f"  MODERATE results    : {count_moderate}")
    print(f"  POOR results        : {count_poor}")
    print(f"  Expected source hit : {count_match}/{total}")

    # Overall health assessment
    good_or_better  = count_excellent + count_good
    health_pct      = (good_or_better / total) * 100

    print(f"\n  Corpus health       : {health_pct:.0f}% "
          f"({good_or_better}/{total} queries rated GOOD or better)")

    if health_pct >= 80:
        print("  Assessment          : CORPUS IS READY — proceed to Phase 2")
    elif health_pct >= 60:
        print("  Assessment          : ACCEPTABLE — minor tuning may improve results")
        print("  Suggestion          : Check POOR queries — may need chunk size adjustment")
    else:
        print("  Assessment          : NEEDS ATTENTION — re-ingest with adjusted settings")
        print("  Suggestion          : Increase CHUNK_SIZE to 1200 in ingest_corpus.py")

    print("=" * 65)


# =============================================================================
# SECTION 6 — REPORT WRITER
# Saves the full results to a text file for later review.
# =============================================================================

def write_report(test_results, all_raw_results, total_chunks_in_db):
    """
    Writes the full verification results to a text file.

    Parameters:
        test_results        : summary list built during the run
        all_raw_results     : list of raw ChromaDB result dictionaries
        total_chunks_in_db  : total number of chunks in the collection
    """

    # Create the output folder if it doesn't exist
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    report_path = os.path.join(OUTPUT_FOLDER, REPORT_FILENAME)

    # Get current date and time for the report header
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(report_path, "w", encoding="utf-8") as report_file:

        # Helper function to write a line to both screen and file
        def write(line=""):
            report_file.write(line + "\n")

        write("=" * 70)
        write("  JUIA — RAG Verification Report")
        write(f"  Generated : {timestamp}")
        write(f"  Collection: {COLLECTION_NAME}")
        write(f"  Total chunks in database: {total_chunks_in_db:,}")
        write("=" * 70)

        for i, (query_info, raw_result) in enumerate(
            zip(TEST_QUERIES, all_raw_results)
        ):
            write()
            write(f"{'─' * 70}")
            write(f"Query {i + 1}/{len(TEST_QUERIES)}: {query_info['description']}")
            write(f"{'─' * 70}")
            write(f"Search text  : {query_info['query']}")
            write(f"Expected src : {query_info['expect_from']}")
            write()

            documents = raw_result["documents"][0]
            metadatas = raw_result["metadatas"][0]
            distances = raw_result["distances"][0]

            for j in range(len(documents)):
                source   = metadatas[j].get("source", "unknown")
                distance = distances[j]
                rating   = rate_score(distance)
                match    = "✓ MATCH" if source == query_info["expect_from"] else "✗ MISS"
                snippet  = make_snippet(documents[j], 400)

                write(f"  Result {j + 1}: {rating} | dist={distance:.4f} | "
                      f"source={source} | {match}")
                write(f"  {snippet}")
                write()

        # Summary section
        write("=" * 70)
        write("SUMMARY")
        write("=" * 70)

        for i, result in enumerate(test_results):
            write(f"  {i+1:>2}. {result['description'][:50]:<50} "
                  f"{result['best_rating']:<10} "
                  f"{'HIT' if result['source_match'] else 'miss'}")

        good_or_better = sum(
            1 for r in test_results
            if r["best_rating"] in ("EXCELLENT", "GOOD")
        )
        health_pct = (good_or_better / len(test_results)) * 100

        write()
        write(f"  Corpus health : {health_pct:.0f}% "
              f"({good_or_better}/{len(test_results)} GOOD or better)")
        write("=" * 70)

    print(f"\n  Full report saved to : {report_path}")


# =============================================================================
# SECTION 7 — DATABASE STATS FUNCTION
# Shows what is currently stored in ChromaDB before running queries.
# =============================================================================

def print_database_stats(collection):
    """
    Prints a breakdown of how many chunks are stored per source document.

    Queries ChromaDB for all metadata and counts chunks by source.

    Parameters:
        collection : ChromaDB collection object
    """

    total_count = collection.count()

    print(f"  Total chunks in collection : {total_count:,}")

    if total_count == 0:
        print("  WARNING: Collection is empty.")
        print("  Run scripts/ingest_corpus.py first.")
        return False

    # Fetch all metadata to count per source
    # include=["metadatas"] fetches only metadata — no embeddings or text
    # (much faster for large collections)
    all_data = collection.get(include=["metadatas"])

    # Count chunks per source using a plain Python dictionary
    # A dictionary here acts as a counter: source_name → count
    source_counts = {}

    for metadata in all_data["metadatas"]:
        source = metadata.get("source", "unknown")

        # If we haven't seen this source before, add it with count 0
        if source not in source_counts:
            source_counts[source] = 0

        # Increment the count for this source
        source_counts[source] += 1

    print()
    print(f"  {'Source':<40} {'Chunks':>8}")
    print(f"  {'─'*40} {'─'*8}")

    # sorted() returns items in alphabetical order by key (source name)
    for source_name, count in sorted(source_counts.items()):
        print(f"  {source_name:<40} {count:>8,}")

    print(f"  {'─'*40} {'─'*8}")
    print(f"  {'TOTAL':<40} {total_count:>8,}")

    return True


# =============================================================================
# SECTION 8 — MAIN FUNCTION
# =============================================================================

def main():
    """
    Main function — runs all verification queries and produces the report.

    Execution order:
        1. Load embedding model
        2. Connect to ChromaDB
        3. Print database stats (what is stored)
        4. Run all 18 test queries
        5. Display each result
        6. Print summary table
        7. Write report to file
    """

    print("=" * 65)
    print("  JUIA — RAG Verification Script")
    print("=" * 65)

    # -------------------------------------------------------------------------
    # Step 1 — Load the embedding model
    # -------------------------------------------------------------------------
    # Must be the same model used in ingest_corpus.py.
    # If already cached from the ingestion run, this is near-instant.
    print("\n[1/4] Loading embedding model...")
    print(f"      Model : {EMBEDDING_MODEL}")

    embedding_model = SentenceTransformer(EMBEDDING_MODEL)

    print("      Model loaded.")

    # -------------------------------------------------------------------------
    # Step 2 — Connect to ChromaDB
    # -------------------------------------------------------------------------
    print(f"\n[2/4] Connecting to ChromaDB...")
    print(f"      Location : {VECTORDB_FOLDER}")

    # Check the database folder exists before trying to connect
    if not os.path.exists(VECTORDB_FOLDER):
        print(f"\n  [ERROR] ChromaDB folder not found: {VECTORDB_FOLDER}")
        print("  Run scripts/ingest_corpus.py first.")
        return

    chroma_client = chromadb.PersistentClient(path=VECTORDB_FOLDER)

    # Get the existing collection — do NOT create it here.
    # If the collection doesn't exist, the ingestion has not been run.
    # get_collection() raises an exception if the collection is missing.
    try:
        collection = chroma_client.get_collection(name=COLLECTION_NAME)
    except Exception:
        print(f"\n  [ERROR] Collection '{COLLECTION_NAME}' not found in ChromaDB.")
        print("  Run scripts/ingest_corpus.py first.")
        return

    print("      Connected.")

    # -------------------------------------------------------------------------
    # Step 3 — Print database stats
    # -------------------------------------------------------------------------
    print(f"\n[3/4] Database contents (collection: '{COLLECTION_NAME}')")
    print()

    # print_database_stats returns False if the collection is empty
    db_has_data = print_database_stats(collection)

    if not db_has_data:
        return

    total_chunks = collection.count()

    # -------------------------------------------------------------------------
    # Step 4 — Run all test queries
    # -------------------------------------------------------------------------
    print(f"\n[4/4] Running {len(TEST_QUERIES)} verification queries...\n")

    # These lists collect results as we process each query
    test_results    = []   # summary info per query (for the final table)
    all_raw_results = []   # full ChromaDB results (for the report file)

    for query_number, query_info in enumerate(TEST_QUERIES, start=1):

        # Run the query against ChromaDB
        raw_result = run_one_query(
            collection      = collection,
            embedding_model = embedding_model,
            query_text      = query_info["query"],
            top_k           = TOP_K_RESULTS
        )

        # Display results for this query on screen
        print_query_results(
            query_info   = query_info,
            results      = raw_result,
            query_number = query_number,
            total_queries = len(TEST_QUERIES)
        )

        # The top result (index 0) is the most relevant chunk returned.
        # We use it to rate this query's overall success.
        best_distance = raw_result["distances"][0][0]
        best_rating   = rate_score(best_distance)
        top_source    = raw_result["metadatas"][0][0].get("source", "unknown")
        source_match  = (top_source == query_info["expect_from"])

        # Build a summary record for this query
        summary_record = {
            "description"    : query_info["description"],
            "expected_source": query_info["expect_from"],
            "actual_top_src" : top_source,
            "best_distance"  : best_distance,
            "best_rating"    : best_rating,
            "source_match"   : source_match,
        }

        test_results.append(summary_record)
        all_raw_results.append(raw_result)

    # -------------------------------------------------------------------------
    # Step 5 — Print summary and write report
    # -------------------------------------------------------------------------
    print_summary(test_results)

    write_report(
        test_results       = test_results,
        all_raw_results    = all_raw_results,
        total_chunks_in_db = total_chunks
    )


# =============================================================================
# SECTION 9 — ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    main()
