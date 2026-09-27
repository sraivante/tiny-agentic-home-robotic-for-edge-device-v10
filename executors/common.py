from __future__ import annotations

import importlib.util
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

from tinyagent.executor import ExecutionError
from tinyagent.schema import CATALOG, ROOT


def profile(e, action, key, default=None):
    value = e.settings.get(action, {}).get(key, default)
    if value is None or value == "":
        raise ExecutionError(f"Configure {action}.{key} in executor_config.json; the model schema does not supply this value.")
    return value


def seconds(a, maximum=3600):
    value = float(a.get("amount", 1)) * {"sec": 1, "min": 60, "hour": 3600}[a.get("unit", "sec")]
    if not 0 < value <= maximum:
        raise ExecutionError(f"Duration must be between 0 and {maximum} seconds")
    return value


def name(value):
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_.:@/+=-]+", value) or value.startswith("-"):
        raise ExecutionError("Invalid identifier: " + value)
    return value


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ExecutionError(f"Expected an integer from {low} to {high}")
    return value


def bind(e, action, function, platforms=("windows", "pi"), mutates=True,
         programs=(), modules=(), configuration=(), description=None, preflight=None):
    e.coverage.setdefault(action, set()).update(platforms)
    if e.platform not in platforms and action in e.adapters:
        return
    e.add(action, function, description or CATALOG[action]["desc"], platforms, mutates)
    e.native_builders.pop(action, None)
    e.requirements[action] = {"programs": list(programs), "modules": list(modules),
                              "configuration": list(configuration)}
    if preflight:
        e.preflights[action] = preflight
    else:
        e.preflights.pop(action, None)


def command(e, action, builder, platforms=("windows", "pi"), mutates=True,
            programs=(), configuration=(), timeout=45, description=None):
    def build(a):
        argv = builder(a) if callable(builder) else list(builder)
        return e.native([str(x) for x in argv], timeout=timeout)
    def execute(a):
        step = build(a)
        return e.command(step["argv"], timeout=step["timeout_seconds"])
    bind(e, action, execute, platforms, mutates, programs, configuration=configuration, description=description)
    if e.platform in platforms or action not in e.native_builders and e.adapters[action].platforms == platforms:
        e.native_builders[action] = build


def ps(e, action, script, mutates=True, configuration=(), timeout=45):
    prelude = "$ErrorActionPreference='Stop'; $a=$env:TINY_UNION_ARGUMENTS | ConvertFrom-Json; "
    def build(a):
        resolved = script(a) if callable(script) else script
        return e.native(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", prelude + resolved],
                        timeout, {"TINY_UNION_ARGUMENTS": json.dumps(a | {"_config": e.settings.get(action, {})})})
    bind(e, action, lambda a: e.powershell(script(a) if callable(script) else script,
                                         a | {"_config": e.settings.get(action, {})}), ("windows",), mutates,
         programs=("powershell.exe",), configuration=configuration)
    if e.platform == "windows" or e.adapters[action].platforms == ("windows",):
        e.native_builders[action] = build


def output_path(e, stem, extension):
    directory = e.files_root / "outputs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.{extension}"


def detached(e, argv, key=None, visible=False):
    if not argv or not e._executable(str(argv[0])):
        raise ExecutionError("Required program is unavailable: " + str(argv[0] if argv else "empty command"))
    logfile = output_path(e, "process", "log")
    options = {"cwd": str(e.files_root), "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_CONSOLE if visible else subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    if visible:
        options.pop("stdin", None)
        process = subprocess.Popen([str(x) for x in argv], **options)
    else:
        with logfile.open("ab") as stream:
            process = subprocess.Popen([str(x) for x in argv], stdout=stream, stderr=stream, **options)
    e.processes[key or str(process.pid)] = process
    e.trace.append({"kind": "background_process", "argv": argv, "pid": process.pid, "log": str(logfile)})
    return {"status": "started", "pid": process.pid, "log": None if visible else str(logfile), "visible": visible}


def stop_owned(e, key):
    process = e.processes.pop(key, None)
    if not process:
        raise ExecutionError("No running task owned by this executor: " + key)
    import psutil
    parent = psutil.Process(process.pid)
    for child in parent.children(recursive=True):
        child.terminate()
    parent.terminate()
    return {"stopped_pid": process.pid}


def is_module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ValueError, ImportError, ModuleNotFoundError):
        return False


def load_settings():
    path = ROOT / "executor_config.json"
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


def register_all(e):
    from . import desktop, portable, windows, linux, hardware, integrations
    portable.register(e)
    desktop.register(e)
    linux.register(e)
    hardware.register(e)
    windows.register(e)
    integrations.register(e)
    absent = set(CATALOG) - set(e.adapters)
    if absent:
        raise RuntimeError("Missing executor implementations: " + ", ".join(sorted(absent)))
