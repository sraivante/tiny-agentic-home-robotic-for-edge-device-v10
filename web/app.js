"use strict";
const $ = id => document.getElementById(id);
const state = {bootstrap: null, selected: new Set(), busy: false, rows: [], example: null, stopDemo: false};
const pretty = value => JSON.stringify(value, null, 2);

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function option(value, text) { const item = node("option", "", text); item.value = value; return item; }
function showError(message) { $("notice").textContent = message; $("notice").hidden = !message; }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json", "X-Lab-Token": state.bootstrap.token},
    body: JSON.stringify(body)
  });
  let data;
  try { data = await response.json(); } catch { throw new Error(`Server returned ${response.status}. Check the terminal running the lab.`); }
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function selectedModels() { return state.bootstrap.models.filter(model => state.selected.has(model.id)); }
function updateControls() {
  $("selection-count").textContent = `${state.selected.size} selected`;
  $("run").disabled = $("predict-only").disabled = state.busy || !state.selected.size || !$("command").value.trim();
  $("select-all").disabled = state.busy;
  $("select-all").textContent = state.selected.size === state.bootstrap.models.length ? "Clear selection" : "Select all";
  document.querySelectorAll(".model-card input").forEach(input => input.disabled = state.busy);
  ["category", "source-model", "action-search", "action", "example", "command"].forEach(id => $(id).disabled = state.busy);
  $("export").disabled = state.busy || !state.rows.length;
  document.querySelectorAll("[data-prompt]").forEach(button => button.disabled = state.busy);
  $("demo").disabled = state.busy || !state.selected.size;
  $("target-window").disabled = $("refresh-windows").disabled = state.busy;
}
function renderModels() {
  state.bootstrap.models.forEach((model, index) => {
    const card = node("label", "model-card");
    const top = node("div", "model-card-top");
    top.append(node("span", "model-type", model.kind || model.architecture));
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox"; checkbox.value = model.id; checkbox.checked = state.selected.has(model.id);
    checkbox.setAttribute("aria-label", `Select ${model.label}`);
    checkbox.addEventListener("change", () => { checkbox.checked ? state.selected.add(model.id) : state.selected.delete(model.id); updateControls(); });
    top.append(checkbox); card.append(top, node("h3", "", model.label), node("p", "model-arch", model.architecture));
    const stats = node("div", "model-stats");
    const size = node("span"); size.append(node("strong", "", `${(model.bytes / 1e6).toFixed(2)} MB`));
    const score = node("span"); score.append(node("strong", "", `${(model.group_dev_exact * 100).toFixed(2)}%`), " dev exact");
    stats.append(size, score); card.append(stats);
    card.append(node("p", "model-note", `${model.training_rows.toLocaleString()} fitting rows${model.epoch ? ` · epoch ${model.epoch}` : ""}`));
    $("models").append(card);
    $("source-model").append(option(model.id, model.label));
  });
  $("source-model").value = state.bootstrap.models.find(model => model.default).id;
}
function populateActions(preferred) {
  const previous = preferred || $("action").value;
  const query = $("action-search").value.toLowerCase().trim();
  const category = $("category").value;
  const actions = state.bootstrap.catalog.filter(action => (!category || action.category === category) &&
    (!query || `${action.action} ${action.desc}`.toLowerCase().includes(query)));
  $("action").replaceChildren();
  for (const name of state.bootstrap.categories) {
    const members = actions.filter(action => action.category === name);
    if (!members.length) continue;
    const group = node("optgroup"); group.label = name;
    members.forEach(action => group.append(option(action.action, `${action.action} — ${action.desc}`)));
    $("action").append(group);
  }
  $("filtered-count").textContent = `${actions.length} available to test`;
  if (actions.some(action => action.action === previous)) $("action").value = previous;
  populateExamples();
}
function populateExamples() {
  const action = state.bootstrap.catalog.find(row => row.action === $("action").value);
  $("example").replaceChildren();
  state.example = null;
  if (!action) {
    $("availability").textContent = "No matching actions. You can still enter a custom command.";
    $("example-source").textContent = ""; updateEditor(); return;
  }
  $("availability").className = `availability${action.live_available ? "" : " unavailable"}`;
  $("availability").textContent = action.live_available ? "● Live adapter available on this device" : `○ ${action.availability_reason}`;
  const model = state.bootstrap.models.find(row => row.id === $("source-model").value);
  const rows = state.bootstrap.library[model.dataset_id][action.action] || [];
  rows.forEach((row, index) => {
    const label = row.lang === "hi" ? "HI" : row.lang === "en" ? "EN" : "MIX";
    $("example").append(option(String(index), `[${label}] ${row.text}`));
  });
  loadExample();
}
function loadExample() {
  const model = state.bootstrap.models.find(row => row.id === $("source-model").value);
  const rows = state.bootstrap.library[model.dataset_id][$("action").value] || [];
  state.example = rows[Number($("example").value)] || null;
  if (state.example) {
    $("command").value = state.example.text;
    $("example-source").textContent = `${state.example.source}${state.example.line ? ` · line ${state.example.line}` : ""}`;
  }
  updateEditor();
}
function updateEditor() {
  const value = $("command").value;
  $("character-count").textContent = `${value.length} / 256`;
  const same = state.example && value === state.example.text;
  $("expected").textContent = same ? `Dataset target: ${state.example.target || state.example.action}` : "Custom command · the model decides the action and arguments";
  updateControls();
}
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  return value;
}
function sameTarget(a, b) { return JSON.stringify(canonical([a.action, a.args])) === JSON.stringify(canonical([b.action, b.args])); }
function makeStage(title, meta, className = "") {
  const stage = node("div", `result-stage ${className}`);
  const label = node("div", "stage-label", title); if (meta) label.append(node("span", "", meta));
  stage.append(label); return stage;
}
function renderResult(record) {
  const card = record.card || node("article", "result-card");
  if (!record.card) { record.card = card; $("results").append(card); }
  card.replaceChildren();
  const head = node("div", "result-head");
  head.append(node("h3", "", record.model.label), node("span", `status ${record.status}`, record.status)); card.append(head);
  const body = node("div", "result-body"); body.append(node("p", "run-text", `“${record.text}”`)); card.append(body);
  if (!record.data) { body.append(node("p", "effect", record.error || "Loading model and running inference…")); return; }
  const {prediction, plan, plan_error, load_ms} = record.data;
  const output = makeStage("1 / Model output", `${prediction.latency_ms} ms inference`);
  output.append(node("div", "prediction-action", prediction.action));
  output.append(node("div", "metrics-line", `${(prediction.confidence * 100).toFixed(1)}% confidence · epoch ${prediction.model_epoch} · ${load_ms} ms load/cache`));
  output.append(node("pre", "", pretty(prediction.args)));
  if (record.expected) {
    const matches = sameTarget(prediction, record.expected);
    output.append(node("div", `metrics-line ${matches ? "match" : "mismatch"}`, matches ? "✓ Exact match with dataset target" : `≠ Dataset target: ${record.expected.target || record.expected.action}`));
  }
  const details = node("details"); details.append(node("summary", "", "Full model output"), node("pre", "", pretty(prediction))); output.append(details); body.append(output);
  const planned = makeStage("2 / Commands to execute", plan ? plan.risk : "");
  if (plan) {
    planned.append(node("p", "effect", plan.effect));
    plan.commands.forEach(command => {
      planned.append(node("pre", "", command.display));
      if (command.environment && Object.keys(command.environment).length) planned.append(node("pre", "", pretty(command.environment)));
    });
    planned.append(node("p", "effect", `Working folder: ${plan.files_root}`));
    if (!plan.live_available) planned.append(node("p", "effect", plan.availability_reason));
  } else planned.append(node("p", "effect", plan_error || prediction.validation_errors.join("; ") || "No executable plan was produced."));
  body.append(planned);
  const executed = makeStage("3 / Execution result", "", "execution");
  if (record.execution) {
    executed.append(node("pre", "", pretty(record.execution.result ?? record.execution.error ?? record.execution)));
    executed.append(node("p", "effect", `${record.execution.executed === null ? "Execution status unknown" : record.execution.executed ? "Executor invoked" : "Not executed"}${record.execution.duration_ms !== undefined ? ` · ${record.execution.duration_ms} ms` : ""}`));
    const trace = node("details"); trace.append(node("summary", "", "Execution details"), node("pre", "", pretty(record.execution))); executed.append(trace);
    const capture = record.execution.desktop || record.execution.result;
    if (capture && capture.screenshot) {
      const shot = node("img", "action-capture"); shot.src = capture.screenshot;
      shot.alt = `Desktop after ${prediction.action}${capture.target ? `: ${capture.target.title}` : ""}`;
      const link = node("a"); link.href = capture.screenshot; link.target = "_blank"; link.rel = "noopener"; link.append(shot);
      executed.append(link, node("p", "capture-caption", capture.target ? `Captured target: ${capture.target.title}` : "Desktop capture"));
    }
  } else executed.append(node("p", "effect", record.message || "Preparing automatic execution…"));
  body.append(executed);
}
// Two animation frames let the output and plan paint before execution is requested.
const painted = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
async function run(execute) {
  if (state.busy || !state.selected.size || !$("command").value.trim()) return;
  showError(""); state.busy = true; state.rows = []; document.body.classList.add("busy");
  $("results").replaceChildren(); $("empty-state").hidden = true;
  const text = $("command").value;
  const expected = state.example && text === state.example.text ? state.example : null;
  const models = selectedModels(); updateControls();
  try {
    for (let i = 0; i < models.length; i++) {
      const record = {model: models[i], text, expected, status: "predicting", mode: execute ? "live" : "predict"};
      state.rows.push(record); renderResult(record);
      $("progress").textContent = `${i + 1} / ${models.length} · ${record.model.label} is predicting…`;
      try {
        record.data = await api("/api/predict", {model_id: record.model.id, text});
        if (!execute) { record.status = "predicted"; record.message = "Prediction only. No command was executed."; }
        else if (!record.data.ticket_id) {
          record.status = record.data.prediction.validation_errors.length || record.data.plan_error ? "invalid" : "unavailable";
          record.message = "Not executed. See the model validation or adapter availability above.";
        } else { record.status = "executing"; record.message = "The plan is displayed. Executing automatically…"; }
        renderResult(record); await painted();
        if (execute && record.data.ticket_id) {
          $("progress").textContent = `${i + 1} / ${models.length} · ${record.model.label} is executing ${record.data.prediction.action}…`;
          try {
            record.execution = await api("/api/execute", {ticket_id: record.data.ticket_id});
            record.status = record.execution.status;
          } catch (error) {
            record.status = "error"; record.message = `Execution response unavailable: ${error.message}. The command may have run. It has not been retried.`;
          }
          renderResult(record);
        }
      } catch (error) { record.status = "error"; record.error = error.message; renderResult(record); }
    }
    const completed = state.rows.filter(row => row.status === "completed").length;
    const issues = state.rows.filter(row => ["failed", "error", "invalid", "unavailable"].includes(row.status)).length;
    $("progress").textContent = execute ? `Finished · ${completed} executed successfully · ${issues} unavailable or failed` : `Finished · ${models.length} models tested · prediction only`;
  } finally { state.busy = false; document.body.classList.remove("busy"); updateControls(); await refreshWindows(); }
}

async function refreshWindows() {
  try {
    const data = await api("/api/desktop");
    $("target-window").replaceChildren(option("auto", "Auto · next app or file opened"));
    data.windows.forEach(window => $("target-window").append(option(window.id, window.title)));
    if (data.target && data.windows.some(window => window.id === data.target.id)) $("target-window").value = data.target.id;
    $("target-status").textContent = data.target ? `Input will go to: ${data.target.title}` : "Open an app or file to set the keyboard target automatically.";
  } catch (error) { showError(error.message); }
}

async function runDemo() {
  if (state.busy || !state.selected.size) return;
  state.busy = true; state.stopDemo = false; state.rows = []; showError("");
  document.body.classList.add("busy"); $("results").replaceChildren(); $("empty-state").hidden = true;
  $("stop-demo").hidden = false; $("stop-demo").disabled = false; updateControls();
  try {
    const demo = await api("/api/demo");
    for (const model of selectedModels()) {
      if (state.stopDemo) break;
      const steps = demo.sequences[model.id];
      for (let index = 0; index < steps.length; index++) {
        if (state.stopDemo) break;
        const step = steps[index];
        $("command").value = step.text;
        $("character-count").textContent = `${step.text.length} / 256`;
        $("progress").textContent = `${model.label} · demo step ${index + 1} / ${steps.length} · ${step.action}`;
        const record = {model, text: step.text, expected: step, status: "predicting", mode: "visible-demo"};
        state.rows.push(record); renderResult(record);
        try {
          record.data = await api("/api/predict", {model_id: model.id, text: step.text});
          if (!sameTarget(record.data.prediction, step)) {
            record.status = "mismatch"; record.message = "Demo stopped for this model: its prediction differs from this step. The displayed prediction was not replaced or executed.";
            renderResult(record); break;
          }
          if (!record.data.ticket_id) {
            record.status = "unavailable"; record.message = "Demo stopped: see the executor requirement above."; renderResult(record); break;
          }
          record.status = "executing"; record.message = "Executing the model output automatically…";
          renderResult(record); record.card.scrollIntoView({behavior: "smooth", block: "nearest"}); await painted();
          record.execution = await api("/api/execute", {ticket_id: record.data.ticket_id});
          record.status = record.execution.status;
          if (step.action === "read_file" && record.status === "completed") {
            const text = steps.find(row => row.action === "type_text").args.text;
            const content = record.execution.result;
            const actual = typeof content === "string" ? content : content.text ?? content.content;
            if (actual?.trim() === text) {
              record.execution.verification = "PASS: saved file contains the exact text predicted by the model.";
              record.execution.result = {saved_content: actual, verification: record.execution.verification};
            } else {
              record.status = "failed"; record.execution.error = "Saved file content differs from the typed text.";
              record.execution.result = {saved_content: content, expected: text, verification: "FAILED"};
            }
          }
          renderResult(record);
          if (record.status !== "completed") break;
          // A short, visible pause separates each agent action. No confirmation.
          await new Promise(resolve => setTimeout(resolve, 850));
        } catch (error) {
          record.status = "error"; record.error = error.message; record.message = error.message;
          renderResult(record); break;
        }
      }
    }
    const verified = state.rows.filter(row => row.execution?.verification).length;
    $("progress").textContent = `${state.stopDemo ? "Stopped" : "Demo finished"} · ${verified} / ${selectedModels().length} models completed and saved content verified`;
  } catch (error) { showError(error.message); }
  finally {
    state.busy = false; $("stop-demo").hidden = true;
    document.body.classList.remove("busy"); updateControls(); await refreshWindows();
  }
}
async function init() {
  try {
    state.bootstrap = await api("/api/bootstrap");
    state.bootstrap.models.filter(model => model.default).forEach(model => state.selected.add(model.id));
    $("connection").textContent = `${state.bootstrap.platform === "windows" ? "Windows" : "Linux / Pi"} · connected`;
    renderModels();
    state.bootstrap.categories.forEach(category => $("category").append(option(category, category)));
    $("action-total").textContent = `${state.bootstrap.catalog.length} actions`;
    $("coverage").textContent = `${state.bootstrap.models.length} models · ${state.bootstrap.categories.length} categories · ${state.bootstrap.implemented_adapters} executors · ${state.bootstrap.live_adapters} ready on this device · risk gate: up to ${state.bootstrap.allow_risk}`;
    $("executor-summary").textContent = `${state.bootstrap.implemented_adapters} / ${state.bootstrap.catalog.length} actions have executors. ${state.bootstrap.live_adapters} pass the platform and installed dependency checks here. Runtime permissions, credentials and hardware are checked when invoked.`;
    for (const action of state.bootstrap.catalog) {
      const row = node("div", "coverage-row");
      row.append(node("strong", "", action.action), node("span", "", action.live_available ? "Ready" : action.availability_reason));
      $("executor-coverage").append(row);
    }
    $("files-root").textContent = `Test files: ${state.bootstrap.files_root} / <model-id>`;
    $("dataset-details").textContent = "Union Command v10 was trained on 1,143,182 synthetic fitting rows (frozen corrected v10 split + v9 supplement). The examples in this lab are up to 6 fitting-data samples per action; the full datasets are not published.";
    populateActions("get_time"); $("command").value = "what time is it"; updateEditor();
    $("category").addEventListener("change", () => populateActions());
    $("action-search").addEventListener("input", () => populateActions());
    $("action").addEventListener("change", populateExamples);
    $("source-model").addEventListener("change", populateExamples);
    $("example").addEventListener("change", loadExample);
    $("command").addEventListener("input", updateEditor);
    $("run").addEventListener("click", () => run(true));
    $("predict-only").addEventListener("click", () => run(false));
    $("demo").addEventListener("click", runDemo);
    $("stop-demo").addEventListener("click", () => { state.stopDemo = true; $("stop-demo").disabled = true; });
    $("refresh-windows").addEventListener("click", refreshWindows);
    $("target-window").addEventListener("change", async () => {
      try { await api("/api/desktop/target", {window_id: $("target-window").value}); await refreshWindows(); }
      catch (error) { showError(error.message); }
    });
    await refreshWindows();
    $("select-all").addEventListener("click", () => {
      const all = state.selected.size !== state.bootstrap.models.length;
      state.selected = new Set(all ? state.bootstrap.models.map(model => model.id) : []);
      document.querySelectorAll(".model-card input").forEach(input => input.checked = all); updateControls();
    });
    document.querySelectorAll("[data-prompt]").forEach(button => button.addEventListener("click", () => {
      $("command").value = button.dataset.prompt; updateEditor(); $("command").focus();
    }));
    document.addEventListener("keydown", event => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); run(true); }
    });
    $("export").addEventListener("click", () => {
      const records = state.rows.map(({card, ...row}) => row);
      const blob = new Blob([pretty({created_at: new Date().toISOString(), platform: state.bootstrap.platform, runs: records})], {type: "application/json"});
      const url = URL.createObjectURL(blob); const link = node("a"); link.href = url; link.download = `command-lab-${Date.now()}.json`;
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  } catch (error) { $("connection").textContent = "Connection failed"; showError(error.message); }
}
init();
