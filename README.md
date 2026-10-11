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

```text
juia/
├── agent/                        # Parser, decoder, generator, validator,
│   ├── main.py                   # output writers, and SQLite run store
│   ├── parser/
│   ├── decoder/
│   ├── generator/
│   ├── validator/
│   ├── memory/
│   └── output/
├── scripts/                      # Corpus tools, legacy phase drivers,
│   ├── run_all_jcl.sh            # and sequential batch wrappers
│   └── run_all_jcl.ps1
├── tests/                        # Automated regression tests
├── jcl/
│   └── TEST.jcl    # Stable nine-step demo/test input
├── cntl/                         # External SYSIN members for the sample
├── corpus/
│   ├── raw/                      # Source manuals
│   ├── extracted/                # Focused PDFs for corpus ingestion
│   ├── synthetic/                # Annotated patterns for ingestion
│   └── vectordb/                 # Generated locally; ignored by Git
├── deployment/                   # Production shipping layout guide
├── outputs/                      # Per-run results and SQLite run index
├── requirements.txt
└── README.md
```

The front end has not been created. See [deployment/README.md](deployment/README.md)
for the files needed in a production installation.

---

## Setup

### Prerequisites

- Python with the packages in `requirements.txt`
- Bash on Linux/Codespaces or PowerShell on Windows for batch operation
- Anthropic API key available to the process as `ANTHROPIC_API_KEY`

### First-Time Setup

From the project root, create a virtual environment and install dependencies:

```bash
# Linux / Codespaces
python -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
```

```powershell
# Windows PowerShell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### API key

Provide `ANTHROPIC_API_KEY` through the Codespaces or host environment before
running a live conversion. The application reads `os.environ` directly; it
does not automatically load a `.env` file. Do not commit credentials.

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
python -m agent.main jcl/TEST.jcl --cntl-folder cntl --output-folder outputs
```

Each invocation writes to `outputs/<job_name>/<run_id>/`, including generated
PySpark files, decoded and validation JSON, a Mermaid diagram, and a migration
specification. Each run also has a `result.json`, including runs that fail
before generation. The SQLite index at `outputs/sessions.db` stores the same
structured result. Run folders and the SQLite index are not Git-ignored, so
they can be committed together. To retrieve a completed or failed run:

```bash
python -m agent.main --get-run RUN_ID --output-folder outputs
```

To convert a folder of JCL files sequentially, use the platform wrapper.
Both wrappers scan recursively for `.jcl` files, continue after a failed or
incomplete job, and exit nonzero if any job did not succeed. They default to
`jcl/` when that folder exists, otherwise `jcl/`. Each job still gets
its own run folder; batch console logs and a summary are written under
`outputs/batch_logs/<batch-id>/` by default.

```bash
# Linux / Codespaces (after activating the virtual environment)
bash scripts/run_all_jcl.sh --jcl-folder jcl --cntl-folder cntl --output-folder outputs
```

```powershell
# Windows PowerShell (after activating the virtual environment)
.\scripts\run_all_jcl.ps1 -JclFolder jcl -CntlFolder cntl -OutputFolder outputs
```

An explicit JCL folder is optional when using either default folder. CNTL
members are resolved from the single CNTL folder as each JCL is processed;
they do not need separate invocations.

### Why the wire payment sample is used in tests

`jcl/TEST.jcl` is a fixed regression input. The automated
tests assert its nine named steps, dataset lineage, generated-step counts, and
`WIREPAY` job identity; `tests/sample_steps.py` builds mocked decoder results
from that JCL and the sample members in `cntl/`. This gives the tests a known
expected result without requiring an API key, vector database, or a manually
created decoded JSON fixture. The five older `scripts/test_*.py` phase drivers
also hardcode that file and write to shared sample paths under `outputs/`.

The production entry point does not require this filename. Pass any JCL file
to `python -m agent.main`, or give a JCL folder to a batch wrapper. Moving or
removing the sample requires updating the phase drivers and the tests that
refer to it; adding a new production JCL does not require a new test script.

The result status is `success`, `incomplete`, or `failed`. An unresolved
external SYSIN marks the run incomplete; setup, decode, generation, or
validation errors mark it failed. A step still failing validation after all
three correction attempts is marked `REVIEW` and listed in `review_steps`;
the run is `success` if no other errors or incomplete steps remain. Such a
script still requires human review before use. Each run owns its final scripts
and validation report; the correction loop does not create source backups or
`.validated` markers. Validation is a Claude review of generated code, not
execution of that code in Spark. The older `scripts/test_*.py` phase drivers
write to shared sample paths under `outputs/`; use `python -m agent.main` for
new job runs.

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
Phase 1  RAG Layer  (ChromaDB ingest + verification)          IMPLEMENTED; LOCAL DB REQUIRED
Phase 2  JCL Parser (jcl_splitter + utility_classifier)       IMPLEMENTED
Phase 3  Decoder    (RAG retrieval + Claude API decode)        IMPLEMENTED
Phase 4  Generator  (PySpark code generation)                  IMPLEMENTED
Phase 5  Validator  (Claude review + self-correction)          IMPLEMENTED
Phase 6  Output     (md_writer + Mermaid diagram)              IMPLEMENTED
Phase 7  Orchestrator (main.py + SQLite session memory)        IMPLEMENTED
Phase 8  Frontend   (FastAPI + HTML interface)                 NOT STARTED
```

The integrated backend has automated tests with mocked API calls. Live runs
require a populated local vector database and `ANTHROPIC_API_KEY` in the
runtime environment. Inspect each run's `result.json` and `validation.json`
for its actual outcome; the presence of generated files alone does not mean
all steps passed validation.

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

## Sample Run Output

For `jcl/TEST.jcl`, the JOB name is `WIREPAY`. The
orchestrator creates a fresh run folder each time:

```text
outputs/WIREPAY/<run_id>/
├── result.json
├── decoded.json
├── validation.json
├── lineage.mmd
├── migration_spec.md
└── pyspark/
    ├── STEP010_IDCAMS.py
    ├── STEP020_IDCAMS.py
    ├── STEP030_DFSORT.py
    ├── STEP040_DFSORT.py
    ├── STEP070_DFSORT.py
    └── STEP090_IDCAMS.py
```

`outputs/sessions.db` indexes run results. The shared files in `outputs/`
and `outputs/pyspark/` are from the older phase drivers. Automated tests
build mocked decoder responses from the current sample JCL, so those shared
files are not needed for the Phase 7 workflow or its test suite. A fresh
conversion creates a new run ID and new scripts. Use the run's `decoded.json`
and `result.json` when assessing that conversion. `--get-run` resolves saved
artifact paths against the current output folder when the run is opened from
another checkout.

---

## Notes

- Parsing, vector retrieval, and artifact writing run locally. Decoding,
  generation, correction, and validation send relevant content to Anthropic.
- The embedding model may be downloaded from Hugging Face on first use.
- `.env` and `corpus/vectordb/` are Git-ignored; API keys belong in the
  runtime environment or a secret manager.
