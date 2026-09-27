from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = {r["action"]: r for r in json.loads((ROOT / "union_catalog.json").read_text("utf-8"))}
CATEGORICAL = {
    "action", "alt", "app", "edge", "folder", "governor", "interface", "key",
    "mode", "numbering", "orientation", "output", "page", "pull", "unit",
}


def canonical_value(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_command(action, args):
    return json.dumps([action, args], ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def lower_chars(text):
    # One codepoint per input character preserves exact copy offsets.
    return "".join(c.lower() if len(c.lower()) == 1 else c for c in text)


def find_span(text, value):
    needle = str(value)
    pattern = re.escape(needle)
    if isinstance(value, (int, float)):
        pattern = r"(?<!\d)" + pattern + r"(?!\d)"
    match = re.search(pattern, text)
    return (match.start(), match.end() - 1) if match else None


def find_argument_span(text, key, args):
    """Prefer a decimal operand over an equal digit inside another argument.

    Original preparation could supervise value=0 using the first character of
    an earlier 0x27 address. This helper preserves the target value and chooses
    its complete occurrence when present; existing checkpoints are unchanged.
    """
    value = args[key]
    if type(value) is not int:
        return find_span(text, value)
    blocked = [(m.start(), m.end()) for m in re.finditer(r"0[xX][0-9a-fA-F]+", text)]
    blocked.extend((m.start(), m.end()) for m in re.finditer(
        r"\b(?:windows|raspberry\s*pi|raspi|rpi|pi)\s+(?:os\s+)?\d+(?:\.\d+)*\b", text, re.I))
    for other_key, other_value in args.items():
        if other_key != key and isinstance(other_value, str):
            blocked.extend((m.start(), m.end()) for m in re.finditer(r"(?<!\w)"+re.escape(other_value)+r"(?!\w)", text))
    for match in re.finditer(r"(?<!\d)"+re.escape(str(value))+r"(?!\d)", text):
        if not any(start < match.end() and end > match.start() for start, end in blocked):
            return match.start(), match.end()-1
    return None


def target_string(action, args):
    return " ".join([action] + [f"{k}={canonical_value(v)}" for k, v in args.items()])


def validate_command(action, args):
    errors = []
    if not isinstance(action, str) or action not in CATALOG:
        return ["Unknown action"]
    if not isinstance(args, dict):
        return ["Arguments must be an object"]
    spec = CATALOG[action]
    missing = set(spec["slots"]) - set(args)
    if missing and action != "clarify":
        errors.append("Missing arguments: " + ", ".join(sorted(missing)))
    extra = set(args) - set(spec["slots"] + spec["optional"])
    if extra:
        errors.append("Unexpected arguments: " + ", ".join(sorted(extra)))
    for k, v in args.items():
        if not isinstance(v, (str, int, float)) or isinstance(v, bool):
            errors.append(f"Invalid type for {k}")
        if isinstance(v, str) and (not v.strip() or len(v) > 4096 or "\x00" in v):
            errors.append(f"Invalid text for {k}")
    if errors:
        return errors
    numeric_bounds = {"set_volume": ("value", 0, 100), "set_brightness": ("value", 0, 100),
                      "gpio_pwm": ("duty", 0, 100), "gpio_servo": ("angle", 0, 180),
                      "set_swappiness": ("value", 0, 200)}
    if action in numeric_bounds:
        k, lo, hi = numeric_bounds[action]
        if k in args and (type(args[k]) is not int or not lo <= args[k] <= hi):
            errors.append(f"{k} must be an integer from {lo} to {hi}")
    if "pin" in args:
        hi, lo = (40, 1) if args.get("numbering") == "board" else (27, 0)
        if type(args["pin"]) is not int or not lo <= args["pin"] <= hi:
            errors.append(f"pin must be an integer from {lo} to {hi}")
    for k in ("port", "pid", "amount", "step", "count", "freq", "baud", "size"):
        if k in args and (type(args[k]) is not int or args[k] <= 0):
            errors.append(f"{k} must be a positive integer")
    if type(args.get("port")) is int and args["port"] > 65535:
        errors.append("port must be at most 65535")
    if "numbering" in args and args["numbering"] != "board":
        errors.append("numbering must be board when specified")
    if "unit" in args and args["unit"] not in {"sec", "min", "hour"}:
        errors.append("unit must be sec, min, or hour")
    if action == "gpio_set_mode" and args.get("mode") not in {"input", "output"}:
        errors.append("mode must be input or output")
    if action == "gpio_set_pull" and args.get("pull") not in {"up", "down", "none"}:
        errors.append("pull must be up, down, or none")
    return errors
