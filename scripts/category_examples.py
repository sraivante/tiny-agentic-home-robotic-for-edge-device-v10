"""Run sample commands from every category through the model and the SAMPLE executor.

For each command: the model's structured output, the executor adapter/tool that would run it on
this platform, the exact native command (when there is one), and the risk-gate decision.
With --execute-safe, commands whose effective risk is "safe" and that do not change the device
(read-only: time, CPU usage, temperature, disk space, ...) are actually executed and their output
is recorded. Nothing else is executed.

    python scripts/category_examples.py --output examples/category_examples_windows.json --execute-safe
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

from categories import category_for  # noqa: E402
from device_info import describe  # noqa: E402
from download_model import ensure_model  # noqa: E402

RISK_LEVELS = {"safe": 0, "caution": 1, "critical": 2}
SAMPLES = [
    ("Audio & media", "volume 40 kar do"),
    ("Audio & media", "gaana pause karo"),
    ("Display & appearance", "brightness thoda kam karo"),
    ("Display & appearance", "dark mode on kar do"),
    ("Network & connectivity", "wifi ka signal kitna strong hai"),
    ("Network & connectivity", "connect to wifi Redmi Note 12 password hello@123"),
    ("GPIO & I2C", "gpio 17 ko high karo"),
    ("GPIO & I2C", "physical pin 11 ki value padho"),
    ("GPIO & I2C", "i2c bus scan karo"),
    ("Hardware & Raspberry Pi", "pi ka temperature batao"),
    ("Hardware & Raspberry Pi", "fan speed kitni hai"),
    ("Files & storage", 'create file "notes/todo.txt"'),
    ("Files & storage", "disk space kitna bacha hai"),
    ("Apps & windows", "notepad kholo"),
    ("Apps & windows", "is window ko minimize karo"),
    ("Browser & web", "youtube pe lofi music search karo"),
    ("Browser & web", "login to github username dev_user password Test@123"),
    ("Keyboard & clipboard", "sab select karo"),
    ("Keyboard & clipboard", "clipboard history dikhao"),
    ("Timers & productivity", "5 minute ka timer lagao"),
    ("Timers & productivity", "calculate 12 + 8"),
    ("Security & accounts", "firewall ka status batao"),
    ("Security & accounts", "firewall mein port 8080 allow karo"),
    ("Services & processes", "ssh service ka status dikhao"),
    ("Services & processes", "nginx service restart karo"),
    ("Software & development", "docker containers list karo"),
    ("Software & development", "numpy python package install karo"),
    ("AI & models", "ollama pe kaunse models hain"),
    ("AI & models", "llama3 model chalao"),
    ("Terminal & sessions", "tmux sessions dikhao"),
    ("Terminal & sessions", 'run command "ls -la"'),
    ("System & power", "cpu usage batao"),
    ("System & power", "10 min baad shutdown kar dena"),
    ("Help & intent handling", "tum kya kya kar sakte ho"),
    ("Help & intent handling", "turn it off"),
]


def shorten(value, limit=240):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


PS_PRELUDE = "$ErrorActionPreference='Stop'; $a=$env:TINY_UNION_ARGUMENTS | ConvertFrom-Json; "


def clean_command(step):
    """Portable, path-free rendering of a native step: program name + its arguments/script."""
    if step.get("kind") != "process":
        return step.get("display", "")
    argv = [str(x) for x in step["argv"]]
    program = Path(argv[0].replace("\\", "/")).name
    if program.lower().startswith("powershell"):
        script = argv[-1][len(PS_PRELUDE):] if argv[-1].startswith(PS_PRELUDE) else argv[-1]
        return "powershell: " + " ".join(script.split())
    rest = [a for a in argv[1:]]
    if program.lower().startswith("python"):
        rest = [a for a in rest if not a.lower().endswith(".exe")]
    return " ".join([program] + rest)


def tool_name(executor, action, plan):
    adapter = executor.adapters.get(action)
    module = adapter.function.__module__ if adapter else None
    step = (plan or {}).get("commands", [{}])[0]
    if step.get("kind") == "process":
        program = Path(step["argv"][0]).name
        return f"native process: {program}", module
    return f"python adapter ({module})", module


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--execute-safe", action="store_true", help="Execute read-only safe commands")
    args = parser.parse_args()

    from tinyagent.runtime import Predictor
    from lab_executor import LabExecutor
    model = Predictor(ensure_model(quiet=True), threads=args.threads)
    workdir = tempfile.mkdtemp(prefix="union-v10-examples-")
    executor = LabExecutor(workdir)
    rows = []
    for intended, text in SAMPLES:
        prediction = model.predict(text)
        action, arguments = prediction["action"], prediction["args"]
        row = {"intended_category": intended, "command": text, "action": action, "args": arguments,
               "predicted_category": category_for(action), "confidence": round(prediction["confidence"], 4),
               "latency_ms": prediction["latency_ms"], "validation_errors": prediction["validation_errors"],
               "platform": executor.platform}
        plan = None
        if not prediction["validation_errors"]:
            try:
                plan = executor.preview(action, arguments)
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                row["plan_error"] = str(exc)
        if plan:
            tool, module = tool_name(executor, action, plan)
            step = plan["commands"][0]
            row.update(executor_tool=tool, handler_module=module, effect=plan["effect"], effective_risk=plan["risk"],
                       changes_device=plan["changes_device"], needs_sudo=plan["needs_sudo"],
                       live_available=plan["live_available"], availability_reason=plan["availability_reason"],
                       planned_command=shorten(clean_command(step), 300),
                       default_gate=("allowed" if RISK_LEVELS[plan["risk"]] <= RISK_LEVELS["caution"]
                                     else "blocked (critical; needs --allow-risk critical)"))
            if (args.execute_safe and plan["live_available"] and plan["risk"] == "safe"
                    and not plan["changes_device"]):
                result = executor.execute_automatically(plan)
                row["executed"] = True
                row["execution_status"] = result.get("status")
                row["execution_output"] = shorten(result.get("result", result.get("error", "")))
            else:
                executor.pending.pop(plan["id"], None)
                row["executed"] = False
        rows.append(row)
        print(f"{text!r:55} -> {action} {json.dumps(arguments, ensure_ascii=False)}"
              f" | {row.get('executor_tool', row.get('plan_error', 'no plan'))}"
              f"{' | ran: ' + row['execution_output'][:80] if row.get('executed') else ''}", flush=True)
    report = {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "device": describe(args.threads),
              "platform": executor.platform, "checkpoint_sha256": model.checkpoint_sha256,
              "note": "Illustrative commands written for this demo (not dataset rows). Accuracy numbers come "
                      "from the held-out test set, not from this table. Only read-only 'safe' commands were executed.",
              "rows": rows}
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")


if __name__ == "__main__":
    main()
