"""Automatic execution of validated model outputs, with concrete command previews.

The UI's Run & execute action authorizes this flow. The original copied executor
retains its direct API confirmation contract; this wrapper supplies the bound
plan confirmation automatically and never substitutes the dataset's target.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shlex
import subprocess
import time
import copy

from tinyagent.executor import Executor, Adapter, ExecutionError
from tinyagent.schema import target_string
from executors.common import is_module, load_settings, register_all

DESKTOP_ACTIONS = set("open_app open_file open_folder open_site open_url open_settings close_app close_window type_text press_key copy cut paste undo redo select_all save_file new_tab close_tab reopen_tab next_tab prev_tab browser_back browser_forward bookmark_page refresh fullscreen zoom_in zoom_out zoom_reset print_page snap_left snap_right show_desktop task_view new_virtual_desktop switch_input_language clipboard_history emoji_panel open_notification_center incognito_window find_in_page scroll_up scroll_down maximize_window minimize_window switch_window web_search youtube_search recent_files web_login".split())


class LabExecutor(Executor):
    def __init__(self, files_root=None):
        self.native_builders = {}
        self.trace = []
        self.coverage, self.requirements, self.preflights = {}, {}, {}
        self.settings = load_settings()
        self.processes, self.gpio_devices, self.gpio_events = {}, {}, []
        self.action_history, self.notifications, self.reminders = [], [], []
        self.alarm_task, self.previous = None, None
        super().__init__(files_root)
        self.native_builders["run_command"] = lambda a: self.native(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", a["command"]]
            if self.platform == "windows" else ["/bin/sh", "-c", a["command"]], timeout=20)
        if self.platform == "windows":
            self.native_builders["shutdown"] = lambda a: self.power_preview(a, False)
            self.native_builders["restart"] = lambda a: self.power_preview(a, True)
        register_all(self)

    def add(self, name, function, description, platforms=("windows", "pi"), mutates=False, executable=None):
        self.coverage.setdefault(name, set()).update(platforms)
        super().add(name, function, description, platforms, mutates, executable)

    def availability(self, action):
        adapter = self.adapters.get(action)
        if not adapter:
            return False, "No executor registered for this action"
        if self.platform not in adapter.platforms:
            return False, "Executor requires " + ", ".join(adapter.platforms)
        req = self.requirements.get(action, {})
        missing = [p for p in req.get("programs", []) if not self._executable(p)]
        if adapter.executable and not self._executable(adapter.executable): missing.append(adapter.executable)
        if missing: return False, "Install required program(s): " + ", ".join(missing)
        missing = [p for p in req.get("modules", []) if not is_module(p)]
        if missing: return False, "Install optional Python module(s): " + ", ".join(missing)
        missing = [p for p in req.get("configuration", []) if not self.settings.get(action, {}).get(p)]
        if missing: return False, f"Configure executor_config.json → {action}: " + ", ".join(missing)
        return True, "Executor ready; arguments, permissions and device/service access are checked when run."

    def require_previous(self):
        if not self.previous:
            raise ExecutionError("There is no successfully executed action to repeat for this model")

    def repeat_previous(self):
        self.require_previous()
        action, args = self.previous
        return self.execute_automatically(self.preview(action, copy.deepcopy(args)))

    def native(self, argv, timeout=15, environment=None):
        argv = [self._executable(argv[0]) or argv[0], *argv[1:]]
        return {"kind": "process", "argv": argv,
                "display": subprocess.list2cmdline(argv) if self.platform == "windows" else shlex.join(argv),
                "cwd": str(self.files_root), "timeout_seconds": timeout,
                "environment": environment or {}}

    def ps(self, name, script, description, mutates=False):
        super().ps(name, script, description, mutates)
        prelude = "$ErrorActionPreference='Stop'; $a=$env:TINY_UNION_ARGUMENTS | ConvertFrom-Json; "
        self.native_builders[name] = lambda a: self.native(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", prelude + script],
            environment={"TINY_UNION_ARGUMENTS": json.dumps(a)})

    def cli(self, name, argv, description, platforms=("pi",), mutates=False):
        super().cli(name, argv, description, platforms, mutates)
        self.native_builders[name] = lambda a: self.native(argv(a) if callable(argv) else list(argv))

    def power_preview(self, a, restart):
        seconds = a.get("amount", 30) * {"sec": 1, "min": 60, "hour": 3600}.get(a.get("unit", "sec"), 1)
        if "at" in a:
            target = dt.datetime.combine(dt.date.today(), dt.time.fromisoformat(a["at"]))
            if target <= dt.datetime.now():
                target += dt.timedelta(days=1)
            seconds = int((target - dt.datetime.now()).total_seconds())
        if not 0 <= seconds <= 315360000:
            raise ExecutionError("Shutdown delay is outside the OS limit")
        return self.native(["shutdown.exe", "/r" if restart else "/s", "/t", str(seconds)])

    def preview(self, action, args):
        plan = self.plan(action, args)
        try:
            if plan["live_available"] and action in self.preflights:
                try:
                    self.preflights[action](args)
                except (ValueError, OSError) as exc:
                    plan.update(live_available=False, availability_reason=str(exc))
            if action in DESKTOP_ACTIONS and self.desktop.target:
                plan["desktop_target"] = copy.deepcopy(self.desktop.target)
            if plan["live_available"] and action in self.native_builders:
                steps = [self.native_builders[action](args)]
                program = steps[0]["argv"][0]
                if not self._executable(program):
                    plan.update(live_available=False, availability_reason=f"Required program is unavailable: {program}")
            else:
                steps = [{"kind": "adapter", "action": action, "args": args,
                          "display": target_string(action, args), "cwd": str(self.files_root),
                          "effect": plan["effect"]}]
            plan["commands"] = steps
            # The one-use base plan must retain the preflight decision too.
            with self.lock:
                self.pending[plan["id"]].update(live_available=plan["live_available"], availability_reason=plan["availability_reason"])
            return plan
        except Exception:
            with self.lock:
                self.pending.pop(plan["id"], None)
            raise

    def command(self, argv, timeout=15, environment=None):
        self.trace.append({"kind": "process", "argv": argv, "cwd": str(self.files_root)})
        return super().command(argv, timeout, environment)

    def execute_automatically(self, plan):
        """Called under the server's execution lock. Native previews are frozen."""
        self.trace = []
        action = plan["action"]
        started = time.time()
        # Keyboard actions are bound to the previewed window, even if the user
        # changes the selection between prediction and execution.
        if plan.get("desktop_target") and action not in {"open_app", "open_file", "open_folder", "open_site", "open_url", "open_settings", "web_search", "youtube_search"}:
            self.desktop.select(plan["desktop_target"]["id"])
        original = self.adapters[action]
        steps = plan["commands"]
        if steps[0]["kind"] == "process":
            step = steps[0]
            self.adapters[action] = Adapter(
                lambda a: self.command(step["argv"], step["timeout_seconds"],
                                       os.environ.copy() | step["environment"]),
                original.description, original.platforms, original.mutates, original.executable)
        else:
            self.trace.append(steps[0])
        try:
            result = self.execute(plan["id"], "live", plan["required_confirmation"])
            if action in {"unknown", "clarify"} and result["status"] == "completed":
                result.update(status=result["result"]["status"], executed=False)
            if action in DESKTOP_ACTIONS and self.desktop.target:
                try:
                    time.sleep(.25)
                    result["desktop"] = self.desktop.capture()
                except Exception as exc:
                    result["capture_note"] = str(exc)
            result.update(commands_executed=copy.deepcopy(self.trace), started_at=started, finished_at=time.time())
            if result["status"] == "completed" and action not in {"repeat_last", "show_command_history"}:
                self.previous = (action, copy.deepcopy(plan["args"]))
            self.action_history.append({"action": action, "args": redact(plan["args"]), "status": result["status"], "time": result["finished_at"]})
            self.action_history[:] = self.action_history[-100:]
            return result
        finally:
            self.adapters[action] = original


def redact(value):
    """Public previews retain commands but never display credential values."""
    if isinstance(value, dict):
        return {k: "[redacted]" if any(word in k.lower() for word in ("password", "token", "secret")) else redact(v) for k, v in value.items()}
    if isinstance(value, list): return [redact(v) for v in value]
    return value
