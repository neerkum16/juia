# Phase 8 front end design

## Purpose and scope

JUIA's first front end is a local web interface for converting one JCL job at a time and reviewing the result. It wraps the existing `agent.main.run_job()` workflow; the browser does not perform parsing, decoding, generation, or validation. The README specifies FastAPI plus an HTML interface for Phase 8. This design uses server-rendered HTML, small vanilla JavaScript interactions, and no separate front end build system.

The main user journey is: select one `.jcl` file, optionally attach external CNTL members, start a conversion, wait for the job to finish, then inspect step-level business rules, lineage, generated code, validation findings, and downloadable artifacts. Previously saved runs remain available by run ID and in a recent-runs list.

## Information architecture

1. **New conversion (`/`)** — input form, environment readiness, and recent runs.
2. **Running conversion (`/tasks/{task_id}`)** — queued/running state and a link to the result when finished. This is a task state, not a claim about individual pipeline stages.
3. **Run report (`/runs/{run_id}`)** — overall outcome, attention items, step explorer, lineage, and artifacts.

Desktop report wireframe:

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ JUIA                                         New conversion    Recent runs    │
├──────────────────────────────────────────────────────────────────────────────┤
│ MYTEST / 4ffa5442…                                      Incomplete           │
│ 9 steps  ·  6 generated  ·  3 manual tasks             Open migration spec   │
│ Generated code requires review and a separate Spark runtime test.           │
├───────────────────────┬──────────────────────────────────────────────────────┤
│ Steps                 │ STEP030 · DFSORT                                   │
│ STEP010  IDCAMS       │ Business rule                                      │
│ STEP020  IDCAMS       │ Filters positive PE wire transactions, then sorts  │
│ STEP030  DFSORT  ●    │ them by account and amount.                        │
│ STEP040  DFSORT       │                                                    │
│ STEP050  IEBGENER     │ Inputs → WIRE.DAILY.INPUT.SEQ                     │
│ STEP060  PROGRAM      │ Outputs → WIRE.WORK.EXTRT.SEQ                     │
│ …                     │                                                    │
│                       │ Rule | JCL/SYSIN | PySpark | Validation           │
│                       │ [selected tab content]                             │
├───────────────────────┴──────────────────────────────────────────────────────┤
│ Lineage diagram                       Artifacts and downloads               │
└──────────────────────────────────────────────────────────────────────────────┘
```

At narrow widths, the step list becomes a full-width list above the selected step. Tabs and action buttons wrap; code and control-card blocks scroll horizontally within their own area.

## Screen details

### New conversion

- A single clearly labeled JCL file picker and an optional multi-file CNTL picker. Drag and drop is an enhancement to the same accessible file inputs.
- Show selected filenames before submission. Explain that CNTL files supply external SYSIN members, while inline SYSIN needs no separate file.
- A primary **Convert job** button. Disable duplicate submission while the request is being accepted.
- Show a compact readiness indicator for the Anthropic key and local vector database. Do not display the key or internal paths. If a requirement is missing, show the specific setup action and keep conversion unavailable.
- Below the form, show recent runs with job name, completion time, status, and review flag. Include an **Open run ID** field so a result can be found even if the local SQLite index was copied or rebuilt.

### Running conversion

- Show the uploaded JCL filename, a queued or running label, and elapsed time. Use an indeterminate activity indicator: `run_job()` currently exposes no durable intermediate progress events.
- Poll task state at a modest interval and redirect to the run report when the final `run_id` is known. A temporary network interruption should leave an explicit retry action rather than start another conversion.
- If the server restarts before completion, explain that this task's live state was lost and offer recent runs or run-ID lookup. Completed results are still stored by the backend.

### Run report

- The header displays job name, short run ID with a copy control, final status, and counts derived from `step_statuses`. `success`, `incomplete`, and `failed` retain their exact backend meanings.
- Put errors, warnings, `manual_steps`, `review_steps`, and cross-step issues in an **Attention needed** area above the step explorer. A successful run with `review_required: true` still has a prominent human-review label.
- Keep a persistent note that validation is model review plus static checks, and generated PySpark has not been run against a configured Spark environment unless `runtime_verified` is explicitly true. Do not turn `PASS` into a claim of executable or production-ready code.
- Step explorer follows original JCL order. Each row shows step name, utility, and plain-text decode/generation/validation states. The detail area shows the business rule and operations first, then inputs/outputs, original SYSIN and DD statements, generated PySpark, and validation findings. Skipped or unresolved steps explain why code is absent.
- A lineage section reads the saved `lineage.mmd` and presents its dataset-to-step and step-to-dataset edges as a readable flow. The source text and download remain available. No external CDN is required at runtime.
- Artifact links are offered only for files that exist in the selected run: migration specification, lineage source, decoded JSON, validation JSON, result JSON, and generated code per step. The specification may open as readable text with a download link; Markdown rendering is optional for the first release.
- Older saved result files may lack newer fields such as `manual_steps`, `cross_step_validation`, or `runtime_verified`. The page uses safe defaults and labels missing verification data as **Not recorded**, never as verified.

## Visual language

- A restrained technical workspace: warm neutral page, white content surfaces, dark ink text, and one teal action color. Monospaced type is reserved for JCL, code, dataset names, and run IDs.
- Status uses words and icons as well as color: green success, amber incomplete/review, and red failed. Utility types use quiet text labels instead of a separate bright color for each type.
- Business rules are readable prose with generous line spacing. Long code, control cards, and validation details stay in collapsible or tabbed areas so the main finding remains visible.
- Keyboard navigation, visible focus, labeled file inputs, semantic headings, and status announcements are required. The design targets desktop review but remains usable on a phone.

## API and server boundary

| Route | Purpose |
| --- | --- |
| `GET /` | Render new-conversion page and recent-run summaries. |
| `POST /api/tasks` | Accept one JCL file and optional CNTL files; return a task ID immediately. |
| `GET /api/tasks/{task_id}` | Return `queued`, `running`, `completed`, or `failed`, with `run_id` when available. |
| `GET /runs/{run_id}` | Render the saved run report using `get_run()`. |
| `GET /api/runs` | Return recent run summaries in completion order. |
| `GET /api/runs/{run_id}` | Return report fields without stored artifact and generated-code filesystem paths. |
| `GET /runs/{run_id}/artifacts/{kind}` | Serve a whitelisted artifact from that run. |
| `GET /runs/{run_id}/code/{step_name}` | Show or download only a code file registered for that step in the run. |

`POST /api/tasks` stages uploaded files in a per-task temporary directory and calls `run_job()` on a worker, so long Claude calls do not hold the upload request open. Run execution is serialized in the first local release to avoid competing for the local model/vector database and to keep resource use predictable. Each task retains its staging directory until `run_job()` finishes. With no CNTL uploads, the existing `cntl/` directory is used; with uploads, the uploaded CNTL set is used for that task. Unresolved external members remain visible as incomplete work in the backend result.

The app binds to `127.0.0.1` by default. Uploads accept one `.jcl` job and plain-text `.jcl`/`.txt`/`.cntl` CNTL members, with filename and size checks. Server code resolves artifacts from the run ID and a fixed whitelist, checks that every path stays inside that run's output folder, and never accepts a browser-supplied filesystem path. Uploaded text and generated Markdown/code are rendered as text, not trusted HTML. The lineage view reads only the generated Mermaid node and edge lines as data; it does not execute diagram directives in the browser.

The API returns status and browser-safe artifact identifiers, not the absolute artifact and generated-code paths stored in `result.json`. Existing `save_run()` and `get_run()` remain the persistence contract. A small recent-run query over the SQLite index supplies recent runs; saved run folders can be searched when the index is absent. The UI handles the current and older result shapes.

## States to design and verify

| State | What the user sees |
| --- | --- |
| Ready | Upload form and recent runs. |
| Missing key or vector database | Specific setup message; conversion unavailable. |
| Queued or running | Indeterminate activity and elapsed time. |
| Success | Completed result, step details, and runtime-verification note. |
| Success with review | Completed result plus explicit human-review action. |
| Incomplete | Manual/unresolved steps and warnings shown first. |
| Failed | Errors shown first; any saved partial steps or artifacts remain inspectable. |
| Unknown run ID | Clear not-found page with a way back to recent runs. |
| Artifact absent | Explain that this run did not produce that artifact. |

## Implementation sequence after design review

1. Add FastAPI, Uvicorn, HTML template, and multipart dependencies; create the local web server and static assets.
2. Add upload staging, a single-worker task runner, task polling, recent-run lookup, and constrained artifact routes.
3. Build the three screens and responsive styles, then connect the existing backend result contract.
4. Verify upload and report flows with mocked `run_job()` and temporary outputs, including incomplete, failed, older-result, missing-artifact, and path-validation cases. Run the existing backend suite to catch regressions.

This design is the Phase 8 design deliverable. It does not change the existing conversion implementation.
