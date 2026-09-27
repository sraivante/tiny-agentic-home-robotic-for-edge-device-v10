"""Presentation categories; classification never affects prediction or execution."""
import re

GROUPS = [
    ("GPIO & I2C", r"gpio|i2c|bcm_to_board|board_to_bcm|show_pinout"),
    ("AI & models", r"llm|ai_accelerator|transcribe"),
    ("Audio & media", r"volume|mute|audio|media(?:_|$)|record_audio"),
    ("Display & appearance", r"brightness|display|screen|wallpaper|resolution|refresh_rate|dark_mode|night_light|high_contrast|magnifier|narrator"),
    ("Browser & web", r"browser|tab$|web_|youtube|open_url|open_site|bookmark|incognito|find_in_page|zoom_|print_page|fullscreen|refresh$"),
    ("Keyboard & clipboard", r"^copy$|^cut$|^paste$|undo|redo|select_all|press_key|type_text|keyboard|clipboard|emoji|input_language|mouse|touchpad|scroll_"),
    ("Network & connectivity", r"wifi|network|bluetooth|ethernet|airplane|hotspot|vpn|dns|ping$|traceroute|bandwidth|_ip$|mac_address|gateway|list_arp|radio|port|connections|discover_devices|packet_capture|scan_lan|speed_test|interface_"),
    ("Security & accounts", r"firewall|antivirus|ssh|banned|unban|manage_users|current_user|logged_in_users|permissions|set_owner|switch_user"),
    ("Files & storage", r"file|folder|disk|drive|partition|archive|symlink|persistent_mount|recycle|save_file|download|transfer|recent_files|print_file"),
    ("Services & processes", r"service|process|cpu_affinity|schedule_task"),
    ("Software & development", r"package|install|update|docker|build_project|virtualenv|find_command"),
    ("Apps & windows", r"(?:^|_)app(?:s|$)|window|desktop|task_view|snap_|open_settings|notification|dnd_|open_config_tool"),
    ("Timers & productivity", r"timer|alarm|stopwatch|reminder|calendar|calculate|send_email|send_message|^get_time$|get_date"),
    ("Hardware & Raspberry Pi", r"firmware|camera|sensor|usb|pci|serial|spi|1wire|overlay|fan|temperature|voltage|throttle|gpu|boot_config|sdcard|pinout|hotplug"),
    ("Terminal & sessions", r"session|run_command|run_script|run_background|watch_command|remote_shell|command_history"),
    ("System & power", r"."),
]


def category_for(action):
    if action in {"help", "unknown", "clarify", "repeat_last"}:
        return "Help & intent handling"
    if action == "list_serial_ports":
        return "Hardware & Raspberry Pi"
    if action == "unban_ip":
        return "Security & accounts"
    return next(name for name, pattern in GROUPS if re.search(pattern, action))
