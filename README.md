# Union Command v10: offline Hinglish/English command parser for laptop and Raspberry Pi agents

**Union Command v10** turns one short natural-language command into a structured, typed function call that an
agent can execute. It understands English, Hinglish (Roman-script Hindi-English) and a little Devanagari Hindi,
and covers **371 actions** in **17 categories**: audio, display, Wi-Fi, files, apps, browser, timers, services,
Docker, local LLMs, Raspberry Pi GPIO/I2C and more.

```text
"volume 40 kar do"          ->  set_volume   {"value": 40}
"gpio 17 ko high karo"      ->  gpio_on      {"pin": 17}
"10 min baad shutdown kar dena" -> shutdown  {"amount": 10, "unit": "min"}
```

| | |
|---|---|
| Model file | 96.2 MB, FP32 PyTorch, 23,889,849 parameters |
| Runs on | CPU only, fully offline after download (Windows, Linux, Raspberry Pi 5) |
| Held-out test accuracy | **94.87% exact** (action + every argument) on 24,498 unseen-template commands; 96.05% action-only |
| Speed | **5.9 ms** median per command on a laptop i7-1360P (2 threads); about **40 ms** on a Raspberry Pi 5 (2 threads, partial run, see below) |
| Links | [GitHub code](https://github.com/sraivante/union-command-minilm-v10) · [HTML test guide](https://sraivante.github.io/union-command-minilm-v10/) · [Hugging Face model](https://huggingface.co/sraivante/union-command-minilm-v10) |

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
| Offline voice assistant for a laptop or home | `volume 40 kar do` | `set_volume {"value": 40}` |
| Raspberry Pi GPIO / I2C by voice or chat | `physical pin 11 ki value padho` | `gpio_read {"numbering": "board", "pin": 11}` |
| Device diagnostics chat-ops | `pi ka temperature batao` | `get_temperature {}` |
| Cheap first-stage router for an LLM agent | `docker containers list karo` | `docker_list {}`, and send `unknown`/low-confidence text to the LLM |
| Hands-free desktop and accessibility | `notepad kholo` | `open_app {"app": "notepad"}` |
| Edge DevOps | `nginx service restart karo` | `service_restart {"service": "nginx"}` |
| Credentials copied verbatim | `connect to wifi Redmi Note 12 password hello@123` | `connect_wifi {"ssid": "Redmi Note 12", "password": "hello@123"}` |
| Safe handling of vague or negated requests | `turn it off` / `shutdown mat karo` | `clarify {}` / `cancel_shutdown {}` |

Every prediction returns JSON with the action, typed arguments, a confidence score, the top-3 alternative actions and
schema validation errors:

```json
{"action": "set_timer", "args": {"amount": 5, "unit": "min"}, "confidence": 0.9998,
  "alternatives": [{"action": "set_timer", "score": 0.9998}, ...], "validation_errors": [], "latency_ms": 6.1}
```

A good agent pattern: execute only when `validation_errors` is empty and confidence is high, ask the user when the
action is `clarify`, and hand `unknown` or low-confidence text to a bigger model.

## Categories, sample commands, outputs and executor tools

One sample per category. "Model output" is the real prediction of v10. "Executor tool" is what the sample executor
would run for that output on each platform; the tag in brackets shows whether that adapter was ready on the test
machine. "Result" is shown only for read-only commands that were actually executed; other rows were planned, not run.

| Category | Sample command | Model output | Executor tool: Windows 11 | Executor tool: Raspberry Pi 5 | Default gate | Result |
|---|---|---|---|---|---|---|
| Audio & media | volume 40 kar do | set_volume {"value": 40} | Python adapter executors/windows.py: Set master volume to an exact percent [needs pycaw] | not measured (Pi went offline) | allowed | plan only (not executed in this demo) |
| Display & appearance | brightness thoda kam karo | brightness_down {} | PowerShell: $b=Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness… [ready] | not measured (Pi went offline) | allowed | plan only (not executed in this demo) |
| Network & connectivity | wifi ka signal kitna strong hai | get_wifi_signal {} | netsh.exe wlan show interfaces [ready] | not measured (Pi went offline) | allowed | completed (output not published: local device details) |
| GPIO & I2C | gpio 17 ko high karo | gpio_on {"pin": 17} | Python adapter executors/common.py: Set a GPIO pin HIGH (e.g. relay / LED on) [Pi only] | not measured (Pi went offline) | blocked | blocked by default gate |
| Hardware & Raspberry Pi | pi ka temperature batao | get_temperature {} | PowerShell: $t=Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTe… [ready] | not measured (Pi went offline) | allowed | failed: needs administrator rights |
| Files & storage | create file "notes/todo.txt" | create_file {"path": "notes/todo.txt"} | Python adapter tinyagent/executor.py: Create a new empty file; existing files are never overwritt… [ready] | not measured (Pi went offline) | allowed | plan only (not executed in this demo) |
| Apps & windows | notepad kholo | open_app {"app": "notepad"} | Python adapter executors/desktop.py: Open / launch an application [needs pywinauto] | not measured (Pi went offline) | allowed | plan only (not executed in this demo) |
| Browser & web | youtube pe lofi music search karo | youtube_search {"query": "lofi music"} | Python adapter executors/desktop.py: Search / play something on YouTube [ready] | not measured (Pi went offline) | allowed | plan only (not executed in this demo) |
| Keyboard & clipboard | sab select karo | select_all {} | Python adapter executors/desktop.py: Send ctrl+a to the selected target window [needs pywinauto] | not measured (Pi went offline) | allowed | plan only (not executed in this demo) |
| Timers & productivity | 5 minute ka timer lagao | set_timer {"amount": 5, "unit": "min"} | Python adapter tinyagent/executor.py: Start a timer in this local server process [ready] | not measured (Pi went offline) | allowed | plan only (not executed in this demo) |
| Security & accounts | firewall ka status batao | firewall_status {} | PowerShell: Get-NetFirewallProfile \| Select-Object Name,Enabled \| ConvertTo-Json [ready] | not measured (Pi went offline) | allowed | completed (output not published: local device details) |
| Services & processes | ssh service ka status dikhao | service_status {"service": "ssh"} | PowerShell: Get-Service -Name $a.service \| Select-Object Name,Status,DisplayName \|… [ready] | not measured (Pi went offline) | allowed | failed: no such service on this OS |
| Software & development | docker containers list karo | docker_list {} | docker.EXE ps -a [ready] | not measured (Pi went offline) | allowed | completed (output not published: local device details) |
| AI & models | ollama pe kaunse models hain | list_llm_models {} | ollama.EXE list [ready] | not measured (Pi went offline) | allowed | completed (output not published: local device details) |
| Terminal & sessions | tmux sessions dikhao | list_sessions {} | Python adapter executors/common.py: List sessions [Pi only] | not measured (Pi went offline) | allowed | plan only (not executed in this demo) |
| System & power | cpu usage batao | get_cpu_usage {} | Python adapter tinyagent/executor.py: Measure CPU use over 200 milliseconds [ready] | not measured (Pi went offline) | allowed | CPU 14.6 % |
| Help & intent handling | tum kya kya kar sakte ho | help {} | Python adapter tinyagent/executor.py: Show action and adapter coverage [ready] | not measured (Pi went offline) | allowed | 371 actions, 221 live adapters here |

All 35 illustrative commands (two per category) are in
[`examples/sample_commands.jsonl`](examples/sample_commands.jsonl), with per-platform executor plans in
[`examples/category_examples_windows.json`](examples/category_examples_windows.json)
and [`examples/category_examples_pi.json`](examples/category_examples_pi.json).
v10 got 34 of 35 exactly right. The miss:

- `run command "ls -la"` gave `run_command {"command": "command \"ls -la"}`; expected `run_command {"command": "ls -la"}`.

These commands were written for this demo, so they are illustrative. The accuracy numbers below come from the
held-out test set.

## Test results

### Accuracy

Metric: **exact match**, meaning the action and every typed argument must be identical to the label. Errors and
abstentions stay in the denominator. Same checkpoint on both devices: SHA-256 `c91d44e4a687152cd65d86d56cfc0e453a60a1afe14f8c395e8af058f2fc4d5b`.

| Dataset | Rows | Laptop CPU exact | Laptop action-only | Raspberry Pi 5 exact | Pi action-only | About the set |
|---|---|---|---|---|---|---|
| Held-out test (unseen templates) | 24,498 | 94.87% (23,241) | 96.05% | not completed (Pi went offline) | — | 24,498 rows · all 371 actions · template families never seen in training |
| Practical dev (A) | 1,321 | 96.44% (1,274) | 96.90% | not completed (Pi went offline) | — | 1,321 realistic phrasings · used during development |
| Golden dev (B) | 383 | 97.39% (373) | 97.91% | not completed (Pi went offline) | — | 383 hand-checked commands · used during development |

The held-out test split is made of whole template families that were never used for training. It was evaluated once
for this release. Practical dev and golden dev guided earlier development, so treat them as regression checks.
Earlier development measurements on the same checkpoint (Windows CPU):
Grouped dev (reserved template groups) 96.50% (30,596/31,707), Validation 98.22% (23,467/23,892).

Held-out test by language:

| Language | Rows | Exact | Action-only |
|---|---|---|---|
| Hinglish (Roman, incl. small Devanagari slice) | 9,958 | 95.43% | 96.37% |
| English | 8,038 | 92.56% | 94.31% |
| Unlabelled (project A rows, mostly English/Hinglish) | 4,884 | 98.73% | 99.45% |
| Code-mixed | 1,618 | 91.22% | 92.46% |

Held-out test by category:

| Category | Rows | Exact | Action-only |
|---|---|---|---|
| Hardware & Raspberry Pi | 1,122 | 98.93% | 98.93% |
| Terminal & sessions | 410 | 98.78% | 98.78% |
| Display & appearance | 1,972 | 98.48% | 98.53% |
| Audio & media | 1,653 | 98.12% | 98.31% |
| Browser & web | 1,527 | 97.84% | 99.61% |
| System & power | 3,085 | 97.50% | 98.64% |
| Security & accounts | 714 | 96.78% | 96.78% |
| Keyboard & clipboard | 883 | 96.60% | 97.28% |
| Files & storage | 2,793 | 95.31% | 96.89% |
| Help & intent handling | 762 | 95.14% | 96.19% |
| Apps & windows | 1,468 | 94.96% | 96.66% |
| Services & processes | 1,088 | 93.57% | 94.76% |
| Software & development | 926 | 92.33% | 92.44% |
| Network & connectivity | 2,951 | 91.70% | 92.82% |
| Timers & productivity | 747 | 90.63% | 93.71% |
| GPIO & I2C | 2,133 | 87.48% | 88.70% |
| AI & models | 264 | 80.68% | 97.73% |

Most frequent action confusions on the held-out test:

| Expected | Predicted | Count |
|---|---|---|
| gpio_stop_watch | gpio_off | 59 |
| i2c_read | i2c_write | 54 |
| create_service | service_enable | 52 |
| hotspot_on | unknown | 51 |
| get_public_ip | get_ip | 51 |
| scan_lan | discover_devices | 37 |
| gpio_blink | gpio_pulse | 35 |
| enable_auto_updates | install_python_package | 35 |

### Speed

Warm, single-command latency over one command per catalog action (371 commands, 2 passes), then batched
throughput (batch 32). Includes Python and PyTorch overhead. "RAM" is the whole process after loading the model.

| Device | Threads | Median | p95 | Single commands/s | Batched commands/s | Model load | RAM |
|---|---|---|---|---|---|---|---|
| Laptop i7-1360P | 1 | 7.4 ms | 9.9 ms | 131 | 141 | 5.3 s | 650 MB |
| Laptop i7-1360P | 2 | 5.9 ms | 9.4 ms | 157 | 249 | 5.3 s | 650 MB |
| Laptop i7-1360P | 4 | 7.2 ms | 11.3 ms | 128 | 237 | 5.3 s | 650 MB |
| Raspberry Pi 5 (partial) | 2 | ~40 ms (2 warm requests) | — | — | 77 (4 threads, batch 64) | 8.6 s | — |

Full accuracy run speed (batched, held-out test):

| Device | Seconds | Commands/s | Threads | Batch |
|---|---|---|---|---|
| Laptop i7-1360P | 95.86 | 255.5 | 4 | 64 |
| Raspberry Pi 5 (partial) | 130 for 10,048 of 24,498 | 77 | 4 | 64 |

### Temperature

| Device | Run | Sensor | Start | Max | Mean | Pi throttle flags after run |
|---|---|---|---|---|---|---|
| Laptop i7-1360P | Accuracy run (26k rows, 4 threads) | Windows ACPI thermal zone (typeperf, Thermal Zone Information) | 60.9 °C | 95.9 °C | 85.3 °C | n/a (not a Pi) |
| Laptop i7-1360P | Latency benchmark (1/2/4 threads) | Windows ACPI thermal zone (typeperf, Thermal Zone Information) | 53.9 °C | 85.9 °C | 71.2 °C | n/a (not a Pi) |
| Raspberry Pi 5 | Accuracy run, partial (4 threads) | vcgencmd SoC sensor (spot readings) | 45.0 °C (idle) | 85.1 °C after 130 s | — | 0x0 before; board went offline |

Raspberry Pi 5 status: partial. On the Pi 5 (16 GB, active cooler, 64-bit Debian 13) the model loaded in 8.6 s and answered warm single commands in about 40 ms with 2 threads (first, cold request 222 ms). The sustained accuracy run on the held-out test used 4 threads and batch 64. It processed 10,048 of 24,498 commands in 130 s (about 77 commands/s). At that point the SoC was at 85.1 °C, the Pi 5 soft-throttle point, with the fan at about 9,900 RPM. Soon after, the Pi stopped responding on the network and had not come back when these results were published. Pi accuracy is therefore not reported yet. The weights and code are identical to the laptop run, so predictions should match closely, but this was not measured. For long jobs on a Pi 5, use 2 threads, make sure the cooler and a 5 V / 5 A supply are fitted, and watch `vcgencmd measure_temp` and `vcgencmd get_throttled`.

### Test devices

| Device | CPU | OS | Python / PyTorch |
|---|---|---|---|
| Laptop | 13th Gen Intel(R) Core(TM) i7-1360P | Windows-11-10.0.26200-SP0 | Python 3.13.14 / torch 2.12.0+cpu |
| Raspberry Pi 5 (16 GB, active cooler) | Broadcom BCM2712, 4x Arm Cortex-A76 @ 2.4 GHz | Debian 13 (trixie) 64-bit, kernel 6.12.47 | Python 3.13.5 / torch 2.8.0+cpu |

## Download and test

### 1. Get the code and the model

```bash
git clone https://github.com/sraivante/union-command-minilm-v10.git
cd union-command-minilm-v10
python download_model.py      # 96.2 MB from Hugging Face, SHA-256 verified
```

Or download the [ZIP of the repository](https://github.com/sraivante/union-command-minilm-v10/archive/refs/heads/main.zip). The model file alone is at
[sraivante/union-command-minilm-v10](https://huggingface.co/sraivante/union-command-minilm-v10/resolve/main/models/a100_minilm_v10_quoted/best.pt).

### 2. Install (Python 3.11 to 3.13, CPU only)

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\activate
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
| `docs/index.html` | The HTML test guide ([online](https://sraivante.github.io/union-command-minilm-v10/)) |

## License and credits

Code and model weights: Apache-2.0, Copyright (c) 2026 sraivante. The word encoder is initialised from
[sentence-transformers/all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
(Apache-2.0); see `provenance/base_models/minilm_l6/`. The training dataset is synthetic and is not published.
Provided as-is, without warranty. You are responsible for anything you let an executor do.
