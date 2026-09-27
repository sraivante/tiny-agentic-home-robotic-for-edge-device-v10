"""Windows native execution, settings UI automation, and Core Audio control."""
from __future__ import annotations

import ctypes
import datetime as dt
import json
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from tinyagent.executor import ExecutionError
from .common import bind, command, detached, output_path, profile, ps, seconds

WINRT_AWAIT = """
Add-Type -AssemblyName System.Runtime.WindowsRuntime;
function Await-Operation($Operation, $ResultType) {
 $m=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {$_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'} | Select-Object -First 1;
 $task=$m.MakeGenericMethod($ResultType).Invoke($null,@($Operation)); $task.Wait(); $task.Result;
}
"""


def register(e):
    def cmd(action, argv, program=None, mutates=True, config=(), timeout=45):
        command(e, action, argv, ("windows",), mutates, programs=(program,) if program else (), configuration=config, timeout=timeout)
    scripts = {
        "list_usb": ("Get-PnpDevice -PresentOnly | Where-Object InstanceId -like 'USB*' | Select-Object FriendlyName,Status,InstanceId | ConvertTo-Json", False),
        "list_pci": ("Get-PnpDevice -PresentOnly | Where-Object InstanceId -like 'PCI*' | Select-Object FriendlyName,Status,InstanceId | ConvertTo-Json", False),
        "list_serial_ports": ("Get-CimInstance Win32_SerialPort | Select-Object DeviceID,Name,Status | ConvertTo-Json", False),
        "list_cameras": ("Get-PnpDevice -PresentOnly -Class Camera | Select-Object FriendlyName,Status,InstanceId | ConvertTo-Json", False),
        "list_bluetooth_devices": ("Get-PnpDevice -PresentOnly -Class Bluetooth | Select-Object FriendlyName,Status,InstanceId | ConvertTo-Json", False),
        "get_radio_status": ("Get-NetAdapter -Physical | Select-Object Name,Status,InterfaceDescription | ConvertTo-Json", False),
        "get_temperature": ("$t=Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature; if (!$t) {throw 'No ACPI thermal sensor is exposed by this hardware'}; $t | Select-Object InstanceName,@{N='Celsius';E={($_.CurrentTemperature/10)-273.15}} | ConvertTo-Json", False),
        "get_fan_speed": ("Get-CimInstance Win32_Fan | Select-Object Name,DesiredSpeed,Status | ConvertTo-Json", False),
        "get_gpu_memory": ("Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json", False),
        "get_gpu_usage": ("(Get-Counter '\\GPU Engine(*)\\Utilization Percentage').CounterSamples | Where-Object CookedValue -gt 0 | Select-Object InstanceName,CookedValue | ConvertTo-Json", False),
        "get_firmware_version": ("Get-CimInstance Win32_BIOS | Select-Object Manufacturer,SMBIOSBIOSVersion,ReleaseDate | ConvertTo-Json", False),
        "get_service_logs": ("Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Service Control Manager'} -MaxEvents 300 | Where-Object Message -Match ([regex]::Escape($a.service)) | Select-Object -First 30 TimeCreated,Id,Message | ConvertTo-Json", False),
        "get_kernel_log": ("Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-Kernel-General'} -MaxEvents 50 | Select-Object TimeCreated,Id,Message | ConvertTo-Json", False),
        "get_boot_config": ("& bcdedit.exe /enum '{current}'; if ($LASTEXITCODE) {throw 'bcdedit failed; administrator access may be required'}", False),
        "wifi_on": ("Get-NetAdapter -Physical | Where-Object {$_.NdisPhysicalMedium -eq 9 -or $_.InterfaceDescription -match 'Wireless|Wi-Fi'} | Enable-NetAdapter -Confirm:$false -PassThru | Select-Object Name,Status | ConvertTo-Json", True),
        "wifi_off": ("Get-NetAdapter -Physical | Where-Object {$_.NdisPhysicalMedium -eq 9 -or $_.InterfaceDescription -match 'Wireless|Wi-Fi'} | Disable-NetAdapter -Confirm:$false -PassThru | Select-Object Name,Status | ConvertTo-Json", True),
        "antivirus_scan": ("Start-MpScan -ScanType QuickScan; Get-MpComputerStatus | Select-Object QuickScanStartTime,QuickScanEndTime,AntivirusEnabled | ConvertTo-Json", True),
        "firewall_on": ("Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled True; Get-NetFirewallProfile | Select-Object Name,Enabled | ConvertTo-Json", True),
        "firewall_off": ("Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False; Get-NetFirewallProfile | Select-Object Name,Enabled | ConvertTo-Json", True),
        "firewall_allow": ("New-NetFirewallRule -DisplayName ('CommandLab allow '+$a.port) -Direction Inbound -Action Allow -Protocol TCP -LocalPort ([int]$a.port) | Select-Object Name,Enabled,Action | ConvertTo-Json", True),
        "firewall_deny": ("New-NetFirewallRule -DisplayName ('CommandLab block '+$a.port) -Direction Inbound -Action Block -Protocol TCP -LocalPort ([int]$a.port) | Select-Object Name,Enabled,Action | ConvertTo-Json", True),
        "brightness_up": ("$b=Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness; $step=10; if($a.step){$step=$a.step}; $v=[byte][math]::Min(100,$b.CurrentBrightness+$step); Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{Timeout=[uint32]1;Brightness=$v}; @{brightness=$v}|ConvertTo-Json", True),
        "brightness_down": ("$b=Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness; $step=10; if($a.step){$step=$a.step}; $v=[byte][math]::Max(0,$b.CurrentBrightness-$step); Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{Timeout=[uint32]1;Brightness=$v}; @{brightness=$v}|ConvertTo-Json", True),
        "set_locale": ("Set-Culture $a.locale; Get-Culture | Select-Object Name | ConvertTo-Json", True),
        "set_keyboard_layout": ("Set-WinUserLanguageList -LanguageList @($a.layout) -Force; Get-WinUserLanguageList | ConvertTo-Json", True),
        "set_disk_label": ("$letter=$a.device.TrimEnd(':','\\'); if($letter -notmatch '^[A-Za-z]$'){throw 'Use a drive letter, for example E:'}; Set-Volume -DriveLetter $letter -NewFileSystemLabel $a.label; Get-Volume -DriveLetter $letter | Select-Object DriveLetter,FileSystemLabel | ConvertTo-Json", True),
        "empty_recycle_bin": ("Clear-RecycleBin -Force -ErrorAction Stop; @{cleared=$true}|ConvertTo-Json", True),
        "enable_auto_updates": ("$p='HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\WindowsUpdate\\AU'; New-Item $p -Force | Out-Null; Set-ItemProperty $p -Name NoAutoUpdate -Value 0; Set-Service wuauserv -StartupType Manual; Start-Service wuauserv; @{automatic_updates=$true}|ConvertTo-Json", True),
        "disable_auto_updates": ("$p='HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\WindowsUpdate\\AU'; New-Item $p -Force | Out-Null; Set-ItemProperty $p -Name NoAutoUpdate -Value 1; @{automatic_updates=$false}|ConvertTo-Json", True),
        "install_updates": ("$s=New-Object -ComObject Microsoft.Update.Session; $r=$s.CreateUpdateSearcher().Search('IsInstalled=0 and IsHidden=0'); $c=New-Object -ComObject Microsoft.Update.UpdateColl; foreach($u in $r.Updates){if(!$u.EulaAccepted){throw ('Update requires separate EULA acceptance: '+$u.Title)}; [void]$c.Add($u)}; if($c.Count){$d=$s.CreateUpdateDownloader();$d.Updates=$c;[void]$d.Download();$i=$s.CreateUpdateInstaller();$i.Updates=$c;$z=$i.Install();@{ResultCode=$z.ResultCode;RebootRequired=$z.RebootRequired;Count=$c.Count}|ConvertTo-Json}else{@{Count=0}|ConvertTo-Json}", True),
        "check_updates": ("$s=New-Object -ComObject Microsoft.Update.Session; $s.CreateUpdateSearcher().Search('IsInstalled=0 and IsHidden=0').Updates | Select-Object Title,IsDownloaded | ConvertTo-Json", False),
        "mount_drive": ("Mount-DiskImage -ImagePath $a.device -PassThru | Select-Object ImagePath,Attached | ConvertTo-Json", True),
        "unmount_drive": ("Dismount-DiskImage -ImagePath $a.device -PassThru | Select-Object ImagePath,Attached | ConvertTo-Json", True),
        "format_drive": ("$d=$a._config.drive; if($d -notmatch '^[A-Za-z]:?$'){throw 'Configure format_drive.drive as a drive letter'}; $d=$d.TrimEnd(':'); if($d -eq $env:SystemDrive.TrimEnd(':')){throw 'The running system drive cannot be formatted'}; Format-Volume -DriveLetter $d -FileSystem $a._config.filesystem -Confirm:$false -Force | ConvertTo-Json", True),
        "partition_disk": ("$number=[int]$a._config.disk_number; $d=Get-Disk -Number $number; if($d.IsBoot -or $d.IsSystem){throw 'Boot/system disk cannot be partitioned by the running OS'}; Initialize-Disk -Number $number -PartitionStyle GPT -PassThru | New-Partition -UseMaximumSize -AssignDriveLetter | Format-Volume -FileSystem NTFS -Confirm:$false | ConvertTo-Json", True),
        "create_service": ("New-Service -Name $a.name -BinaryPathName $a._config.command -StartupType Automatic | Select-Object Name,Status | ConvertTo-Json", True),
        "set_swap_size": ("$c=Get-CimInstance Win32_ComputerSystem; Set-CimInstance $c -Property @{AutomaticManagedPagefile=$false}; $p=Get-CimInstance Win32_PageFileSetting; if(!$p){throw 'No configured page file found'}; Set-CimInstance $p -Property @{InitialSize=[uint32]$a.size;MaximumSize=[uint32]$a.size}; @{size_mb=$a.size;requires_restart=$true}|ConvertTo-Json", True),
        "factory_reset": ("if($a._config.mode -notin @('keep_user_data','remove_everything')){throw 'Configure factory_reset.mode'}; $w=Get-CimInstance -Namespace root/cimv2/mdm/dmmap -ClassName MDM_RemoteWipe; $method=if($a._config.mode -eq 'keep_user_data'){'doWipePersistUserDataMethod'}else{'doWipeMethod'}; Invoke-CimMethod -InputObject $w -MethodName $method | ConvertTo-Json", True),
    }
    configuration = {"format_drive": ("drive", "filesystem"), "partition_disk": ("disk_number",), "factory_reset": ("mode",), "create_service": ("command",)}
    for action, (script, mutates) in scripts.items():
        ps(e, action, script, mutates, configuration.get(action, ()), timeout=1800 if action == "install_updates" else 90)
    for action, light in [("dark_mode_on", False), ("dark_mode_off", True)]:
        ps(e, action, f"$p='HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize'; New-Item $p -Force | Out-Null; Set-ItemProperty $p AppsUseLightTheme {int(light)}; Set-ItemProperty $p SystemUsesLightTheme {int(light)}; @{{light_theme=${str(light).lower()}}}|ConvertTo-Json")
    for action, mode in [("set_volume", "set"), ("get_volume", "get"), ("volume_up", "up"), ("volume_down", "down"), ("mute", "mute"), ("unmute", "unmute"), ("mic_mute", "mic_mute"), ("mic_unmute", "mic_unmute"), ("set_audio_output", "output")]:
        bind(e, action, lambda a, m=mode: audio(e, a, m), ("windows",), mutates=action != "get_volume", modules=("pycaw",))
    for action, uri, labels, desired in [
        ("night_light_on", "nightlight", ["Night light"], True), ("night_light_off", "nightlight", ["Night light"], False),
        ("dnd_on", "notifications", ["Do not disturb"], True), ("dnd_off", "notifications", ["Do not disturb"], False),
        ("battery_saver_on", "batterysaver", ["Energy saver", "Battery saver", "Always use energy saver"], True),
        ("battery_saver_off", "batterysaver", ["Energy saver", "Battery saver", "Always use energy saver"], False),
        ("bluetooth_on", "bluetooth", ["Bluetooth"], True), ("bluetooth_off", "bluetooth", ["Bluetooth"], False),
        ("airplane_mode_on", "network-airplanemode", ["Airplane mode"], True), ("airplane_mode_off", "network-airplanemode", ["Airplane mode"], False),
        ("touchpad_on", "devices-touchpad", ["Touchpad"], True), ("touchpad_off", "devices-touchpad", ["Touchpad"], False),
    ]:
        bind(e, action, lambda a, u=uri, l=labels, d=desired, n=action: e.desktop.setting_toggle(u, e.settings.get(n, {}).get("labels", l), d), ("windows",), modules=("pywinauto",))
    native = {
        "get_cpu_governor": ["powercfg.exe", "/getactivescheme"],
        "cancel_shutdown": ["shutdown.exe", "/a"], "hibernate": ["shutdown.exe", "/h"], "sign_out": ["shutdown.exe", "/l"],
        "sync_time": ["w32tm.exe", "/resync"], "repair_packages": ["DISM.exe", "/Online", "/Cleanup-Image", "/RestoreHealth"],
        "update_packages": ["winget.exe", "source", "update", "--disable-interactivity"],
    }
    for action, argv in native.items():
        cmd(action, argv, argv[0], mutates=action != "get_cpu_governor", timeout=600 if action == "repair_packages" else 45)
    for action, verb in [("install_package", "install"), ("uninstall_package", "uninstall"), ("uninstall_app", "uninstall")]:
        cmd(action, lambda a, v=verb: ["winget.exe", v, "--exact", "--id", a.get("name", a.get("app")), "--silent", "--disable-interactivity"], "winget.exe", timeout=600)
    cmd("power_mode", lambda a: ["powercfg.exe", "/setactive", {"performance": "SCHEME_MIN", "balanced": "SCHEME_BALANCED"}[a["mode"]]], "powercfg.exe")
    cmd("check_filesystem", lambda a: ["chkdsk.exe", a["device"], "/scan"], "chkdsk.exe", mutates=False, timeout=300)
    cmd("forget_wifi", lambda a: ["netsh.exe", "wlan", "delete", "profile", "name=" + a["ssid"]], "netsh.exe")
    cmd("show_wifi_password", lambda a: ["netsh.exe", "wlan", "show", "profile", "name=" + (a.get("ssid") or profile(e, "show_wifi_password", "ssid")), "key=clear"], "netsh.exe", mutates=False)
    cmd("vpn_connect", lambda a: ["rasdial.exe", str(profile(e, "vpn_connect", "name"))], "rasdial.exe", config=("name",))
    cmd("vpn_disconnect", lambda a: ["rasdial.exe", str(profile(e, "vpn_disconnect", "name")), "/disconnect"], "rasdial.exe", config=("name",))
    cmd("backup_disk_image", lambda a: ["wbadmin.exe", "start", "backup", "-backupTarget:" + str(profile(e, "backup_disk_image", "destination")), "-include:" + a["device"], "-quiet"], "wbadmin.exe", config=("destination",), timeout=1800)
    cmd("set_boot_order", lambda a: ["bcdedit.exe", "/bootsequence", str(a.get("order") or profile(e, "set_boot_order", "identifier"))], "bcdedit.exe")
    bind(e, "open_serial_terminal", lambda a: detached(e, [str(profile(e, "open_serial_terminal", "putty", "putty.exe")), "-serial", a["serial_port"], "-sercfg", str(a.get("baud", 115200))], visible=True), ("windows",), programs=("putty.exe",))
    for action, fn, config in [
        ("connect_wifi", lambda a: connect_wifi(e, a), ()), ("hotspot_on", lambda a: hotspot(e, a, True), ()),
        ("hotspot_off", lambda a: hotspot(e, a, False), ()), ("bluetooth_connect", lambda a: bluetooth_connect(e, a), ()),
        ("set_wallpaper", lambda a: wallpaper(e, a), ()), ("set_mouse_speed", lambda a: mouse_speed(a), ()),
        ("high_contrast_on", lambda a: high_contrast(True), ()), ("high_contrast_off", lambda a: high_contrast(False), ()),
        ("lock_screen", lambda a: lock_screen(), ()), ("sleep", lambda a: suspend(a), ()),
        ("screen_off", lambda a: screen_off(), ()), ("switch_user", lambda a: switch_user(e), ()),
        ("print_file", lambda a: print_file(e, a), ()), ("open_recycle_bin", lambda a: e.desktop.track_open(lambda: os.startfile("shell:RecycleBinFolder")), ()),
        ("set_resolution", lambda a: display(e, a), ()), ("set_refresh_rate", lambda a: display(e, a), ()),
        ("rotate_screen", lambda a: display(e, a), ()), ("set_display_scale", lambda a: display_scale(e, a), ()),
        ("display_mode", lambda a: e.command(["DisplaySwitch.exe", {"pc_only": "/internal", "duplicate": "/clone", "extend": "/extend", "second_only": "/external"}[a["mode"]]]), ()),
        ("clear_notifications", lambda a: clear_notifications(e), ()), ("schedule_task", lambda a: schedule(e, a), ()),
        ("set_owner", lambda a: e.command(["icacls.exe", str(e.path(a["path"])), "/setowner", a["user"]]), ()),
        ("set_permissions", lambda a: e.command(["icacls.exe", str(e.path(a["path"])), "/grant:r", str(profile(e, "set_permissions", "grant"))]), ("grant",)),
        ("manage_users", lambda a: manage_users(e, a), ("operation",)),
        ("edit_system_config", lambda a: edit_config(e, a), ("file",)),
        ("clone_disk", lambda a: clone_disk(e, a), ("source",)),
    ]:
        bind(e, action, fn, ("windows",), configuration=config)
    for action, app in [("magnifier_on", "magnify.exe"), ("onscreen_keyboard", "osk.exe"), ("narrator_on", "Narrator.exe")]:
        bind(e, action, lambda a, exe=app: e.desktop.track_open(lambda: subprocess.Popen([e._executable(exe)])), ("windows",), programs=(app,))
    cmd("magnifier_off", ["taskkill.exe", "/IM", "Magnify.exe"], "taskkill.exe")
    cmd("narrator_off", ["taskkill.exe", "/IM", "Narrator.exe"], "taskkill.exe")
    for action, method in [("media_play", "TryPlayAsync"), ("media_pause", "TryPauseAsync")]:
        ps(e, action, WINRT_AWAIT + "[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType=WindowsRuntime] | Out-Null; "
           "$m=Await-Operation ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]); $s=$m.GetCurrentSession(); if(!$s){throw 'No media session is active'}; "
           + f"$ok=Await-Operation ($s.{method}()) ([bool]); if(!$ok){{throw 'The media session rejected the action'}}; @{{accepted=$ok}}|ConvertTo-Json")


def audio(e, a, mode):
    import comtypes
    comtypes.CoInitialize()
    try:
        from pycaw.pycaw import AudioUtilities
        if mode == "output":
            from pycaw.constants import ERole
            target = str(e.settings.get("set_audio_output", {}).get(a["output"], a["output"])).casefold()
            matches = [d for d in AudioUtilities.GetAllDevices() if target in str(d.FriendlyName).casefold() or target == str(d.id).casefold()]
            if len(matches) != 1:
                raise ExecutionError("Set set_audio_output." + a["output"] + " to one exact device ID/name; matches=" + str(len(matches)))
            AudioUtilities.SetDefaultDevice(matches[0].id, roles=[ERole.eConsole, ERole.eMultimedia, ERole.eCommunications])
            return {"default_output": matches[0].FriendlyName}
        device = AudioUtilities.GetMicrophone() if mode.startswith("mic_") else AudioUtilities.GetSpeakers()
        volume = device.EndpointVolume
        if mode == "set": volume.SetMasterVolumeLevelScalar(a["value"] / 100, None)
        if mode in {"up", "down"}:
            value = volume.GetMasterVolumeLevelScalar() + (a.get("step", 10) / 100) * (1 if mode == "up" else -1)
            volume.SetMasterVolumeLevelScalar(max(0, min(1, value)), None)
        if mode in {"mute", "mic_mute", "unmute", "mic_unmute"}: volume.SetMute(mode in {"mute", "mic_mute"}, None)
        return {"device": device.FriendlyName, "volume_percent": round(volume.GetMasterVolumeLevelScalar() * 100, 2), "muted": bool(volume.GetMute())}
    finally:
        comtypes.CoUninitialize()


def connect_wifi(e, a):
    if a.get("password"):
        ns = "http://www.microsoft.com/networking/WLAN/profile/v1"
        ET.register_namespace("", ns)
        root = ET.Element("{" + ns + "}WLANProfile")
        def child(parent, tag, text=None):
            item = ET.SubElement(parent, "{" + ns + "}" + tag); item.text = text; return item
        child(root, "name", a["ssid"]); config = child(root, "SSIDConfig"); ssid = child(config, "SSID"); child(ssid, "name", a["ssid"])
        child(root, "connectionType", "ESS"); child(root, "connectionMode", "auto")
        sec = child(child(root, "MSM"), "security"); auth = child(sec, "authEncryption")
        child(auth, "authentication", "WPA2PSK"); child(auth, "encryption", "AES"); child(auth, "useOneX", "false")
        key = child(sec, "sharedKey"); child(key, "keyType", "passPhrase"); child(key, "protected", "false"); child(key, "keyMaterial", a["password"])
        path = output_path(e, "wifi-profile", "xml")
        try:
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            e.command(["netsh.exe", "wlan", "add", "profile", "filename=" + str(path), "user=current"])
        finally:
            path.unlink(missing_ok=True)
    return e.command(["netsh.exe", "wlan", "connect", "name=" + a["ssid"], "ssid=" + a["ssid"]])


def hotspot(e, a, enabled):
    if enabled:
        ssid = a.get("ssid", "CommandLab"); password = a.get("password") or profile(e, "hotspot_on", "password")
        e.command(["netsh.exe", "wlan", "set", "hostednetwork", "mode=allow", "ssid=" + ssid, "key=" + str(password)])
    return e.command(["netsh.exe", "wlan", "start" if enabled else "stop", "hostednetwork"])


def bluetooth_connect(e, a):
    from pywinauto import Desktop
    e.desktop.track_open(lambda: os.startfile("ms-settings:bluetooth"), title_hint="Settings")
    window = Desktop(backend="uia").window(handle=int(e.desktop.require_target()["id"]))
    matches = [c for c in window.descendants() if a["device"].casefold() in c.window_text().casefold() and c.element_info.control_type in {"ListItem", "Group"}]
    if len(matches) != 1:
        raise ExecutionError("A unique paired Bluetooth device was not found in Settings: " + a["device"])
    buttons = [b for b in matches[0].descendants(control_type="Button") if b.window_text().casefold() == "connect"]
    if not buttons:
        raise ExecutionError("The paired device exposes no Connect button; check its profile and connection state")
    buttons[0].click()
    return {"connection_requested": a["device"], "target": e.desktop.target}


def wallpaper(e, a):
    path = e.path(a["path"])
    if not ctypes.windll.user32.SystemParametersInfoW(20, 0, str(path), 3):
        raise ExecutionError("Windows rejected the wallpaper change")
    return {"wallpaper": str(path)}


def mouse_speed(a):
    raw = str(a["value"]).lower(); value = {"slow": 5, "slower": 5, "dheere": 5, "medium": 10, "normal": 10, "fast": 15, "faster": 15, "tez": 15}.get(raw, raw)
    if isinstance(value, str) and value.isdigit(): value = int(value)
    if type(value) is not int or not 1 <= value <= 20:
        raise ExecutionError("Mouse speed must be 1–20, slow or fast")
    if not ctypes.windll.user32.SystemParametersInfoW(0x71, 0, value, 3):
        raise ExecutionError("Windows rejected mouse speed")
    return {"mouse_speed": value}


def high_contrast(enabled):
    from ctypes import wintypes as w
    class HC(ctypes.Structure):
        _fields_ = [("size", w.UINT), ("flags", w.DWORD), ("scheme", w.LPWSTR)]
    value = HC(); value.size = ctypes.sizeof(HC)
    if not ctypes.windll.user32.SystemParametersInfoW(0x42, value.size, ctypes.byref(value), 0):
        raise ExecutionError("Cannot read high-contrast settings")
    value.flags = (value.flags | 1) if enabled else (value.flags & ~1)
    if not ctypes.windll.user32.SystemParametersInfoW(0x43, value.size, ctypes.byref(value), 3):
        raise ExecutionError("Cannot set high contrast")
    return {"high_contrast": enabled}


def lock_screen():
    if not ctypes.windll.user32.LockWorkStation(): raise ExecutionError("Windows did not lock the workstation")
    return {"lock_requested": True}


def suspend(a):
    if a.get("amount"): time.sleep(seconds(a, 300))
    if not ctypes.windll.powrprof.SetSuspendState(False, False, False): raise ExecutionError("Windows rejected suspend")
    return {"suspend_requested": True}


def screen_off():
    ctypes.windll.user32.SendMessageW(0xFFFF, 0x112, 0xF170, 2)
    return {"display_power_off_requested": True}


def switch_user(e):
    return e.command(["tsdiscon.exe"])


def print_file(e, a):
    path = e.path(a["path"]); os.startfile(str(path), "print")
    return {"print_requested": str(path), "message": "Submitted to the registered application and default printer."}


def display(e, a):
    import win32api, win32con
    mode = win32api.EnumDisplaySettings(None, win32con.ENUM_CURRENT_SETTINGS)
    if "resolution" in a:
        match = re.fullmatch(r"(\d{3,5})\s*[xX×]\s*(\d{3,5})", a["resolution"])
        if not match: raise ExecutionError("Resolution must be WIDTHxHEIGHT")
        mode.PelsWidth, mode.PelsHeight = map(int, match.groups()); mode.Fields |= win32con.DM_PELSWIDTH | win32con.DM_PELSHEIGHT
    if "value" in a:
        mode.DisplayFrequency = int(a["value"]); mode.Fields |= win32con.DM_DISPLAYFREQUENCY
    if "orientation" in a:
        orientation = {"landscape": 0, "portrait": 1, "landscape_flipped": 2}[a["orientation"]]
        if mode.DisplayOrientation % 2 != orientation % 2: mode.PelsWidth, mode.PelsHeight = mode.PelsHeight, mode.PelsWidth
        mode.DisplayOrientation = orientation; mode.Fields |= win32con.DM_DISPLAYORIENTATION | win32con.DM_PELSWIDTH | win32con.DM_PELSHEIGHT
    test = win32api.ChangeDisplaySettings(mode, win32con.CDS_TEST)
    if test != 0: raise ExecutionError("Display mode is unsupported; Windows code " + str(test))
    result = win32api.ChangeDisplaySettings(mode, win32con.CDS_UPDATEREGISTRY)
    if result != 0: raise ExecutionError("Display change failed; Windows code " + str(result))
    return {"width": mode.PelsWidth, "height": mode.PelsHeight, "refresh_hz": mode.DisplayFrequency, "orientation": mode.DisplayOrientation}


def display_scale(e, a):
    from pywinauto import Desktop
    e.desktop.track_open(lambda: os.startfile("ms-settings:display"), title_hint="Settings")
    window = Desktop(backend="uia").window(handle=int(e.desktop.require_target()["id"]))
    controls = [c for c in window.descendants(control_type="ComboBox") if "scale" in c.window_text().casefold() or "scale" in c.element_info.automation_id.casefold()]
    if len(controls) != 1: raise ExecutionError("Windows Settings does not expose a unique Scale dropdown")
    combo = controls[0]; combo.expand()
    items = combo.descendants(control_type="ListItem")
    match = next((item for item in items if item.window_text().startswith(str(a["value"]) + "%")), None)
    if not match: raise ExecutionError("Requested display scale is not offered by Windows")
    match.select(); return {"display_scale_percent": a["value"]}


def clear_notifications(e):
    from pywinauto import Desktop
    e.desktop.keys("win+n", focus=False); time.sleep(.5)
    buttons = [b for w in Desktop(backend="uia").windows() for b in w.descendants(control_type="Button") if b.window_text().casefold() in {"clear all", "clear all notifications"}]
    if not buttons: return {"cleared": 0, "message": "No Clear all control is present; notification center may be empty."}
    buttons[0].click(); return {"clear_all_invoked": True}


def schedule(e, a):
    from .scheduling import task_schedule
    spec = task_schedule(a["schedule"], str(profile(e, "schedule_task", "daily_time", "09:00")))
    if spec["kind"] == "cron": raise ExecutionError("Cron syntax requires Linux; Windows accepts daily, at HH:MM, every N minutes, every hour or on reboot")
    args = ["/SC", "ONSTART"] if spec["kind"] == "boot" else (["/SC", "MINUTE", "/MO", str(spec["interval"])] if spec["kind"] == "minutes" else ["/SC", "DAILY", "/ST", spec["at"]])
    name = "CommandLab-" + dt.datetime.now().strftime("%Y%m%d%H%M%S")
    import base64
    script = "Set-Location -LiteralPath '" + str(e.files_root).replace("'", "''") + "'; " + a["command"]
    task = 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + base64.b64encode(script.encode("utf-16-le")).decode()
    return {"schedule": spec, "task_name": name, "native": e.command(["schtasks.exe", "/Create", "/TN", name, *args, "/TR", task, "/F"])}


def manage_users(e, a):
    user = a.get("user") or profile(e, "manage_users", "user")
    operation = profile(e, "manage_users", "operation")
    choices = {"add": ["net.exe", "user", user, "/add"], "remove": ["net.exe", "user", user, "/delete"],
               "lock": ["net.exe", "user", user, "/active:no"], "unlock": ["net.exe", "user", user, "/active:yes"],
               "administrator": ["net.exe", "localgroup", "Administrators", user, "/add"]}
    if operation not in choices: raise ExecutionError("Configure add, remove, lock, unlock or administrator operation")
    return e.command(choices[operation])


def edit_config(e, a):
    path = a.get("file") or profile(e, "edit_system_config", "file")
    return e.desktop.track_open(lambda: subprocess.Popen([e._executable("notepad.exe"), str(path)]), title_hint=Path(path).name)


def clone_disk(e, a):
    # qemu-img understands raw device sources and destinations; targets are explicit.
    source = str(profile(e, "clone_disk", "source")); target = str(a["device"])
    if source == target: raise ExecutionError("Clone source and destination are identical")
    return e.command(["qemu-img.exe", "convert", "-p", "-f", "raw", "-O", "raw", source, target], timeout=1800)
