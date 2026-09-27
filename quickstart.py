"""Parse natural-language device commands with Union Command v10 (prediction only, nothing is executed).

    python quickstart.py                         # built-in demo commands
    python quickstart.py "volume 40 kar do"      # your own command(s)
    python quickstart.py --json "wifi on karo"   # full JSON output
    python quickstart.py --threads 4 ...         # CPU threads (default 2)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from download_model import ensure_model  # noqa: E402

DEMO = [
    "volume 40 kar do",
    "brightness thoda kam karo",
    "what is the cpu temperature",
    "gpio 17 ko high karo",
    "physical pin 11 ki value padho",
    'create file "notes/todo.txt"',
    "notepad kholo",
    "youtube pe lofi music search karo",
    "5 minute ka timer lagao",
    "calculate 12 + 8",
    "10 min baad shutdown kar dena",
    "shutdown mat karo",
    "connect to wifi Redmi Note 12 password hello@123",
    "turn it off",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("commands", nargs="*", help="Commands to parse (max 256 characters each)")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--json", action="store_true", help="Print the full prediction JSON")
    args = parser.parse_args()
    checkpoint = ensure_model(quiet=True)
    from tinyagent.runtime import Predictor
    start = time.perf_counter()
    model = Predictor(checkpoint, threads=args.threads)
    print(f"Loaded Union Command v10 in {time.perf_counter() - start:.1f}s "
          f"({model.parameters:,} parameters, {model.model_bytes / 1e6:.1f} MB, {args.threads} CPU threads)\n")
    for text in args.commands or DEMO:
        result = model.predict(text)
        if args.json:
            print(json.dumps({"text": text, **result}, ensure_ascii=False, indent=2))
            continue
        arguments = json.dumps(result["args"], ensure_ascii=False)
        flag = "" if not result["validation_errors"] else "  [invalid: " + "; ".join(result["validation_errors"]) + "]"
        print(f"{text!r:52} -> {result['action']} {arguments}  "
              f"({result['confidence']:.0%}, {result['latency_ms']:.1f} ms){flag}")


if __name__ == "__main__":
    main()
