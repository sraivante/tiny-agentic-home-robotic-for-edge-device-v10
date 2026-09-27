"""Raspberry Pi GPIO, I2C and device adapters; no simulated hardware success."""
from __future__ import annotations
import datetime as dt
import os
import re
import time
from pathlib import Path

from tinyagent.executor import ExecutionError
from .common import bind, command, detached, name, profile, seconds


def register(e):
    def cmd(action, builder, program, mutates=False, config=()):
        command(e, action, builder, ("pi",), mutates, programs=(program,), configuration=config)
    commands = {
        "get_temperature": ["vcgencmd", "measure_temp"], "get_voltage": ["vcgencmd", "measure_volts"],
        "get_throttle_status": ["vcgencmd", "get_throttled"], "get_firmware_version": ["vcgencmd", "version"],
        "get_firmware_log": ["sudo", "-n", "vcdbg", "log", "msg"], "get_gpu_memory": ["vcgencmd", "get_mem", "gpu"],
        "get_power_draw": ["vcgencmd", "pmic_read_adc"],
        "get_boot_config": ["vcgencmd", "get_config", "int"], "list_gpio": ["pinctrl", "get"],
        "gpio_debug_all": ["pinctrl", "get"], "list_gpio_chips": ["gpiodetect"], "show_pinout": ["pinout"],
        "gpio_conflicts": ["gpioinfo"], "list_cameras": ["rpicam-hello", "--list-cameras"],
        "check_ai_accelerator": ["lspci", "-nn"], "gpio_reset_all": ["pinctrl", "set", "0-27", "ip", "pn"],
        "flash_firmware": ["sudo", "-n", "rpi-eeprom-update", "-a"],
        "expand_filesystem": ["sudo", "-n", "raspi-config", "nonint", "do_expand_rootfs"],
    }
    for action, argv in commands.items():
        cmd(action, argv, argv[2] if argv[:2] == ["sudo", "-n"] else argv[0], action in {"gpio_reset_all", "flash_firmware", "expand_filesystem"})
    for action in ["gpio_read", "gpio_info", "gpio_debug"]:
        cmd(action, lambda a: ["pinctrl", "get", str(e._bcm(a))], "pinctrl")
    for action, suffix in [("gpio_on", ["op", "dh"]), ("gpio_off", ["op", "dl"]), ("gpio_reset", ["ip", "pn"])]:
        cmd(action, lambda a, s=suffix: ["pinctrl", "set", str(e._bcm(a)), *s], "pinctrl", True)
    cmd("gpio_set_mode", lambda a: ["pinctrl", "set", str(e._bcm(a)), {"input": "ip", "output": "op"}[a["mode"]]], "pinctrl", True)
    cmd("gpio_set_pull", lambda a: ["pinctrl", "set", str(e._bcm(a)), {"up": "pu", "down": "pd", "none": "pn"}[a["pull"]]], "pinctrl", True)
    cmd("gpio_set_alt", lambda a: ["pinctrl", "set", str(e._bcm(a)), alt(a["alt"])], "pinctrl", True)
    cmd("gpio_alt_functions", lambda a: ["pinctrl", "funcs", str(e._bcm(a))], "pinctrl")
    cmd("load_overlay", lambda a: ["sudo", "-n", "dtoverlay", name(a["overlay"])], "dtoverlay", True)
    cmd("fix_gpio_permissions", lambda a: ["sudo", "-n", "usermod", "-aG", "gpio", os.environ.get("USER", "pi")], "usermod", True)
    cmd("i2c_scan", lambda a: ["i2cdetect", "-y", str(profile(e, "i2c", "bus", 1))], "i2cdetect")
    cmd("i2c_dump", lambda a: ["i2cdump", "-y", str(profile(e, "i2c", "bus", 1)), address(a["address"])], "i2cdump")
    cmd("i2c_read", lambda a: ["i2cget", "-y", str(profile(e, "i2c", "bus", 1)), address(a["address"]), byte(a["register"])], "i2cget")
    cmd("i2c_write", lambda a: ["i2cset", "-y", str(profile(e, "i2c", "bus", 1)), address(a["address"]), byte(a["register"]), byte(a["value"])], "i2cset", True)
    for action, enabled in [("interface_enable", True), ("interface_disable", False)]:
        cmd(action, lambda a, on=enabled: interface(a, on), "raspi-config", True)
    cmd("set_boot_order", lambda a: ["sudo", "-n", "raspi-config", "nonint", "do_boot_order", boot_order(e, a)], "raspi-config", True)
    for action, fn in [
        ("get_fan_speed", lambda a: sysfiles("/sys/class/hwmon", "fan*_input")),
        ("get_sdcard_info", lambda a: sysfiles("/sys/block/mmcblk0/device", "*")),
        ("list_1wire_sensors", lambda a: [p.name for p in Path("/sys/bus/w1/devices").glob("*-*")]),
        ("list_spi_devices", lambda a: [str(p) for p in Path("/dev").glob("spidev*")]),
        ("list_serial_ports", lambda a: sorted(str(p) for pattern in ["ttyUSB*", "ttyACM*", "serial*"] for p in Path("/dev").glob(pattern))),
        ("bcm_to_board", lambda a: {"bcm": a["pin"], "board": e._pin_map().get(a["pin"])}),
        ("board_to_bcm", lambda a: {"board": a["pin"], "bcm": e._bcm(a)}),
        ("gpio_pin_role", lambda a: pin_role(e, a)),
        ("list_free_gpio", lambda a: free_gpio(e)),
        ("gpio_toggle", lambda a: toggle(e, a)),
        ("gpio_blink", lambda a: blink(e, a)),
        ("gpio_pulse", lambda a: pulse(e, a)),
    ]:
        bind(e, action, fn, ("pi",), mutates=action in {"gpio_toggle", "gpio_blink", "gpio_pulse"},
             programs=("pinctrl",) if action in {"gpio_toggle", "gpio_blink", "gpio_pulse", "list_free_gpio"} else ())
    bind(e, "gpio_pwm", lambda a: pwm(e, a), ("pi",), modules=("gpiozero",))
    bind(e, "gpio_servo", lambda a: servo(e, a), ("pi",), modules=("gpiozero",))
    bind(e, "gpio_watch", lambda a: watch(e, a), ("pi",), modules=("gpiozero",))
    bind(e, "gpio_stop_watch", lambda a: stop_watch(e, a), ("pi",))
    bind(e, "get_gpu_usage", lambda a: gpu_usage(), ("pi",), mutates=False)
    bind(e, "gpio_reset", lambda a: reset(e, a), ("pi",), programs=("pinctrl",))
    bind(e, "gpio_reset_all", lambda a: reset(e, None), ("pi",), programs=("pinctrl",))


def boot_order(e, a):
    value = str(a.get("order") or profile(e, "set_boot_order", "order")).strip().lower()
    choices = {"b1": "B1", "sd card": "B1", "sd": "B1", "b2": "B2", "nvme": "B2", "usb": "B2", "nvme then usb": "B2", "b3": "B3", "network": "B3"}
    if value not in choices: raise ExecutionError("Use sd card, nvme, usb, network or raspi-config B1/B2/B3; ambiguous boot order is not guessed")
    return choices[value]


def reset(e, a):
    pins = [e._bcm(a)] if a is not None else list(range(28))
    for pin in pins:
        if pin in e.gpio_devices: e.gpio_devices.pop(pin).close()
    return e.command(["pinctrl", "set", str(pins[0]) if len(pins) == 1 else "0-27", "ip", "pn"])


def gpu_usage():
    """Measure exported DRM engine busy-time counters, not GPU clock speed."""
    def sample():
        rows = {}
        for folder in Path("/proc").glob("[0-9]*/fdinfo"):
            try:
                for path in folder.iterdir():
                    try: raw = path.read_text()
                    except (OSError, UnicodeError): continue
                    if "drm-client-id:" not in raw: continue
                    data = dict(line.split(":", 1) for line in raw.splitlines() if ":" in line)
                    client = (data.get("drm-driver", "").strip(), data.get("drm-pdev", "").strip(), data["drm-client-id"].strip())
                    for key, value in data.items():
                        if key.startswith("drm-engine-") and not key.startswith("drm-engine-capacity-"):
                            match = re.fullmatch(r"\s*(\d+)\s+ns\s*", value)
                            if match:
                                capacity = max(1, int(data.get("drm-engine-capacity-" + key[11:], "1")))
                                rows[client + (key[11:],)] = (int(match[1]), capacity)
            except OSError: continue
        return rows
    first = sample(); started = time.monotonic_ns(); time.sleep(1); second = sample(); elapsed = time.monotonic_ns() - started
    common = set(first) & set(second)
    if not common: raise ExecutionError("No readable DRM engine counters. This driver/kernel may not export GPU usage, or no GPU clients are active.")
    return {"sample_seconds": elapsed / 1e9, "scope": "Readable DRM clients, deduplicated by device/client ID", "clients": [
        {"driver": key[0], "device": key[1], "client_id": key[2], "engine": key[3],
         "busy_percent": round(max(0, second[key][0] - first[key][0]) / elapsed / second[key][1] * 100, 2)} for key in sorted(common)]}


def alt(value):
    text = str(value).lower().replace("alt", "a")
    if not re.fullmatch(r"a[0-8]", text):
        raise ExecutionError("GPIO ALT function must be a0 through a8")
    return text


def byte(value):
    number = int(str(value), 0) if isinstance(value, str) else int(value)
    if not 0 <= number <= 255:
        raise ExecutionError("I2C register/value must be one byte")
    return hex(number)


def address(value):
    number = int(str(value), 0) if isinstance(value, str) else int(value)
    if not 3 <= number <= 0x77:
        raise ExecutionError("I2C address must be between 0x03 and 0x77")
    return hex(number)


def interface(a, enabled):
    choices = {"i2c": "do_i2c", "spi": "do_spi", "serial": "do_serial_hw", "onewire": "do_onewire", "camera": "do_camera"}
    if a["interface"] not in choices:
        raise ExecutionError("Unknown Pi interface")
    return ["sudo", "-n", "raspi-config", "nonint", choices[a["interface"]], "0" if enabled else "1"]


def sysfiles(root, pattern):
    rows = {}
    for path in Path(root).rglob(pattern):
        if path.is_file():
            try:
                rows[str(path)] = path.read_text().strip()[:1000]
            except (OSError, UnicodeError):
                continue
    if not rows:
        raise ExecutionError("No matching hardware readings exist under " + root)
    return rows


def level(e, pin):
    text = e.command(["pinctrl", "get", str(pin)])["stdout"]
    match = re.search(r"\|\s*(hi|lo)\b|\b(dh|dl)\b", text)
    if not match:
        raise ExecutionError("Could not parse pinctrl level: " + text)
    return (match.group(1) or match.group(2)) in {"hi", "dh"}


def toggle(e, a):
    pin = e._bcm(a); value = not level(e, pin)
    e.command(["pinctrl", "set", str(pin), "op", "dh" if value else "dl"])
    return {"pin": pin, "high": value}


def pulse(e, a):
    pin = e._bcm(a); duration = seconds(a, 300)
    e.command(["pinctrl", "set", str(pin), "op", "dh"])
    try:
        time.sleep(duration)
    finally:
        e.command(["pinctrl", "set", str(pin), "op", "dl"])
    return {"pin": pin, "pulse_seconds": duration, "final_level": "low"}


def blink(e, a):
    pin, count, interval = e._bcm(a), a.get("count", 5), a.get("interval", 500) / 1000
    if not 1 <= count <= 1000 or interval < .01 or 2 * interval * count > 300:
        raise ExecutionError("Blink count/duration exceeds 300 seconds or interval is below 10 ms")
    try:
        for _ in range(count):
            e.command(["pinctrl", "set", str(pin), "op", "dh"]); time.sleep(interval)
            e.command(["pinctrl", "set", str(pin), "op", "dl"]); time.sleep(interval)
    finally:
        e.command(["pinctrl", "set", str(pin), "op", "dl"])
    return {"pin": pin, "blinks": count, "final_level": "low"}


def pwm(e, a):
    from gpiozero import PWMOutputDevice
    pin = e._bcm(a)
    if pin in e.gpio_devices:
        e.gpio_devices.pop(pin).close()
    device = PWMOutputDevice(pin, frequency=a.get("freq", 100), initial_value=a["duty"] / 100)
    e.gpio_devices[pin] = device
    return {"pin": pin, "duty_percent": a["duty"], "frequency_hz": device.frequency, "scope": "While this executor is running"}


def servo(e, a):
    from gpiozero import AngularServo
    pin = e._bcm(a)
    if pin in e.gpio_devices:
        e.gpio_devices.pop(pin).close()
    device = AngularServo(pin, min_angle=0, max_angle=180, initial_angle=a["angle"])
    e.gpio_devices[pin] = device
    return {"pin": pin, "angle": device.angle}


def watch(e, a):
    from gpiozero import DigitalInputDevice
    pin = e._bcm(a)
    if pin in e.gpio_devices:
        e.gpio_devices.pop(pin).close()
    device = DigitalInputDevice(pin, pull_up=None, active_state=True)
    def event(value):
        e.gpio_events.append({"pin": pin, "high": value, "time": dt.datetime.now().isoformat()})
        e.gpio_events[:] = e.gpio_events[-500:]
    edge = a.get("edge", "both")
    if edge in {"rising", "both"}: device.when_activated = lambda: event(True)
    if edge in {"falling", "both"}: device.when_deactivated = lambda: event(False)
    e.gpio_devices[pin] = device
    return {"watching": pin, "edge": edge, "initial_high": bool(device.value)}


def stop_watch(e, a):
    pins = [e._bcm(a)] if "pin" in a else list(e.gpio_devices)
    stopped = []
    for pin in pins:
        if pin in e.gpio_devices:
            e.gpio_devices.pop(pin).close(); stopped.append(pin)
    return {"stopped": stopped, "events": list(e.gpio_events)}


def free_gpio(e):
    text = e.command(["pinctrl", "get"])["stdout"]
    return {"input_mode_candidates": [line for line in text.splitlines() if re.search(r"\bip\b", line)],
            "message": "Input mode alone does not prove a pin is unclaimed. Check gpioinfo before attaching hardware."}


def pin_role(e, a):
    roles = {2: "SDA1", 3: "SCL1", 14: "TXD", 15: "RXD", 10: "SPI MOSI", 9: "SPI MISO", 11: "SPI SCLK", 8: "SPI CE0", 7: "SPI CE1"}
    fixed = {1: "3V3", 17: "3V3", 2: "5V", 4: "5V", **{p: "GND" for p in (6, 9, 14, 20, 25, 30, 34, 39)}}
    if "pin" in a:
        if a.get("numbering") == "board" and a["pin"] in fixed:
            return {"board": a["pin"], "bcm": None, "role": fixed[a["pin"]]}
        pin = e._bcm(a)
        return {"bcm": pin, "board": e._pin_map().get(pin), "role": roles.get(pin, "general-purpose GPIO")}
    query = str(a.get("role", "")).casefold()
    query = "gnd" if query in {"ground", "gnd"} else query
    return ([{"bcm": pin, "board": e._pin_map()[pin], "role": role} for pin, role in roles.items() if query in role.casefold()]
            + [{"board": pin, "bcm": None, "role": role} for pin, role in fixed.items() if query in role.casefold()])
