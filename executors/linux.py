"""Linux/Pi command adapters. Missing utilities and privilege errors stay explicit."""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

from tinyagent.executor import ExecutionError
from .common import bind, command, detached, integer, name, output_path, profile, seconds


def register(e):
    def cmd(action, argv, *, mutates=True, programs=(), config=(), timeout=45):
        command(e, action, argv, ("pi",), mutates, programs=programs, configuration=config, timeout=timeout)
    readers = {
        "get_cpu_clock": ["vcgencmd", "measure_clock", "arm"], "get_cpu_governor": ["cat", "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"],
        "get_cpu_pressure": ["cat", "/proc/pressure/cpu"], "get_memory_pressure": ["cat", "/proc/pressure/memory"],
        "get_slab_usage": ["cat", "/proc/slabinfo"], "get_interrupts": ["cat", "/proc/interrupts"],
        "get_kernel_log": ["dmesg", "--ctime"], "list_kernel_modules": ["lsmod"], "list_usb": ["lsusb"], "list_pci": ["lspci"],
        "get_radio_status": ["rfkill", "list"], "list_bluetooth_devices": ["bluetoothctl", "devices"],
        "list_banned_ips": ["sudo", "-n", "fail2ban-client", "status", "sshd"],
        "discover_devices": ["avahi-browse", "-art"], "list_sessions": ["tmux", "list-sessions"],
        "get_volume": ["pactl", "get-sink-volume", "@DEFAULT_SINK@"], "list_audio_devices": ["pactl", "list", "short", "sinks"],
        "check_updates": ["apt", "list", "--upgradable"], "list_installed_apps": ["dpkg-query", "-W"],
        "list_packages": ["dpkg-query", "-W"], "firewall_status": ["sudo", "-n", "ufw", "status"],
        "get_default_gateway": ["ip", "route", "show", "default"], "list_arp": ["ip", "neigh"],
        "list_services": ["systemctl", "list-units", "--type=service", "--no-pager"],
        "list_failed_services": ["systemctl", "--failed", "--no-pager"],
        "list_displays": ["xrandr", "--query"], "list_printers": ["lpstat", "-p", "-d"],
        "list_wifi": ["nmcli", "device", "wifi", "list"], "get_wifi_signal": ["nmcli", "device", "wifi", "list"],
        "get_time_sync": ["timedatectl", "status"], "get_boot_logs": ["journalctl", "-b", "-n", "80", "--no-pager"],
        "get_boot_time": ["systemd-analyze", "time"], "get_reboot_history": ["last", "-n", "15", "reboot"],
        "get_ethernet_status": ["ip", "-s", "link"],
    }
    for action, argv in readers.items():
        cmd(action, argv, mutates=False, programs=(argv[2] if argv[:2] == ["sudo", "-n"] else argv[0],))
    # The core executor registers traceroute and kill_process for Windows only; without these
    # Linux/Pi adapters register_all() raised "Missing executor implementations" on a Pi.
    trace = "traceroute" if e._executable("traceroute") else "tracepath"
    cmd("traceroute", lambda a: ([trace, "-n", "-m", "8", "-w", "1"] if trace == "traceroute" else [trace, "-n", "-m", "8"])
        + [e._host(a["host"])], mutates=False, programs=(trace,), timeout=60)
    cmd("kill_process", lambda a: ["kill", "-s", "TERM", str(integer(a["pid"], 1, 4194304))], programs=("kill",))
    commands = {
        "wifi_on": ["nmcli", "radio", "wifi", "on"], "wifi_off": ["nmcli", "radio", "wifi", "off"],
        "bluetooth_on": ["bluetoothctl", "power", "on"], "bluetooth_off": ["bluetoothctl", "power", "off"],
        "airplane_mode_on": ["rfkill", "block", "all"], "airplane_mode_off": ["rfkill", "unblock", "all"],
        "hotspot_off": ["nmcli", "connection", "down", "Hotspot"],
        "mute": ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"], "unmute": ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"],
        "mic_mute": ["pactl", "set-source-mute", "@DEFAULT_SOURCE@", "1"], "mic_unmute": ["pactl", "set-source-mute", "@DEFAULT_SOURCE@", "0"],
        "media_play": ["playerctl", "play"], "media_pause": ["playerctl", "pause"], "media_play_pause": ["playerctl", "play-pause"],
        "media_next": ["playerctl", "next"], "media_prev": ["playerctl", "previous"],
        "shutdown": ["shutdown", "-h", "+1"], "restart": ["shutdown", "-r", "+1"], "cancel_shutdown": ["shutdown", "-c"],
        "sleep": ["systemctl", "suspend"], "hibernate": ["systemctl", "hibernate"], "lock_screen": ["loginctl", "lock-session"],
        "screen_off": ["xset", "dpms", "force", "off"],
        "dark_mode_on": ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", "prefer-dark"],
        "dark_mode_off": ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", "default"],
        "night_light_on": ["gsettings", "set", "org.gnome.settings-daemon.plugins.color", "night-light-enabled", "true"],
        "night_light_off": ["gsettings", "set", "org.gnome.settings-daemon.plugins.color", "night-light-enabled", "false"],
        "dnd_on": ["gsettings", "set", "org.gnome.desktop.notifications", "show-banners", "false"],
        "dnd_off": ["gsettings", "set", "org.gnome.desktop.notifications", "show-banners", "true"],
        "narrator_on": ["gsettings", "set", "org.gnome.desktop.a11y.applications", "screen-reader-enabled", "true"],
        "narrator_off": ["gsettings", "set", "org.gnome.desktop.a11y.applications", "screen-reader-enabled", "false"],
        "magnifier_on": ["gsettings", "set", "org.gnome.desktop.a11y.applications", "screen-magnifier-enabled", "true"],
        "magnifier_off": ["gsettings", "set", "org.gnome.desktop.a11y.applications", "screen-magnifier-enabled", "false"],
        "onscreen_keyboard": ["gsettings", "set", "org.gnome.desktop.a11y.applications", "screen-keyboard-enabled", "true"],
        "high_contrast_on": ["gsettings", "set", "org.gnome.desktop.interface", "gtk-theme", "HighContrast"],
        "high_contrast_off": ["gsettings", "set", "org.gnome.desktop.interface", "gtk-theme", "Adwaita"],
        "touchpad_on": ["gsettings", "set", "org.gnome.desktop.peripherals.touchpad", "send-events", "enabled"],
        "touchpad_off": ["gsettings", "set", "org.gnome.desktop.peripherals.touchpad", "send-events", "disabled"],
        "battery_saver_on": ["powerprofilesctl", "set", "power-saver"], "battery_saver_off": ["powerprofilesctl", "set", "balanced"],
        "flush_dns": ["resolvectl", "flush-caches"], "update_packages": ["sudo", "-n", "apt-get", "update"],
        "repair_packages": ["sudo", "-n", "apt-get", "-f", "install", "-y"], "install_updates": ["sudo", "-n", "apt-get", "upgrade", "-y"],
        "enable_auto_updates": ["sudo", "-n", "systemctl", "enable", "--now", "unattended-upgrades"],
        "disable_auto_updates": ["sudo", "-n", "systemctl", "disable", "--now", "unattended-upgrades"],
        "firewall_on": ["sudo", "-n", "ufw", "--force", "enable"], "firewall_off": ["sudo", "-n", "ufw", "disable"],
        "sync_time": ["sudo", "-n", "timedatectl", "set-ntp", "true"], "empty_recycle_bin": ["gio", "trash", "--empty"],
        "open_recycle_bin": ["gio", "open", "trash:///"], "open_config_tool": ["x-terminal-emulator", "-e", "sudo", "raspi-config"],
    }
    for action, argv in commands.items():
        cmd(action, argv, programs=(argv[2] if argv[:2] == ["sudo", "-n"] else argv[0],), timeout=600 if "package" in action or action == "install_updates" else 45)
    recipes = {
        "set_volume": (lambda a: ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{a['value']}%"], "pactl"),
        "volume_up": (lambda a: ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{a.get('step', 10)}%"], "pactl"),
        "volume_down": (lambda a: ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{a.get('step', 10)}%"], "pactl"),
        "set_audio_output": (lambda a: ["pactl", "set-default-sink", str(profile(e, "set_audio_output", a["output"]))], "pactl"),
        "get_brightness": (lambda a: ["brightnessctl", "get"], "brightnessctl"),
        "set_brightness": (lambda a: ["brightnessctl", "set", f"{a['value']}%"], "brightnessctl"),
        "brightness_up": (lambda a: ["brightnessctl", "set", f"+{a.get('step', 10)}%"], "brightnessctl"),
        "brightness_down": (lambda a: ["brightnessctl", "set", f"{a.get('step', 10)}%-"], "brightnessctl"),
        "bluetooth_connect": (lambda a: ["bluetoothctl", "connect", a["device"]], "bluetoothctl"),
        "connect_wifi": (lambda a: ["nmcli", "device", "wifi", "connect", a["ssid"], *(["password", a["password"]] if a.get("password") else [])], "nmcli"),
        "forget_wifi": (lambda a: ["nmcli", "connection", "delete", "id", a["ssid"]], "nmcli"),
        "hotspot_on": (lambda a: ["nmcli", "device", "wifi", "hotspot", "ssid", a.get("ssid", "CommandLab"), "password", a.get("password") or profile(e, "hotspot_on", "password")], "nmcli"),
        "show_wifi_password": (lambda a: ["nmcli", "--show-secrets", "connection", "show", a.get("ssid") or profile(e, "show_wifi_password", "ssid")], "nmcli"),
        "vpn_connect": (lambda a: ["nmcli", "connection", "up", str(profile(e, "vpn_connect", "name"))], "nmcli"),
        "vpn_disconnect": (lambda a: ["nmcli", "connection", "down", str(profile(e, "vpn_disconnect", "name"))], "nmcli"),
        "install_package": (lambda a: ["sudo", "-n", "apt-get", "install", "-y", "--", name(a["name"])], "apt-get"),
        "uninstall_package": (lambda a: ["sudo", "-n", "apt-get", "remove", "-y", "--", name(a["name"])], "apt-get"),
        "uninstall_app": (lambda a: ["sudo", "-n", "apt-get", "remove", "-y", "--", name(a["app"])], "apt-get"),
        "search_package": (lambda a: ["apt-cache", "search", a["name"]], "apt-cache"),
        "get_service_logs": (lambda a: ["journalctl", "-u", a["service"], "-n", "80", "--no-pager"], "journalctl"),
        "service_status": (lambda a: ["systemctl", "status", "--no-pager", "--", a["service"]], "systemctl"),
        "firewall_allow": (lambda a: ["sudo", "-n", "ufw", "allow", str(a["port"])], "ufw"),
        "firewall_deny": (lambda a: ["sudo", "-n", "ufw", "deny", str(a["port"])], "ufw"),
        "unban_ip": (lambda a: ["sudo", "-n", "fail2ban-client", "set", str(profile(e, "unban_ip", "jail", "sshd")), "unbanip", a["ip"]], "fail2ban-client"),
        "copy_ssh_key": (lambda a: ["ssh-copy-id", "-i", str(profile(e, "copy_ssh_key", "public_key", str(Path.home() / ".ssh/id_ed25519.pub"))), a["host"]], "ssh-copy-id"),
        "set_timezone": (lambda a: ["sudo", "-n", "timedatectl", "set-timezone", a["tz"]], "timedatectl"),
        "set_locale": (lambda a: ["sudo", "-n", "localectl", "set-locale", "LANG=" + a["locale"]], "localectl"),
        "set_keyboard_layout": (lambda a: ["setxkbmap", a["layout"]], "setxkbmap"),
        "set_cpu_governor": (lambda a: ["sudo", "-n", "cpupower", "frequency-set", "-g", a["governor"]], "cpupower"),
        "set_swappiness": (lambda a: ["sudo", "-n", "sysctl", f"vm.swappiness={a['value']}"], "sysctl"),
        "set_owner": (lambda a: ["sudo", "-n", "chown", a["user"], str(e.path(a["path"]))], "chown"),
        "set_permissions": (lambda a: ["chmod", str(profile(e, "set_permissions", "mode")), str(e.path(a["path"]))], "chmod"),
        "set_default_printer": (lambda a: ["lpoptions", "-d", a["name"]], "lpoptions"),
        "print_file": (lambda a: ["lp", "--", str(e.path(a["path"]))], "lp"),
        "power_mode": (lambda a: ["powerprofilesctl", "set", {"performance": "performance", "balanced": "balanced"}[a["mode"]]], "powerprofilesctl"),
        "start_session": (lambda a: ["tmux", "new-session", "-d", "-s", name(a["name"])], "tmux"),
        "attach_session": (lambda a: ["x-terminal-emulator", "-e", "tmux", "attach-session", "-t", name(a["name"])], "tmux"),
        "open_serial_terminal": (lambda a: ["x-terminal-emulator", "-e", "picocom", "--baud", str(a.get("baud", 115200)), a["serial_port"]], "picocom"),
        "mount_drive": (lambda a: ["udisksctl", "mount", "-b", a["device"]], "udisksctl"),
        "unmount_drive": (lambda a: ["udisksctl", "unmount", "-b", a["device"]], "udisksctl"),
        "set_disk_label": (lambda a: ["sudo", "-n", "e2label", a["device"], a["label"]], "e2label"),
        "check_filesystem": (lambda a: ["sudo", "-n", "fsck", "-n", a["device"]], "fsck"),
        "set_boot_target": (lambda a: ["sudo", "-n", "systemctl", "set-default", {"desktop": "graphical.target", "console": "multi-user.target"}.get(a["target"], a["target"])], "systemctl"),
        "sign_out": (lambda a: ["loginctl", "terminate-session", os.environ.get("XDG_SESSION_ID") or profile(e, "sign_out", "session_id")], "loginctl"),
        "switch_user": (lambda a: ["dm-tool", "switch-to-greeter"], "dm-tool"),
        "edit_system_config": (lambda a: ["x-terminal-emulator", "-e", "sudo", "nano", a.get("file") or "/boot/firmware/config.txt"], "nano"),
        "set_wallpaper": (lambda a: ["gsettings", "set", "org.gnome.desktop.background", "picture-uri", e.path(a["path"]).as_uri()], "gsettings"),
        "set_display_scale": (lambda a: display_scale(e, a), "xrandr"),
        "set_mouse_speed": (lambda a: ["gsettings", "set", "org.gnome.desktop.peripherals.mouse", "speed", str(mouse_speed_value(a["value"]))], "gsettings"),
        "set_resolution": (lambda a: ["xrandr", "--output", str(profile(e, "display", "output")), "--mode", a["resolution"]], "xrandr"),
        "set_refresh_rate": (lambda a: ["xrandr", "--output", str(profile(e, "display", "output")), "--rate", str(a["value"])], "xrandr"),
        "rotate_screen": (lambda a: ["xrandr", "--output", str(profile(e, "display", "output")), "--rotate", {"landscape": "normal", "portrait": "left", "landscape_flipped": "inverted"}[a["orientation"]]], "xrandr"),
        "display_mode": (lambda a: display_mode(e, a), "xrandr"),
        "antivirus_scan": (lambda a: ["clamscan", "-r", "--infected", str(e.files_root)], "clamscan"),
        "format_drive": (lambda a: ["sudo", "-n", "mkfs.ext4", "--", block_device(profile(e, "format_drive", "device"))], "mkfs.ext4"),
        "partition_disk": (lambda a: ["sudo", "-n", "parted", "-s", block_device(a.get("device") or profile(e, "partition_disk", "device")), "mklabel", str(profile(e, "partition_disk", "table", "gpt")), "mkpart", "primary", "ext4", "1MiB", "100%"], "parted"),
        "backup_disk_image": (lambda a: ["sudo", "-n", "dd", "if=" + block_device(a["device"]), "of=" + str(e.path(profile(e, "backup_disk_image", "destination"), exists=False)), "bs=4M", "conv=fsync", "status=progress"], "dd"),
        "clone_disk": (lambda a: ["sudo", "-n", "dd", "if=" + block_device(profile(e, "clone_disk", "source")), "of=" + block_device(a["device"]), "bs=4M", "conv=fsync", "status=progress"], "dd"),
        "factory_reset": (lambda a: ["sudo", "-n", "dd", "if=" + str(e.path(profile(e, "factory_reset", "recovery_image"))), "of=" + block_device(profile(e, "factory_reset", "device")), "bs=4M", "conv=fsync", "status=progress"], "dd"),
    }
    for action, (builder, program) in recipes.items():
        required = {"run_script": ("path",), "set_permissions": ("mode",), "vpn_connect": ("name",), "vpn_disconnect": ("name",),
                    "format_drive": ("device",), "backup_disk_image": ("destination",), "clone_disk": ("source",),
                    "factory_reset": ("device", "recovery_image")}.get(action, ())
        cmd(action, builder, programs=(program,), config=required, timeout=600 if action in {"backup_disk_image", "clone_disk", "factory_reset", "install_package", "uninstall_package", "antivirus_scan"} else 45)
    for action, verb in [("service_start", "start"), ("service_stop", "stop"), ("service_restart", "restart"), ("service_enable", "enable"), ("service_disable", "disable")]:
        cmd(action, lambda a, v=verb: ["sudo", "-n", "systemctl", v, "--", a["service"]], programs=("systemctl",))
    for action, restart in [("shutdown", False), ("restart", True)]:
        cmd(action, lambda a, r=restart: ["shutdown", "-r" if r else "-h", a.get("at") or ("now" if not a.get("amount") else "+" + str(max(1, int(seconds(a, 86400) / 60))))], programs=("shutdown",))
    # These operations need atomic writes or more than one native command.
    for action, function, config in [
        ("schedule_task", lambda a: schedule(e, a), ()), ("create_service", lambda a: create_service(e, a), ("command",)),
        ("add_persistent_mount", lambda a: persistent_mount(e, a), ("mountpoint", "filesystem")),
        ("harden_ssh", lambda a: harden_ssh(e), ()), ("drop_caches", lambda a: syswrite(e, "/proc/sys/vm/drop_caches", "3\n"), ()),
        ("enable_zram", lambda a: zram(e), ()), ("set_swap_size", lambda a: swap(e, a), ()),
        ("manage_users", lambda a: users(e, a), ("operation",)),
        ("clear_notifications", lambda a: clear_notifications(e), ()),
    ]:
        bind(e, action, function, ("pi",), configuration=config)
    bind(e, "monitor_hotplug", lambda a: detached(e, ["udevadm", "monitor", "--udev", "--property"], key="hotplug"), ("pi",), programs=("udevadm",))
    bind(e, "packet_capture", lambda a: detached(e, ["tcpdump", "-i", a.get("iface", "any"), "-c", "1000", "-w", str(output_path(e, "capture", "pcap"))], key="packet-capture"), ("pi",), programs=("tcpdump",))
    bind(e, "get_disk_health", lambda a: disk_health(e), ("pi",), mutates=False, programs=("smartctl",))
    bind(e, "clear_notifications", lambda a: clear_notifications(e), ("pi",), programs=("dunstctl",))
    for action in ("attach_session", "open_serial_terminal", "open_config_tool", "edit_system_config"):
        if e.platform == "pi":
            builder = e.native_builders[action]
            bind(e, action, lambda a, b=builder: detached(e, b(a)["argv"], visible=True), ("pi",),
                 programs=tuple(e.requirements[action]["programs"]) + ("x-terminal-emulator",))


def block_device(value):
    value = str(value)
    if not re.fullmatch(r"/dev/(?:sd[a-z][0-9]*|mmcblk[0-9]+(?:p[0-9]+)?|nvme[0-9]+n[0-9]+(?:p[0-9]+)?|loop[0-9]+)", value):
        raise ExecutionError("Set an explicit /dev block device")
    return value


def display_mode(e, a):
    first, second = profile(e, "display", "output"), profile(e, "display", "second_output")
    modes = {"duplicate": ["--output", first, "--auto", "--output", second, "--auto", "--same-as", first],
             "extend": ["--output", first, "--auto", "--output", second, "--auto", "--right-of", first],
             "pc_only": ["--output", first, "--auto", "--output", second, "--off"],
             "second_only": ["--output", first, "--off", "--output", second, "--auto"]}
    return ["xrandr", *modes[a["mode"]]]


def syswrite(e, path, value):
    return e.command(["sudo", "-n", sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])", path, str(value)])


def schedule(e, a):
    from .scheduling import task_schedule, cron_expression
    spec = task_schedule(a["schedule"], str(profile(e, "schedule_task", "daily_time", "09:00")))
    schedule = cron_expression(spec)
    if "\n" in a["command"] or "\r" in a["command"]:
        raise ExecutionError("Cron command must be one line")
    result = subprocess_result(["crontab", "-l"])
    current = result.stdout if result.returncode == 0 else ""
    path = output_path(e, "crontab", "txt")
    path.write_text(current.rstrip() + "\n" + schedule + " " + a["command"].replace("%", "\\%") + "\n", "utf-8")
    return {"schedule": spec, "cron": schedule, "native": e.command(["crontab", str(path)])}


def display_scale(e, a):
    percent = int(a["value"])
    if not 50 <= percent <= 400: raise ExecutionError("Display scale must be 50–400 percent")
    ratio = 100 / percent
    return ["xrandr", "--output", str(profile(e, "display", "output")), "--scale", f"{ratio:.4f}x{ratio:.4f}"]


def mouse_speed_value(value):
    aliases = {"slow": -.6, "slower": -.6, "dheere": -.6, "fast": .6, "faster": .6, "tez": .6, "medium": 0, "normal": 0}
    if str(value).lower() in aliases: return aliases[str(value).lower()]
    try: number = float(value)
    except ValueError: raise ExecutionError("Use a speed from 1–20, slow, medium or fast")
    if not 1 <= number <= 20: raise ExecutionError("Mouse speed must be 1–20")
    return (number - 10) / 10


def disk_health(e):
    devices = json.loads(e.command(["smartctl", "--scan", "--json"])["stdout"]).get("devices", [])
    if not devices: raise ExecutionError("No SMART-capable disk was found")
    results = []
    import subprocess
    for device in devices:
        argv = ["smartctl", "--health", "--json", device["name"]]
        process = subprocess.run(argv, text=True, capture_output=True, timeout=30)
        e.trace.append({"kind": "process", "argv": argv})
        # smartctl uses a bitmask: nonzero may itself be a health finding.
        result = json.loads(process.stdout) if process.stdout.strip() else {"error": process.stderr}
        results.append({"device": device["name"], "exit_bitmask": process.returncode, "health": result})
    return results


def subprocess_result(argv):
    import subprocess
    return subprocess.run(argv, text=True, capture_output=True, timeout=15)


def create_service(e, a):
    service = name(a["name"])
    cmd = str(profile(e, "create_service", "command"))
    if "\n" in cmd or "\r" in cmd:
        raise ExecutionError("Service ExecStart must be one line")
    unit = "[Unit]\nDescription=Command Lab " + service + "\n[Service]\nExecStart=" + cmd + "\nRestart=on-failure\n[Install]\nWantedBy=multi-user.target\n"
    path = e.files_root / (service + ".service"); path.write_text(unit, "utf-8")
    e.command(["sudo", "-n", "install", "-m", "644", str(path), "/etc/systemd/system/" + service + ".service"])
    return e.command(["sudo", "-n", "systemctl", "daemon-reload"])


def persistent_mount(e, a):
    device, mountpoint, filesystem = block_device(a["device"]), str(profile(e, "add_persistent_mount", "mountpoint")), name(profile(e, "add_persistent_mount", "filesystem"))
    if not mountpoint.startswith("/") or any(c.isspace() for c in mountpoint):
        raise ExecutionError("Use an absolute mountpoint without whitespace")
    entry = f"{device} {mountpoint} {filesystem} defaults,nofail 0 2\n"
    script = "import pathlib,sys; p=pathlib.Path('/etc/fstab'); t=p.read_text(); line=sys.argv[1]; p.write_text(t.rstrip()+'\\n'+line) if line.strip() not in t else None"
    return e.command(["sudo", "-n", sys.executable, "-c", script, entry])


def harden_ssh(e):
    text = "PasswordAuthentication no\nPermitRootLogin no\nPubkeyAuthentication yes\n"
    syswrite(e, "/etc/ssh/sshd_config.d/80-command-lab.conf", text)
    e.command(["sudo", "-n", "sshd", "-t"])
    return e.command(["sudo", "-n", "systemctl", "reload", "ssh"])


def zram(e):
    e.command(["sudo", "-n", "modprobe", "zram"])
    e.command(["sudo", "-n", "zramctl", "/dev/zram0", "--size", str(profile(e, "enable_zram", "size", "512M"))])
    e.command(["sudo", "-n", "mkswap", "/dev/zram0"])
    return e.command(["sudo", "-n", "swapon", "--priority", "100", "/dev/zram0"])


def swap(e, a):
    size = int(a["size"])
    if not 64 <= size <= 65536:
        raise ExecutionError("Swap size is in MiB, between 64 and 65536")
    script = "import pathlib,re,sys; p=pathlib.Path('/etc/dphys-swapfile'); t=p.read_text(); t=re.sub(r'^CONF_SWAPSIZE=.*$', 'CONF_SWAPSIZE='+sys.argv[1],t,flags=re.M); p.write_text(t)"
    e.command(["sudo", "-n", "dphys-swapfile", "swapoff"])
    e.command(["sudo", "-n", sys.executable, "-c", script, str(size)])
    e.command(["sudo", "-n", "dphys-swapfile", "setup"])
    return e.command(["sudo", "-n", "dphys-swapfile", "swapon"])


def users(e, a):
    user, operation = name(a.get("user") or profile(e, "manage_users", "user")), profile(e, "manage_users", "operation")
    choices = {"add": ["useradd", "-m", user], "remove": ["userdel", user], "lock": ["usermod", "-L", user],
               "unlock": ["usermod", "-U", user], "sudo": ["usermod", "-aG", "sudo", user]}
    if operation not in choices:
        raise ExecutionError("manage_users.operation must be add, remove, lock, unlock or sudo")
    return e.command(["sudo", "-n", *choices[operation]])


def clear_notifications(e):
    # Dunst provides an explicit notification history API; GNOME does not expose it universally.
    return e.command(["dunstctl", "close-all"])
