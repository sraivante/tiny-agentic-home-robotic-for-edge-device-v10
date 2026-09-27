"""Latency, throughput, memory and temperature of Union Command v10 on this CPU. Never runs the executor.

Single-request latency uses one warm request per catalog action (371 commands from
command_library.json), the same protocol as the original Windows benchmark.

    python scripts/benchmark.py --threads 1,2,4 --output benchmark.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import psutil  # noqa: E402
import torch  # noqa: E402

from device_info import TemperatureSampler, describe, throttled  # noqa: E402
from download_model import ensure_model  # noqa: E402


def percentile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--threads", default="2,4", help="Comma-separated CPU thread counts")
    parser.add_argument("--repeats", type=int, default=2, help="Passes over the 371 commands per thread count")
    parser.add_argument("--batch-size", type=int, default=32, help="Batch size for the throughput pass")
    parser.add_argument("--output", default="benchmark_report.json")
    args = parser.parse_args()
    thread_counts = [int(t) for t in args.threads.split(",")]

    from tinyagent.runtime import Predictor
    library = json.loads((ROOT / "command_library.json").read_text("utf-8"))
    dataset = next(iter(library.values()))
    texts = [rows[0]["text"] for rows in dataset.values() if rows]
    checkpoint = ensure_model(quiet=True)
    process = psutil.Process()
    rss_before = process.memory_info().rss
    begin = time.perf_counter()
    model = Predictor(checkpoint, threads=thread_counts[0])
    load_seconds = time.perf_counter() - begin
    report = {"device": describe(thread_counts[0]), "checkpoint_sha256": model.checkpoint_sha256,
              "parameters": model.parameters, "model_bytes": model.model_bytes,
              "load_seconds": round(load_seconds, 2), "requests_per_pass": len(texts),
              "protocol": "warm single-request latency, one command per catalog action; then batched throughput",
              "throttled_before": throttled(), "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "runs": []}
    sampler = TemperatureSampler(interval=1.0).start()
    try:
        for threads in thread_counts:
            torch.set_num_threads(threads)
            for text in texts[:20]:
                model.predict(text)
            latencies = []
            for _ in range(args.repeats):
                for text in texts:
                    start = time.perf_counter()
                    model.predict(text)
                    latencies.append((time.perf_counter() - start) * 1000)
            start = time.perf_counter()
            for offset in range(0, len(texts), args.batch_size):
                model.predict(texts[offset:offset + args.batch_size])
            batch_seconds = time.perf_counter() - start
            run = {"threads": threads, "requests": len(latencies),
                   "latency_ms": {"median": round(statistics.median(latencies), 2),
                                  "mean": round(statistics.fmean(latencies), 2),
                                  "p95": round(percentile(latencies, 0.95), 2),
                                  "p99": round(percentile(latencies, 0.99), 2),
                                  "max": round(max(latencies), 2)},
                   "single_request_per_second": round(1000 / statistics.fmean(latencies), 1),
                   "batched_commands_per_second": round(len(texts) / batch_seconds, 1),
                   "batch_size": args.batch_size}
            report["runs"].append(run)
            print(json.dumps(run), flush=True)
    finally:
        report["temperature"] = sampler.stop()
    report["throttled_after"] = throttled()
    report["process_rss_mb"] = round(process.memory_info().rss / 2**20, 1)
    report["model_rss_increase_mb"] = round((process.memory_info().rss - rss_before) / 2**20, 1)
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2), "utf-8")
    print(json.dumps({k: report[k] for k in ("load_seconds", "process_rss_mb", "temperature")}, indent=2))


if __name__ == "__main__":
    main()
