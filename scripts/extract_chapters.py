# =============================================================================
# scripts/extract_chapters.py
#
# PROJECT : JUIA — JCL Utility Intelligence Agent
# PURPOSE : Extract targeted chapters from IBM mainframe reference manuals.
#           The extracted PDFs are smaller, focused files that will be
#           used as the agent's knowledge corpus.
#
# HOW TO RUN (always from the juia project root folder):
#   source venv/Scripts/activate
#   python scripts/extract_chapters.py
#
# BEFORE RUNNING — verify these physical page numbers in your PDF viewer:
#   icea100_v3r2.pdf  page 29  → should show "Chapter 1. Introducing DFSORT"
#   idau100_v3r2.pdf  page 33  → should show "Chapter 1. Introduction"
#   ieab600_v2r3.pdf  page 49  → should show "Chapter 1. Job control statements"
#   idai200_v2r4.pdf  page 35  → should show "Chapter 1. Using Access Method Services"
#
# If any page lands on wrong content, please adjust the offset.
# The offset for that manual needs adjusting.
#
# OUTPUT FILES (written to corpus/extracted/):
#   dfsort_apg_extract.pdf          ~512 pages
#   dfsmdfp_utilities_extract.pdf   ~ 77 pages
#   mvs_jcl_reference_extract.pdf   ~ 94 pages
#   idcams_extract.pdf              ~120 pages
#
# REQUIREMENT:
#   pip install pypdf
# =============================================================================

import os
from pypdf import PdfReader, PdfWriter


def extract_pages(input_pdf, output_pdf, page_ranges, label):
    """
    Copies specific pages from a large PDF into a new smaller PDF file.

    Parameters:
        input_pdf   : path to the original full IBM manual (the big source file)
        output_pdf  : path where the smaller output file will be saved
        page_ranges : list of (start, end) pairs using physical page numbers,
                      both start and end are included in the output
        label       : short description shown in the printed progress messages

    Note on page numbers:
        Physical page numbers count from the very first page of the PDF file,
        including the cover page and all front matter (table of contents etc.).
        These are NOT the printed numbers shown at the bottom of manual pages.
    """

    # Check the source file exists before doing anything
    if not os.path.exists(input_pdf):
        print(f"\n[ERROR] File not found : {input_pdf}")
        print(f"        Put the PDF in corpus/raw/ and run the script again.")
        return

    # Create the output folder if it does not exist yet
    os.makedirs(os.path.dirname(output_pdf), exist_ok=True)

    reader      = PdfReader(input_pdf)
    writer      = PdfWriter()
    total_pages = len(reader.pages)
    pages_added = 0

    print(f"\n{'=' * 60}")
    print(f"  Processing : {label}")
    print(f"  Source     : {input_pdf}")
    print(f"  Total pages in source PDF : {total_pages}")
    print(f"{'=' * 60}")

    for start, end in page_ranges:

        # Skip the entire range if start is past the last page
        if start > total_pages:
            print(f"  [WARN] Range ({start}-{end}) skipped — beyond last page ({total_pages})")
            continue

        # Trim the end if it overshoots the last page
        if end > total_pages:
            print(f"  [WARN] Range end {end} trimmed to last page ({total_pages})")
            end = total_pages

        # pypdf uses 0-based index internally, so subtract 1 from each number
        for page_num in range(start - 1, end):
            writer.add_page(reader.pages[page_num])
            pages_added += 1

    # Save all collected pages into the output file
    with open(output_pdf, "wb") as out_file:
        writer.write(out_file)

    print(f"  [OK] {pages_added} pages extracted")
    print(f"  [OK] Saved to : {output_pdf}")


# =============================================================================
# MANUAL 1 — DFSORT Application Programming Guide
#
# Raw file    : corpus/raw/icea100_v3r2.pdf
# Front matter: 28 pages (i through xxviii)
# Ch1 starts  : physical page 29
# Offset rule : physical page = printed logical page number + 28
#
# What we are extracting and why:
#   Ch1  pg 29–40  : DFSORT overview — how SORT, MERGE, COPY modes work
#   Ch3  pg105–118 : Control statement list — the agent's classification map
#   Ch3  pg119–153 : INCLUDE statement — all filtering and comparison rules
#                    (ZD/PD/CH format comparisons, AND/OR logic, date ranges)
#   Ch3  pg154–187 : INREC statement — input record layout reformatting
#   Ch3  pg196–198 : OMIT statement — record exclusion rules
#   Ch3  pg247–443 : OUTFIL + OUTREC — multi-file output splitting and
#                    output record reformatting (highest business logic density)
#   Ch3  pg449–462 : SORT + SUM — sort key definitions and aggregation rules
#   Ch4  pg465–491 : JOINKEYS — two-file join and lookup logic
#   Ch7  pg561–707 : ICETOOL — SELECT/SPLICE/STATS/UNIQUE/SORT operators
#   Ch11 pg789–819 : Full annotated job stream examples (best RAG pattern source)
#   AppC pg847–859 : Data format codes: CH, ZD, PD, BI, FI, CSF with byte rules
# =============================================================================

DFSORT_RANGES = [
    ( 29,  40),   # Ch1  : DFSORT overview and processing modes
    (105, 118),   # Ch3  : Control statement summary and classification
    (119, 153),   # Ch3  : INCLUDE — filter and comparison business rules
    (154, 187),   # Ch3  : INREC — input record field reformatting
    (196, 198),   # Ch3  : OMIT — record exclusion logic
    (247, 443),   # Ch3  : OUTFIL and OUTREC — output splitting and reformatting
    (449, 462),   # Ch3  : SORT and SUM — sort keys and aggregation
    (465, 491),   # Ch4  : JOINKEYS — file join and lookup operations
    (561, 707),   # Ch7  : ICETOOL operators — full chapter
    (789, 819),   # Ch11 : Complete job stream examples
    (847, 859),   # AppC : Data format reference table
]

extract_pages(
    input_pdf   = "corpus/raw/icea100_v3r2.pdf",
    output_pdf  = "corpus/extracted/dfsort_apg_extract.pdf",
    page_ranges = DFSORT_RANGES,
    label       = "DFSORT APG — icea100_v3r2.pdf"
)


# =============================================================================
# MANUAL 2 — DFSMSdfp Utilities  (IEBGENER, IEBCOPY, etc.)
#
# Raw file    : corpus/raw/idau100_v3r2.pdf
# Front matter: 32 pages (i through xxx)
# Ch1 starts  : physical page 33
# Offset rule : physical page = printed logical page number + 32
#
# What we are extracting and why:
#   Ch1  pg 33–41  : Utility classification table — maps PGM= names to functions
#                    Agent uses this to identify what each JCL step does
#   Ch4  pg 95–147 : IEBCOPY — SELECT/EXCLUDE/COPY/MERGE for PDS members
#                    Library copy operations with member-level business logic
#   Ch7  pg181–201 : IEBGENER — GENERATE and RECORD control statements
#                    Sequential dataset copy with optional reformatting
# =============================================================================

DFSMDFP_RANGES = [
    ( 33,  41),   # Ch1  : Utility program classification table
    ( 95, 147),   # Ch4  : IEBCOPY — member copy and merge operations
    (181, 201),   # Ch7  : IEBGENER — sequential copy and generation
]

extract_pages(
    input_pdf   = "corpus/raw/idau100_v3r2.pdf",
    output_pdf  = "corpus/extracted/dfsmdfp_utilities_extract.pdf",
    page_ranges = DFSMDFP_RANGES,
    label       = "DFSMSdfp Utilities — idau100_v3r2.pdf"
)


# =============================================================================
# MANUAL 3 — MVS JCL Reference
#
# Raw file    : corpus/raw/ieab600_v2r3.pdf
# Front matter: 48 pages (i through xxxvi plus figures and tables pages)
# Ch1 starts  : physical page 49
# Offset rule : physical page = printed logical page number + 48
#
# What we are extracting and why:
#   Ch1  pg 49–51  : JCL statement types — JCL vs JECL overview
#   Ch3  pg 61–65  : Statement format rules — name/operation/parameter fields
#   Ch12 pg125–141 : DD statement core syntax — ddnames, parameter overview
#   Ch12 pg173–185 : DCB parameter — RECFM, LRECL, BLKSIZE (record layout)
#   Ch12 pg194–206 : DISP parameter — NEW/OLD/SHR/MOD dataset handling
#   Ch12 pg210–217 : DSNAME parameter — how datasets are referenced in JCL
#   Ch12 pg279–282 : RECFM parameter — FB/VB/U record format codes
#   Ch12 pg309–313 : SYSOUT parameter — print and report output routing
#   Ch12 pg318–323 : UNIT parameter — device type specification
#   Ch13 pg335–347 : Special DD statements — SYSIN, STEPLIB, JOBLIB patterns
#   Ch16 pg353–392 : EXEC statement — PGM= COND= PARM= (step execution control)
#   Ch18 pg401–413 : IF/THEN/ELSE/ENDIF — conditional step execution logic
# =============================================================================

MVS_JCL_RANGES = [
    ( 49,  51),   # Ch1  : JCL statement type overview
    ( 61,  65),   # Ch3  : Statement format and field structure
    (125, 141),   # Ch12 : DD statement core syntax and ddname rules
    (173, 185),   # Ch12 : DCB parameter — record format and length
    (194, 206),   # Ch12 : DISP parameter — dataset disposition
    (210, 217),   # Ch12 : DSNAME parameter — dataset name references
    (279, 282),   # Ch12 : RECFM — FB/VB/U record format codes
    (309, 313),   # Ch12 : SYSOUT — output routing
    (318, 323),   # Ch12 : UNIT — device type
    (335, 347),   # Ch13 : Special DD: SYSIN, STEPLIB, JOBLIB
    (353, 392),   # Ch16 : EXEC statement — PGM, COND, PARM parameters
    (401, 413),   # Ch18 : IF/THEN/ELSE/ENDIF conditional logic
]

extract_pages(
    input_pdf   = "corpus/raw/ieab600_v2r3.pdf",
    output_pdf  = "corpus/extracted/mvs_jcl_reference_extract.pdf",
    page_ranges = MVS_JCL_RANGES,
    label       = "MVS JCL Reference — ieab600_v2r3.pdf"
)


# =============================================================================
# MANUAL 4 — DFSMS Access Method Services Commands  (IDCAMS)
#
# Raw file    : corpus/raw/idai200_v2r4.pdf
# Front matter: 34 pages (i through xxxii plus figures and tables pages)
# Ch1 starts  : physical page 35
# Offset rule : physical page = printed logical page number + 34
#
# What we are extracting and why:
#   Ch1  pg 35–47  : How to invoke AMS — JCL patterns, DD statement wiring,
#                    how IDCAMS steps are structured in a job
#   Ch2  pg 49–57  : Modal commands — IF-THEN-ELSE and DO-END control flow
#                    inside IDCAMS SYSIN. This is embedded business logic.
#   Ch3  pg 59–61  : Functional command syntax summary — full command list
#                    Agent uses this to classify what an IDCAMS step does
#   Ch14 pg179–209 : DEFINE CLUSTER — KSDS/ESDS/RRDS VSAM dataset creation
#                    Most common VSAM operation found in batch jobs
#   Ch20 pg249–271 : DELETE command — dataset deletion with all options
#                    Appears in nearly every batch job for cleanup steps
#   Ch27 pg311–321 : LISTCAT command — catalog query and listing
#                    Used for verification steps in batch jobs
#   Ch30 pg343–363 : REPRO command — copy records between VSAM and sequential
#                    THE most common IDCAMS command in production batch jobs
#                    Used for loads, extracts, backups, and restores
#   AppB pg401–450 : LISTCAT output interpretation — keyword field descriptions
#                    Agent needs this to decode what a LISTCAT step reveals
# =============================================================================

IDCAMS_RANGES = [
    ( 35,  47),   # Ch1  : Invoking AMS — JCL invocation and DD statement patterns
    ( 49,  57),   # Ch2  : Modal commands — IF-THEN-ELSE and DO-END control flow
    ( 59,  61),   # Ch3  : Functional command syntax summary
    (179, 209),   # Ch14 : DEFINE CLUSTER — VSAM dataset definition
    (249, 271),   # Ch20 : DELETE — dataset deletion command
    (311, 321),   # Ch27 : LISTCAT — catalog query command
    (343, 363),   # Ch30 : REPRO — file copy and load command
    (401, 450),   # AppB : LISTCAT output field descriptions
]

extract_pages(
    input_pdf   = "corpus/raw/idai200_v2r4.pdf",
    output_pdf  = "corpus/extracted/idcams_extract.pdf",
    page_ranges = IDCAMS_RANGES,
    label       = "IDCAMS — idai200_v2r4.pdf"
)


# =============================================================================
# MANUAL 5 — Local JCL + VSAM Guide  (~50-60 pages)
#
# This guide is already compact so take the whole thing.
# Step 1: Check the actual page count by opening it in a PDF viewer.
# Step 2: Replace 60 below with the real last page number.
# Step 3: Uncomment this block and run the script again.
# =============================================================================

# extract_pages(
#     input_pdf   = "corpus/raw/jcl_vsam_local_guide.pdf",
#     output_pdf  = "corpus/extracted/jcl_vsam_local_guide.pdf",
#     page_ranges = [
#         (1, 60),   # <-- replace 60 with the actual page count of your guide
#     ],
#     label       = "Local JCL and VSAM Guide"
# )


# =============================================================================
print("\n" + "=" * 60)
print("  All extractions complete.")
print("  Output files are in : corpus/extracted/")
print("  Next step           : run scripts/upload_corpus.py")
print("=" * 60)