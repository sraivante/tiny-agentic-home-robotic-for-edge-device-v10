# Union Command v10: offline Hinglish/English command parser for laptop and Raspberry Pi agents

**Union Command v10** turns one short natural-language command into a structured, typed function call that an
agent can execute. It understands English, Hinglish (Roman-script Hindi-English) and a little Devanagari Hindi,
and covers **371 actions** in **17 categories**: audio, display, Wi-Fi, files, apps, browser, timers, services,
Docker, local LLMs, Raspberry Pi GPIO/I2C and more.

```text
ENGLISH   "set the volume to 40"          ->  set_volume {"value": 40}
ENGLISH   "set gpio 17 high"              ->  gpio_on {"pin": 17}
ENGLISH   "shut down in 10 minutes"       ->  shutdown {"amount": 10, "unit": "min"}
HINGLISH  "volume 40 kar do"              ->  set_volume {"value": 40}
HINGLISH  "gpio 17 ko high karo"          ->  gpio_on {"pin": 17}
HINGLISH  "10 min baad shutdown kar dena" ->  shutdown {"amount": 10, "unit": "min"}
```

| | |
|---|---|
| Model file | 96.2 MB, FP32 PyTorch, 23,889,849 parameters |
| Runs on | CPU only, fully offline after download (Windows, Linux, Raspberry Pi 5) |
| Held-out test accuracy | **94.87% exact** (action + every argument) on 24,498 unseen-template commands; 96.05% action-only |
| Accuracy by language | ENGLISH **92.56%** · HINGLISH **95.43%** exact (8,038 and 9,958 held-out commands) |
| Speed | **5.9 ms** median per command on a laptop i7-1360P (2 threads); **40 ms** median per command on a Raspberry Pi 5 (2 threads) |
| Links | [GitHub code](https://github.com/sraivante/tiny-agentic-home-robotic-for-edge-device-v10) · [HTML test guide](https://sraivante.github.io/tiny-agentic-home-robotic-for-edge-device-v10/) · [Hugging Face model](https://huggingface.co/sraivante/tiny-agentic-home-robotic-for-edge-device-v10) |

> **Executor notice.** The model only *parses* commands. The executor included in this repository is a **sample
> for testing, not a fully developed product**. It is there so you can see a parsed command turn into a real
> action on your own machine. By default it refuses every `critical` action (shutdown, delete, `run_command`,
> GPIO on/off, service changes, ...). Use it on a test machine and read the plan before you run anything.

## What it is for

The model is the "understanding" step of an on-device agent:
speech-to-text (for example Whisper) → **Union Command v10** → validator/risk gate → executor (tool call).
It replaces a large LLM for the common, well-defined device commands, so the reply is instant, private and free.

Every use case is shown in ENGLISH first, then the same command in HINGLISH. The outputs are the real predictions of v10.

| Use case | Language | Example command | Model output |
|---|---|---|---|
| Offline voice assistant for a laptop or home | ENGLISH | `set the volume to 40` | `set_volume {"value": 40}` |
| Raspberry Pi GPIO / I2C by voice or chat | ENGLISH | `read the value of physical pin 11` | `gpio_read {"numbering": "board", "pin": 11}` |
| Device diagnostics chat-ops | ENGLISH | `what is the pi temperature` | `get_temperature {}` |
| Cheap first-stage router for an LLM agent | ENGLISH | `list docker containers` | `docker_list {}` |
| Hands-free desktop and accessibility | ENGLISH | `open notepad` | `open_app {"app": "notepad"}` |
| Edge DevOps | ENGLISH | `restart the nginx service` | `service_restart {"service": "nginx"}` |
| Credentials copied verbatim | ENGLISH | `connect to wifi Redmi Note 12 password hello@123` | `connect_wifi {"password": "hello@123", "ssid": "Redmi Note 12"}` |
| Negated requests | ENGLISH | `don't shut down the laptop` | `cancel_shutdown {}` |
| Offline voice assistant for a laptop or home | HINGLISH | `volume 40 kar do` | `set_volume {"value": 40}` |
| Raspberry Pi GPIO / I2C by voice or chat | HINGLISH | `physical pin 11 ki value padho` | `gpio_read {"numbering": "board", "pin": 11}` |
| Device diagnostics chat-ops | HINGLISH | `pi ka temperature batao` | `get_temperature {}` |
| Cheap first-stage router for an LLM agent | HINGLISH | `docker containers list karo` | `docker_list {}` |
| Hands-free desktop and accessibility | HINGLISH | `notepad kholo` | `open_app {"app": "notepad"}` |
| Edge DevOps | HINGLISH | `nginx service restart karo` | `service_restart {"service": "nginx"}` |
| Credentials copied verbatim | HINGLISH | `Redmi Note 12 wifi se connect karo password hello@123` | `connect_wifi {"password": "hello@123", "ssid": "Redmi Note 12"}` |
| Negated requests | HINGLISH | `shutdown mat karo` | `cancel_shutdown {}` |

For an LLM agent, send `unknown` or low-confidence text on to the bigger model. Vague commands such as `turn it off`
come back as `clarify`, so the agent can ask the user what to switch off.

Every prediction returns JSON with the action, typed arguments, a confidence score, the top-3 alternative actions and
schema validation errors:

```json
{"action": "set_timer", "args": {"amount": 5, "unit": "min"}, "confidence": 0.9998,
  "alternatives": [{"action": "set_timer", "score": 0.9998}, ...], "validation_errors": [], "latency_ms": 6.1}
```

A good agent pattern: execute only when `validation_errors` is empty and confidence is high, ask the user when the
action is `clarify`, and hand `unknown` or low-confidence text to a bigger model.

## Categories, sample commands, outputs and executor tools

One intent per category: all ENGLISH commands first, then the same commands in HINGLISH. "Model output" is the real prediction of v10. "Executor tool" is what the sample executor
would run for that output on each platform; the tag in brackets shows whether that adapter was ready on the test
machine. "Result" is shown only for read-only commands that were actually executed; other rows were planned, not run.

| Category | Language | Sample command | Model output | Executor tool: Windows 11 | Executor tool: Raspberry Pi 5 | Default gate | Result |
|---|---|---|---|---|---|---|---|
| Audio & media | ENGLISH | set the volume to 40 | set_volume {"value": 40} | Python adapter executors/windows.py: Set master volume to an exact percent [needs pycaw] | Python adapter executors/common.py: Set master volume to an exact percent [needs pactl] | allowed | plan only (not executed in this demo) |
| Display & appearance | ENGLISH | lower the brightness a little | brightness_down {} | PowerShell: $b=Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness… [ready] | Python adapter executors/common.py: Decrease brightness (default step 10) [needs brightnessctl] | allowed | plan only (not executed in this demo) |
| Network & connectivity | ENGLISH | how strong is the wifi signal | get_wifi_signal {} | netsh.exe wlan show interfaces [ready] | nmcli device wifi list [ready] | allowed | Windows: completed (output not published: local device details); Pi: completed (output not published: local device details) |
| GPIO & I2C | ENGLISH | set gpio 17 high | gpio_on {"pin": 17} | Python adapter executors/common.py: Set a GPIO pin HIGH (e.g. relay / LED on) [Pi only] | pinctrl set 17 op dh [ready] | blocked | blocked by default gate |
| Hardware & Raspberry Pi | ENGLISH | what is the pi temperature | get_temperature {} | PowerShell: $t=Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTe… [ready] | vcgencmd measure_temp [ready] | allowed | Windows: failed: needs administrator rights; Pi: temp=43.9'C |
| Files & storage | ENGLISH | create file "notes/todo.txt" | create_file {"path": "notes/todo.txt"} | Python adapter tinyagent/executor.py: Create a new empty file; existing files are never overwritt… [ready] | Python adapter tinyagent/executor.py: Create a new empty file; existing files are never overwritt… [ready] | allowed | plan only (not executed in this demo) |
| Apps & windows | ENGLISH | open notepad | open_app {"app": "notepad"} | Python adapter executors/desktop.py: Open / launch an application [needs pywinauto] | Python adapter executors/desktop.py: Open / launch an application [ready] | allowed | plan only (not executed in this demo) |
| Browser & web | ENGLISH | search youtube for lofi music | youtube_search {"query": "lofi music"} | Python adapter executors/desktop.py: Search / play something on YouTube [ready] | Python adapter executors/desktop.py: Search / play something on YouTube [ready] | allowed | plan only (not executed in this demo) |
| Keyboard & clipboard | ENGLISH | select all | select_all {} | Python adapter executors/desktop.py: Send ctrl+a to the selected target window [needs pywinauto] | Python adapter executors/desktop.py: Send ctrl+a to the selected target window [needs wmctrl] | allowed | plan only (not executed in this demo) |
| Timers & productivity | ENGLISH | set a timer for 5 minutes | set_timer {"amount": 5, "unit": "min"} | Python adapter tinyagent/executor.py: Start a timer in this local server process [ready] | Python adapter tinyagent/executor.py: Start a timer in this local server process [ready] | allowed | plan only (not executed in this demo) |
| Security & accounts | ENGLISH | show the firewall status | firewall_status {} | PowerShell: Get-NetFirewallProfile \| Select-Object Name,Enabled \| ConvertTo-Json [ready] | Python adapter executors/common.py: show firewall status [needs ufw] | allowed | Windows: completed (output not published: local device details) |
| Services & processes | ENGLISH | show the status of the ssh service | service_status {"service": "ssh"} | PowerShell: Get-Service -Name $a.service \| Select-Object Name,Status,DisplayName \|… [ready] | systemctl status --no-pager -- ssh [ready] | allowed | Windows: failed: no such service on this OS |
| Software & development | ENGLISH | list docker containers | docker_list {} | docker.EXE ps -a [ready] | docker ps -a [ready] | allowed | Windows: completed (output not published: local device details); Pi: completed (output not published: local device details) |
| AI & models | ENGLISH | which models are available in ollama | list_llm_models {} | ollama.EXE list [ready] | ollama list [ready] | allowed | Windows: completed (output not published: local device details); Pi: completed (output not published: local device details) |
| Terminal & sessions | ENGLISH | show tmux sessions | list_sessions {} | Python adapter executors/common.py: List sessions [Pi only] | Python adapter executors/common.py: List sessions [needs tmux] | allowed | plan only (not executed in this demo) |
| System & power | ENGLISH | what is the cpu usage | get_cpu_usage {} | Python adapter tinyagent/executor.py: Measure CPU use over 200 milliseconds [ready] | Python adapter tinyagent/executor.py: Measure CPU use over 200 milliseconds [ready] | allowed | Windows: CPU 7.5 %; Pi: CPU 0.0 % |
| Help & intent handling | ENGLISH | what can you do | help {} | Python adapter tinyagent/executor.py: Show action and adapter coverage [ready] | Python adapter tinyagent/executor.py: Show action and adapter coverage [ready] | allowed | Windows: 371 actions, 221 live adapters here; Pi: 371 actions, 251 live adapters here |
| Audio & media | HINGLISH | volume 40 kar do | set_volume {"value": 40} | Python adapter executors/windows.py: Set master volume to an exact percent [needs pycaw] | Python adapter executors/common.py: Set master volume to an exact percent [needs pactl] | allowed | plan only (not executed in this demo) |
| Display & appearance | HINGLISH | brightness thoda kam karo | brightness_down {} | PowerShell: $b=Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness… [ready] | Python adapter executors/common.py: Decrease brightness (default step 10) [needs brightnessctl] | allowed | plan only (not executed in this demo) |
| Network & connectivity | HINGLISH | wifi ka signal kitna strong hai | get_wifi_signal {} | netsh.exe wlan show interfaces [ready] | nmcli device wifi list [ready] | allowed | Windows: completed (output not published: local device details); Pi: completed (output not published: local device details) |
| GPIO & I2C | HINGLISH | gpio 17 ko high karo | gpio_on {"pin": 17} | Python adapter executors/common.py: Set a GPIO pin HIGH (e.g. relay / LED on) [Pi only] | pinctrl set 17 op dh [ready] | blocked | blocked by default gate |
| Hardware & Raspberry Pi | HINGLISH | pi ka temperature batao | get_temperature {} | PowerShell: $t=Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTe… [ready] | vcgencmd measure_temp [ready] | allowed | Windows: failed: needs administrator rights; Pi: temp=43.3'C |
| Files & storage | HINGLISH | file "notes/todo.txt" bana do | create_file {"path": "notes/todo.txt"} | Python adapter tinyagent/executor.py: Create a new empty file; existing files are never overwritt… [ready] | Python adapter tinyagent/executor.py: Create a new empty file; existing files are never overwritt… [ready] | allowed | plan only (not executed in this demo) |
| Apps & windows | HINGLISH | notepad kholo | open_app {"app": "notepad"} | Python adapter executors/desktop.py: Open / launch an application [needs pywinauto] | Python adapter executors/desktop.py: Open / launch an application [ready] | allowed | plan only (not executed in this demo) |
| Browser & web | HINGLISH | youtube pe lofi music search karo | youtube_search {"query": "lofi music"} | Python adapter executors/desktop.py: Search / play something on YouTube [ready] | Python adapter executors/desktop.py: Search / play something on YouTube [ready] | allowed | plan only (not executed in this demo) |
| Keyboard & clipboard | HINGLISH | sab select karo | select_all {} | Python adapter executors/desktop.py: Send ctrl+a to the selected target window [needs pywinauto] | Python adapter executors/desktop.py: Send ctrl+a to the selected target window [needs wmctrl] | allowed | plan only (not executed in this demo) |
| Timers & productivity | HINGLISH | 5 minute ka timer lagao | set_timer {"amount": 5, "unit": "min"} | Python adapter tinyagent/executor.py: Start a timer in this local server process [ready] | Python adapter tinyagent/executor.py: Start a timer in this local server process [ready] | allowed | plan only (not executed in this demo) |
| Security & accounts | HINGLISH | firewall ka status batao | firewall_status {} | PowerShell: Get-NetFirewallProfile \| Select-Object Name,Enabled \| ConvertTo-Json [ready] | Python adapter executors/common.py: show firewall status [needs ufw] | allowed | Windows: completed (output not published: local device details) |
| Services & processes | HINGLISH | ssh service ka status dikhao | service_status {"service": "ssh"} | PowerShell: Get-Service -Name $a.service \| Select-Object Name,Status,DisplayName \|… [ready] | systemctl status --no-pager -- ssh [ready] | allowed | Windows: failed: no such service on this OS |
| Software & development | HINGLISH | docker containers list karo | docker_list {} | docker.EXE ps -a [ready] | docker ps -a [ready] | allowed | Windows: completed (output not published: local device details); Pi: completed (output not published: local device details) |
| AI & models | HINGLISH | ollama pe kaunse models hain | list_llm_models {} | ollama.EXE list [ready] | ollama list [ready] | allowed | Windows: completed (output not published: local device details); Pi: completed (output not published: local device details) |
| Terminal & sessions | HINGLISH | tmux sessions dikhao | list_sessions {} | Python adapter executors/common.py: List sessions [Pi only] | Python adapter executors/common.py: List sessions [needs tmux] | allowed | plan only (not executed in this demo) |
| System & power | HINGLISH | cpu usage batao | get_cpu_usage {} | Python adapter tinyagent/executor.py: Measure CPU use over 200 milliseconds [ready] | Python adapter tinyagent/executor.py: Measure CPU use over 200 milliseconds [ready] | allowed | Windows: CPU 6.7 %; Pi: CPU 3.7 % |
| Help & intent handling | HINGLISH | tum kya kya kar sakte ho | help {} | Python adapter tinyagent/executor.py: Show action and adapter coverage [ready] | Python adapter tinyagent/executor.py: Show action and adapter coverage [ready] | allowed | Windows: 371 actions, 221 live adapters here; Pi: 371 actions, 251 live adapters here |

<details>
<summary>All 72 example commands: 36 intents, each in ENGLISH and HINGLISH</summary>

| Category | Language | Command | Model output | Confidence | Correct |
|---|---|---|---|---|---|
| Audio & media | ENGLISH | `set the volume to 40` | `set_volume {"value": 40}` | 100% | ✓ |
| Audio & media | ENGLISH | `pause the music` | `media_pause {}` | 100% | ✓ |
| Display & appearance | ENGLISH | `lower the brightness a little` | `brightness_down {}` | 100% | ✓ |
| Display & appearance | ENGLISH | `turn on dark mode` | `dark_mode_on {}` | 100% | ✓ |
| Network & connectivity | ENGLISH | `how strong is the wifi signal` | `get_wifi_signal {}` | 100% | ✓ |
| Network & connectivity | ENGLISH | `connect to wifi Redmi Note 12 password hello@123` | `connect_wifi {"password": "hello@123", "ssid": "Redmi Note 12"}` | 100% | ✓ |
| GPIO & I2C | ENGLISH | `set gpio 17 high` | `gpio_on {"pin": 17}` | 100% | ✓ |
| GPIO & I2C | ENGLISH | `read the value of physical pin 11` | `gpio_read {"numbering": "board", "pin": 11}` | 100% | ✓ |
| GPIO & I2C | ENGLISH | `scan the i2c bus` | `i2c_scan {}` | 100% | ✓ |
| Hardware & Raspberry Pi | ENGLISH | `what is the pi temperature` | `get_temperature {}` | 100% | ✓ |
| Hardware & Raspberry Pi | ENGLISH | `what is the fan speed` | `get_fan_speed {}` | 100% | ✓ |
| Files & storage | ENGLISH | `create file "notes/todo.txt"` | `create_file {"path": "notes/todo.txt"}` | 100% | ✓ |
| Files & storage | ENGLISH | `how much disk space is left` | `get_disk_space {}` | 100% | ✓ |
| Apps & windows | ENGLISH | `open notepad` | `open_app {"app": "notepad"}` | 100% | ✓ |
| Apps & windows | ENGLISH | `minimize this window` | `minimize_window {}` | 100% | ✓ |
| Browser & web | ENGLISH | `search youtube for lofi music` | `youtube_search {"query": "lofi music"}` | 100% | ✓ |
| Browser & web | ENGLISH | `login to github username dev_user password Test@123` | `web_login {"password": "Test@123", "site": "github", "username": "dev_user"}` | 100% | ✓ |
| Keyboard & clipboard | ENGLISH | `select all` | `select_all {}` | 100% | ✓ |
| Keyboard & clipboard | ENGLISH | `show clipboard history` | `clipboard_history {}` | 100% | ✓ |
| Timers & productivity | ENGLISH | `set a timer for 5 minutes` | `set_timer {"amount": 5, "unit": "min"}` | 100% | ✓ |
| Timers & productivity | ENGLISH | `calculate 12 + 8` | `calculate {"expression": "12 + 8"}` | 100% | ✓ |
| Security & accounts | ENGLISH | `show the firewall status` | `firewall_status {}` | 100% | ✓ |
| Security & accounts | ENGLISH | `allow port 8080 in the firewall` | `firewall_allow {"port": 8080}` | 100% | ✓ |
| Services & processes | ENGLISH | `show the status of the ssh service` | `service_status {"service": "ssh"}` | 100% | ✓ |
| Services & processes | ENGLISH | `restart the nginx service` | `service_restart {"service": "nginx"}` | 100% | ✓ |
| Software & development | ENGLISH | `list docker containers` | `docker_list {}` | 100% | ✓ |
| Software & development | ENGLISH | `install the numpy python package` | `install_python_package {"name": "numpy"}` | 100% | ✓ |
| AI & models | ENGLISH | `which models are available in ollama` | `list_llm_models {}` | 100% | ✓ |
| AI & models | ENGLISH | `run the llama3 model` | `run_llm {"model": "llama3"}` | 100% | ✓ |
| Terminal & sessions | ENGLISH | `show tmux sessions` | `list_sessions {}` | 100% | ✓ |
| Terminal & sessions | ENGLISH | `run command "ls -la"` | `run_command {"command": "command \"ls -la"}` | 100% | ✗ (see note) |
| System & power | ENGLISH | `what is the cpu usage` | `get_cpu_usage {}` | 100% | ✓ |
| System & power | ENGLISH | `shut down in 10 minutes` | `shutdown {"amount": 10, "unit": "min"}` | 100% | ✓ |
| System & power | ENGLISH | `don't shut down the laptop` | `cancel_shutdown {}` | 100% | ✓ |
| Help & intent handling | ENGLISH | `what can you do` | `help {}` | 99% | ✓ |
| Help & intent handling | ENGLISH | `turn it off` | `clarify {}` | 100% | ✓ |
| Audio & media | HINGLISH | `volume 40 kar do` | `set_volume {"value": 40}` | 100% | ✓ |
| Audio & media | HINGLISH | `gaana pause karo` | `media_pause {}` | 100% | ✓ |
| Display & appearance | HINGLISH | `brightness thoda kam karo` | `brightness_down {}` | 100% | ✓ |
| Display & appearance | HINGLISH | `dark mode on kar do` | `dark_mode_on {}` | 100% | ✓ |
| Network & connectivity | HINGLISH | `wifi ka signal kitna strong hai` | `get_wifi_signal {}` | 100% | ✓ |
| Network & connectivity | HINGLISH | `Redmi Note 12 wifi se connect karo password hello@123` | `connect_wifi {"password": "hello@123", "ssid": "Redmi Note 12"}` | 100% | ✓ |
| GPIO & I2C | HINGLISH | `gpio 17 ko high karo` | `gpio_on {"pin": 17}` | 100% | ✓ |
| GPIO & I2C | HINGLISH | `physical pin 11 ki value padho` | `gpio_read {"numbering": "board", "pin": 11}` | 100% | ✓ |
| GPIO & I2C | HINGLISH | `i2c bus scan karo` | `i2c_scan {}` | 100% | ✓ |
| Hardware & Raspberry Pi | HINGLISH | `pi ka temperature batao` | `get_temperature {}` | 82% | ✓ |
| Hardware & Raspberry Pi | HINGLISH | `fan speed kitni hai` | `get_fan_speed {}` | 100% | ✓ |
| Files & storage | HINGLISH | `file "notes/todo.txt" bana do` | `create_file {"path": "notes/todo.txt"}` | 100% | ✓ |
| Files & storage | HINGLISH | `disk space kitna bacha hai` | `get_disk_space {}` | 100% | ✓ |
| Apps & windows | HINGLISH | `notepad kholo` | `open_app {"app": "notepad"}` | 100% | ✓ |
| Apps & windows | HINGLISH | `is window ko minimize karo` | `minimize_window {}` | 100% | ✓ |
| Browser & web | HINGLISH | `youtube pe lofi music search karo` | `youtube_search {"query": "lofi music"}` | 100% | ✓ |
| Browser & web | HINGLISH | `github pe login karo username dev_user password Test@123` | `web_login {"password": "Test@123", "site": "github", "username": "dev_user"}` | 100% | ✓ |
| Keyboard & clipboard | HINGLISH | `sab select karo` | `select_all {}` | 100% | ✓ |
| Keyboard & clipboard | HINGLISH | `clipboard history dikhao` | `clipboard_history {}` | 100% | ✓ |
| Timers & productivity | HINGLISH | `5 minute ka timer lagao` | `set_timer {"amount": 5, "unit": "min"}` | 100% | ✓ |
| Timers & productivity | HINGLISH | `12 + 8 calculate karo` | `calculate {"expression": "12 + 8"}` | 100% | ✓ |
| Security & accounts | HINGLISH | `firewall ka status batao` | `firewall_status {}` | 100% | ✓ |
| Security & accounts | HINGLISH | `firewall mein port 8080 allow karo` | `firewall_allow {"port": 8080}` | 100% | ✓ |
| Services & processes | HINGLISH | `ssh service ka status dikhao` | `service_status {"service": "ssh"}` | 100% | ✓ |
| Services & processes | HINGLISH | `nginx service restart karo` | `service_restart {"service": "nginx"}` | 100% | ✓ |
| Software & development | HINGLISH | `docker containers list karo` | `docker_list {}` | 100% | ✓ |
| Software & development | HINGLISH | `numpy python package install karo` | `install_python_package {"name": "numpy"}` | 100% | ✓ |
| AI & models | HINGLISH | `ollama pe kaunse models hain` | `list_llm_models {}` | 100% | ✓ |
| AI & models | HINGLISH | `llama3 model chalao` | `run_llm {"model": "llama3"}` | 100% | ✓ |
| Terminal & sessions | HINGLISH | `tmux sessions dikhao` | `list_sessions {}` | 100% | ✓ |
| Terminal & sessions | HINGLISH | `command "ls -la" chalao` | `run_command {"command": "command \"ls -la"}` | 100% | ✗ (see note) |
| System & power | HINGLISH | `cpu usage batao` | `get_cpu_usage {}` | 100% | ✓ |
| System & power | HINGLISH | `10 min baad shutdown kar dena` | `shutdown {"amount": 10, "unit": "min"}` | 100% | ✓ |
| System & power | HINGLISH | `shutdown mat karo` | `cancel_shutdown {}` | 100% | ✓ |
| Help & intent handling | HINGLISH | `tum kya kya kar sakte ho` | `help {}` | 100% | ✓ |
| Help & intent handling | HINGLISH | `ise band kar do` | `close_window {}` | 100% | ✗ (see note) |

</details>

All 72 illustrative commands are in
[`examples/sample_commands.jsonl`](examples/sample_commands.jsonl), with per-platform executor plans in
[`examples/category_examples_windows.json`](examples/category_examples_windows.json)
and [`examples/category_examples_pi.json`](examples/category_examples_pi.json).
v10 got 69 of 72 exactly right (ENGLISH 35 of 36, HINGLISH 34 of 36). The misses:

- ENGLISH `run command "ls -la"` gave `run_command {"command": "command \"ls -la"}`; expected `run_command {"command": "ls -la"}`.
- HINGLISH `command "ls -la" chalao` gave `run_command {"command": "command \"ls -la"}`; expected `run_command {"command": "ls -la"}`.
- HINGLISH `ise band kar do` gave `close_window {}`; expected `clarify {}`.

These commands were written for this demo, so they are illustrative. The accuracy numbers below come from the
held-out test set.

## Test results

### Accuracy

Metric: **exact match**, meaning the action and every typed argument must be identical to the label. Errors and
abstentions stay in the denominator. Same checkpoint on both devices: SHA-256 `c91d44e4a687152cd65d86d56cfc0e453a60a1afe14f8c395e8af058f2fc4d5b`.

| Dataset | Rows | Laptop CPU exact | Laptop action-only | Raspberry Pi 5 exact | Pi action-only | About the set |
|---|---|---|---|---|---|---|
| Held-out test (unseen templates) | 24,498 | 94.87% (23,241) | 96.05% | 94.87% (23,241) | 96.05% | 24,498 rows · all 371 actions · template families never seen in training |
| Practical dev (A) | 1,321 | 96.44% (1,274) | 96.90% | 96.44% (1,274) | 96.90% | 1,321 realistic phrasings · used during development |
| Golden dev (B) | 383 | 97.39% (373) | 97.91% | 97.39% (373) | 97.91% | 383 hand-checked commands · used during development |

The held-out test split is made of whole template families that were never used for training. It was evaluated once
for this release. Practical dev and golden dev guided earlier development, so treat them as regression checks.
Earlier development measurements on the same checkpoint (Windows CPU):
Grouped dev (reserved template groups) 96.50% (30,596/31,707), Validation 98.22% (23,467/23,892).

Held-out test by language:

| Language | Rows | Exact | Action-only |
|---|---|---|---|
| HINGLISH (Roman script, incl. a small Devanagari slice) | 9,958 | 95.43% | 96.37% |
| ENGLISH | 8,038 | 92.56% | 94.31% |
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
| Raspberry Pi 5 | 1 | 32.8 ms | 40.8 ms | 30 | 91 | 8.3 s | 533 MB |
| Raspberry Pi 5 | 2 | 40.4 ms | 47.6 ms | 24 | 88 | 8.3 s | 533 MB |
| Raspberry Pi 5 | 4 | 36.2 ms | 44.0 ms | 27 | 100 | 8.3 s | 533 MB |

Full accuracy run speed (batched, held-out test):

| Device | Seconds | Commands/s | Threads | Batch |
|---|---|---|---|---|
| Laptop i7-1360P | 95.86 | 255.5 | 4 | 64 |
| Raspberry Pi 5 | 331.81 | 73.8 | 2 | 64 |

### Temperature

| Device | Run | Sensor | Start | Max | Mean | Pi throttle flags after run |
|---|---|---|---|---|---|---|
| Laptop i7-1360P | Accuracy run (26k rows, 4 threads) | Windows ACPI thermal zone (typeperf, Thermal Zone Information) | 60.9 °C | 95.9 °C | 85.3 °C | n/a (not a Pi) |
| Laptop i7-1360P | Latency benchmark (1/2/4 threads) | Windows ACPI thermal zone (typeperf, Thermal Zone Information) | 53.9 °C | 85.9 °C | 71.2 °C | n/a (not a Pi) |
| Raspberry Pi 5 | Accuracy run | sysfs cpu-thermal (Raspberry Pi SoC sensor) | 51.8 °C | 73.2 °C | 68.2 °C | throttled=0x0 |
| Raspberry Pi 5 | Latency benchmark | sysfs cpu-thermal (Raspberry Pi SoC sensor) | 41.9 °C | 65.5 °C | 56.5 °C | throttled=0x0 |

Raspberry Pi 5 notes. The first attempt ran the held-out test in one go with 4 threads. After 202 s and 15,040 commands the SoC reached about 85 °C with the fan near 9,900 RPM, and the Pi dropped off the network until it was power-cycled. The reported Pi results come from a second, gentler run: 2 threads, the held-out test split into five chunks of 5,000 rows, and a cool-down below 65 °C before each chunk. Temperature, the 5 V rail, throttle flags and fan speed were logged every 5 seconds (test_results/rpi5/sensors.log). That run finished with throttle flags at 0x0 throughout, a peak of 73.2 °C and a 5 V rail between 5.05 V and 5.20 V. The Pi made exactly the same predictions as the laptop: the same 1,314 commands were wrong on both devices across the three sets. On the Pi, 1 thread gives the lowest single-command latency; extra threads mainly help batched throughput. For long jobs on a Pi 5, use 1 or 2 threads, keep the active cooler and a 5 V / 5 A supply, and watch `vcgencmd measure_temp` and `vcgencmd get_throttled`.

### Test devices

| Device | CPU | OS | Python / PyTorch |
|---|---|---|---|
| Laptop | 13th Gen Intel(R) Core(TM) i7-1360P | Windows-11-10.0.26200-SP0 | Python 3.13.14 / torch 2.12.0+cpu |
| Raspberry Pi 5 | Raspberry Pi 5 Model B Rev 1.1 (Arm Cortex-A76, 4 cores) | Linux-6.12.47+rpt-rpi-2712-aarch64-with-glibc2.41 | Python 3.13.5 / torch 2.8.0+cpu |

## Download and test

### 1. Get the code and the model

```bash
git clone https://github.com/sraivante/tiny-agentic-home-robotic-for-edge-device-v10.git
cd tiny-agentic-home-robotic-for-edge-device-v10
python download_model.py      # 96.2 MB from Hugging Face, SHA-256 verified
```

Or download the [ZIP of the repository](https://github.com/sraivante/tiny-agentic-home-robotic-for-edge-device-v10/archive/refs/heads/main.zip). The model file alone is at
[sraivante/tiny-agentic-home-robotic-for-edge-device-v10](https://huggingface.co/sraivante/tiny-agentic-home-robotic-for-edge-device-v10/resolve/main/models/a100_minilm_v10_quoted/best.pt).

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
python quickstart.py                                  # demo: ENGLISH first, then HINGLISH
python quickstart.py "turn off the wifi" "wifi band karo"
python quickstart.py --json "set a timer for 5 minutes"
```

From Python:

```python
from tinyagent.runtime import Predictor
model = Predictor("models/a100_minilm_v10_quoted/best.pt", threads=2)
print(model.predict("set the volume to 40"))    # ENGLISH
print(model.predict("volume 40 kar do"))        # HINGLISH
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
| `config.json` | Model metadata; also the file Hugging Face uses to count downloads |
| `tinyagent/` | Model and inference code (`Predictor`) plus the base executor |
| `union_catalog.json` | The 371 actions with slots, risk, sudo and platform metadata |
| `quickstart.py`, `download_model.py` | Command-line demo and verified downloader |
| `scripts/` | `evaluate.py`, `benchmark.py`, `category_examples.py`, `build_docs.py` |
| `app.py`, `lab_executor.py`, `executors/`, `web/` | The sample executor lab |
| `test_results/` | Accuracy, speed and temperature reports for the laptop and the Raspberry Pi 5 |
| `examples/` | Illustrative commands and per-platform executor plans |
| `docs/index.html` | The HTML test guide ([online](https://sraivante.github.io/tiny-agentic-home-robotic-for-edge-device-v10/)) |

## License and credits

Code and model weights: Apache-2.0, Copyright (c) 2026 sraivante. The word encoder is initialised from
[sentence-transformers/all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
(Apache-2.0); see `provenance/base_models/minilm_l6/`. The training dataset is synthetic and is not published.
Provided as-is, without warranty. You are responsible for anything you let an executor do.
