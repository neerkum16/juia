# JUIA — JCL Utility Intelligence Agent

A locally-run AI agent that decodes IBM mainframe JCL utility steps into
plain English business rules and generates modern PySpark equivalents.

---

## What Problem This Solves

Every mainframe batch job contains utility steps — DFSORT, IDCAMS, IEBGENER,
IEBCOPY — that hold critical business logic inside control cards. These steps
are ignored by every existing COBOL migration tool. A JCL job with 25 steps
might have 18 utility steps and 4 COBOL steps. The industry migrates the 4
COBOL steps and leaves the 18 utility steps as undocumented tribal knowledge.

JUIA reads a JCL job, decodes every utility step, and produces:

- Plain English business rule for each step
- Data lineage diagram showing file flow across all steps
- PySpark equivalent code per step
- A structured migration specification document (`.md`)

---

## Tech Stack

| Component | Technology |
|---|---|
| LLM | Claude API (`claude-sonnet-4-6`) via Anthropic |
| Vector Database | ChromaDB (local, no Docker required) |
| Embeddings | `sentence-transformers` — `all-MiniLM-L6-v2` (local CPU) |
| PDF Processing | `pypdf` |
| Session Memory | SQLite (built into Python) |
| API Framework | FastAPI |
| Language | Python 3.x |
| Environment | Local Windows machine, Git Bash, venv |

---

## Project Structure

```
juia/
│
├── README.md
├── requirements.txt
├── .env                          # API keys and config — never commit to git
├── .gitignore
│
├── corpus/
│   ├── raw/                      # Original IBM manuals (not in git)
│   │   ├── icea100_v3r2.pdf      # DFSORT Application Programming Guide
│   │   ├── idau100_v3r2.pdf      # DFSMSdfp Utilities
│   │   ├── ieab600_v2r3.pdf      # cu
│   │   └── idai200_v2r4.pdf      # DFSMS Access Method Services (IDCAMS)
│   │
│   ├── extracted/                # Targeted chapter extracts — RAG input
│   │   ├── dfsort_apg_extract.pdf
│   │   ├── dfsmdfp_utilities_extract.pdf
│   │   ├── mvs_jcl_reference_extract.pdf
│   │   └── idcams_extract.pdf
│   │
│   ├── synthetic/                # Hand-authored annotated pattern files
│   │   ├── dfsort_patterns.txt
│   │   ├── idcams_patterns.txt
│   │   └── iebgener_patterns.txt
│   │
│   └── vectordb/                 # ChromaDB persistent storage (auto-created)
│
├── scripts/
│   ├── extract_chapters.py       # Slices IBM PDFs to targeted chapter extracts
│   ├── ingest_corpus.py          # Chunks + embeds + loads into ChromaDB
│   └── verify_rag.py             # Runs 18 test queries to validate retrieval
│
├── agent/                        # Core agent logic (Phase 2 onwards)
│   ├── main.py
│   ├── parser/
│   │   ├── jcl_splitter.py
│   │   └── utility_classifier.py
│   ├── decoder/
│   │   ├── rag_retriever.py
│   │   ├── claude_decoder.py
│   │   ├── dfsort_decoder.py
│   │   ├── idcams_decoder.py
│   │   └── iebgener_decoder.py
│   ├── generator/
│   │   ├── pyspark_generator.py
│   │   └── synthetic_data_generator.py
│   ├── validator/
│   │   ├── sandbox_executor.py
│   │   └── self_correction_loop.py
│   ├── memory/
│   │   └── session_store.py
│   └── output/
│       ├── md_writer.py
│       └── diagram_generator.py
│
├── frontend/
│   ├── Dockerfile
│   ├── app.py
│   ├── templates/
│   │   └── index.html
│   └── static/
│       └── style.css
│
├── sample_jcl/
│   ├── wire_payment_batch.jcl    # Primary demo and test JCL
│   └── account_extract.jcl
│
└── outputs/                      # Generated migration specs and reports
```

---

## Setup

### Prerequisites

- Python 3.9 or later
- Git Bash (for Windows path conventions)
- Anthropic API key (Cognizant enterprise account)

### First-Time Setup

```bash
# 1. Navigate to project root
cd C:/Users/169424/Projects/juia

# 2. Create virtual environment
python -m venv venv

# 3. Activate it
source venv/Scripts/activate

# 4. Install all dependencies
pip install -r requirements.txt
```

### Environment File

Create a `.env` file at the project root:

```
ANTHROPIC_API_KEY=your_key_here
```

### requirements.txt

```
pypdf
chromadb
sentence-transformers
langchain
langchain-community
anthropic
```

FastAPI and Uvicorn are planned for the frontend phase and are not yet in
`requirements.txt`.

---

## Running the Scripts

All commands run from the project root with the venv active.

### Step 1 — Extract targeted chapters from IBM manuals

```bash
python scripts/extract_chapters.py
```

Reads raw PDFs from `corpus/raw/`, extracts targeted chapters,
writes focused PDFs to `corpus/extracted/`.

Run once. Re-run only if raw PDFs change.

### Step 2 — Ingest corpus into ChromaDB

```bash
python scripts/ingest_corpus.py
```

Reads all files from `corpus/extracted/` and `corpus/synthetic/`.
Splits into 1000-character overlapping chunks.
Generates embeddings using `all-MiniLM-L6-v2`.
Loads everything into ChromaDB at `corpus/vectordb/`.

First run downloads the embedding model (~90MB).
Processing ~900 pages takes approximately 5-15 minutes.

Key settings at the top of the script:

| Setting | Default | Effect |
|---|---|---|
| `RESET_DATABASE` | `True` | Delete and rebuild from scratch |
| `CHUNK_SIZE` | `1000` | Characters per chunk |
| `CHUNK_OVERLAP` | `200` | Overlap between chunks |

### Step 3 — Verify retrieval quality

```bash
python scripts/verify_rag.py
```

Runs 18 test queries covering all 7 source documents.
Prints relevance scores and source matches to screen.
Writes full report to `outputs/rag_verification_report.txt`.

**Healthy output**: 80%+ queries rated GOOD or EXCELLENT with correct
source match. If results are poor, increase `CHUNK_SIZE` to 1200 in
`ingest_corpus.py` and re-ingest.

### End-to-end backend run

From the project root, with dependencies installed, the vector database
populated, and `ANTHROPIC_API_KEY` available in the environment:

```bash
python -m agent.main sample_jcl/wire_payment_batch.jcl --cntl-folder cntl --output-folder outputs
```

Each invocation writes to `outputs/<job_name>/<run_id>/`, including generated
PySpark files, decoded and validation JSON, a Mermaid diagram, and a migration
specification. A SQLite index at `outputs/sessions.db` stores the structured
result. To retrieve a completed or failed run:

```bash
python -m agent.main --get-run RUN_ID --output-folder outputs
```

The result status is `success`, `incomplete`, or `failed`. An unresolved
external SYSIN marks the run incomplete; decode, generation, or validation
errors mark it failed. Validation is a Claude review of generated code, not
execution of that code in Spark.

---

## RAG Corpus

### IBM Manuals (extracted chapters only)

| File | Manual | Pages Extracted |
|---|---|---|
| `dfsort_apg_extract.pdf` | DFSORT Application Programming Guide (SC23-6878) | 527 |
| `dfsmdfp_utilities_extract.pdf` | DFSMSdfp Utilities (SC23-6864) | 83 |
| `mvs_jcl_reference_extract.pdf` | MVS JCL Reference (SA23-1385) | 140 |
| `idcams_extract.pdf` | DFSMS Access Method Services (SC23-6846) | 161 |

Chapters selected cover: INCLUDE/OMIT/SORT/OUTFIL/OUTREC/INREC/JOINKEYS/ICETOOL
for DFSORT; REPRO/DEFINE CLUSTER/DELETE/LISTCAT for IDCAMS; IEBGENER/IEBCOPY
for utilities; DD/EXEC/DCB/DISP/IF-THEN-ELSE for JCL.

Chapters excluded: installation, performance tuning, user exit routines,
abend recovery, tape utilities — none contain control card business logic.

### Synthetic Pattern Files

| File | Patterns | Coverage |
|---|---|---|
| `dfsort_patterns.txt` | 14 | INCLUDE filters, OUTFIL splits, SORT/SUM aggregation, JOINKEYS, ICETOOL |
| `idcams_patterns.txt` | 10 | REPRO load/extract/replace, DEFINE CLUSTER, DELETE/DEFINE, modal IF-THEN-ELSE |
| `iebgener_patterns.txt` | 10 | Straight copy, GENERATE/RECORD field extraction, header/trailer, ICEGENER |

Each pattern contains: actual control cards, record layout (offsets/lengths/formats),
plain English business rule, PySpark or Python modern equivalent.

---

## Development Status

```
Phase 0  Foundation (folder structure, venv, PDF extraction)  COMPLETE
Phase 1  RAG Layer  (ChromaDB ingest + verification)          90% DONE
Phase 2  JCL Parser (jcl_splitter + utility_classifier)       IMPLEMENTED
Phase 3  Decoder    (RAG retrieval + Claude API decode)        IMPLEMENTED
Phase 4  Generator  (PySpark code generation)                  IMPLEMENTED
Phase 5  Validator  (Claude review + self-correction)          IMPLEMENTED
Phase 6  Output     (md_writer + Mermaid diagram)              IMPLEMENTED
Phase 7  Orchestrator (main.py + SQLite session memory)        IMPLEMENTED
Phase 8  Frontend   (FastAPI + HTML interface)                 NOT STARTED
```

The integrated backend has automated tests with mocked API calls. A live
Codespaces conversion still needs to be run against its local vector database
and repository secret.

---

## Utilities Supported

| Utility | PGM= values | Business logic decoded |
|---|---|---|
| DFSORT | `SORT`, `DFSORT` | INCLUDE/OMIT filters, SORT keys, OUTFIL splits, OUTREC/INREC reformatting, SUM aggregation, JOINKEYS joins |
| ICETOOL | `ICETOOL` | SELECT, STATS, SPLICE, UNIQUE, SORT, COUNT operators |
| IDCAMS | `IDCAMS` | REPRO (load/extract), DEFINE CLUSTER, DELETE, LISTCAT, modal IF-THEN-ELSE |
| IEBGENER | `IEBGENER`, `ICEGENER` | Straight copy, GENERATE/RECORD field selection and reformatting |
| IEBCOPY | `IEBCOPY` | Member SELECT/EXCLUDE, COPY/MERGE operations |

---

## Sample Output (target)

Given `wire_payment_batch.jcl`, JUIA produces:

**`outputs/wire_payment_batch_migration_spec.md`**
```
# Migration Spec — WIRE_PAYMENT_BATCH

## Job Summary
Steps: 8 | Utility steps: 7 | Program steps: 1
Utilities: IDCAMS(2), DFSORT(3), IEBGENER(2)

## Step 1 — LOADVSAM (IDCAMS)
Business Rule: Load daily wire payment flat file into VSAM KSDS.
               No REPLACE — duplicate keys will fail intentionally.
Input:  PAYMENT.DAILY.INPUT.SEQ
Output: PAYMENT.WORK.KSDS
...
```

**`outputs/wire_payment_batch_lineage.mmd`**
```
graph LR
  SEQIN[PAYMENT.DAILY.INPUT.SEQ] --> LOADVSAM
  LOADVSAM --> WORKVSAM[PAYMENT.WORK.KSDS]
  WORKVSAM --> EXTRACT
  EXTRACT --> ACTIVE[PAYMENT.ACTIVE.SEQ]
  ...
```

---

## Notes

- All processing is local. No data leaves the machine.
- Claude API calls go to Anthropic only for LLM inference.
- IBM manuals are publicly available free PDFs — no license required.
- The `.env` file and `corpus/raw/` PDFs are excluded from git.
