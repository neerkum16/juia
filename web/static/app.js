"use strict";

const app = document.getElementById("app");
const artifactLabels = {
  spec: ["Migration specification", "Markdown report"],
  diagram: ["Lineage source", "Mermaid diagram"],
  decoded: ["Decoded steps", "JSON"],
  validation: ["Validation report", "JSON"],
  result: ["Run result", "JSON"],
};

function node(tag, className = "", text = null) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== null) item.textContent = String(text);
  return item;
}

function append(parent, ...children) {
  children.forEach((child) => parent.append(child));
  return parent;
}

function status(value) {
  const label = String(value || "not_run");
  const css = label.toLowerCase().replace(/[^a-z_]/g, "");
  return node("span", `status ${css}`, label.replaceAll("_", " "));
}

function dateLabel(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Time unavailable" :
    new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

async function jsonRequest(url, options) {
  const response = await fetch(url, options);
  let payload;
  try { payload = await response.json(); } catch { payload = {}; }
  if (!response.ok) {
    const detail = payload.detail;
    const message = typeof detail === "string" ? detail :
      detail?.issues?.join(" ") || `Request failed (${response.status}).`;
    throw new Error(message);
  }
  return payload;
}

function notice(message, kind = "") {
  return node("div", `notice ${kind}`, message);
}

function action(label, href, primary = false) {
  const link = node("a", `button${primary ? " primary" : ""}`, label);
  link.href = href;
  return link;
}

function clear() { app.replaceChildren(); }

async function showHome() {
  clear();
  const head = node("div", "page-head");
  append(head, node("div", "eyebrow", "Mainframe modernization"),
    node("h1", "", "Turn utility JCL into a migration plan"),
    node("p", "lead", "Upload one job to decode its business rules, trace data flow, and generate PySpark for review."));
  app.append(head);

  const layout = node("div", "grid-home");
  const formCard = node("section", "surface pad");
  append(formCard, node("h2", "", "New conversion"));
  const readinessBox = node("div", "readiness");
  formCard.append(readinessBox);
  const form = node("form");
  form.enctype = "multipart/form-data";
  const jclField = node("label", "field");
  append(jclField, node("span", "field-label", "JCL job · required"));
  const jcl = node("input", "file-input");
  jcl.type = "file"; jcl.name = "jcl"; jcl.accept = ".jcl"; jcl.required = true;
  append(jclField, jcl, node("span", "field-help", "Choose one .jcl file containing one JOB card."));
  const cntlField = node("label", "field");
  append(cntlField, node("span", "field-label", "External CNTL members · optional"));
  const cntl = node("input", "file-input");
  cntl.type = "file"; cntl.name = "cntl_files"; cntl.accept = ".jcl,.txt,.cntl"; cntl.multiple = true;
  append(cntlField, cntl, node("span", "field-help", "Attach control cards referenced by external SYSIN. Without attachments, JUIA uses the local cntl/ folder."));
  const submit = node("button", "button primary", "Convert job");
  submit.type = "submit";
  const errorArea = node("div");
  errorArea.setAttribute("role", "alert");
  append(form, jclField, cntlField, node("div", "action-row"));
  form.lastChild.append(submit);
  form.append(errorArea);
  formCard.append(form);
  layout.append(formCard);

  const side = node("aside", "surface pad");
  append(side, node("h2", "", "Open a saved run"),
    node("p", "small muted", "Enter a run ID to return to a previous result, including one copied from another checkout."));
  const lookup = node("form", "lookup");
  const idInput = node("input");
  idInput.name = "run_id"; idInput.placeholder = "32-character run ID";
  idInput.setAttribute("aria-label", "Run ID");
  const open = node("button", "button", "Open run"); open.type = "submit";
  append(lookup, idInput, open);
  const lookupError = node("div", "small error-text"); lookupError.setAttribute("role", "alert");
  append(side, lookup, lookupError, node("hr", "divider"));
  const recentHead = node("div", "recent-head");
  append(recentHead, node("h2", "", "Recent runs"));
  side.append(recentHead);
  const recent = node("div", "run-list", "Loading runs…");
  recent.id = "recent";
  side.append(recent);
  layout.append(side);
  app.append(layout);

  lookup.addEventListener("submit", (event) => {
    event.preventDefault();
    const id = idInput.value.trim();
    if (!/^[0-9a-f]{32}$/i.test(id)) {
      lookupError.textContent = "Enter a 32-character hexadecimal run ID.";
      return;
    }
    location.href = `/runs/${id}`;
  });

  let ready = false;
  try {
    const health = await jsonRequest("/api/health");
    ready = health.ready;
    readinessBox.replaceChildren(ready ? notice("Local prerequisites found. Analysis uses the configured corpus and Anthropic API.") :
      notice(health.issues.join(" "), "warn"));
  } catch (error) {
    readinessBox.replaceChildren(notice(`Readiness check failed: ${error.message}`, "error"));
  }
  submit.disabled = !ready;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorArea.replaceChildren();
    submit.disabled = true; submit.textContent = "Submitting…";
    try {
      const data = new FormData(form);
      if (!cntl.files.length) data.delete("cntl_files");
      const task = await jsonRequest("/api/tasks", { method: "POST", body: data });
      location.href = `/tasks/${task.task_id}`;
    } catch (error) {
      errorArea.append(notice(error.message, "error"));
      submit.disabled = false; submit.textContent = "Convert job";
    }
  });

  try {
    const data = await jsonRequest("/api/runs");
    recent.replaceChildren();
    if (!data.runs.length) {
      recent.append(node("p", "small muted", "No saved runs yet."));
    } else {
      data.runs.slice(0, 8).forEach((run) => {
        const row = node("div", "run-row");
        const left = node("div");
        append(left, action(run.job_name, `/runs/${run.run_id}`), node("div", "run-meta", dateLabel(run.completed_at)));
        left.firstChild.className = "run-title";
        const right = node("div", "run-row-right");
        append(right, status(run.status));
        if (run.review_required) right.append(node("div", "run-meta", "Human review"));
        append(row, left, right); recent.append(row);
      });
    }
  } catch (error) {
    recent.replaceChildren(node("p", "small error-text", error.message));
  }
}

async function showTask(taskId) {
  clear();
  const wrap = node("div", "task-center");
  const card = node("section", "surface");
  const activity = node("div", "activity"); activity.setAttribute("aria-hidden", "true");
  const title = node("h1", "", "Preparing conversion");
  const description = node("p", "muted", "Checking the task…");
  const elapsed = node("p", "small muted");
  append(card, activity, title, description, elapsed, action("New conversion", "/"));
  append(wrap, card); app.append(wrap);
  let timer;
  async function refresh() {
    try {
      const task = await jsonRequest(`/api/tasks/${taskId}`);
      title.textContent = task.state === "queued" ? "Conversion queued" :
        task.state === "running" ? "Converting JCL" :
          task.state === "failed" ? "Conversion stopped" : "Conversion complete";
      description.textContent = task.state === "queued" ? `${task.filename} is waiting for the current conversion to finish.` :
        task.state === "running" ? `JUIA is analyzing ${task.filename}. This can take several minutes.` :
          task.error || "Opening the saved report…";
      elapsed.textContent = `Started ${dateLabel(task.created_at)} · ${Math.max(0, Math.floor((Date.now() - new Date(task.created_at).getTime()) / 1000))} s elapsed`;
      if (task.state === "completed" && task.run_id) {
        clearInterval(timer); location.replace(`/runs/${task.run_id}`);
      } else if (task.state === "failed") {
        clearInterval(timer); activity.remove();
      }
    } catch (error) {
      clearInterval(timer); activity.remove();
      title.textContent = "Task unavailable";
      description.textContent = `${error.message} The server may have restarted. Check recent runs or open a saved run ID.`;
    }
  }
  await refresh();
  if (document.contains(card) && document.contains(activity)) timer = setInterval(refresh, 2500);
}

function datasetTags(items) {
  const list = node("div", "dataset-list");
  if (!items?.length) list.append(node("span", "small muted", "None recorded"));
  else items.forEach((item) => list.append(node("span", "dataset", item)));
  return list;
}

function detailRule(step) {
  const view = node("div");
  if (!step) return append(view, notice("This run stopped before this step was decoded.", "warn"));
  view.append(node("p", "rule-text", step.business_rule || "No business rule was recorded."));
  const pair = node("div", "flow-pair");
  const input = node("div"); append(input, node("h3", "", "Inputs"), datasetTags(step.input_files));
  const output = node("div"); append(output, node("h3", "", "Outputs"), datasetTags(step.output_files));
  append(pair, input, output); view.append(pair);
  if (step.operations?.length) {
    view.append(node("h3", "", "Operations"));
    step.operations.forEach((operation) => {
      const block = node("div", "operation");
      append(block, node("strong", "", operation.type || "Operation"), node("p", "", operation.description || ""));
      view.append(block);
    });
  }
  if (step.notes) {
    const details = node("details", "section-block");
    append(details, node("summary", "", "Decoder notes"), node("p", "small", step.notes));
    view.append(details);
  }
  return view;
}

function detailJcl(step) {
  const view = node("div");
  if (!step) return append(view, notice("JCL details were not saved for this step.", "warn"));
  append(view, node("h3", "", "SYSIN control cards"));
  const source = step.sysin_source || "not recorded";
  view.append(node("p", "small muted", `Source: ${source}${step.sysin_dsn ? ` · ${step.sysin_dsn}` : ""}`));
  view.append(node("pre", "", step.sysin_content || "No SYSIN content was recorded."));
  if (step.dd_statements?.length) {
    const section = node("section", "section-block");
    section.append(node("h3", "", "DD statements"));
    const table = node("table", "data-table");
    const head = node("thead");
    const header = node("tr"); append(header, node("th", "", "DD name"), node("th", "", "Parameters")); head.append(header);
    const body = node("tbody");
    step.dd_statements.forEach((dd) => {
      const row = node("tr"); append(row, node("td", "", dd.ddname || ""), node("td", "", dd.parameters || "")); body.append(row);
    });
    append(table, head, body); section.append(table); view.append(section);
  }
  return view;
}

async function detailCode(run, stepName, view) {
  if (!run.code_steps.includes(stepName)) {
    view.append(notice("No PySpark file was generated for this step. Check its decode and generation states.", "warn"));
    return;
  }
  view.append(node("p", "small muted", "Generated code is for review. It has not been verified in a Spark runtime."));
  view.append(action("Download Python file", `/runs/${run.run_id}/code/${encodeURIComponent(stepName)}?download=true`));
  const code = node("pre", "code-block", "Loading code…"); view.append(code);
  try {
    const response = await fetch(`/runs/${run.run_id}/code/${encodeURIComponent(stepName)}`);
    if (!response.ok) throw new Error("Code file is unavailable.");
    code.textContent = await response.text();
  } catch (error) { code.textContent = error.message; }
}

function detailValidation(run, stepName) {
  const view = node("div");
  const validation = run.validation_results.find((item) => item.step_name === stepName);
  if (!validation) return append(view, notice("No validation report was recorded for this step.", "warn"));
  view.append(status(validation.validation_status));
  if (validation.issues_found?.length) {
    view.append(node("h3", "section-block", "Issues found"));
    const list = node("ul", "attention-list");
    validation.issues_found.forEach((issue) => list.append(node("li", "", issue)));
    view.append(list);
  }
  if (validation.checks && Object.keys(validation.checks).length) {
    view.append(node("h3", "section-block", "Checks"));
    Object.entries(validation.checks).forEach(([name, check]) => {
      const block = node("div", "operation");
      append(block, node("strong", "", `${name.replaceAll("_", " ")} · ${check.status || "not recorded"}`),
        node("p", "", check.detail || ""));
      view.append(block);
    });
  }
  if (validation.corrections_applied) {
    view.append(node("p", "small muted section-block", `${validation.corrections_applied} correction attempt(s) applied.`));
  }
  return view;
}

function showStep(run, stepStatus, container) {
  const step = run.steps.find((item) => item.step_name === stepStatus.step_name);
  container.replaceChildren();
  const head = node("div", "detail-header");
  const names = node("div");
  append(names, node("div", "eyebrow", stepStatus.utility_type || "Utility"),
    node("h2", "", stepStatus.step_name),
    node("p", "small muted", `Decode: ${stepStatus.decode_status || "not run"} · Generation: ${stepStatus.generation_status || "not run"} · Validation: ${stepStatus.validation_status || "not run"}`));
  append(head, names, status(stepStatus.validation_status === "PASS" ? "pass" :
    stepStatus.validation_status === "REVIEW" ? "review" :
      stepStatus.decode_status === "skipped" ? "skipped" :
        stepStatus.validation_status === "not_run" ? "not_run" : "error"));
  container.append(head);
  const tabs = node("div", "tabs"); tabs.setAttribute("role", "tablist"); tabs.setAttribute("aria-label", "Step details");
  const panel = node("div"); panel.setAttribute("role", "tabpanel");
  const namesAndRenderers = [
    ["Business rule", () => detailRule(step)],
    ["JCL / SYSIN", () => detailJcl(step)],
    ["PySpark", () => { const view = node("div"); detailCode(run, stepStatus.step_name, view); return view; }],
    ["Validation", () => detailValidation(run, stepStatus.step_name)],
  ];
  function select(index) {
    [...tabs.children].forEach((tab, position) => {
      tab.classList.toggle("active", position === index);
      tab.setAttribute("aria-selected", String(position === index));
    });
    panel.replaceChildren(namesAndRenderers[index][1]());
  }
  namesAndRenderers.forEach(([label], index) => {
    const tab = node("button", "tab", label); tab.type = "button";
    tab.setAttribute("role", "tab"); tab.setAttribute("aria-selected", "false");
    tab.addEventListener("click", () => select(index)); tabs.append(tab);
  });
  append(container, tabs, panel); select(0);
}

function parseLineage(source) {
  const labels = new Map();
  for (const match of source.matchAll(/^\s*(DS_[A-Za-z0-9_]+)\[\("([^"]+)"\)\]/gm)) labels.set(match[1], match[2]);
  const edges = [];
  for (const match of source.matchAll(/^\s*([A-Za-z0-9_]+)\s+-->\s+([A-Za-z0-9_]+)\s*$/gm)) edges.push([match[1], match[2]]);
  return { labels, edges };
}

async function showLineage(run, container) {
  container.append(node("p", "small muted", "Loading lineage…"));
  if (!run.artifacts.includes("diagram")) {
    container.replaceChildren(notice("No lineage artifact was produced for this run.", "warn")); return;
  }
  try {
    const response = await fetch(`/runs/${run.run_id}/artifacts/diagram`);
    if (!response.ok) throw new Error("Lineage source is unavailable.");
    const source = await response.text();
    const { labels, edges } = parseLineage(source);
    container.replaceChildren();
    if (!edges.length) container.append(notice("No dataset edges were recorded in the lineage source.", "warn"));
    run.step_statuses.forEach((step) => {
      const inputs = edges.filter((edge) => edge[1] === step.step_name).map((edge) => labels.get(edge[0]) || edge[0]);
      const outputs = edges.filter((edge) => edge[0] === step.step_name).map((edge) => labels.get(edge[1]) || edge[1]);
      if (!inputs.length && !outputs.length) return;
      const row = node("div", "lineage-row");
      const left = node("div", "lineage-side");
      append(left, datasetTags(inputs));
      const mid = node("div", "lineage-step", `${step.step_name} →`);
      const right = node("div", "lineage-side out");
      append(right, datasetTags(outputs));
      append(row, left, mid, right); container.append(row);
    });
    const details = node("details", "section-block");
    append(details, node("summary", "small", "View Mermaid source"), node("pre", "code-block", source));
    container.append(details);
  } catch (error) { container.replaceChildren(notice(error.message, "error")); }
}

async function showRun(runId) {
  clear(); app.append(node("p", "muted", "Loading run…"));
  let run;
  try { run = await jsonRequest(`/api/runs/${runId}`); }
  catch (error) {
    clear(); append(app, node("h1", "", "Run not found"), notice(error.message, "error"),
      node("p", "", "Check the run ID or return to recent runs."), action("Recent runs", "/#recent")); return;
  }
  clear();
  const header = node("div", "report-header");
  const heading = node("div");
  append(heading, node("div", "eyebrow", "Conversion report"), node("h1", "", run.job_name));
  const meta = node("div", "report-meta");
  const copy = node("button", "button subtle small", `${run.run_id.slice(0, 8)}… · Copy ID`);
  copy.type = "button"; copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(run.run_id); copy.textContent = "Copied run ID"; }
    catch { copy.textContent = run.run_id; }
  });
  const generated = run.code_steps.length;
  append(meta, status(run.status));
  if (run.review_required) meta.append(status("review"));
  append(meta, node("span", "", `${run.step_statuses.length} steps`),
    node("span", "dot", "·"), node("span", "", `${generated} generated`),
    node("span", "dot", "·"), copy);
  heading.append(meta);
  const actions = node("div", "report-actions");
  actions.append(action("New conversion", "/"));
  if (run.artifacts.includes("spec")) actions.append(action("Download migration spec", `/runs/${run.run_id}/artifacts/spec`, true));
  append(header, heading, actions); app.append(header);

  const attention = node("section", "surface pad attention");
  attention.append(node("h2", "", "Run assessment"));
  const skipped = run.step_statuses.filter((step) => step.decode_status === "skipped").map((step) => step.step_name);
  if (run.errors.length || run.warnings.length || run.manual_steps.length || run.review_steps.length || skipped.length || run.review_required) {
    const messages = node("ul", "attention-list");
    run.errors.forEach((item) => messages.append(node("li", "error-text", item)));
    run.warnings.forEach((item) => messages.append(node("li", "", item)));
    if (run.manual_steps.length) messages.append(node("li", "", `Manual migration tasks: ${run.manual_steps.join(", ")}.`));
    if (run.review_steps.length) messages.append(node("li", "", `Human review required: ${run.review_steps.join(", ")}.`));
    else if (run.review_required) messages.append(node("li", "", "Human review is required for generated code."));
    if (skipped.length) messages.append(node("li", "", `Steps without generated implementations: ${skipped.join(", ")}.`));
    attention.append(messages);
  } else attention.append(node("p", "", "No errors or warnings were recorded."));
  if (run.cross_step_validation?.issues?.length) {
    const cross = node("div", "section-block");
    cross.append(node("h3", "", `Cross-step contracts · ${run.cross_step_validation.status || "not recorded"}`));
    const list = node("ul", "attention-list");
    run.cross_step_validation.issues.forEach((issue) => list.append(node("li", "", issue.message || "Issue recorded")));
    cross.append(list); attention.append(cross);
  }
  const verification = run.runtime_verified === true ?
    "Spark runtime verification is recorded for this run." :
    run.runtime_verified === false ?
      "Generated PySpark has not been run against a configured Spark environment. Review it and test with representative data before use." :
      "Spark runtime verification was not recorded for this saved run. Review and test generated code before use.";
  attention.append(node("p", "small muted section-block", verification));
  app.append(attention);

  const grid = node("div", "report-grid");
  const list = node("aside", "surface step-list"); list.append(node("h2", "", "Steps"));
  const detail = node("section", "surface detail");
  if (run.step_statuses.length) {
    const buttons = [];
    run.step_statuses.forEach((stepStatus, index) => {
      const button = node("button", "step-button"); button.type = "button";
      const left = node("span");
      append(left, node("span", "step-id", stepStatus.step_name),
        node("span", "step-util", stepStatus.utility_type || "Unknown"));
      const label = stepStatus.validation_status === "PASS" ? "pass" :
        stepStatus.validation_status === "REVIEW" ? "review" :
          stepStatus.decode_status === "skipped" ? "skipped" :
            stepStatus.validation_status === "not_run" ? "not_run" : "error";
      append(button, left, status(label));
      button.addEventListener("click", () => {
        buttons.forEach((item) => item.classList.remove("active"));
        button.classList.add("active"); showStep(run, stepStatus, detail);
      });
      buttons.push(button); list.append(button);
      if (index === 0) { button.classList.add("active"); showStep(run, stepStatus, detail); }
    });
  } else detail.append(notice("This run stopped before step details were available.", "warn"));
  append(grid, list, detail); app.append(grid);

  const bottom = node("div", "bottom-grid");
  const lineageCard = node("section", "surface lineage");
  append(lineageCard, node("h2", "", "Data lineage"));
  const lineageBody = node("div"); lineageCard.append(lineageBody);
  const artifactCard = node("section", "surface pad");
  append(artifactCard, node("h2", "", "Artifacts"));
  const artifacts = node("ul", "artifact-list");
  run.artifacts.forEach((kind) => {
    const item = node("li");
    const [label, description] = artifactLabels[kind] || [kind, "File"];
    append(item, action(label, `/runs/${run.run_id}/artifacts/${kind}`), node("span", "", description));
    item.firstChild.className = "";
    artifacts.append(item);
  });
  if (!run.artifacts.length) artifacts.append(node("li", "muted", "No artifacts were saved."));
  artifactCard.append(artifacts);
  append(bottom, lineageCard, artifactCard); app.append(bottom);
  showLineage(run, lineageBody);
}

const parts = location.pathname.split("/").filter(Boolean);
if (parts[0] === "tasks" && parts[1]) showTask(parts[1]);
else if (parts[0] === "runs" && parts[1]) showRun(parts[1]);
else showHome();
