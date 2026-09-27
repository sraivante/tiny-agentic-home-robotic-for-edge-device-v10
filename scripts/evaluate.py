"""Exact-match accuracy of Union Command v10 on any JSONL file. Never runs the executor.

Each line needs {"text": ..., "action": ..., "args": {...}}; optional "lang".
A row counts as correct only if the action AND every typed argument match exactly.

    python scripts/evaluate.py --data examples/sample_commands.jsonl --output my_eval.json
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import psutil  # noqa: E402

from categories import category_for  # noqa: E402
from device_info import TemperatureSampler, describe, throttled  # noqa: E402
from download_model import ensure_model  # noqa: E402


def wilson(success: int, total: int) -> list[float]:
    if not total:
        return [0.0, 0.0]
    z, p = 1.95996398454, success / total
    center = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5) / (1 + z * z / total)
    return [round(center - half, 6), round(center + half, 6)]


def summarize(counts):
    return {key: {"rows": n, "action_correct": a, "exact_correct": e,
                  "action_accuracy": round(a / n, 6), "exact_accuracy": round(e / n, 6),
                  "exact_95pct_wilson": wilson(e, n)}
            for key, (n, a, e) in sorted(counts.items())}


def evaluate_file(model, path: Path, batch_size: int, limit: int | None, errors_out):
    from tinyagent.schema import canonical_command
    with path.open(encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    if limit:
        rows = rows[:limit]
    overall, by_lang, by_category = [0, 0, 0], collections.defaultdict(lambda: [0, 0, 0]), collections.defaultdict(lambda: [0, 0, 0])
    confusion = collections.Counter()
    start = time.perf_counter()
    for offset in range(0, len(rows), batch_size):
        part = rows[offset:offset + batch_size]
        for truth, guess in zip(part, model.predict([r["text"] for r in part])):
            action_ok = truth["action"] == guess["action"]
            exact = canonical_command(truth["action"], truth["args"]) == canonical_command(guess["action"], guess["args"])
            for bucket in (overall, by_lang[str(truth.get("lang", "unknown"))], by_category[category_for(truth["action"])]):
                bucket[0] += 1
                bucket[1] += action_ok
                bucket[2] += exact
            if not exact:
                confusion[(truth["action"], guess["action"])] += 1
                if errors_out:
                    errors_out.write(json.dumps({"file": path.name, "text": truth["text"],
                                                 "expected": {"action": truth["action"], "args": truth["args"]},
                                                 "predicted": {"action": guess["action"], "args": guess["args"]},
                                                 "confidence": round(guess["confidence"], 4)}, ensure_ascii=False) + "\n")
        done = offset + len(part)
        if done // 5000 != offset // 5000 or done == len(rows):
            print(f"  {path.name}: {done:,}/{len(rows):,} rows, {time.perf_counter() - start:.0f}s", flush=True)
    seconds = time.perf_counter() - start
    n, a, e = overall
    return {"file": path.name, "rows": n, "action_correct": a, "exact_correct": e,
            "action_accuracy": round(a / n, 6), "exact_accuracy": round(e / n, 6), "exact_95pct_wilson": wilson(e, n),
            "seconds": round(seconds, 2), "rows_per_second": round(n / seconds, 1),
            "by_language": summarize(by_lang), "by_category": summarize(by_category),
            "top_action_confusions": [{"expected": x, "predicted": y, "count": c}
                                      for (x, y), c in confusion.most_common(15) if x != y]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", action="append", required=True, help="JSONL file (repeatable)")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--limit", type=int, help="Evaluate only the first N rows of each file")
    parser.add_argument("--output", default="evaluation_report.json")
    parser.add_argument("--errors", help="Optional JSONL path for mismatched rows")
    parser.add_argument("--label", default="", help="Free-text label stored in the report")
    args = parser.parse_args()

    from tinyagent.runtime import Predictor
    checkpoint = ensure_model(quiet=True)
    begin = time.perf_counter()
    model = Predictor(checkpoint, threads=args.threads)
    load_seconds = time.perf_counter() - begin
    report = {"label": args.label, "device": describe(args.threads), "checkpoint_sha256": model.checkpoint_sha256,
              "parameters": model.parameters, "model_bytes": model.model_bytes, "model_epoch": model.epoch,
              "batch_size": args.batch_size, "load_seconds": round(load_seconds, 2),
              "metric": "exact match of action and every typed argument; all rows count",
              "throttled_before": throttled(), "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "files": []}
    sampler = TemperatureSampler(interval=2.0).start()
    errors_out = open(args.errors, "w", encoding="utf-8") if args.errors else None
    try:
        for name in args.data:
            report["files"].append(evaluate_file(model, Path(name), args.batch_size, args.limit, errors_out))
            print(f"{Path(name).name}: exact {report['files'][-1]['exact_accuracy']:.4%}, "
                  f"action {report['files'][-1]['action_accuracy']:.4%}", flush=True)
    finally:
        if errors_out:
            errors_out.close()
        report["temperature"] = sampler.stop()
    report["throttled_after"] = throttled()
    report["process_rss_mb"] = round(psutil.Process().memory_info().rss / 2**20, 1)
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")
    print(json.dumps({"temperature": report["temperature"], "rss_mb": report["process_rss_mb"]}, indent=2))


if __name__ == "__main__":
    main()
