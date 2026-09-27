"""Audit registration and plan construction only. Never invokes device actions."""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab_executor import LabExecutor
from tinyagent.schema import ROOT, CATALOG


def main():
    executor = LabExecutor(ROOT / "runtime/audit")
    library = json.loads((ROOT / "command_library.json").read_text("utf-8"))
    rows, errors, expected = [], [], []
    for action in CATALOG:
        available, reason = executor.availability(action)
        adapter = executor.adapters.get(action)
        rows.append({"action": action, "implemented": adapter is not None,
                     "platforms": sorted(executor.coverage.get(action, [])),
                     "ready_here": available, "reason": reason,
                     "requirements": executor.requirements.get(action, {}),
                     "handler_module": adapter.function.__module__ if adapter else None})
        if action not in executor.native_builders or not available: continue
        seen = set()
        for dataset in library.values():
            for example in dataset[action]:
                identity = json.dumps(example["args"], sort_keys=True)
                if identity in seen: continue
                seen.add(identity)
                try:
                    plan = executor.preview(action, example["args"])
                    executor.pending.pop(plan["id"], None)
                except (ValueError, OSError) as exc:
                    expected.append({"action": action, "args": example["args"], "requirement": str(exc)})
                except Exception as exc:
                    errors.append({"action": action, "args": example["args"], "error": repr(exc)})
    report = {"created_at": dt.datetime.now().isoformat(), "platform": executor.platform,
              "actions": len(CATALOG), "implemented": len(executor.adapters),
              "ready_here": sum(row["ready_here"] for row in rows),
              "note": "Registration/dependency and non-executing plan audit. This is not hardware or live-action verification.",
              "unexpected_plan_errors": errors, "sample_prerequisites": expected, "coverage": rows}
    (ROOT / "executor_coverage.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")
    print(json.dumps({k: report[k] for k in ("actions", "implemented", "ready_here", "unexpected_plan_errors")}, indent=2))
    if errors or len(executor.adapters) != len(CATALOG): raise SystemExit(1)


if __name__ == "__main__": main()
