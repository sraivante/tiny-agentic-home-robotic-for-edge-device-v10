"""Regenerate README.md and docs/index.html from the measured JSON results.

Inputs (all in this repository):
  test_results/laptop_cpu/{evaluation,benchmark}.json
  test_results/rpi5/{evaluation,benchmark}.json          (optional)
  examples/category_examples_{windows,pi}.json            (optional Pi file)

    python scripts/build_docs.py
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from categories import category_for  # noqa: E402

HF_REPO = "sraivante/union-command-minilm-v10"
HF_URL = f"https://huggingface.co/{HF_REPO}"
GH_REPO = "sraivante/union-command-minilm-v10"
GH_URL = f"https://github.com/{GH_REPO}"
PAGES_URL = "https://sraivante.github.io/union-command-minilm-v10/"
ZIP_URL = f"{GH_URL}/archive/refs/heads/main.zip"
WEIGHTS_URL = f"{HF_URL}/resolve/main/models/a100_minilm_v10_quoted/best.pt"
SHA256 = "c91d44e4a687152cd65d86d56cfc0e453a60a1afe14f8c395e8af058f2fc4d5b"

CATEGORY_ORDER = ["Audio & media", "Display & appearance", "Network & connectivity", "GPIO & I2C",
                  "Hardware & Raspberry Pi", "Files & storage", "Apps & windows", "Browser & web",
                  "Keyboard & clipboard", "Timers & productivity", "Security & accounts",
                  "Services & processes", "Software & development", "AI & models", "Terminal & sessions",
                  "System & power", "Help & intent handling"]
LANG_NAMES = {"en": "English", "hi": "Hinglish (Roman, incl. small Devanagari slice)", "mix": "Code-mixed",
              "None": "Unlabelled (project A rows, mostly English/Hinglish)"}
FILE_NAMES = {"union_test.jsonl": "Held-out test (unseen templates)", "A_practical-dev.jsonl": "Practical dev (A)",
              "B_golden_dev.jsonl": "Golden dev (B)"}
FILE_NOTES = {"union_test.jsonl": "24,498 rows · all 371 actions · template families never seen in training",
              "A_practical-dev.jsonl": "1,321 realistic phrasings · used during development",
              "B_golden_dev.jsonl": "383 hand-checked commands · used during development"}
# Development results measured earlier on Windows CPU (same checkpoint, original labels).
DEV = [("Grouped dev (reserved template groups)", 30596, 31707), ("Validation", 23467, 23892)]


def load(path):
    path = ROOT / path
    return json.loads(path.read_text("utf-8")) if path.exists() else None


def pct(value, digits=2):
    return f"{value * 100:.{digits}f}%"


def scrub(text):
    return re.sub(r"[A-Za-z]:\\+Users\\+[^\\\"']+", "%USERPROFILE%", str(text))


def short_reason(reason):
    reason = reason or ""
    if reason.startswith("Executor ready"):
        return "ready"
    rules = [(r"Executor requires pi", "Pi only"), (r"Executor requires windows", "Windows only"),
             (r"Install optional Python module\(s\): (.+)", r"needs \1"),
             (r"Install required program\(s\): (.+)", r"needs \1"),
             (r"Required program is unavailable: (.+)", r"needs \1"),
             (r"Configure executor_config\.json.*", "needs executor_config.json"),
             (r"Path does not exist.*", "needs an existing file")]
    for pattern, replacement in rules:
        match = re.match(pattern, reason)
        if match:
            return match.expand(replacement) if "\\1" in replacement else replacement
    return reason[:60]


def display_result(row):
    """Only publish outputs that cannot reveal personal/device details."""
    if not row or not row.get("executed"):
        return None
    if "published_result" in row:
        return row["published_result"]
    status, output, action = row.get("execution_status"), row.get("execution_output", ""), row["action"]
    if status != "completed":
        low = output.lower()
        if "access to a cim resource" in low or "access denied" in low:
            return "failed: needs administrator rights"
        if "cannot find any service" in low:
            return "failed: no such service on this OS"
        return "failed: " + scrub(output)[:70]
    try:
        value = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        value = output
    if action == "calculate" and isinstance(value, dict):
        return f"result = {value.get('value')}"
    if action == "get_cpu_usage" and isinstance(value, dict):
        return f"CPU {value.get('percent')} %"
    if action == "get_disk_space" and isinstance(value, dict):
        return f"disk {value.get('percent')} % used"
    if action == "help" and isinstance(value, dict):
        return f"{value.get('actions')} actions, {value.get('live_adapters')} live adapters here"
    if action == "clarify":
        return "asks the user for the missing detail"
    if action in ("get_temperature", "get_fan_speed"):
        text = value.get("stdout", value) if isinstance(value, dict) else value
        text = " ".join(str(text).split())
        return (text[:60] or "completed") if re.search(r"\d", text) else "completed (no sensor value reported)"
    return "completed (output not published: local device details)"


def tool_text(row):
    if not row or "executor_tool" not in row:
        return None
    if row["executor_tool"].startswith("native process"):
        return row.get("planned_command", "")
    module = row.get("handler_module") or ""
    return f"Python adapter {module.replace('.', '/')}.py: {row.get('effect', '')}"


def tool_short(row):
    """Compact tool label for tables."""
    if not row or "executor_tool" not in row:
        return "—"
    if row["executor_tool"].startswith("native process"):
        command = row.get("planned_command", "")
        if command.startswith("powershell:"):
            script = command[len("powershell:"):].strip()
            return "PowerShell: " + (script[:70] + "…" if len(script) > 70 else script)
        return command[:90]
    module = (row.get("handler_module") or "").replace(".", "/") + ".py"
    effect = (row.get("effect") or "").rstrip(".")
    effect = effect if len(effect) <= 60 else effect[:59] + "…"
    return f"Python adapter {module}: {effect}"


def pair_examples():
    win = load("examples/category_examples_windows.json") or {"rows": []}
    pi = load("examples/category_examples_pi.json") or {"rows": []}
    pi_rows = {r["command"]: r for r in pi["rows"]}
    rows = []
    for w in win["rows"]:
        p = pi_rows.get(w["command"])
        rows.append({"category": w["intended_category"], "command": w["command"], "action": w["action"],
                     "args": w["args"], "confidence": w["confidence"], "win": w, "pi": p,
                     "gate": w.get("default_gate", "—")})
    return rows, win, pi


def output_text(row):
    return f"{row['action']} {json.dumps(row['args'], ensure_ascii=False)}"


def ready_text(side):
    if not side or "live_available" not in side:
        return "—"
    return "ready" if side["live_available"] else short_reason(side.get("availability_reason"))


# ---------------------------------------------------------------- markdown helpers
def md_table(header, rows):
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(esc(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def code(s):
    s = str(s)
    return f"`{s}`" if "`" not in s else s


def build(results):
    lap_eval, lap_bench, pi_eval, pi_bench = results
    examples, win, pi = pair_examples()
    primary = {}
    for row in examples:
        primary.setdefault(row["category"], row)

    # --- accuracy
    acc_rows = []
    for index, lap in enumerate(lap_eval["files"]):
        piv = next((f for f in (pi_eval or {}).get("files", []) if f["file"] == lap["file"]), None)
        acc_rows.append([FILE_NAMES.get(lap["file"], lap["file"]), f"{lap['rows']:,}",
                         f"{pct(lap['exact_accuracy'])} ({lap['exact_correct']:,})", pct(lap["action_accuracy"]),
                         f"{pct(piv['exact_accuracy'])} ({piv['exact_correct']:,})" if piv else PI_MISSING,
                         pct(piv["action_accuracy"]) if piv else "—", FILE_NOTES.get(lap["file"], "")])
    test = lap_eval["files"][0]
    lang_rows = [[LANG_NAMES.get(k, k), f"{v['rows']:,}", pct(v["exact_accuracy"]), pct(v["action_accuracy"])]
                 for k, v in sorted(test["by_language"].items(), key=lambda kv: -kv[1]["rows"])]
    cat_acc = sorted(test["by_category"].items(), key=lambda kv: -kv[1]["exact_accuracy"])
    cat_rows = [[k, f"{v['rows']:,}", pct(v["exact_accuracy"]), pct(v["action_accuracy"])] for k, v in cat_acc]
    confusions = [[c["expected"], c["predicted"], c["count"]] for c in test["top_action_confusions"][:8]]

    # --- speed
    def speed_rows(bench, name):
        if not bench:
            return [[name, "—", "not measured", "—", "—", "—", "—", "—"]]
        out = []
        for run in bench["runs"]:
            out.append([name, run["threads"], f"{run['latency_ms']['median']:.1f} ms", f"{run['latency_ms']['p95']:.1f} ms",
                        f"{run['single_request_per_second']:.0f}", f"{run['batched_commands_per_second']:.0f}",
                        f"{bench['load_seconds']:.1f} s", f"{bench['process_rss_mb']:.0f} MB"])
        return out
    speed = speed_rows(lap_bench, "Laptop i7-1360P") + speed_rows(pi_bench, "Raspberry Pi 5")
    partial = NOTES.get("measurements") if NOTES.get("status") == "partial" else None
    if partial and not pi_bench:
        warm = partial["warm_request_ms_2_threads"]
        speed[-1] = ["Raspberry Pi 5 (partial)", 2, f"~{sum(warm) / len(warm):.0f} ms ({len(warm)} warm requests)", "—", "—",
                     f"{partial['accuracy_run']['commands_per_second']} (4 threads, batch 64)",
                     f"{partial['model_load_seconds_2_threads']:.1f} s", "—"]
    eval_speed = [["Laptop i7-1360P", test["seconds"], test["rows_per_second"], lap_eval["device"]["threads"], lap_eval["batch_size"]]]
    if pi_eval:
        pt = pi_eval["files"][0]
        eval_speed.append(["Raspberry Pi 5", pt["seconds"], pt["rows_per_second"], pi_eval["device"]["threads"], pi_eval["batch_size"]])
    elif partial:
        run = partial["accuracy_run"]
        eval_speed.append(["Raspberry Pi 5 (partial)", f"{run['seconds']} for {run['rows_done']:,} of {run['rows_total']:,}",
                           run["commands_per_second"], run["threads"], run["batch_size"]])

    # --- temperature
    def temp_row(name, run, report):
        if not report:
            return [name, run, "not measured", "—", "—", "—", "—"]
        t = report.get("temperature", {})
        flags = report.get("throttled_after") or "n/a (not a Pi)"
        return [name, run, t.get("source", "—"), f"{t.get('start_c', '—')} °C", f"{t.get('max_c', '—')} °C",
                f"{t.get('mean_c', '—')} °C", flags]
    temps = [temp_row("Laptop i7-1360P", "Accuracy run (26k rows, 4 threads)", lap_eval),
             temp_row("Laptop i7-1360P", "Latency benchmark (1/2/4 threads)", lap_bench),
             temp_row("Raspberry Pi 5", "Accuracy run", pi_eval),
             temp_row("Raspberry Pi 5", "Latency benchmark", pi_bench)]
    if partial and not pi_eval:
        run = partial["accuracy_run"]
        temps[2] = ["Raspberry Pi 5", f"Accuracy run, partial ({run['threads']} threads)", "vcgencmd SoC sensor (spot readings)",
                    f"{partial['idle_soc_temperature_c']} °C (idle)", f"{run['soc_temperature_c_at_that_point']} °C after {run['seconds']} s",
                    "—", f"{partial['throttled_flags_at_start']} before; board went offline"]
    if partial and not pi_bench:
        temps = temps[:3]

    # --- categories
    cat_table = []
    for category in CATEGORY_ORDER:
        row = primary.get(category)
        if not row:
            continue
        result = display_result(row["pi"]) or display_result(row["win"]) or ("blocked by default gate" if row["gate"].startswith("blocked") else "plan only (not executed in this demo)")
        cat_table.append([category, row["command"], output_text(row), tool_short(row["win"]) + f" [{ready_text(row['win'])}]",
                          (tool_short(row["pi"]) + f" [{ready_text(row['pi'])}]") if row["pi"] else ("not measured (Pi went offline)" if NOTES.get("status") == "partial" else "not measured"),
                          row["gate"].split(" (")[0], result])
    all_examples = [[row["category"], row["command"], output_text(row), f"{row['confidence']:.0%}",
                     "✓" if row.get("expected_ok", True) else "✗"] for row in examples]
    return dict(acc_rows=acc_rows, lang_rows=lang_rows, cat_rows=cat_rows, confusions=confusions, speed=speed,
                eval_speed=eval_speed, temps=temps, cat_table=cat_table, examples=examples, test=test,
                lap_eval=lap_eval, lap_bench=lap_bench, pi_eval=pi_eval, pi_bench=pi_bench, win=win, pi=pi,
                all_examples=all_examples)


def expected_labels():
    path = ROOT / "examples/sample_commands.jsonl"
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    return {r["text"]: (r["action"], r["args"]) for r in rows}


def mark_examples(data):
    expected = expected_labels()
    wrong = []
    for row in data["examples"]:
        want = expected.get(row["command"])
        ok = want is not None and want[0] == row["action"] and want[1] == row["args"]
        row["expected_ok"] = ok
        if not ok:
            wrong.append((row, want))
    data["all_examples"] = [[r["category"], r["command"], output_text(r), f"{r['confidence']:.0%}",
                             "✓" if r["expected_ok"] else "✗ (see note)"] for r in data["examples"]]
    data["wrong"] = wrong
    return data


# ---------------------------------------------------------------- README
def readme(d):
    lap, pi_eval, pi_bench = d["lap_eval"], d["pi_eval"], d["pi_bench"]
    test = d["test"]
    lap2 = next(r for r in d["lap_bench"]["runs"] if r["threads"] == 2)
    pi2 = next((r for r in pi_bench["runs"] if r["threads"] == 2), None) if pi_bench else None
    pi_test = pi_eval["files"][0] if pi_eval else None
    wrong_note = "\n".join(f"- `{r['command']}` gave `{output_text(r)}`; expected `{w[0]} {json.dumps(w[1], ensure_ascii=False)}`."
                           for r, w in d["wrong"]) or "- None in this sample."
    pi_speed_line = (f"**{pi2['latency_ms']['median']:.0f} ms** median per command on a Raspberry Pi 5 (2 threads)"
                     if pi2 else (f"about **{sum(NOTES['measurements']['warm_request_ms_2_threads']) / 2:.0f} ms** on a Raspberry Pi 5 (2 threads, partial run, see below)"
                                  if NOTES.get("status") == "partial" else "Raspberry Pi 5 latency: see the Pi section below"))
    pi_acc = (f"{pct(pi_test['exact_accuracy'])} on the Pi (identical predictions)" if pi_test else "Pi run: see below")
    s = []
    s.append(f"""# Union Command v10: offline Hinglish/English command parser for laptop and Raspberry Pi agents

**Union Command v10** turns one short natural-language command into a structured, typed function call that an
agent can execute. It understands English, Hinglish (Roman-script Hindi-English) and a little Devanagari Hindi,
and covers **371 actions** in **17 categories**: audio, display, Wi-Fi, files, apps, browser, timers, services,
Docker, local LLMs, Raspberry Pi GPIO/I2C and more.

```text
"volume 40 kar do"          ->  set_volume   {{"value": 40}}
"gpio 17 ko high karo"      ->  gpio_on      {{"pin": 17}}
"10 min baad shutdown kar dena" -> shutdown  {{"amount": 10, "unit": "min"}}
```

| | |
|---|---|
| Model file | 96.2 MB, FP32 PyTorch, 23,889,849 parameters |
| Runs on | CPU only, fully offline after download (Windows, Linux, Raspberry Pi 5) |
| Held-out test accuracy | **{pct(test['exact_accuracy'])} exact** (action + every argument) on {test['rows']:,} unseen-template commands; {pct(test['action_accuracy'])} action-only |
| Speed | **{lap2['latency_ms']['median']:.1f} ms** median per command on a laptop i7-1360P (2 threads); {pi_speed_line} |
| Links | [GitHub code]({GH_URL}) · [HTML test guide]({PAGES_URL}) · [Hugging Face model]({HF_URL}) |

> **Executor notice.** The model only *parses* commands. The executor included in this repository is a **sample
> for testing, not a fully developed product**. It is there so you can see a parsed command turn into a real
> action on your own machine. By default it refuses every `critical` action (shutdown, delete, `run_command`,
> GPIO on/off, service changes, ...). Use it on a test machine and read the plan before you run anything.

## What it is for

The model is the "understanding" step of an on-device agent:
speech-to-text (for example Whisper) → **Union Command v10** → validator/risk gate → executor (tool call).
It replaces a large LLM for the common, well-defined device commands, so the reply is instant, private and free.

| Use case | Example command | Model output |
|---|---|---|
| Offline voice assistant for a laptop or home | `volume 40 kar do` | `set_volume {{"value": 40}}` |
| Raspberry Pi GPIO / I2C by voice or chat | `physical pin 11 ki value padho` | `gpio_read {{"numbering": "board", "pin": 11}}` |
| Device diagnostics chat-ops | `pi ka temperature batao` | `get_temperature {{}}` |
| Cheap first-stage router for an LLM agent | `docker containers list karo` | `docker_list {{}}`, and send `unknown`/low-confidence text to the LLM |
| Hands-free desktop and accessibility | `notepad kholo` | `open_app {{"app": "notepad"}}` |
| Edge DevOps | `nginx service restart karo` | `service_restart {{"service": "nginx"}}` |
| Credentials copied verbatim | `connect to wifi Redmi Note 12 password hello@123` | `connect_wifi {{"ssid": "Redmi Note 12", "password": "hello@123"}}` |
| Safe handling of vague or negated requests | `turn it off` / `shutdown mat karo` | `clarify {{}}` / `cancel_shutdown {{}}` |

Every prediction returns JSON with the action, typed arguments, a confidence score, the top-3 alternative actions and
schema validation errors:

```json
{{"action": "set_timer", "args": {{"amount": 5, "unit": "min"}}, "confidence": 0.9998,
  "alternatives": [{{"action": "set_timer", "score": 0.9998}}, ...], "validation_errors": [], "latency_ms": 6.1}}
```

A good agent pattern: execute only when `validation_errors` is empty and confidence is high, ask the user when the
action is `clarify`, and hand `unknown` or low-confidence text to a bigger model.

## Categories, sample commands, outputs and executor tools

One sample per category. "Model output" is the real prediction of v10. "Executor tool" is what the sample executor
would run for that output on each platform; the tag in brackets shows whether that adapter was ready on the test
machine. "Result" is shown only for read-only commands that were actually executed; other rows were planned, not run.

{md_table(["Category", "Sample command", "Model output", "Executor tool: Windows 11", "Executor tool: Raspberry Pi 5", "Default gate", "Result"], d["cat_table"])}

All {len(d['examples'])} illustrative commands (two per category) are in
[`examples/sample_commands.jsonl`](examples/sample_commands.jsonl), with per-platform executor plans in
[`examples/category_examples_windows.json`](examples/category_examples_windows.json)
and [`examples/category_examples_pi.json`](examples/category_examples_pi.json).
v10 got {sum(r['expected_ok'] for r in d['examples'])} of {len(d['examples'])} exactly right. The miss:

{wrong_note}

These commands were written for this demo, so they are illustrative. The accuracy numbers below come from the
held-out test set.

## Test results

### Accuracy

Metric: **exact match**, meaning the action and every typed argument must be identical to the label. Errors and
abstentions stay in the denominator. Same checkpoint on both devices: SHA-256 `{SHA256}`.

{md_table(["Dataset", "Rows", "Laptop CPU exact", "Laptop action-only", "Raspberry Pi 5 exact", "Pi action-only", "About the set"], d["acc_rows"])}

The held-out test split is made of whole template families that were never used for training. It was evaluated once
for this release. Practical dev and golden dev guided earlier development, so treat them as regression checks.
Earlier development measurements on the same checkpoint (Windows CPU):
{", ".join(f"{name} {pct(c / n)} ({c:,}/{n:,})" for name, c, n in DEV)}.

Held-out test by language:

{md_table(["Language", "Rows", "Exact", "Action-only"], d["lang_rows"])}

Held-out test by category:

{md_table(["Category", "Rows", "Exact", "Action-only"], d["cat_rows"])}

Most frequent action confusions on the held-out test:

{md_table(["Expected", "Predicted", "Count"], d["confusions"])}

### Speed

Warm, single-command latency over one command per catalog action (371 commands, 2 passes), then batched
throughput (batch 32). Includes Python and PyTorch overhead. "RAM" is the whole process after loading the model.

{md_table(["Device", "Threads", "Median", "p95", "Single commands/s", "Batched commands/s", "Model load", "RAM"], d["speed"])}

Full accuracy run speed (batched, held-out test):

{md_table(["Device", "Seconds", "Commands/s", "Threads", "Batch"], d["eval_speed"])}

### Temperature

{md_table(["Device", "Run", "Sensor", "Start", "Max", "Mean", "Pi throttle flags after run"], d["temps"])}

{PI_NOTES}

### Test devices

{md_table(["Device", "CPU", "OS", "Python / PyTorch"], DEVICE_ROWS)}

## Download and test

### 1. Get the code and the model

```bash
git clone {GH_URL}.git
cd union-command-minilm-v10
python download_model.py      # 96.2 MB from Hugging Face, SHA-256 verified
```

Or download the [ZIP of the repository]({ZIP_URL}). The model file alone is at
[{HF_REPO}]({WEIGHTS_URL}).

### 2. Install (Python 3.11 to 3.13, CPU only)

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\\Scripts\\activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

Raspberry Pi OS 64-bit / Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

### 3. Parse commands (nothing is executed)

```bash
python quickstart.py                                  # demo commands
python quickstart.py "wifi band karo" "pin 17 ki value padho"
python quickstart.py --json "5 minute ka timer lagao"
```

From Python:

```python
from tinyagent.runtime import Predictor
model = Predictor("models/a100_minilm_v10_quoted/best.pt", threads=2)
print(model.predict("volume 40 kar do"))
```

### 4. Reproduce the tests on your device

```bash
python scripts/evaluate.py --data examples/sample_commands.jsonl --output my_eval.json
python scripts/benchmark.py --threads 1,2,4 --output my_benchmark.json
```

`evaluate.py` accepts any JSONL file with `text`, `action` and `args`. The full training and test datasets are not
published; the measured reports are in [`test_results/`](test_results).

### 5. Try the sample executor lab (optional)

```bash
run.bat              # Windows
bash run.sh          # Linux / Raspberry Pi
```

The launcher installs what it needs into `.venv`, downloads the model if missing and opens
<http://127.0.0.1:8770>. Pick a category and an example (or type your own), then press **Predict only** to see the
parse, or **Run & execute** to let the sample executor act on this computer.

- It listens on 127.0.0.1 only and needs a per-session token for every request.
- File actions are confined to `runtime/files/`.
- `critical` actions are refused unless you start it with `--allow-risk critical`.
- Many adapters need extra tools or hardware (pycaw, pywinauto, pinctrl, i2c-tools, NetworkManager, Docker, Ollama).
  The UI shows which ones are ready. See [`executor_config.example.json`](executor_config.example.json) for the
  integrations that need your own settings.

The executor is a **sample for testing, not a finished product**. Adapter coverage, error handling and
security hardening are incomplete. Do not expose it to a network or run it unattended.

## How the model works

- **Inputs:** one command, up to 256 characters.
- **Encoders:** a character-level Transformer (width 128, 3 layers) for typos and code-mixing, plus the
  MiniLM-L6 word encoder from `sentence-transformers/all-MiniLM-L6-v2` (fine-tuned), fused per character.
- **Heads:** a 371-way action classifier and typed argument heads. Each argument is either copied as an exact span
  of the input (names, paths, passwords, numbers) or chosen from a learned label set (units, modes, apps).
- **Decoding v3:** keeps quoted file names, host names and whole numbers intact when a span overlaps them.
- **Validation:** every output is checked against the catalog (`union_catalog.json`): required slots, types,
  ranges such as volume 0 to 100 and BCM pin 0 to 27.
- **Training:** 1,143,182 synthetic fitting rows, NVIDIA A100 (bf16), epoch 5 of 9 selected by grouped-dev accuracy.
- **Catalog metadata:** each action carries `risk` (safe/caution/critical), `needs_sudo` and `platforms`, so your
  executor can decide what to run, confirm or refuse. The model never refuses anything itself.

## Limitations

- Training data is synthetic. There is no real speech-to-text output in it, so test with your own ASR transcripts.
- Devanagari Hindi coverage is small. English and Roman-script Hinglish dominate.
- One command per utterance. It is a parser, not a multi-step planner.
- Weakest areas on the held-out test: near-duplicate GPIO/I2C actions (`i2c_read` vs `i2c_write`, `gpio_blink` vs
  `gpio_pulse`), a few near-synonyms (`get_public_ip` vs `get_ip`, `scan_lan` vs `discover_devices`) and some
  free-text argument boundaries, like the `run_command` example above.
- The model does not refuse dangerous requests. Always put a validator and a risk gate in front of any executor.

## Files

| Path | What it is |
|---|---|
| `models/a100_minilm_v10_quoted/best.pt` | The model (Hugging Face only; GitHub users run `download_model.py`) |
| `tinyagent/` | Model and inference code (`Predictor`) plus the base executor |
| `union_catalog.json` | The 371 actions with slots, risk, sudo and platform metadata |
| `quickstart.py`, `download_model.py` | Command-line demo and verified downloader |
| `scripts/` | `evaluate.py`, `benchmark.py`, `category_examples.py`, `build_docs.py` |
| `app.py`, `lab_executor.py`, `executors/`, `web/` | The sample executor lab |
| `test_results/` | Accuracy, speed and temperature reports for the laptop and the Raspberry Pi 5 |
| `examples/` | Illustrative commands and per-platform executor plans |
| `docs/index.html` | The HTML test guide ([online]({PAGES_URL})) |

## License and credits

Code and model weights: Apache-2.0, Copyright (c) 2026 sraivante. The word encoder is initialised from
[sentence-transformers/all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
(Apache-2.0); see `provenance/base_models/minilm_l6/`. The training dataset is synthetic and is not published.
Provided as-is, without warranty. You are responsible for anything you let an executor do.
""")
    return "\n".join(s)


PI_NOTES = ""
DEVICE_ROWS = []
NOTES = {}
PI_MISSING = "not measured"


def html_table(header, rows, cls=""):
    head = "".join(f"<th>{html.escape(str(h))}</th>" for h in header)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>" for row in rows)
    return f'<div class="table-wrap"><table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def page(d, readme_text):
    test = d["test"]
    lap2 = next(r for r in d["lap_bench"]["runs"] if r["threads"] == 2)
    pi2 = next((r for r in d["pi_bench"]["runs"] if r["threads"] == 2), None) if d["pi_bench"] else None
    pi_test = d["pi_eval"]["files"][0] if d["pi_eval"] else None
    wrong = "".join(f"<li><code>{html.escape(r['command'])}</code> gave <code>{html.escape(output_text(r))}</code>; "
                    f"expected <code>{html.escape(w[0] + ' ' + json.dumps(w[1], ensure_ascii=False))}</code>.</li>"
                    for r, w in d["wrong"])
    stat = lambda value, label: f'<div class="stat"><strong>{html.escape(value)}</strong><span>{html.escape(label)}</span></div>'
    stats = "".join([
        stat(pct(test["exact_accuracy"]), f"exact match, held-out test ({test['rows']:,} commands)"),
        stat(f"{lap2['latency_ms']['median']:.1f} ms", "per command, laptop i7-1360P, 2 threads"),
        stat(f"{pi2['latency_ms']['median']:.0f} ms" if pi2 else (f"~{sum(NOTES['measurements']['warm_request_ms_2_threads']) / 2:.0f} ms" if NOTES.get("status") == "partial" else "see below"),
             "per command, Raspberry Pi 5, 2 threads" + ("" if pi2 else " (partial run)")),
        stat("371", "actions in 17 categories"),
        stat("96.2 MB", "FP32 model, CPU only, offline"),
    ])
    use_cases = [
        ("Offline voice assistant", "volume 40 kar do", 'set_volume {"value": 40}'),
        ("Raspberry Pi GPIO / I2C", "physical pin 11 ki value padho", 'gpio_read {"numbering": "board", "pin": 11}'),
        ("Device diagnostics", "pi ka temperature batao", "get_temperature {}"),
        ("First-stage router for LLM agents", "docker containers list karo", "docker_list {}"),
        ("Hands-free desktop", "notepad kholo", 'open_app {"app": "notepad"}'),
        ("Edge DevOps", "nginx service restart karo", 'service_restart {"service": "nginx"}'),
        ("Credentials copied verbatim", "connect to wifi Redmi Note 12 password hello@123",
         'connect_wifi {"ssid": "Redmi Note 12", "password": "hello@123"}'),
        ("Vague or negated requests", "turn it off  ·  shutdown mat karo", "clarify {}  ·  cancel_shutdown {}"),
    ]
    cards = "".join(f'<div class="card"><h3>{html.escape(t)}</h3><p class="say">“{html.escape(c)}”</p>'
                    f'<p class="out"><code>{html.escape(o)}</code></p></div>' for t, c, o in use_cases)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Union Command v10 · Test Guide</title>
<meta name="description" content="Offline Hinglish/English command parser for laptop and Raspberry Pi agents: how to download, test and read the results.">
<style>
:root {{ --bg:#f7f8fa; --panel:#ffffff; --text:#1b1f24; --muted:#5b6470; --line:#dfe3e8; --accent:#0f5c8c;
  --accent-soft:#e3f0f8; --warn-bg:#fff6df; --warn-line:#c98a00; --warn-text:#4a3500; --code:#eef1f4; --ok:#1d7a3e; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#101418; --panel:#171c21; --text:#e6e9ec;
  --muted:#9aa4af; --line:#2a323a; --accent:#6db3e3; --accent-soft:#16283a; --warn-bg:#3a2e10; --warn-line:#d9a32a;
  --warn-text:#ffe6a8; --code:#222a31; --ok:#5fcf8a; }} }}
:root[data-theme="dark"] {{ --bg:#101418; --panel:#171c21; --text:#e6e9ec; --muted:#9aa4af; --line:#2a323a;
  --accent:#6db3e3; --accent-soft:#16283a; --warn-bg:#3a2e10; --warn-line:#d9a32a; --warn-text:#ffe6a8; --code:#222a31; --ok:#5fcf8a; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }}
main {{ max-width:1080px; margin:0 auto; padding:32px 16px 64px; }}
h1 {{ font-size:clamp(1.7rem,4vw,2.4rem); line-height:1.2; margin:.2em 0 .4em; }}
h2 {{ margin-top:2.4em; padding-top:.6em; border-top:1px solid var(--line); }}
h3 {{ margin:.2em 0; font-size:1.02rem; }}
a {{ color:var(--accent); }}
p.lede {{ font-size:1.12rem; color:var(--muted); max-width:760px; }}
.eyebrow {{ text-transform:uppercase; letter-spacing:.08em; font-size:.78rem; color:var(--accent); font-weight:600; }}
.buttons {{ display:flex; flex-wrap:wrap; gap:10px; margin:18px 0 8px; }}
.button {{ display:inline-block; padding:9px 16px; border-radius:8px; background:var(--accent); color:#fff; text-decoration:none; font-weight:600; }}
.button.secondary {{ background:var(--accent-soft); color:var(--accent); }}
.stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin:22px 0; }}
.stat {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px; }}
.stat strong {{ display:block; font-size:1.45rem; }}
.stat span {{ color:var(--muted); font-size:.86rem; }}
.warn {{ border-left:4px solid var(--warn-line); background:var(--warn-bg); color:var(--warn-text); padding:12px 16px; border-radius:8px; margin:20px 0; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:12px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px; }}
.card .say {{ margin:.4em 0; font-style:italic; }}
.card .out {{ margin:0; font-size:.88rem; }}
.pipeline {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:12px 0; }}
.pipeline span {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:6px 10px; font-size:.9rem; }}
.pipeline .model {{ border-color:var(--accent); color:var(--accent); font-weight:600; }}
code, pre {{ font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:.86em; }}
code {{ background:var(--code); padding:1px 5px; border-radius:4px; }}
pre {{ background:var(--code); padding:12px 14px; border-radius:8px; overflow-x:auto; line-height:1.45; }}
pre code {{ background:none; padding:0; }}
.table-wrap {{ overflow-x:auto; margin:12px 0 18px; border:1px solid var(--line); border-radius:10px; background:var(--panel); }}
table {{ border-collapse:collapse; width:100%; font-size:.88rem; }}
th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ background:var(--accent-soft); font-weight:600; white-space:nowrap; }}
tr:last-child td {{ border-bottom:none; }}
.steps {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:14px; }}
.muted {{ color:var(--muted); }}
footer {{ margin-top:48px; color:var(--muted); font-size:.88rem; }}
</style>
</head>
<body>
<main>
<p class="eyebrow">Union Command v10 · Test guide</p>
<h1>Say it in English or Hinglish. Get a function call your agent can run.</h1>
<p class="lede">Union Command v10 is a small offline model that turns one spoken or typed command into a structured action with typed arguments, for laptops and Raspberry Pi. This page shows what it does, how to download and test it, and how it scored.</p>
<div class="buttons">
<a class="button" href="{ZIP_URL}">Download code (ZIP)</a>
<a class="button secondary" href="{GH_URL}">GitHub repository</a>
<a class="button secondary" href="{HF_URL}">Hugging Face model</a>
<a class="button secondary" href="{GH_URL}#readme">README</a>
</div>
<div class="stats">{stats}</div>
<div class="warn"><strong>The executor is sample testing only, not fully developed.</strong> The model parses; the included executor exists so you can watch a parsed command become a real action. It refuses every <code>critical</code> action by default. Use a test machine and read each plan before you run it.</div>

<h2>How it fits in an agent</h2>
<div class="pipeline"><span>Voice or text</span><span>→</span><span>Speech-to-text (optional)</span><span>→</span><span class="model">Union Command v10</span><span>→</span><span>Validator + risk gate</span><span>→</span><span>Executor (tool call)</span></div>
<p>It covers 371 actions across 17 categories. Every prediction includes a confidence score, the top-3 alternatives and schema validation errors, so an agent can execute, ask the user (<code>clarify</code>) or hand the text to a larger LLM (<code>unknown</code> or low confidence).</p>
<pre><code>{html.escape('{"action": "set_timer", "args": {"amount": 5, "unit": "min"}, "confidence": 0.9998,\n "alternatives": [...], "validation_errors": [], "latency_ms": 6.1}')}</code></pre>

<h2>Uses, with examples</h2>
<div class="cards">{cards}</div>

<h2>Categories, sample commands, outputs and executor tools</h2>
<p>One sample per category. <em>Model output</em> is the real v10 prediction. <em>Executor tool</em> is what the sample executor would run on each platform; the bracket shows whether that adapter was ready on the test machine. <em>Result</em> appears only for read-only commands that were actually executed.</p>
{html_table(["Category", "Sample command", "Model output", "Executor tool: Windows 11", "Executor tool: Raspberry Pi 5", "Default gate", "Result"], d["cat_table"])}
<details><summary>All {len(d['examples'])} illustrative commands ({sum(r['expected_ok'] for r in d['examples'])} exactly right)</summary>
{html_table(["Category", "Command", "Model output", "Confidence", "Correct"], d["all_examples"])}
<ul>{wrong}</ul>
</details>

<h2>Test results</h2>
<p><strong>Metric: exact match.</strong> The action and every typed argument must equal the label; errors stay in the denominator. Same checkpoint on both devices (SHA-256 <code>{SHA256[:16]}…</code>).</p>
<h3>Accuracy</h3>
{html_table(["Dataset", "Rows", "Laptop CPU exact", "Laptop action-only", "Raspberry Pi 5 exact", "Pi action-only", "About the set"], d["acc_rows"])}
<p class="muted">The held-out test split contains whole template families never used for training and was evaluated once for this release. Earlier development results on the same checkpoint: {html.escape(", ".join(f"{n} {pct(c / t)}" for n, c, t in DEV))}.</p>
<h3>By language (held-out test)</h3>
{html_table(["Language", "Rows", "Exact", "Action-only"], d["lang_rows"])}
<h3>By category (held-out test)</h3>
{html_table(["Category", "Rows", "Exact", "Action-only"], d["cat_rows"])}
<h3>Speed</h3>
{html_table(["Device", "Threads", "Median", "p95", "Single commands/s", "Batched commands/s", "Model load", "RAM"], d["speed"])}
{html_table(["Device", "Seconds (held-out test)", "Commands/s", "Threads", "Batch"], d["eval_speed"])}
<h3>Temperature</h3>
{html_table(["Device", "Run", "Sensor", "Start", "Max", "Mean", "Pi throttle flags after run"], d["temps"])}
<p>{html.escape(PI_NOTES)}</p>
<h3>Test devices</h3>
{html_table(["Device", "CPU", "OS", "Python / PyTorch"], DEVICE_ROWS)}

<h2>Download and test</h2>
<div class="steps">
<div><h3>1 · Get the code and model</h3>
<pre><code>git clone {GH_URL}.git
cd union-command-minilm-v10
python download_model.py</code></pre>
<p class="muted">The downloader fetches the 96.2 MB model from Hugging Face and verifies its SHA-256.</p></div>
<div><h3>2 · Install (Python 3.11–3.13, CPU)</h3>
<pre><code>python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Pi/Linux: . .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt</code></pre></div>
<div><h3>3 · Parse commands (no execution)</h3>
<pre><code>python quickstart.py
python quickstart.py "wifi band karo"
python quickstart.py --json "5 minute ka timer lagao"</code></pre></div>
<div><h3>4 · Reproduce the tests</h3>
<pre><code>python scripts/evaluate.py --data examples/sample_commands.jsonl
python scripts/benchmark.py --threads 1,2,4</code></pre></div>
<div><h3>5 · Sample executor lab (optional)</h3>
<pre><code>run.bat          # Windows
bash run.sh      # Linux / Raspberry Pi
# opens http://127.0.0.1:8770</code></pre>
<p class="muted">Local only, token-protected, file actions confined to <code>runtime/files/</code>. Critical actions need <code>--allow-risk critical</code>.</p></div>
<div><h3>Python API</h3>
<pre><code>from tinyagent.runtime import Predictor
m = Predictor("models/a100_minilm_v10_quoted/best.pt", threads=2)
print(m.predict("volume 40 kar do"))</code></pre></div>
</div>

<h2>Limitations</h2>
<ul>
<li>Synthetic training data with no real speech-to-text output; test with your own ASR transcripts.</li>
<li>Small Devanagari coverage; English and Roman-script Hinglish dominate.</li>
<li>One command per utterance; it parses, it does not plan.</li>
<li>Weakest spots: near-duplicate GPIO/I2C actions, a few near-synonyms and some free-text argument boundaries.</li>
<li>The model never refuses; always keep a validator and risk gate in front of an executor.</li>
</ul>

<footer>Code and weights: Apache-2.0, © 2026 sraivante. Word encoder initialised from sentence-transformers/all-MiniLM-L6-v2 (Apache-2.0). Training data is synthetic and not published. Provided as-is, without warranty. · <a href="{HF_URL}">Hugging Face</a> · <a href="{GH_URL}">GitHub</a></footer>
</main>
</body>
</html>
"""


def sanitize_published_json():
    """Keep only non-identifying execution summaries and relative checkpoint paths in published JSON."""
    for name in ("examples/category_examples_windows.json", "examples/category_examples_pi.json"):
        path = ROOT / name
        if not path.exists():
            continue
        report = json.loads(path.read_text("utf-8"))
        for row in report["rows"]:
            if row.get("executed") and "execution_output" in row:
                row["published_result"] = display_result(row)
                del row["execution_output"]
        report["published_output_policy"] = ("Raw executor output is replaced by a short summary; outputs that could "
                                             "reveal local device details are not published.")
        path.write_text(scrub(json.dumps(report, indent=2, ensure_ascii=False)), "utf-8")
    for path in (ROOT / "models").rglob("*_report.json"):
        report = json.loads(path.read_text("utf-8"))
        if "checkpoint" in report:
            report["checkpoint"] = "models/a100_minilm_v10_quoted/best.pt"
            path.write_text(json.dumps(report, indent=2), "utf-8")


def main():
    global PI_NOTES, DEVICE_ROWS, NOTES, PI_MISSING
    sanitize_published_json()
    results = (load("test_results/laptop_cpu/evaluation.json"), load("test_results/laptop_cpu/benchmark.json"),
               load("test_results/rpi5/evaluation.json"), load("test_results/rpi5/benchmark.json"))
    notes = load("test_results/rpi5/notes.json") or {}
    NOTES = notes
    if notes.get("status") == "partial" and not results[2]:
        PI_MISSING = "not completed (Pi went offline)"
    PI_NOTES = notes.get("text", "")
    DEVICE_ROWS = []
    for name, report in (("Laptop", results[0]), ("Raspberry Pi 5", results[2] or results[3])):
        if report:
            dev = report["device"]
            DEVICE_ROWS.append([name, dev["cpu"], dev["os"], f"Python {dev['python']} / torch {dev['torch']}"])
        elif name.startswith("Raspberry") and notes.get("device_row"):
            DEVICE_ROWS.append(notes["device_row"])
    data = mark_examples(build(results))
    text = readme(data)
    (ROOT / "README.md").write_text(scrub(text), "utf-8")
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs/index.html").write_text(scrub(page(data, text)), "utf-8")
    print("wrote README.md and docs/index.html")


if __name__ == "__main__":
    main()
