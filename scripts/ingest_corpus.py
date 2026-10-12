# =============================================================================
# scripts/ingest_corpus.py
#
# PROJECT : JUIA — JCL Utility Intelligence Agent
# PURPOSE : Load all source documents into ChromaDB so the agent can
#           search them by meaning when analysing a JCL job.
#
# WHAT THIS SCRIPT DOES:
#   1. Reads each extracted IBM manual PDF from corpus/extracted/
#   2. Reads each synthetic pattern file from corpus/synthetic/
#   3. Breaks every document into small overlapping pieces called "chunks"
#   4. Converts each chunk into a list of numbers called an "embedding"
#      (these numbers capture the meaning of the text mathematically)
#   5. Stores chunks + embeddings + metadata in ChromaDB (our local database)
#
#   After this script runs, the JUIA agent can ask ChromaDB:
#   "Find me everything about INCLUDE COND ZD field comparison"
#   and ChromaDB will return the most relevant chunks from all our documents.
#
# HOW TO RUN (from the juia project root folder):
#   source venv/Scripts/activate
#   python scripts/ingest_corpus.py
#
# DATA FLOW:
#   corpus/extracted/*.pdf  ──► pypdf reads text  ──┐
#                                                    ├──► chunker splits text
#   corpus/synthetic/*.txt  ──► open() reads text ──┘         │
#                                                              ▼
#                                                   sentence-transformers
#                                                   converts text to numbers
#                                                   (embeddings)
#                                                              │
#                                                              ▼
#                                                   ChromaDB stores everything
#                                                   at corpus/vectordb/
#
# FIRST RUN TIME:
#   The first run downloads the embedding model (~90MB) from the internet.
#   Subsequent runs use the cached model.
#   Embedding 900+ pages takes approximately 5-15 minutes depending on CPU.
# =============================================================================


# =============================================================================
# SECTION 1 — IMPORTS
# Each library explained below.
# =============================================================================

# os — built into Python, no install needed.
# Used to: check if files/folders exist, list directory contents,
#          build file paths that work on both Windows and Linux.
import argparse
import os

# time — built into Python, no install needed.
# Used to: measure how long the ingestion takes.
import time

# pypdf — install: pip install pypdf
# Used to: open PDF files and extract their text content page by page.
# Without this we cannot read IBM manual PDFs.
from pypdf import PdfReader

# chromadb — install: pip install chromadb
# Used to: create and manage our local vector database.
# ChromaDB stores text chunks alongside their numerical embeddings
# and lets us search them by meaning (semantic search).
import chromadb

# SentenceTransformer — install: pip install sentence-transformers
# Used to: convert text into embeddings (lists of numbers that represent
# the meaning of the text). Two chunks about similar topics will have
# similar numbers, which is what makes semantic search work.
from sentence_transformers import SentenceTransformer


# =============================================================================
# SECTION 2 — CONFIGURATION
# All settings are in one place so they are easy to change later.
# =============================================================================

# --- Folder paths ---
# These paths are relative to the project root (juia/).
# Always run this script from the project root, not from inside scripts/.

PDF_INPUT_FOLDER      = "corpus/extracted"   # where our extracted PDFs live
SYNTHETIC_INPUT_FOLDER = "corpus/synthetic"  # where our .txt pattern files live
VECTORDB_FOLDER       = "corpus/vectordb"    # where ChromaDB will store its data

# --- ChromaDB collection name ---
# A "collection" in ChromaDB is like a table in a regular database.
# All our document chunks will live in this single collection.
COLLECTION_NAME = "juia_corpus"

# --- Embedding model ---
# "all-MiniLM-L6-v2" is a small, fast model that produces good quality
# embeddings for technical text. It runs entirely on your local CPU.
# It outputs 384 numbers per chunk of text.
# First run: downloaded automatically from HuggingFace (~90MB).
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

# --- Chunking settings ---
# CHUNK_SIZE: how many characters per chunk.
#   Too large = chunks contain too many topics, retrieval becomes vague.
#   Too small = chunks lose context, retrieval misses connections.
#   1000 characters (~150 words) works well for technical reference text.
CHUNK_SIZE = 1000

# CHUNK_OVERLAP: how many characters the next chunk shares with the previous one.
#   Overlap ensures that a sentence cut in half by a chunk boundary
#   still appears complete in at least one chunk.
#   200 characters = roughly 1-2 sentences of overlap.
CHUNK_OVERLAP = 200

# --- Batch size ---
# Instead of adding chunks one by one (slow) or all at once (high memory),
# we add them in groups called batches.
# 50 chunks per batch is a safe number for most laptops.
BATCH_SIZE = 50

# --- Reset flag ---
# Existing collections are preserved by default. Pass --reset explicitly when
# source material changes and the stored chunks need to be rebuilt.
RESET_DATABASE = False

# --- Map each PDF filename to a short identifying name ---
# This short name is stored as metadata with every chunk so we can later
# filter searches to a specific manual.
# Format: "filename.pdf" : "short_name_for_metadata"
PDF_FILE_MAP = {
    "dfsort_apg_extract.pdf"         : "dfsort_apg",
    "dfsmdfp_utilities_extract.pdf"  : "dfsmdfp_utilities",
    "mvs_jcl_reference_extract.pdf"  : "mvs_jcl_reference",
    "idcams_extract.pdf"             : "idcams",
}

# --- Map each synthetic file to a short identifying name ---
SYNTHETIC_FILE_MAP = {
    "dfsort_patterns.txt"   : "dfsort_patterns",
    "idcams_patterns.txt"   : "idcams_patterns",
    "iebgener_patterns.txt" : "iebgener_patterns",
}


# =============================================================================
# SECTION 3 — TEXT EXTRACTION FUNCTIONS
# These functions read raw text from files.
# =============================================================================

def extract_text_from_pdf(pdf_file_path):
    """
    Opens a PDF file and extracts all text from every page.

    Parameters:
        pdf_file_path : full path to the PDF file (string)

    Returns:
        full_text     : a single long string containing all the text
                        from every page of the PDF, joined together.

    How it works:
        pypdf reads each page of the PDF and calls extract_text() on it.
        Some pages (diagrams, blank pages) return empty text — we skip those.
        Page texts are joined with two newlines so page boundaries are visible.
    """

    print(f"    Reading PDF : {os.path.basename(pdf_file_path)}")

    # PdfReader opens the PDF file and gives us access to all its pages
    pdf_reader = PdfReader(pdf_file_path)

    total_pages = len(pdf_reader.pages)
    print(f"    Pages found : {total_pages}")

    # We will collect text from each page into this list
    all_page_texts = []

    for page_number in range(total_pages):

        # extract_text() reads the text content from one page.
        # It can return None if the page has no extractable text (e.g. a diagram).
        page_text = pdf_reader.pages[page_number].extract_text()

        # Skip this page if extraction returned nothing useful
        if page_text is None:
            continue

        # Strip removes leading/trailing whitespace from the whole page text
        page_text = page_text.strip()

        # Skip pages that ended up empty after stripping
        if len(page_text) == 0:
            continue

        all_page_texts.append(page_text)

    # Join all page texts into one long string with a clear separator
    full_text = "\n\n".join(all_page_texts)

    print(f"    Characters extracted : {len(full_text):,}")
    return full_text


def read_text_file(text_file_path):
    """
    Opens a plain text file and returns its full contents as a string.

    Parameters:
        text_file_path : full path to the .txt file (string)

    Returns:
        full_text      : string containing the entire file content

    Note:
        encoding="utf-8" tells Python which character encoding to use.
        UTF-8 handles accented characters and special symbols correctly.
        errors="replace" means if a character cannot be decoded,
        replace it with a placeholder instead of crashing.
    """

    print(f"    Reading text file : {os.path.basename(text_file_path)}")

    with open(text_file_path, encoding="utf-8", errors="replace") as file:
        full_text = file.read()

    print(f"    Characters read : {len(full_text):,}")
    return full_text


# =============================================================================
# SECTION 4 — TEXT CLEANING FUNCTION
# Raw PDF text often contains noise. We clean it before chunking.
# =============================================================================

def clean_extracted_text(raw_text):
    """
    Cleans up raw text extracted from PDFs.

    Problems this function fixes:
        1. Multiple blank lines collapsed to a single blank line
        2. Lines that are just a page number or single character removed
        3. Excessive spaces within a line compressed to single spaces

    Parameters:
        raw_text : the unprocessed text string from PDF extraction

    Returns:
        cleaned_text : a cleaner version of the same text
    """

    # Split the text into individual lines
    lines = raw_text.split("\n")

    cleaned_lines = []

    for line in lines:

        # Remove leading and trailing spaces from this line
        line = line.strip()

        # Skip lines that are very short — these are usually
        # page numbers, header/footer fragments, or noise.
        # Lines with real content are at least 4 characters long.
        if len(line) < 4:
            continue

        # Compress multiple spaces within a line to a single space.
        # PDF extraction sometimes duplicates spaces between words.
        # The split() without arguments splits on any whitespace,
        # and " ".join() puts single spaces back between words.
        line = " ".join(line.split())

        cleaned_lines.append(line)

    # Join the cleaned lines back into a single string
    cleaned_text = "\n".join(cleaned_lines)

    return cleaned_text


# =============================================================================
# SECTION 5 — TEXT CHUNKING FUNCTION
# Splits a long text into overlapping pieces for the vector database.
# =============================================================================

def split_text_into_chunks(full_text, chunk_size, chunk_overlap, source_name):
    """
    Splits a long text string into a list of overlapping chunks.

    Why overlapping?
        If a key sentence falls exactly at the boundary between two chunks,
        it would appear only partially in each chunk, hurting retrieval.
        Overlap ensures important sentences appear complete in at least
        one chunk.

    Example with chunk_size=20 and chunk_overlap=5:
        Text    : "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        Chunk 1 : "ABCDEFGHIJKLMNOPQRST"   (characters 0-19)
        Chunk 2 : "PQRSTUVWXYZ..."          (starts at 15 = 20-5)

    Parameters:
        full_text    : the complete text to split (string)
        chunk_size   : maximum characters per chunk (integer)
        chunk_overlap: characters shared between consecutive chunks (integer)
        source_name  : short name of the source document (used for display only)

    Returns:
        chunks : a list of strings, each one chunk of text
    """

    chunks = []

    # Total length of the text we need to split
    total_length = len(full_text)

    # Starting position for the current chunk
    start_position = 0

    # chunk_number is used only for reporting progress
    chunk_number = 0

    # Keep creating chunks until we reach the end of the text
    while start_position < total_length:

        # Calculate where this chunk ends
        end_position = start_position + chunk_size

        # If end_position goes past the end of the text, clip it
        if end_position > total_length:
            end_position = total_length

        # Extract the chunk — Python slice notation [start:end]
        # gives us characters from start_position up to (not including) end_position
        chunk_text = full_text[start_position : end_position]

        # Only add the chunk if it has meaningful content
        # (skip chunks that are just whitespace)
        if len(chunk_text.strip()) > 0:
            chunks.append(chunk_text)
            chunk_number += 1

        # Move the start position forward.
        # Subtracting chunk_overlap creates the overlap with the next chunk.
        # If overlap=200 and size=1000, next chunk starts 800 chars ahead,
        # meaning we re-read the last 200 chars of the current chunk.
        start_position = start_position + chunk_size - chunk_overlap

        # Safety check: if start didn't advance (would cause infinite loop),
        # force it forward by at least 1 character.
        if chunk_size <= chunk_overlap:
            start_position = start_position + 1

    return chunks


# =============================================================================
# SECTION 6 — CHUNK ID GENERATION
# Each chunk needs a unique identifier in ChromaDB.
# =============================================================================

def make_chunk_id(source_short_name, chunk_index):
    """
    Creates a unique, readable ID for each chunk.

    ChromaDB requires every chunk to have a unique ID string.
    We build it from the source document name and the chunk's position.

    Example output: "dfsort_apg_chunk_00042"

    Parameters:
        source_short_name : short identifier for the source (e.g. "dfsort_apg")
        chunk_index       : position of this chunk in the document (integer)

    Returns:
        A unique string ID for this chunk.

    Note on f-strings:
        f"some text {variable}" is Python's way of building a string that
        includes the value of a variable.
        {chunk_index:05d} formats the number with leading zeros to 5 digits,
        so chunk 3 becomes "00003" and chunk 100 becomes "00100".
        This keeps IDs in alphabetical = chronological order.
    """
    return f"{source_short_name}_chunk_{chunk_index:05d}"


# =============================================================================
# SECTION 7 — BATCH INGESTION FUNCTION
# Adds a batch of chunks to ChromaDB.
# =============================================================================

def ingest_batch_into_chromadb(collection, embedding_model,
                                batch_texts, batch_ids, batch_metadatas):
    """
    Converts a batch of text chunks to embeddings and adds them to ChromaDB.

    Parameters:
        collection      : the ChromaDB collection object to add data to
        embedding_model : the loaded SentenceTransformer model
        batch_texts     : list of text strings (the chunks)
        batch_ids       : list of unique ID strings (one per chunk)
        batch_metadatas : list of metadata dictionaries (one per chunk)

    How embeddings work:
        The SentenceTransformer model reads each chunk of text and outputs
        a list of 384 floating point numbers. These numbers capture the
        "meaning" of the text. Chunks about similar topics end up with
        similar numbers. This is what makes semantic search possible.

    What ChromaDB stores per chunk:
        - The original text (so we can show it to the user)
        - The embedding (384 numbers, for similarity search)
        - The metadata (source name, doc_type — for filtering)
        - The ID (unique string, for lookups)
    """

    # Generate embeddings for all texts in this batch at once.
    # model.encode() accepts a list of strings and returns a numpy array
    # where each row is the 384-number embedding for one text.
    # show_progress_bar=False keeps the output clean.
    embeddings_array = embedding_model.encode(
        batch_texts,
        show_progress_bar=False
    )

    # ChromaDB expects embeddings as a plain Python list of lists,
    # not as a numpy array. .tolist() converts the numpy array.
    embeddings_list = embeddings_array.tolist()

    # Add everything to ChromaDB in one operation.
    # ChromaDB stores the text, the embedding, the metadata, and the ID together.
    collection.add(
        documents  = batch_texts,
        embeddings = embeddings_list,
        metadatas  = batch_metadatas,
        ids        = batch_ids
    )


# =============================================================================
# SECTION 8 — PROCESS ONE DOCUMENT
# Handles the full pipeline for a single file: read → clean → chunk → ingest.
# =============================================================================

def process_one_document(collection, embedding_model,
                          file_path, source_short_name, doc_type,
                          already_ingested_sources):
    """
    Runs the full ingestion pipeline for one source document.

    Steps:
        1. Check if this document was already ingested (skip if so)
        2. Extract text (PDF or plain text depending on doc_type)
        3. Clean the text
        4. Split into chunks
        5. Add chunks to ChromaDB in batches

    Parameters:
        collection               : ChromaDB collection object
        embedding_model          : loaded SentenceTransformer model
        file_path                : full path to the file
        source_short_name        : short name used in IDs and metadata
        doc_type                 : "pdf" or "synthetic"
        already_ingested_sources : set of source names already in the database
                                   (used when RESET_DATABASE = False)

    Returns:
        chunks_added : number of chunks added to ChromaDB (integer)
    """

    # --- Step 1: Skip if already ingested and we are not resetting ---
    if source_short_name in already_ingested_sources:
        print(f"  [SKIP] {source_short_name} already in database — skipping")
        return 0

    # --- Step 2: Extract text based on file type ---
    if doc_type == "pdf":
        raw_text = extract_text_from_pdf(file_path)
    else:
        # doc_type == "synthetic" means a plain .txt file
        raw_text = read_text_file(file_path)

    # Guard: if the file had no extractable content, skip it
    if len(raw_text.strip()) == 0:
        print(f"  [WARN] No text extracted from {source_short_name} — skipping")
        return 0

    # --- Step 3: Clean the extracted text ---
    # PDF text often has noise; synthetic files are already clean but
    # running clean_extracted_text on them does no harm.
    cleaned_text = clean_extracted_text(raw_text)

    # --- Step 4: Split the cleaned text into chunks ---
    chunks = split_text_into_chunks(
        full_text    = cleaned_text,
        chunk_size   = CHUNK_SIZE,
        chunk_overlap = CHUNK_OVERLAP,
        source_name  = source_short_name
    )

    print(f"    Chunks created : {len(chunks)}")

    if len(chunks) == 0:
        print(f"  [WARN] Zero chunks for {source_short_name} — skipping")
        return 0

    # --- Step 5: Add chunks to ChromaDB in batches ---

    # We will collect chunks into batches of BATCH_SIZE before sending
    current_batch_texts     = []
    current_batch_ids       = []
    current_batch_metadatas = []

    # global_chunk_index counts chunks across the whole document
    global_chunk_index = 0

    # batches_added counts how many batches we have sent to ChromaDB
    batches_added = 0

    for chunk_text in chunks:

        # Build a unique ID for this chunk
        chunk_id = make_chunk_id(source_short_name, global_chunk_index)

        # Build metadata for this chunk.
        # Metadata is stored alongside the chunk and can be used to filter
        # searches. For example: only search within "dfsort_apg" chunks.
        chunk_metadata = {
            "source"      : source_short_name,  # e.g. "dfsort_apg"
            "doc_type"    : doc_type,            # "pdf" or "synthetic"
            "chunk_index" : global_chunk_index   # position in document
        }

        # Add this chunk to the current batch
        current_batch_texts.append(chunk_text)
        current_batch_ids.append(chunk_id)
        current_batch_metadatas.append(chunk_metadata)

        global_chunk_index += 1

        # When the batch is full, send it to ChromaDB and reset
        if len(current_batch_texts) >= BATCH_SIZE:

            ingest_batch_into_chromadb(
                collection      = collection,
                embedding_model = embedding_model,
                batch_texts     = current_batch_texts,
                batch_ids       = current_batch_ids,
                batch_metadatas = current_batch_metadatas
            )

            batches_added += 1
            print(f"    Batch {batches_added} sent "
                  f"({global_chunk_index} chunks so far...)")

            # Reset the batch lists for the next batch
            current_batch_texts     = []
            current_batch_ids       = []
            current_batch_metadatas = []

    # After the loop, there may be a partial batch left over.
    # (The last batch is rarely exactly BATCH_SIZE chunks.)
    if len(current_batch_texts) > 0:

        ingest_batch_into_chromadb(
            collection      = collection,
            embedding_model = embedding_model,
            batch_texts     = current_batch_texts,
            batch_ids       = current_batch_ids,
            batch_metadatas = current_batch_metadatas
        )

        batches_added += 1
        print(f"    Final batch sent ({global_chunk_index} total chunks)")

    return global_chunk_index


# =============================================================================
# SECTION 9 — GET ALREADY-INGESTED SOURCES
# When RESET_DATABASE = False, we need to know what is already in ChromaDB.
# =============================================================================

def get_already_ingested_sources(collection):
    """
    Queries ChromaDB to find which source documents are already stored.

    This is used when RESET_DATABASE = False so we can skip files
    that were already processed in a previous run.

    Parameters:
        collection : ChromaDB collection object

    Returns:
        A Python set of source short names already in the database.
        Example: {"dfsort_apg", "idcams", "dfsort_patterns"}

    How it works:
        collection.get() fetches all stored chunks with their metadata.
        We look at the "source" field of each chunk's metadata.
        Using a Python set automatically deduplicates the values.
    """

    # Check how many items are in the collection first
    item_count = collection.count()

    if item_count == 0:
        # Empty collection — nothing has been ingested yet
        return set()

    # Fetch all chunks — we only need their metadata, not the text or embeddings
    # include=["metadatas"] tells ChromaDB to only return metadata (faster)
    result = collection.get(include=["metadatas"])

    # Build a set of unique source names from the metadata
    # A Python set is like a list but automatically removes duplicates
    sources_found = set()

    for metadata in result["metadatas"]:
        if "source" in metadata:
            sources_found.add(metadata["source"])

    return sources_found


# =============================================================================
# SECTION 10 — MAIN FUNCTION
# Orchestrates the entire ingestion process.
# =============================================================================

def main(reset_database=RESET_DATABASE):
    """
    Main function — runs the complete corpus ingestion pipeline.

    Execution order:
        1. Load the embedding model
        2. Connect to (or create) the ChromaDB collection
        3. Optionally reset the collection if RESET_DATABASE = True
        4. Find which sources are already ingested (if not resetting)
        5. Process all PDF files
        6. Process all synthetic text files
        7. Print a summary report
    """

    # Record start time so we can report total duration at the end
    start_time = time.time()

    print("=" * 65)
    print("  JUIA — Corpus Ingestion Script")
    print("=" * 65)

    # -------------------------------------------------------------------------
    # Step 1 — Load the embedding model
    # -------------------------------------------------------------------------
    # SentenceTransformer downloads and caches the model on first run.
    # On subsequent runs it loads from the cache (fast).
    # The model runs on CPU — no GPU required.
    print("\n[1/5] Loading embedding model...")
    print(f"      Model : {EMBEDDING_MODEL_NAME}")
    print("      (First run will download ~90MB — this is normal)")

    embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)

    print("      Model loaded.")

    # -------------------------------------------------------------------------
    # Step 2 — Connect to ChromaDB
    # -------------------------------------------------------------------------
    # PersistentClient creates a ChromaDB database stored on disk.
    # If the folder does not exist, ChromaDB creates it automatically.
    # If the folder already exists, ChromaDB reopens the existing database.
    print(f"\n[2/5] Connecting to ChromaDB at : {VECTORDB_FOLDER}")

    chroma_client = chromadb.PersistentClient(path=VECTORDB_FOLDER)

    print("      Connected.")

    # -------------------------------------------------------------------------
    # Step 3 — Set up the collection
    # -------------------------------------------------------------------------
    print(f"\n[3/5] Setting up collection : '{COLLECTION_NAME}'")

    if reset_database:
        # Delete the collection if it exists, then create it fresh.
        # try/except catches the error if the collection doesn't exist yet.
        try:
            chroma_client.delete_collection(name=COLLECTION_NAME)
            print("      Existing collection deleted (RESET_DATABASE = True)")
        except Exception:
            # Collection did not exist — nothing to delete
            print("      No existing collection found — creating new one")

    # get_or_create_collection either opens an existing collection
    # or creates a new one if it does not exist.
    collection = chroma_client.get_or_create_collection(
        name     = COLLECTION_NAME,
        # metadata tells ChromaDB which similarity metric to use.
        # "cosine" compares the angle between embedding vectors.
        # It is the standard choice for text similarity.
        metadata = {"hnsw:space": "cosine"}
    )

    print(f"      Collection ready. Current chunk count: {collection.count()}")

    # -------------------------------------------------------------------------
    # Step 4 — Find already-ingested sources (only relevant if not resetting)
    # -------------------------------------------------------------------------
    print("\n[4/5] Checking for already-ingested sources...")

    already_ingested = get_already_ingested_sources(collection)

    if len(already_ingested) == 0:
        print("      Database is empty — all files will be processed")
    else:
        print(f"      Already in database : {sorted(already_ingested)}")

    # -------------------------------------------------------------------------
    # Step 5 — Process all documents
    # -------------------------------------------------------------------------
    print("\n[5/5] Processing documents...\n")

    # Dictionary to track results: source_name → chunks_added
    ingestion_results = {}

    # --- Process PDF files ---
    print("--- PDF Files ---")

    for pdf_filename, source_short_name in PDF_FILE_MAP.items():

        # Build the full file path by joining folder + filename
        full_path = os.path.join(PDF_INPUT_FOLDER, pdf_filename)

        print(f"\n  Processing : {pdf_filename}")
        print(f"  Source ID  : {source_short_name}")

        # Check the file actually exists before trying to process it
        if not os.path.exists(full_path):
            print(f"  [ERROR] File not found: {full_path}")
            print(f"          Run extract_chapters.py first.")
            ingestion_results[source_short_name] = 0
            continue

        chunks_added = process_one_document(
            collection               = collection,
            embedding_model          = embedding_model,
            file_path                = full_path,
            source_short_name        = source_short_name,
            doc_type                 = "pdf",
            already_ingested_sources = already_ingested
        )

        ingestion_results[source_short_name] = chunks_added

    # --- Process synthetic pattern files ---
    print("\n--- Synthetic Pattern Files ---")

    for txt_filename, source_short_name in SYNTHETIC_FILE_MAP.items():

        full_path = os.path.join(SYNTHETIC_INPUT_FOLDER, txt_filename)

        print(f"\n  Processing : {txt_filename}")
        print(f"  Source ID  : {source_short_name}")

        if not os.path.exists(full_path):
            print(f"  [ERROR] File not found: {full_path}")
            print(f"          Run the synthetic file creation step first.")
            ingestion_results[source_short_name] = 0
            continue

        chunks_added = process_one_document(
            collection               = collection,
            embedding_model          = embedding_model,
            file_path                = full_path,
            source_short_name        = source_short_name,
            doc_type                 = "synthetic",
            already_ingested_sources = already_ingested
        )

        ingestion_results[source_short_name] = chunks_added

    # -------------------------------------------------------------------------
    # Step 6 — Summary Report
    # -------------------------------------------------------------------------
    elapsed_seconds = time.time() - start_time

    # Convert seconds to minutes and remaining seconds for display
    elapsed_minutes = int(elapsed_seconds // 60)
    remaining_secs  = int(elapsed_seconds % 60)

    print("\n" + "=" * 65)
    print("  INGESTION COMPLETE — SUMMARY")
    print("=" * 65)
    print(f"\n  {'Source':<35} {'Chunks Added':>12}")
    print(f"  {'-'*35} {'-'*12}")

    total_chunks_added = 0

    for source_name, chunk_count in ingestion_results.items():
        print(f"  {source_name:<35} {chunk_count:>12,}")
        total_chunks_added += chunk_count

    print(f"  {'-'*35} {'-'*12}")
    print(f"  {'TOTAL':<35} {total_chunks_added:>12,}")
    print(f"\n  Total chunks now in ChromaDB : {collection.count():,}")
    print(f"  Time taken                   : {elapsed_minutes}m {remaining_secs}s")
    print(f"  Database location            : {VECTORDB_FOLDER}")
    print(f"\n  Next step : run scripts/verify_rag.py to test retrieval")
    print("=" * 65)


# =============================================================================
# SECTION 11 — ENTRY POINT
#
# This block runs main() when you execute the script directly:
#   python scripts/ingest_corpus.py
#
# It does NOT run if this file is imported by another script.
# This is a standard Python pattern — the if __name__ == "__main__" check
# ensures the code only executes when intended.
# =============================================================================
if __name__ == "__main__":
    argument_parser = argparse.ArgumentParser(description="Ingest the JUIA reference corpus")
    argument_parser.add_argument(
        "--reset", action="store_true",
        help="Delete and rebuild the existing ChromaDB collection explicitly",
    )
    arguments = argument_parser.parse_args()
    main(reset_database=arguments.reset)
