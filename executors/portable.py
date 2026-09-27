from __future__ import annotations

import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import venv
from pathlib import Path

import psutil
from tinyagent.executor import ExecutionError
from .common import bind, command, detached, output_path, profile, seconds, stop_owned
from .scheduling import event_time


def register(e):
    bind(e, "unknown", lambda a: {"status": "no_action", "message": "The model did not recognize an actionable request."}, mutates=False)
    bind(e, "clarify", lambda a: {"status": "needs_input", "message": "The model needs a complete request.", "action": a.get("action")}, mutates=False)
    bind(e, "repeat_last", lambda a: e.repeat_previous(), preflight=lambda a: e.require_previous())
    bind(e, "show_command_history", lambda a: list(e.action_history), mutates=False)
    bind(e, "get_load_average", lambda a: {"load_1_5_15_minutes": psutil.getloadavg()}, mutates=False)
    bind(e, "list_open_files", lambda a: open_files(a), mutates=False)
    bind(e, "set_cpu_affinity", lambda a: affinity(a))
    bind(e, "set_process_priority", lambda a: priority(a))
    bind(e, "get_cpu_clock", lambda a: psutil.cpu_freq()._asdict() if psutil.cpu_freq() else {"available": False}, mutates=False)
    bind(e, "search_in_files", lambda a: search_contents(e, a), mutates=False)
    bind(e, "create_symlink", lambda a: symlink(e, a))
    bind(e, "create_virtualenv", lambda a: create_venv(e, a))
    bind(e, "clear_temp_files", lambda a: clear_temporary(e))
    bind(e, "download_file", lambda a: download(e, a))
    bind(e, "get_public_ip", lambda a: public_ip(e), mutates=False)
    bind(e, "set_alarm", lambda a: alarm(e, a))
    bind(e, "cancel_alarm", lambda a: cancel_alarm(e))
    bind(e, "set_reminder", lambda a: reminder(e, a))
    bind(e, "create_calendar_event", lambda a: calendar(e, a))
    bind(e, "run_background", lambda a: detached(e, shell(e, a["command"])))
    bind(e, "watch_command", lambda a: watch(e, a))
    bind(e, "run_script", lambda a: script(e), configuration=("path",))
    bind(e, "run_benchmark", lambda a: benchmark(e, a), mutates=True)
    bind(e, "run_stress_test", lambda a: stress(e, a))
    bind(e, "monitor_health", lambda a: health(e), mutates=False)
    bind(e, "build_project", lambda a: build(e, a))
    bind(e, "scan_ports", lambda a: scan_ports(e, a), mutates=False)
    bind(e, "speed_test", lambda a: speed_test(e), mutates=False, modules=("speedtest",))
    command(e, "install_python_package", lambda a: [sys.executable, "-m", "pip", "install", "--", a["name"]], timeout=600)
    command(e, "docker_list", ["docker", "ps", "-a"], programs=("docker",), mutates=False)
    command(e, "docker_run", lambda a: ["docker", "run", "-d", a["image"]], programs=("docker",), timeout=300)
    command(e, "docker_stop", lambda a: ["docker", "stop", a["container"]], programs=("docker",))
    command(e, "docker_prune", ["docker", "system", "prune", "-f"], programs=("docker",), timeout=300)
    command(e, "docker_compose_up", lambda a: ["docker", "compose", "-f", str(e.path(profile(e, "docker_compose_up", "file"))), "up", "-d"],
            programs=("docker",), configuration=("file",), timeout=300)
    command(e, "run_network_benchmark", lambda a: ["iperf3", "-c", e._host(a["host"]), "-t", "10", "-J"], programs=("iperf3",), timeout=30, mutates=False)
    command(e, "scan_lan", lambda a: ["nmap", "-sn", str(profile(e, "scan_lan", "subnet"))], programs=("nmap",), configuration=("subnet",), mutates=False)
    command(e, "transfer_files", lambda a: ["scp", "--", str(e.path(a["file"])), e._host(a["host"]) + ":" + str(profile(e, "transfer_files", "destination", "~/"))], programs=("scp",), timeout=300)
    bind(e, "remote_shell", lambda a: detached(e, ["ssh", "-o", "BatchMode=yes", "-l", a.get("user", profile(e, "remote_shell", "user", os.environ.get("USERNAME", os.environ.get("USER", "pi")))) ,e._host(a["host"])], visible=True), programs=("ssh",))
    bind(e, "port_forward", lambda a: detached(e, ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-L",
         f"{a['port']}:{profile(e, 'port_forward', 'destination_host', '127.0.0.1')}:{profile(e, 'port_forward', 'destination_port', a['port'])}",
         a.get("host") or profile(e, "port_forward", "ssh_host")], key="port-forward"), programs=("ssh",))


def shell(e, text):
    return [e._executable("powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", text] if e.platform == "windows" else ["/bin/sh", "-c", text]


def open_files(a):
    rows = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            for item in proc.open_files():
                if not a.get("device") or a["device"].casefold() in item.path.casefold():
                    rows.append({"pid": proc.pid, "name": proc.info["name"], "path": item.path})
        except (psutil.Error, OSError):
            continue
        if len(rows) >= 500:
            break
    return rows[:500]


def affinity(a):
    cores = []
    for part in str(a["cores"]).replace(" ", "").split(","):
        if re.fullmatch(r"\d+-\d+", part):
            first, last = map(int, part.split("-"))
            if first > last or last >= psutil.cpu_count(): raise ExecutionError("Invalid CPU range")
            cores.extend(range(first, last + 1))
        else: cores.append(int(part))
    cores = sorted(set(cores))
    if any(x < 0 or x >= psutil.cpu_count() for x in cores):
        raise ExecutionError("CPU core index is outside this device's range")
    p = psutil.Process(a["pid"])
    p.cpu_affinity(cores)
    return {"pid": p.pid, "cores": p.cpu_affinity()}


def priority(a):
    raw = a.get("priority", "normal")
    win = {"low": psutil.IDLE_PRIORITY_CLASS, "below_normal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
           "normal": psutil.NORMAL_PRIORITY_CLASS, "above_normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
           "high": psutil.HIGH_PRIORITY_CLASS} if os.name == "nt" else {}
    value = win.get(str(raw)) if os.name == "nt" else {"low": 10, "normal": 0, "high": -10}.get(str(raw), raw)
    if value is None:
        raise ExecutionError("Use low, below_normal, normal, above_normal, or high priority")
    p = psutil.Process(a["pid"]); p.nice(int(value))
    return {"pid": p.pid, "priority": p.nice()}


def search_contents(e, a):
    found = []
    for file in e._walk():
        if not file.is_file() or file.stat().st_size > 2_000_000:
            continue
        try:
            for number, line in enumerate(file.read_text("utf-8").splitlines(), 1):
                if str(a["query"]).casefold() in line.casefold():
                    found.append({"path": str(file.relative_to(e.files_root)), "line": number, "text": line[:300]})
                if len(found) >= 100:
                    return found
        except (UnicodeError, OSError):
            continue
    return found


def symlink(e, a):
    source, target = e.path(a["path"]), e.path(a["link"], exists=False)
    target.symlink_to(source, target_is_directory=source.is_dir())
    return {"link": str(target), "target": str(source)}


def create_venv(e, a):
    dest = e.path(a["path"], exists=False)
    if dest.exists():
        raise ExecutionError("Virtualenv destination already exists")
    venv.EnvBuilder(with_pip=True).create(dest)
    return {"created": str(dest)}


def clear_temporary(e):
    root = e.files_root / "temp"
    root.mkdir(exist_ok=True)
    removed = []
    for path in root.iterdir():
        checked = e.path(str(path))
        if checked.is_file():
            checked.unlink(); removed.append(path.name)
    return {"deleted": removed, "scope": str(root)}


def download(e, a):
    parsed = urllib.parse.urlsplit(a["url"])
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username:
        raise ExecutionError("Use an HTTP(S) URL without embedded credentials")
    filename = Path(urllib.parse.unquote(parsed.path)).name or "download.bin"
    dest = e.path("downloads/" + filename, exists=False)
    dest.parent.mkdir(exist_ok=True)
    with urllib.request.urlopen(a["url"], timeout=30) as response, dest.open("xb") as stream:
        shutil.copyfileobj(response, stream, 1024 * 1024)
    return {"downloaded": str(dest), "bytes": dest.stat().st_size}


def public_ip(e):
    with urllib.request.urlopen("https://api.ipify.org?format=json", timeout=10) as response:
        return json.load(response)


def notify(e, text):
    e.notifications.append({"at": dt.datetime.now().isoformat(), "message": text})
    e.desktop.notify(text)


def alarm(e, a):
    now = dt.datetime.now()
    target = dt.datetime.combine(now.date(), dt.time.fromisoformat(a["time"]))
    if target <= now:
        target += dt.timedelta(days=1)
    cancel_alarm(e)
    timer = threading.Timer((target - now).total_seconds(), notify, args=(e, "Alarm: " + a["time"]))
    timer.daemon = True; e.alarm_task = timer; timer.start()
    return {"alarm_at": target.isoformat(), "scope": "While Command Lab is running"}


def cancel_alarm(e):
    if getattr(e, "alarm_task", None):
        e.alarm_task.cancel(); e.alarm_task = None
    return {"alarm": "cancelled"}


def reminder(e, a):
    delay = seconds(a, 86400 * 7)
    timer = threading.Timer(delay, notify, args=(e, a["text"]))
    timer.daemon = True; e.reminders.append(timer); timer.start()
    return {"reminder": a["text"], "in_seconds": delay, "scope": "While Command Lab is running"}


def calendar(e, a):
    raw = str(a["time"])
    stamp = event_time(raw)
    title = str(a["title"]).replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,").replace("\r", "")
    dest = output_path(e, "calendar", "ics")
    dest.write_text("\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//CommandLab//EN", "BEGIN:VEVENT",
        "UID:" + str(uuid.uuid4()), "DTSTAMP:" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "DTSTART:" + stamp.strftime("%Y%m%dT%H%M%S"), "SUMMARY:" + title, "END:VEVENT", "END:VCALENDAR", ""]), "utf-8")
    return {"calendar_file": str(dest), "created": True, "scheduled_local_time": stamp.isoformat(),
            "message": "Created a local calendar event file. A morning without an hour means 09:00. No external calendar account was changed."}


def script(e):
    path = e.path(profile(e, "run_script", "path"))
    argv = [sys.executable, str(path)] if path.suffix == ".py" else ([e._executable("powershell.exe"), "-NoProfile", "-File", str(path)] if path.suffix == ".ps1" else ["/bin/sh", str(path)])
    return e.command(argv, timeout=300)


def watch(e, a):
    interval = float(a.get("interval", 2))
    if interval < .5 or interval > 3600:
        raise ExecutionError("Watch interval must be 0.5–3600 seconds")
    return detached(e, [sys.executable, str(Path(__file__).with_name("worker.py")), "watch", json.dumps({"argv": shell(e, a["command"]), "interval": interval})], key="watch-command")


def stress(e, a):
    return detached(e, [sys.executable, str(Path(__file__).with_name("worker.py")), "stress", json.dumps({"seconds": seconds(a, 3600)})], key="stress")


def benchmark(e, a):
    target = a["target"].lower(); started = time.perf_counter()
    if target == "cpu":
        count = 0; payload = b"command-lab" * 1024
        while time.perf_counter() - started < 1:
            hashlib.sha256(payload).digest(); count += 1
        return {"sha256_operations": count, "seconds": time.perf_counter() - started}
    if target in {"memory", "ram"}:
        data = bytearray(32 * 1024 * 1024); copied = bytes(data)
        return {"copied_bytes": len(copied), "seconds": time.perf_counter() - started}
    if target == "disk":
        dest = output_path(e, "disk-benchmark", "bin")
        with dest.open("xb") as stream:
            stream.write(b"\0" * (16 * 1024 * 1024)); stream.flush(); os.fsync(stream.fileno())
        elapsed = time.perf_counter() - started
        dest.unlink()
        return {"write_mb_per_second": 16 / elapsed, "seconds": elapsed}
    raise ExecutionError("Benchmark target must be cpu, memory or disk")


def health(e):
    return {"samples": [{"time": dt.datetime.now().isoformat(), "cpu_percent": psutil.cpu_percent(.3),
                           "memory_percent": psutil.virtual_memory().percent} for _ in range(5)]}


def build(e, a):
    directory = e.path(a["path"])
    if (directory / "package.json").is_file():
        argv = [e._executable("npm.cmd" if e.platform == "windows" else "npm"), "--prefix", str(directory), "run", "build"]
    elif (directory / "Cargo.toml").is_file():
        argv = ["cargo", "build", "--manifest-path", str(directory / "Cargo.toml")]
    elif (directory / "CMakeLists.txt").is_file():
        e.command(["cmake", "-S", str(directory), "-B", str(directory / "build")], timeout=300)
        argv = ["cmake", "--build", str(directory / "build")]
    elif (directory / "Makefile").is_file():
        argv = ["make", "-C", str(directory)]
    else:
        raise ExecutionError("No package.json, Cargo.toml, CMakeLists.txt or Makefile found")
    return e.command(argv, timeout=600)


def scan_ports(e, a):
    host = e._host(a["host"])
    ports = e.settings.get("scan_ports", {}).get("ports", [22, 53, 80, 139, 443, 445, 3389, 5432, 8000, 8080])
    if len(ports) > 128 or any(type(p) is not int or not 1 <= p <= 65535 for p in ports):
        raise ExecutionError("Configure up to 128 valid TCP ports")
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        rows = list(pool.map(lambda port: {"port": port, **e._check_port({"host": host, "port": port})}, ports))
    return {"host": host, "ports": rows}


def speed_test(e):
    import speedtest
    test = speedtest.Speedtest(secure=True); test.get_best_server(); test.download(); test.upload()
    return test.results.dict()
