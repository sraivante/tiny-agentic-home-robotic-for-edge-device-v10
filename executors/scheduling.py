"""Deterministic English/Hinglish time phrases used by the command datasets."""
import datetime as dt
import re
from tinyagent.executor import ExecutionError


def clock_time(text, default=None):
    text = text.lower().strip()
    if "midnight" in text: return dt.time(0)
    if "noon" in text: return dt.time(12)
    match = re.search(r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje)?\b", text)
    if not match:
        if default: return dt.time.fromisoformat(default)
        raise ExecutionError("Supply a clock time, for example 09:30 or 3 pm")
    hour, minute = int(match[1]), int(match[2] or 0)
    suffix = match[3]
    if suffix in {"am", "pm"} and not 1 <= hour <= 12: raise ExecutionError("AM/PM hours must be 1–12")
    if suffix == "am": hour %= 12
    elif suffix == "pm" or any(word in text for word in ("shaam", "dopahar", "evening", "afternoon")):
        if hour < 12: hour += 12
    return dt.time(hour, minute)


def event_time(text, now=None):
    now = now or dt.datetime.now()
    try: return dt.datetime.fromisoformat(text)
    except ValueError: pass
    text = text.lower().strip()
    date = now.date()
    if "parso" in text or "day after tomorrow" in text: date += dt.timedelta(days=2)
    elif "tomorrow" in text or re.search(r"\bkal\b", text): date += dt.timedelta(days=1)
    else:
        for index, weekday in enumerate(("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")):
            if weekday in text:
                date += dt.timedelta(days=(index - now.weekday()) % 7 or 7)
                break
    default = "09:00" if "morning" in text or "subah" in text else None
    stamp = dt.datetime.combine(date, clock_time(text, default))
    if stamp <= now and date == now.date() and not any(word in text for word in ("today", "aaj")):
        stamp += dt.timedelta(days=1)
    return stamp


def task_schedule(text, daily_time="09:00"):
    value = text.strip().lower()
    if value in {"on reboot", "at boot", "startup"}: return {"kind": "boot"}
    if value in {"every hour", "hourly", "har ghante"}: return {"kind": "minutes", "interval": 60}
    match = re.fullmatch(r"every (\d+) minutes?", value)
    if match:
        interval = int(match[1])
        if not 1 <= interval <= 1439: raise ExecutionError("Task interval must be 1–1439 minutes")
        return {"kind": "minutes", "interval": interval}
    if value in {"daily", "roz", "roz subah"}: return {"kind": "daily", "at": dt.time.fromisoformat(daily_time).strftime("%H:%M")}
    if re.fullmatch(r"[\d*/?,\-]+(?:\s+[\d*/?,\-]+){4}", value): return {"kind": "cron", "expression": value}
    return {"kind": "daily", "at": clock_time(value).strftime("%H:%M")}


def cron_expression(schedule):
    if schedule["kind"] == "boot": return "@reboot"
    if schedule["kind"] == "cron": return schedule["expression"]
    if schedule["kind"] == "daily":
        hour, minute = map(int, schedule["at"].split(":")); return f"{minute} {hour} * * *"
    interval = schedule["interval"]
    if interval <= 60 and 60 % interval == 0: return "0 * * * *" if interval == 60 else f"*/{interval} * * * *"
    if interval % 60 == 0 and 24 % (interval // 60) == 0: return f"0 */{interval // 60} * * *"
    raise ExecutionError("This interval cannot be represented exactly by cron. Use a divisor of 60 minutes or 24 hours.")
