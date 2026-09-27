"""Device description and a background temperature sampler.

Raspberry Pi / Linux: /sys/class/thermal (the Pi 5 'cpu-thermal' SoC sensor), plus
`vcgencmd get_throttled` when available.
Windows: the ACPI "Thermal Zone Information" performance counter via one long-lived `typeperf`
process (no administrator rights needed). That is a board/ACPI zone, not a per-core sensor.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import statistics
import subprocess
import threading
import time
from pathlib import Path

import psutil


def cpu_name() -> str:
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            return platform.processor()
    board = ""
    try:
        board = Path("/proc/device-tree/model").read_text(errors="ignore").strip("\x00 \n")
    except OSError:
        pass
    try:
        text = Path("/proc/cpuinfo").read_text(errors="ignore")
    except OSError:
        return board or platform.machine()
    cores = len(re.findall(r"^processor\s*:", text, re.M))
    name = re.search(r"^model name\s*:\s*(.+)$", text, re.M)
    if name:
        cpu = name.group(1).strip()
    elif re.search(r"^CPU part\s*:\s*0xd0b", text, re.M):
        cpu = "Arm Cortex-A76"
    else:
        cpu = platform.machine()
    return f"{board} ({cpu}, {cores} cores)" if board else f"{cpu} ({cores} cores)"


def throttled() -> str | None:
    if not shutil.which("vcgencmd"):
        return None
    try:
        return subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def describe(threads: int) -> dict:
    import torch
    return {"os": platform.platform(), "machine": platform.machine(), "cpu": cpu_name(),
            "logical_cpus": psutil.cpu_count(), "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
            "python": platform.python_version(), "torch": torch.__version__, "threads": threads}


class TemperatureSampler:
    """Samples temperature every `interval` seconds on a background thread."""

    def __init__(self, interval: float = 1.0):
        self.interval, self.samples, self.source = interval, [], None
        self._stop = threading.Event()
        self._thread = None
        self._process = None

    @staticmethod
    def _linux_zone():
        zones = sorted(Path("/sys/class/thermal").glob("thermal_zone*"))
        for zone in zones:
            try:
                kind = (zone / "type").read_text().strip()
            except OSError:
                kind = ""
            if kind in ("cpu-thermal", "cpu_thermal", "x86_pkg_temp", "soc_thermal"):
                return zone / "temp", kind
        return (zones[0] / "temp", zones[0].name) if zones else (None, None)

    def _run_linux(self, path):
        while not self._stop.is_set():
            try:
                self.samples.append((time.time(), int(path.read_text().strip()) / 1000))
            except (OSError, ValueError):
                pass
            self._stop.wait(self.interval)

    def _run_windows(self):
        counter = r"\Thermal Zone Information(*)\Temperature"
        try:
            self._process = subprocess.Popen(["typeperf", counter, "-si", str(max(1, int(self.interval)))],
                                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            return
        for line in self._process.stdout:
            if self._stop.is_set():
                break
            fields = [f.strip().strip('"') for f in line.strip().split(",")]
            if len(fields) < 2:
                continue
            try:
                kelvin = [float(v) for v in fields[1:] if v]
            except ValueError:
                continue  # header line
            if kelvin:
                self.samples.append((time.time(), max(kelvin) - 273.15))

    def start(self):
        if os.name == "nt" and shutil.which("typeperf"):
            self.source = "Windows ACPI thermal zone (typeperf, Thermal Zone Information)"
            self._thread = threading.Thread(target=self._run_windows, daemon=True)
        else:
            path, kind = self._linux_zone()
            if path is None:
                self.source = "unavailable"
                return self
            self.source = f"sysfs {kind}" + (" (Raspberry Pi SoC sensor)" if shutil.which("vcgencmd") else "")
            self._thread = threading.Thread(target=self._run_linux, args=(path,), daemon=True)
        self._thread.start()
        deadline = time.time() + 6
        while not self.samples and time.time() < deadline:
            time.sleep(0.2)
        return self

    def stop(self) -> dict:
        self._stop.set()
        if self._process:
            self._process.terminate()
        if self._thread:
            self._thread.join(timeout=5)
        values = [v for _, v in self.samples]
        if not values:
            return {"source": self.source, "samples": 0}
        return {"source": self.source, "samples": len(values), "start_c": round(values[0], 1),
                "end_c": round(values[-1], 1), "min_c": round(min(values), 1), "max_c": round(max(values), 1),
                "mean_c": round(statistics.fmean(values), 1)}
