"""Explicit, reviewed local execution. Dataset strings are never auto-executed.

Adapters accept structured arguments. Native command adapters use argument arrays,
not shell interpolation. The run_command adapter alone intentionally accepts shell
code and always requires an exact, one-use critical-action confirmation.
"""
from __future__ import annotations

import ast
import datetime as dt
import getpass
import hashlib
import json
import math
import operator
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
import zipfile
from dataclasses import dataclass
from pathlib import Path

import psutil

from .schema import CATALOG, ROOT, canonical_command, validate_command


@dataclass
class Adapter:
    function: object
    description: str
    platforms: tuple = ("windows", "pi")
    mutates: bool = False
    executable: str | None = None


class ExecutionError(ValueError):
    pass


class Executor:
    def __init__(self, files_root=None):
        self.platform = "windows" if os.name == "nt" else "pi" if sys.platform.startswith("linux") else "unsupported"
        self.files_root = Path(files_root or ROOT / "runtime" / "files").resolve()
        self.files_root.mkdir(parents=True, exist_ok=True)
        self.adapters = {}
        self.pending = {}
        self.lock = threading.Lock()
        self.timer = None
        self.stopwatch = None
        self._register()

    def add(self, name, function, description, platforms=("windows", "pi"), mutates=False, executable=None):
        if name not in CATALOG:
            raise ValueError(name)
        self.adapters[name] = Adapter(function, description, platforms, mutates, executable)

    def path(self, value, exists=True):
        path = (self.files_root / value).resolve()
        if not path.is_relative_to(self.files_root):
            raise ExecutionError(f"Path must stay inside the configured files folder: {self.files_root}")
        if path == self.files_root and not exists:
            raise ExecutionError("The configured files folder itself cannot be changed")
        if exists and not path.exists():
            raise ExecutionError(f"Path does not exist: {path}")
        return path

    def command(self, argv, timeout=15, environment=None):
        if not isinstance(argv, list) or not argv or not all(isinstance(s, str) for s in argv):
            raise ExecutionError("Invalid command adapter")
        argv = [self._executable(argv[0]) or argv[0], *argv[1:]]
        result = subprocess.run(argv, cwd=self.files_root, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout, shell=False,
                                env=environment, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode:
            raise ExecutionError((result.stderr or result.stdout or f"Exit code {result.returncode}")[:12000])
        return {"stdout": result.stdout[:16000], "stderr": result.stderr[:2000], "exit_code": result.returncode}

    @staticmethod
    def _executable(name):
        found = shutil.which(name)
        if not found and os.name == "nt":
            relative = "WindowsPowerShell/v1.0/powershell.exe" if name.casefold() == "powershell.exe" else name
            path = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / relative
            if path.is_file():
                return str(path)
        return found

    def powershell(self, script, args=None):
        environment = os.environ.copy()
        environment["TINY_UNION_ARGUMENTS"] = json.dumps(args or {})
        prelude = "$ErrorActionPreference='Stop'; $a=$env:TINY_UNION_ARGUMENTS | ConvertFrom-Json; "
        return self.command(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", prelude + script], environment=environment)

    def ps(self, name, script, description, mutates=False):
        self.add(name, lambda a: self.powershell(script, a), description, ("windows",), mutates, "powershell.exe")

    def cli(self, name, argv, description, platforms=("pi",), mutates=False):
        executable = argv[0] if isinstance(argv, list) else None
        self.add(name, lambda a: self.command(argv(a) if callable(argv) else argv), description, platforms, mutates, executable)

    def availability(self, action):
        adapter = self.adapters.get(action)
        if action in {"unknown", "clarify"}:
            return False, "Provide a complete, supported command before execution."
        if not adapter:
            return False, "This action has a model template; a live adapter is not implemented yet."
        if self.platform not in CATALOG[action]["platforms"] or self.platform not in adapter.platforms:
            return False, "This adapter requires " + ", ".join(adapter.platforms) + "."
        if adapter.executable and not self._executable(adapter.executable):
            return False, f"Required program is unavailable: {adapter.executable}"
        return True, "Live adapter available; OS permissions and device availability are checked when run."

    def plan(self, action, args):
        errors = validate_command(action, args)
        if errors:
            raise ExecutionError("; ".join(errors))
        args = json.loads(json.dumps(args))
        available, reason = self.availability(action)
        spec, adapter = CATALOG[action], self.adapters.get(action)
        # Catalog risk labels do not determine whether an operation changes the host.
        risk = spec["risk"]
        if adapter and adapter.mutates and risk == "safe":
            risk = "caution"
        if action in {"run_command", "run_background", "watch_command", "run_script"}:
            risk = "critical"
        plan = {"id": secrets.token_urlsafe(24), "action": action, "args": args, "risk": risk,
                "platform": self.platform, "needs_sudo": spec["needs_sudo"],
                "live_available": available, "availability_reason": reason,
                "effect": adapter.description if adapter else spec["desc"],
                "changes_device": bool(adapter and adapter.mutates), "files_root": str(self.files_root),
                "expires_at": time.time()+600, "required_confirmation": action if risk == "critical" else "Run"}
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if v["expires_at"] > time.time()}
            if len(self.pending) >= 2048:
                raise ExecutionError("Too many pending plans; wait for older previews to expire")
            self.pending[plan["id"]] = plan
        return json.loads(json.dumps(plan))

    def execute(self, plan_id, mode="simulate", confirmation=""):
        if not isinstance(plan_id, str) or not plan_id:
            raise ExecutionError("A valid plan ID is required")
        if not isinstance(mode, str) or mode not in {"simulate", "live"}:
            raise ExecutionError("Mode must be simulate or live")
        with self.lock:
            plan = self.pending.get(plan_id)
            if not plan or plan["expires_at"] < time.time():
                raise ExecutionError("Plan expired or already used; preview it again")
            if mode == "live":
                if not plan["live_available"]:
                    raise ExecutionError(plan["availability_reason"])
                if confirmation != plan["required_confirmation"]:
                    raise ExecutionError("Confirmation does not match this specific plan")
                del self.pending[plan_id]
        if mode == "simulate":
            return {"status": "simulated", "executed": False, "action": plan["action"],
                    "message": "Validated plan only. No device operation was performed.",
                    "effect": plan["effect"], "live_available": plan["live_available"]}
        begin = time.perf_counter()
        try:
            result = self.adapters[plan["action"]].function(plan["args"])
        except (OSError, ValueError, subprocess.SubprocessError, psutil.Error) as exc:
            return {"status": "failed", "executed": True, "action": plan["action"], "error": str(exc),
                    "duration_ms": round((time.perf_counter()-begin)*1000, 2)}
        return {"status": "completed", "executed": True, "action": plan["action"], "result": result,
                "duration_ms": round((time.perf_counter()-begin)*1000, 2)}

    def _register(self):
        self.add("help", lambda a: {"actions": len(CATALOG), "live_adapters": sum(self.availability(k)[0] for k in CATALOG), "files_root": str(self.files_root)}, "Show action and adapter coverage")
        self.add("get_time", lambda a: dt.datetime.now().isoformat(timespec="seconds"), "Read the local system clock")
        self.add("get_date", lambda a: dt.date.today().isoformat(), "Read today's local date")
        self.add("get_hostname", lambda a: socket.gethostname(), "Read the host name")
        self.add("get_current_user", lambda a: getpass.getuser(), "Read the current user name")
        self.add("get_uptime", lambda a: {"seconds": round(time.time()-psutil.boot_time(), 1)}, "Read elapsed time since boot")
        self.add("get_boot_time", lambda a: dt.datetime.fromtimestamp(psutil.boot_time()).isoformat(), "Read the boot timestamp")
        self.add("get_cpu_usage", lambda a: {"percent": psutil.cpu_percent(interval=.2)}, "Measure CPU use over 200 milliseconds")
        self.add("get_per_core_usage", lambda a: psutil.cpu_percent(interval=.2, percpu=True), "Measure use of each CPU core")
        self.add("get_ram_usage", lambda a: psutil.virtual_memory()._asdict(), "Read RAM usage in bytes")
        self.add("get_swap_usage", lambda a: psutil.swap_memory()._asdict(), "Read swap/pagefile use")
        self.add("get_disk_space", lambda a: psutil.disk_usage(str(self.files_root))._asdict(), "Read free space on the files drive")
        self.add("get_disk_io", lambda a: {k:v._asdict() for k,v in psutil.disk_io_counters(perdisk=True).items()}, "Read disk I/O counters")
        self.add("list_disks", lambda a: [p._asdict() for p in psutil.disk_partitions()], "List mounted disks")
        self.add("list_partitions", lambda a: [p._asdict() for p in psutil.disk_partitions(all=True)], "List disk partitions")
        self.add("get_bandwidth_usage", lambda a: {k:v._asdict() for k,v in psutil.net_io_counters(pernic=True).items()}, "Read per-interface network counters")
        self.add("network_status", lambda a: {k:v._asdict() for k,v in psutil.net_if_stats().items()}, "Read network interface status")
        self.add("get_ip", lambda a: {k:[v.address for v in vs if v.family in (socket.AF_INET,socket.AF_INET6)] for k,vs in psutil.net_if_addrs().items()}, "Read local IP addresses")
        self.add("get_mac_address", lambda a: {k:[v.address for v in vs if v.family == psutil.AF_LINK] for k,vs in psutil.net_if_addrs().items()}, "Read network hardware addresses")
        self.add("get_kernel_version", lambda a: platform.release(), "Read the OS kernel version")
        self.add("get_system_info", lambda a: {"system": platform.platform(), "cpu": platform.processor(), "logical_cpus": psutil.cpu_count(), "ram_bytes": psutil.virtual_memory().total}, "Read OS, CPU and RAM information")
        self.add("get_diagnostics", lambda a: {"cpu_percent": psutil.cpu_percent(.2), "memory": psutil.virtual_memory()._asdict(), "disk": psutil.disk_usage(str(self.files_root))._asdict()}, "Read CPU, memory and disk diagnostics")
        self.add("get_battery", lambda a: self._battery(), "Read battery charge and AC state")
        self.add("list_logged_in_users", lambda a: [u._asdict() for u in psutil.users()], "List logged-in sessions")
        self.add("list_processes", lambda a: [p.info for p in psutil.process_iter(["pid", "name", "memory_info"])][:300], "List up to 300 processes")
        self.add("list_connections", lambda a: [c._asdict() for c in psutil.net_connections(kind="inet")][:300], "Read up to 300 network connections")
        self.add("list_open_ports", lambda a: [c._asdict() for c in psutil.net_connections(kind="inet") if c.status == "LISTEN"][:300], "Read listening network ports")
        self.add("dns_lookup", lambda a: {"host": a["host"], "addresses": sorted({v[4][0] for v in socket.getaddrinfo(self._host(a["host"]), None)})}, "Resolve the supplied host name")
        self.add("check_port", lambda a: self._check_port(a), "Attempt a TCP connection to one host and port")
        self.add("find_command", lambda a: {"name": a["name"], "path": shutil.which(a["name"])}, "Find an executable on PATH")
        self.add("calculate", lambda a: self._calculate(a["expression"]), "Evaluate bounded arithmetic without Python eval")
        self.add("list_files", lambda a: [{"name": p.name, "directory": p.is_dir(), "bytes": p.stat().st_size if p.is_file() else None} for p in list(self.path(a["path"]).iterdir())[:300]], "List up to 300 entries inside the configured files folder")
        self.add("read_file", lambda a: self._read_file(a["path"]), "Read up to 16,000 characters inside the files folder")
        self.add("hash_file", lambda a: self._hash_file(a["path"]), "Compute SHA-256 of a file inside the files folder")
        self.add("create_file", lambda a: self._create(a["path"], False), "Create a new empty file; existing files are never overwritten", mutates=True)
        self.add("create_folder", lambda a: self._create(a["path"], True), "Create a directory inside the files folder", mutates=True)
        self.add("copy_file", lambda a: self._copy(a, False), "Copy a file inside the files folder without overwriting", mutates=True)
        self.add("move_file", lambda a: self._copy(a, True), "Move a file inside the files folder without overwriting", mutates=True)
        self.add("rename_file", lambda a: self._copy(a, True), "Rename a file inside the files folder without overwriting", mutates=True)
        self.add("delete_file", lambda a: self._delete(a["path"]), "Delete one file inside the files folder; directories cannot be deleted", mutates=True)
        self.add("compress_file", lambda a: self._compress(a["path"]), "Create a ZIP for one file inside the files folder", mutates=True)
        self.add("extract_archive", lambda a: self._extract(a["path"]), "Extract a ZIP to a new folder, checking paths and size limits", mutates=True)
        self.add("search_files", lambda a: [str(p.relative_to(self.files_root)) for p in self._walk() if a["query"].casefold() in p.name.casefold()][:100], "Search filenames in the configured files folder")
        self.add("get_folder_sizes", lambda a: {"files_root": str(self.files_root), "bytes": sum(p.stat().st_size for p in self._walk() if p.is_file())}, "Sum file sizes inside the files folder")
        self.add("set_timer", lambda a: self._timer(a), "Start a timer in this local server process", mutates=True)
        self.add("cancel_timer", lambda a: self._cancel_timer(), "Cancel the timer in this server process", mutates=True)
        self.add("stopwatch_start", lambda a: self._stopwatch(True), "Start this server's stopwatch", mutates=True)
        self.add("stopwatch_stop", lambda a: self._stopwatch(False), "Stop this server's stopwatch", mutates=True)
        self.add("open_url", lambda a: self._open_url(a["url"]), "Open a reviewed HTTP(S) URL in the default browser", mutates=True)
        self.add("web_search", lambda a: self._open_url("https://www.google.com/search?"+urllib.parse.urlencode({"q":a["query"]})), "Search the web in the default browser", mutates=True)
        self.add("youtube_search", lambda a: self._open_url("https://www.youtube.com/results?"+urllib.parse.urlencode({"search_query":a["query"]})), "Search YouTube in the default browser", mutates=True)
        self.add("run_command", lambda a: self.command(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", a["command"]] if self.platform == "windows" else ["/bin/sh", "-c", a["command"]], timeout=20), "Execute the exact reviewed shell command with your account permissions (20-second timeout)", mutates=True)
        if self.platform == "windows":
            self._windows()
        else:
            self._linux()

    def _windows(self):
        readers = {
            "list_services": "Get-Service | Select-Object Name,Status,DisplayName | ConvertTo-Json",
            "service_status": "Get-Service -Name $a.service | Select-Object Name,Status,DisplayName | ConvertTo-Json",
            "get_default_gateway": "Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object NextHop,InterfaceAlias | ConvertTo-Json",
            "get_ethernet_status": "Get-NetAdapter -Physical | Select-Object Name,Status,LinkSpeed | ConvertTo-Json",
            "get_time_sync": "w32tm /query /status",
            "get_brightness": "Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness | Select-Object CurrentBrightness | ConvertTo-Json",
            "get_disk_health": "Get-PhysicalDisk | Select-Object FriendlyName,HealthStatus,OperationalStatus | ConvertTo-Json",
            "get_boot_logs": "Get-WinEvent -FilterHashtable @{LogName='System';Id=12} -MaxEvents 10 | Select-Object TimeCreated,Message | ConvertTo-Json",
            "get_reboot_history": "Get-WinEvent -FilterHashtable @{LogName='System';Id=1074,6005,6006} -MaxEvents 20 | Select-Object TimeCreated,Message | ConvertTo-Json",
            "get_boot_time": "Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Diagnostics-Performance/Operational';Id=100} -MaxEvents 1 | Select-Object TimeCreated,Message | ConvertTo-Json",
            "list_printers": "Get-Printer | Select-Object Name,PrinterStatus,DriverName | ConvertTo-Json",
            "list_displays": "Get-CimInstance Win32_VideoController | Select-Object Name,CurrentHorizontalResolution,CurrentVerticalResolution,CurrentRefreshRate | ConvertTo-Json",
            "list_audio_devices": "Get-CimInstance Win32_SoundDevice | Select-Object Name,Status | ConvertTo-Json",
            "firewall_status": "Get-NetFirewallProfile | Select-Object Name,Enabled | ConvertTo-Json",
            "list_failed_services": "Get-CimInstance Win32_Service | Where-Object { $_.StartMode -eq 'Auto' -and $_.State -ne 'Running' } | Select-Object Name,State | ConvertTo-Json",
        }
        for name, script in readers.items():
            self.ps(name, script, CATALOG[name]["desc"])
        for name, verb in [("service_start", "Start"), ("service_stop", "Stop"), ("service_restart", "Restart")]:
            self.ps(name, f"{verb}-Service -Name $a.service -ErrorAction Stop; Get-Service -Name $a.service | Select-Object Name,Status | ConvertTo-Json", CATALOG[name]["desc"], True)
        self.ps("service_enable", "Set-Service -Name $a.service -StartupType Automatic", "Enable automatic startup of the reviewed service", True)
        self.ps("service_disable", "Set-Service -Name $a.service -StartupType Disabled", "Disable startup of the reviewed service", True)
        self.ps("set_brightness", "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods) | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{Timeout=[uint32]1;Brightness=[byte]$a.value}", "Set supported internal display brightness", True)
        self.ps("set_timezone", "Set-TimeZone -Id $a.tz", "Set the Windows time zone using a Windows time-zone ID", True)
        self.ps("set_default_printer", "$p=Get-CimInstance Win32_Printer | Where-Object Name -eq $a.name; if (!$p) {throw 'Printer not found'}; $p | Invoke-CimMethod -MethodName SetDefaultPrinter", "Set the default printer", True)
        self.cli("list_wifi", ["netsh.exe", "wlan", "show", "networks"], "List visible Wi-Fi networks", ("windows",))
        self.cli("get_wifi_signal", ["netsh.exe", "wlan", "show", "interfaces"], "Read Wi-Fi connection and signal status", ("windows",))
        self.cli("list_arp", ["arp.exe", "-a"], "Read the ARP table", ("windows",))
        self.cli("flush_dns", ["ipconfig.exe", "/flushdns"], "Flush the DNS resolver cache", ("windows",), True)
        self.cli("cancel_shutdown", ["shutdown.exe", "/a"], "Cancel a scheduled shutdown", ("windows",), True)
        self.cli("ping", lambda a: ["ping.exe", "-n", "2", "-w", "1500", self._host(a["host"])], "Ping one reviewed host twice", ("windows",))
        self.cli("traceroute", lambda a: ["tracert.exe", "-d", "-h", "8", "-w", "700", self._host(a["host"])], "Trace up to eight hops to a reviewed host", ("windows",))
        self.ps("kill_process", "Stop-Process -Id ([int]$a.pid) -ErrorAction Stop", "Terminate the reviewed process ID", True)
        self.cli("list_packages", ["winget.exe", "list", "--disable-interactivity"], "List installed packages using winget", ("windows",))
        self.cli("list_installed_apps", ["winget.exe", "list", "--disable-interactivity"], "List installed applications using winget", ("windows",))
        self.cli("check_updates", ["winget.exe", "upgrade", "--disable-interactivity"], "List available winget upgrades", ("windows",))
        self.cli("search_package", lambda a: ["winget.exe", "search", "--query", a["name"], "--disable-interactivity"], "Search the configured winget sources", ("windows",))
        self.add("shutdown", lambda a: self._power(a, False), "Shut down Windows at the reviewed time", ("windows",), True)
        self.add("restart", lambda a: self._power(a, True), "Restart Windows at the reviewed time", ("windows",), True)
        self.add("open_file", lambda a: self._open_file(a["path"]), "Open a file inside the configured files folder", ("windows",), True)

    def _linux(self):
        readers = {
            "get_cpu_clock": ["vcgencmd", "measure_clock", "arm"], "get_temperature": ["vcgencmd", "measure_temp"],
            "get_voltage": ["vcgencmd", "measure_volts"], "get_throttle_status": ["vcgencmd", "get_throttled"],
            "get_firmware_version": ["vcgencmd", "version"], "get_gpu_memory": ["vcgencmd", "get_mem", "gpu"],
            "list_gpio": ["pinctrl", "get"], "show_pinout": ["pinout"], "list_gpio_chips": ["gpiodetect"],
            "list_usb": ["lsusb"], "list_pci": ["lspci"], "list_kernel_modules": ["lsmod"],
            "list_wifi": ["nmcli", "device", "wifi", "list"], "get_wifi_signal": ["nmcli", "device", "wifi", "list"],
            "list_arp": ["ip", "neigh"], "get_default_gateway": ["ip", "route", "show", "default"],
            "get_kernel_log": ["dmesg", "--ctime"], "get_firmware_log": ["sudo", "-n", "vcdbg", "log", "msg"],
            "get_boot_logs": ["journalctl", "-b", "-n", "80", "--no-pager"], "get_boot_time": ["systemd-analyze", "time"],
            "get_time_sync": ["timedatectl", "status"], "list_services": ["systemctl", "list-units", "--type=service", "--no-pager"],
            "list_failed_services": ["systemctl", "--failed", "--no-pager"], "list_audio_devices": ["aplay", "-l"],
            "list_cameras": ["rpicam-hello", "--list-cameras"], "list_packages": ["dpkg-query", "-W"],
            "get_reboot_history": ["last", "-n", "15", "reboot"], "list_sessions": ["tmux", "list-sessions"],
        }
        for name, argv in readers.items():
            self.cli(name, argv, CATALOG[name]["desc"])
        self.cli("service_status", lambda a: ["systemctl", "status", "--no-pager", "--", a["service"]], "Read the reviewed service status")
        self.cli("get_service_logs", lambda a: ["journalctl", "-u", a["service"], "-n", "80", "--no-pager"], "Read the last 80 service log entries")
        for name, verb in [("service_start","start"),("service_stop","stop"),("service_restart","restart"),("service_enable","enable"),("service_disable","disable")]:
            self.cli(name, lambda a, v=verb: ["sudo", "-n", "systemctl", v, "--", a["service"]], CATALOG[name]["desc"], mutates=True)
        self.cli("ping", lambda a: ["ping", "-c", "2", "-W", "2", "--", self._host(a["host"])], "Ping one reviewed host twice")
        self.cli("get_cpu_governor", ["cat", "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"], "Read the current CPU governor")
        for name, file in [("get_cpu_pressure","cpu"),("get_memory_pressure","memory")]:
            self.cli(name, ["cat", "/proc/pressure/"+file], "Read Linux pressure-stall information")
        self.cli("get_interrupts", ["cat", "/proc/interrupts"], "Read hardware interrupt counters")
        self.cli("get_slab_usage", ["cat", "/proc/slabinfo"], "Read kernel slab usage")
        for action in ["gpio_read", "gpio_info", "gpio_debug"]:
            self.cli(action, lambda a: ["pinctrl", "get", str(self._bcm(a))], "Read pin control state without changing it")
        for name, state in [("gpio_on", "dh"), ("gpio_off", "dl")]:
            self.cli(name, lambda a, s=state: ["pinctrl", "set", str(self._bcm(a)), "op", s], "Set the reviewed GPIO output state", mutates=True)
        self.cli("gpio_set_mode", lambda a: ["pinctrl", "set", str(self._bcm(a)), {"input":"ip","output":"op"}[a["mode"]]], "Configure GPIO input/output mode", mutates=True)
        self.cli("gpio_set_pull", lambda a: ["pinctrl", "set", str(self._bcm(a)), {"up":"pu","down":"pd","none":"pn"}[a["pull"]]], "Configure GPIO pull resistor", mutates=True)
        self.add("bcm_to_board", lambda a: {"bcm": a["pin"], "board": self._pin_map().get(a["pin"])}, "Convert BCM to physical header numbering", ("pi",))
        self.add("board_to_bcm", lambda a: {"board": a["pin"], "bcm": self._bcm(a)}, "Convert physical header to BCM numbering", ("pi",))
        self.cli("i2c_scan", ["i2cdetect", "-y", "1"], "Scan I2C bus 1 for device addresses")

    @staticmethod
    def _pin_map():
        return dict(zip([2,3,4,14,15,17,18,27,22,23,24,10,9,25,11,8,7,0,1,5,6,12,13,19,16,26,20,21], [3,5,7,8,10,11,12,13,15,16,18,19,21,22,23,24,26,27,28,29,31,32,33,35,36,37,38,40]))

    def _bcm(self, args):
        if args.get("numbering") != "board":
            return args["pin"]
        inverse = {v:k for k,v in self._pin_map().items()}
        if args["pin"] not in inverse:
            raise ExecutionError("This physical pin is power/ground, not a GPIO")
        return inverse[args["pin"]]

    @staticmethod
    def _host(host):
        if len(host) > 253 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:%_-]*", host):
            raise ExecutionError("Invalid host name or IP address")
        return host

    def _check_port(self, a):
        try:
            with socket.create_connection((self._host(a["host"]), a["port"]), timeout=3):
                return {"open": True}
        except OSError as exc:
            return {"open": False, "message": str(exc)}

    @staticmethod
    def _battery():
        battery = psutil.sensors_battery()
        if battery is None:
            raise ExecutionError("No battery sensor is available")
        return battery._asdict()

    def _walk(self):
        total = 0
        for directory, subdirs, files in os.walk(self.files_root, followlinks=False):
            subdirs[:] = [d for d in subdirs if not (Path(directory)/d).is_symlink()]
            for name in files:
                path = Path(directory)/name
                if path.is_symlink() or not path.resolve().is_relative_to(self.files_root):
                    continue
                yield path
                total += 1
                if total >= 10000:
                    return

    def _hash_file(self, path):
        file = self.path(path)
        if file.stat().st_size > 512*1024*1024:
            raise ExecutionError("Demo hashing is limited to files below 512 MiB")
        with file.open("rb") as f:
            return {"sha256": hashlib.file_digest(f, "sha256").hexdigest()}

    def _read_file(self, value):
        with self.path(value).open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(16000)

    def _create(self, path, directory):
        file = self.path(path, exists=False)
        if file.exists():
            raise ExecutionError("Path already exists")
        if directory:
            file.mkdir(parents=True, exist_ok=False)
        else:
            file.parent.mkdir(parents=True, exist_ok=True)
            with file.open("x", encoding="utf-8"):
                pass
        return {"path": str(file)}

    def _copy(self, a, move):
        source, dest = self.path(a["src"]), self.path(a["dst"], False)
        if not source.is_file() or dest.exists():
            raise ExecutionError("Source must be a file and destination must not exist")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if move:
            source.rename(dest)
        else:
            with source.open("rb") as src, dest.open("xb") as dst:
                shutil.copyfileobj(src, dst)
        return {"path": str(dest)}

    def _delete(self, value):
        file = self.path(value)
        if not file.is_file():
            raise ExecutionError("Only a single file can be deleted")
        file.unlink()
        return {"deleted": str(file)}

    def _compress(self, value):
        source = self.path(value)
        if not source.is_file():
            raise ExecutionError("Choose a single file to compress")
        dest = source.with_name(source.name+".zip")
        with zipfile.ZipFile(dest, "x", zipfile.ZIP_DEFLATED) as archive:
            archive.write(source, source.name)
        return {"path": str(dest)}

    def _extract(self, value):
        source = self.path(value)
        dest = source.with_name(source.stem+"_extracted")
        if dest.exists():
            raise ExecutionError("Extraction folder already exists")
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if len(members) > 1000 or sum(v.file_size for v in members) > 128*1024*1024:
                raise ExecutionError("Archive exceeds the demo size/count limit")
            for item in members:
                if not (dest/item.filename).resolve().is_relative_to(dest) or (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ExecutionError("Archive contains an unsafe path or symbolic link")
            archive.extractall(dest)
        return {"path": str(dest), "entries": len(members)}

    @staticmethod
    def _calculate(expression):
        expression = expression.casefold().strip()
        for a,b in [("plus","+"),("minus","-"),("times","*"),("into","*"),("divided by","/"),("x","*")]:
            expression = expression.replace(a,b)
        expression = re.sub(r"sqrt of (\d+(?:\.\d+)?)", r"sqrt(\1)", expression)
        expression = re.sub(r"(\d+) se (\d+) ghatao", r"\1-\2", expression)
        expression = re.sub(r"(\d+) percent of (\d+)", r"(\1/100)*\2", expression)
        ops = {ast.Add:operator.add, ast.Sub:operator.sub, ast.Mult:operator.mul, ast.Div:operator.truediv, ast.Mod:operator.mod}
        def visit(node, depth=0):
            if depth > 12:
                raise ExecutionError("Expression too complex")
            if isinstance(node,ast.Constant) and type(node.value) in (int,float) and abs(node.value) < 1e15:
                return node.value
            if isinstance(node,ast.BinOp) and type(node.op) in ops:
                return ops[type(node.op)](visit(node.left,depth+1),visit(node.right,depth+1))
            if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
                return (-1 if isinstance(node.op,ast.USub) else 1)*visit(node.operand,depth+1)
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id == "sqrt" and len(node.args)==1 and not node.keywords:
                return math.sqrt(visit(node.args[0],depth+1))
            raise ExecutionError("Use numbers, arithmetic operators, parentheses or sqrt")
        try:
            result = visit(ast.parse(expression, mode="eval").body)
        except (SyntaxError, ZeroDivisionError, OverflowError) as exc:
            raise ExecutionError("Invalid arithmetic expression") from exc
        if not math.isfinite(result) or abs(result)>1e30:
            raise ExecutionError("Arithmetic result is too large")
        return {"value": result}

    def _timer(self, a):
        seconds = a["amount"]*{"sec":1,"min":60,"hour":3600}[a["unit"]]
        self.timer = time.time()+seconds
        return {"ends_at": self.timer, "seconds": seconds, "scope": "This server session; no background OS alarm is created"}

    def _cancel_timer(self):
        self.timer = None
        return {"timer": "cancelled"}

    def _stopwatch(self, start):
        if start:
            self.stopwatch = time.perf_counter()
            return {"stopwatch": "running"}
        if self.stopwatch is None:
            raise ExecutionError("No stopwatch is running")
        elapsed, self.stopwatch = time.perf_counter()-self.stopwatch, None
        return {"elapsed_seconds": elapsed}

    @staticmethod
    def _open_url(url):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ExecutionError("Use an HTTP(S) URL without embedded credentials")
        if not webbrowser.open(url):
            raise ExecutionError("The OS did not report opening a browser")
        return {"opened": url}

    def _open_file(self, value):
        path = self.path(value)
        os.startfile(str(path))
        return {"opened": str(path)}

    def _power(self, a, restart):
        seconds = a.get("amount", 30)*{"sec":1,"min":60,"hour":3600}.get(a.get("unit", "sec"), 1)
        if "at" in a:
            target = dt.datetime.combine(dt.date.today(), dt.time.fromisoformat(a["at"]))
            if target <= dt.datetime.now():
                target += dt.timedelta(days=1)
            seconds = int((target-dt.datetime.now()).total_seconds())
        if not 0 <= seconds <= 315360000:
            raise ExecutionError("Shutdown delay is outside the OS limit")
        return self.command(["shutdown.exe", "/r" if restart else "/s", "/t", str(seconds)])
