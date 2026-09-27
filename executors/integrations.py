"""Real media, local inference and configured account integrations.

Account credentials are read from named environment variables, never bundled.
Optional integrations fail explicitly when their service/device is unavailable.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import smtplib
import ssl
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import wave
from email.message import EmailMessage
from pathlib import Path

from tinyagent.executor import ExecutionError
from .common import bind, command, is_module, output_path, profile, seconds


def register(e):
    bind(e, "camera_photo", lambda a: camera(e, a, False), modules=("cv2",))
    bind(e, "camera_video", lambda a: camera(e, a, True), modules=("cv2",))
    bind(e, "record_audio", lambda a: record_audio(e, a), modules=("sounddevice", "numpy"))
    bind(e, "screen_record_start", lambda a: recording(e, True), programs=("ffmpeg",))
    bind(e, "screen_record_stop", lambda a: recording(e, False), preflight=lambda a: require_recording(e))
    bind(e, "convert_media", lambda a: convert_media(e, a), preflight=lambda a: conversion_requirements(e, a))
    if not is_module("faster_whisper"):
        e.requirements["convert_media"]["programs"] = ["ffmpeg"]
    bind(e, "transcribe_audio", lambda a: transcribe(e, a), modules=("faster_whisper",))
    command(e, "list_llm_models", ["ollama", "list"], programs=("ollama",), mutates=False)
    command(e, "pull_llm_model", lambda a: ["ollama", "pull", a["model"]], programs=("ollama",), timeout=3600)
    bind(e, "run_llm", lambda a: local_llm(e, a, False))
    bind(e, "run_llm_benchmark", lambda a: local_llm(e, a, True), mutates=False)
    bind(e, "stop_llm", lambda a: stop_llm(e))
    bind(e, "send_email", lambda a: send_email(e, a), configuration=("host", "from", "username", "password_env"),
         preflight=lambda a: secret(e.settings.get("send_email", {}), "password_env"))
    bind(e, "send_message", lambda a: send_message(e, a), configuration=("apps",), preflight=lambda a: message_profile(e, a))
    bind(e, "web_login", lambda a: browser_agent.login(e, a), modules=("playwright",), configuration=("sites",),
         preflight=lambda a: login_profile(e, a))


def camera(e, a, video):
    import cv2
    action = "camera_video" if video else "camera_photo"
    index = profile(e, action, "device", 0)
    capture = cv2.VideoCapture(index, cv2.CAP_DSHOW if os.name == "nt" and isinstance(index, int) else cv2.CAP_ANY)
    writer = None
    try:
        if not capture.isOpened(): raise ExecutionError("Camera is unavailable or its permission is disabled")
        ok, frame = capture.read()
        if not ok: raise ExecutionError("Camera produced no frame")
        dest = output_path(e, "camera", "avi" if video else "jpg")
        if not video:
            if not cv2.imwrite(str(dest), frame): raise ExecutionError("Could not save camera image")
            return {"file": str(dest), "width": frame.shape[1], "height": frame.shape[0]}
        duration = seconds(a, 300)
        fps = float(profile(e, action, "fps", 20))
        if not 1 <= fps <= 60: raise ExecutionError("Camera FPS must be 1–60")
        writer = cv2.VideoWriter(str(dest), cv2.VideoWriter_fourcc(*"MJPG"), fps, (frame.shape[1], frame.shape[0]))
        if not writer.isOpened(): raise ExecutionError("Video encoder could not open the output file")
        started = time.monotonic(); count = 0
        while time.monotonic() - started < duration:
            writer.write(frame); count += 1
            time.sleep(max(0, started + count / fps - time.monotonic()))
            ok, frame = capture.read()
            if not ok: raise ExecutionError("Camera disconnected during recording")
        return {"file": str(dest), "frames": count, "seconds": round(time.monotonic() - started, 2), "audio": False}
    finally:
        capture.release()
        if writer: writer.release()


def record_audio(e, a):
    import sounddevice as sd
    duration = seconds(a, 300)
    rate = int(profile(e, "record_audio", "sample_rate", 16000))
    if not 8000 <= rate <= 192000: raise ExecutionError("Invalid audio sample rate")
    dest = output_path(e, "microphone", "wav")
    device = e.settings.get("record_audio", {}).get("device")
    audio = sd.rec(int(duration * rate), samplerate=rate, channels=1, dtype="int16", device=device)
    sd.wait()
    with wave.open(str(dest), "wb") as writer:
        writer.setnchannels(1); writer.setsampwidth(2); writer.setframerate(rate); writer.writeframes(audio.tobytes())
    return {"file": str(dest), "seconds": duration, "sample_rate": rate}


def require_recording(e):
    job = e.processes.get("screen-record")
    if not job or job.poll() is not None: raise ExecutionError("This model has no active screen recording")


def recording(e, start):
    if not start:
        require_recording(e)
        process = e.processes.pop("screen-record")
        process.communicate(input=b"q\n", timeout=15)
        e.screen_record_log.close()
        if process.returncode: raise ExecutionError("Screen recorder failed. See " + e.screen_record_log.name)
        return {"stopped": True, "file": str(e.screen_record_file), "bytes": e.screen_record_file.stat().st_size}
    old = e.processes.get("screen-record")
    if old and old.poll() is None: raise ExecutionError("A recording is already running for this model")
    dest = output_path(e, "screen", "mkv")
    duration = int(profile(e, "screen_record_start", "maximum_seconds", 300))
    if not 1 <= duration <= 3600: raise ExecutionError("Recording limit must be 1–3600 seconds")
    source = ["-f", "gdigrab", "-i", "desktop"] if os.name == "nt" else ["-f", "x11grab", "-i", os.environ.get("DISPLAY", ":0")]
    argv = ["ffmpeg", "-n", *source, "-t", str(duration), "-r", "15", "-c:v", "libx264", "-preset", "ultrafast", str(dest)]
    stream = output_path(e, "screen-recorder", "log").open("ab")
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=stream, stderr=stream,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    time.sleep(.5)
    if process.poll() is not None:
        stream.close(); raise ExecutionError("Screen recorder did not start. See " + stream.name)
    e.screen_record_log, e.screen_record_file = stream, dest
    e.processes["screen-record"] = process
    e.trace.append({"kind": "process", "argv": argv, "pid": process.pid})
    return {"recording": True, "file": str(dest), "pid": process.pid, "automatic_stop_seconds": duration, "scope": "entire desktop"}


def convert_output(e, a):
    extension = str(a.get("format") or profile(e, "convert_media", "format", "mp4")).lower().lstrip(".")
    if extension not in {"mp4", "mkv", "avi", "webm", "mov", "mp3", "wav", "flac", "ogg", "gif"}:
        raise ExecutionError("Unsupported media output format")
    return output_path(e, "converted", extension)


def conversion_requirements(e, a):
    format_name = str(a["format"]).lower().strip()
    if format_name in {"text", "txt"}:
        if not is_module("faster_whisper"): raise ExecutionError("Install faster-whisper and a local transcription model for text conversion")
    elif not e._executable("ffmpeg"):
        raise ExecutionError("Install ffmpeg for media conversion")


def convert_media(e, a):
    format_name = str(a["format"]).lower().strip()
    if format_name in {"text", "txt"}: return transcribe(e, {"file": a["file"]})
    audio_options = ["-ar", "16000", "-ac", "1"] if format_name in {"16khz wav", "16 khz wav"} else []
    normalized = a | {"format": "wav" if audio_options else format_name}
    dest = convert_output(e, normalized)
    result = e.command(["ffmpeg", "-nostdin", "-n", "-i", str(e.path(a["file"])), *audio_options, str(dest)], timeout=600)
    return {"file": str(dest), "conversion": result}


def transcribe(e, a):
    from faster_whisper import WhisperModel
    source = e.path(a["file"])
    model_path = str(profile(e, "transcribe_audio", "model", "base"))
    model = WhisperModel(model_path, device="cpu", compute_type="int8", local_files_only=True)
    segments, info = model.transcribe(str(source), language=a.get("language"))
    rows = [{"start": s.start, "end": s.end, "text": s.text} for s in segments]
    dest = output_path(e, "transcript", "json")
    result = {"language": info.language, "text": "".join(s["text"] for s in rows), "segments": rows}
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    return result | {"file": str(dest)}


def json_request(url, body=None, headers=None, timeout=60):
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def local_llm(e, a, benchmark):
    action = "run_llm_benchmark" if benchmark else "run_llm"
    base = str(profile(e, "ollama", "url", "http://127.0.0.1:11434")).rstrip("/")
    started = time.perf_counter()
    result = json_request(base + "/api/generate", {"model": a["model"], "prompt": profile(e, action, "prompt", "Explain in three sentences what a computer does."),
        "stream": False, "keep_alive": "5m", "options": {"num_predict": 128}}, timeout=300)
    if result.get("error"): raise ExecutionError(result["error"])
    e.last_llm = a["model"]
    count, nanos = result.get("eval_count", 0), result.get("eval_duration", 0)
    return {"model": a["model"], "response": result.get("response"), "seconds": round(time.perf_counter() - started, 3),
            "generated_tokens": count, "tokens_per_second": round(count / (nanos / 1e9), 2) if nanos else None}


def stop_llm(e):
    model = getattr(e, "last_llm", None) or profile(e, "stop_llm", "model")
    base = str(profile(e, "ollama", "url", "http://127.0.0.1:11434")).rstrip("/")
    result = json_request(base + "/api/generate", {"model": model, "keep_alive": 0})
    if result.get("error"): raise ExecutionError(result["error"])
    return {"unloaded_model": model, "response": result}


def secret(config, key):
    variable = config.get(key)
    value = os.environ.get(str(variable)) if variable else None
    if not value: raise ExecutionError(f"Set {key} to the name of an environment variable containing the credential, and define it before starting the lab")
    return value


def send_email(e, a):
    config = e.settings["send_email"]
    message = EmailMessage(); message["From"] = config["from"]; message["To"] = a["to"]
    message["Subject"] = a.get("subject", "Message from Command Lab"); message.set_content(a["text"])
    context = ssl.create_default_context()
    if config.get("tls", "ssl") == "ssl":
        client = smtplib.SMTP_SSL(config["host"], int(config.get("port", 465)), timeout=30, context=context)
    else:
        client = smtplib.SMTP(config["host"], int(config.get("port", 587)), timeout=30); client.starttls(context=context)
    with client:
        client.login(config["username"], secret(config, "password_env"))
        refused = client.send_message(message)
    if refused: raise ExecutionError("SMTP server refused recipient(s): " + str(refused))
    return {"accepted_by_smtp": True, "to": a["to"], "subject": str(message["Subject"])}


def message_profile(e, a):
    config = e.settings.get("send_message", {}).get("apps", {}).get(a["app"])
    if not isinstance(config, dict): raise ExecutionError("Configure send_message.apps." + a["app"])
    contacts = config.get("contacts", {})
    if a["contact"] not in contacts: raise ExecutionError("Configure the exact recipient ID for contact: " + a["contact"])
    provider = config.get("provider", a["app"])
    if provider == "signal":
        if not config.get("account") or not e._executable("signal-cli"):
            raise ExecutionError("Signal requires signal-cli, an already registered account and send_message.apps.signal.account")
    else: secret(config, "token_env")
    if provider not in {"telegram", "slack", "discord", "whatsapp", "sms", "teams", "signal"}:
        raise ExecutionError("Use telegram, slack, discord, whatsapp, sms, teams or signal")
    return config, contacts[a["contact"]], provider


def send_message(e, a):
    config, recipient, provider = message_profile(e, a)
    if provider == "signal":
        return {"provider": provider, "contact": a["contact"], "native": e.command(["signal-cli", "-a", config["account"], "send", "-m", a["text"], str(recipient)], timeout=60)}
    token = secret(config, "token_env")
    text = a["text"]
    if provider == "telegram":
        response = json_request(f"https://api.telegram.org/bot{token}/sendMessage", {"chat_id": recipient, "text": text})
        if not response.get("ok"): raise ExecutionError("Telegram rejected the message: " + str(response.get("description")))
        identity = response["result"]["message_id"]
    elif provider == "slack":
        response = json_request("https://slack.com/api/chat.postMessage", {"channel": recipient, "text": text}, {"Authorization": "Bearer " + token})
        if not response.get("ok"): raise ExecutionError("Slack rejected the message: " + str(response.get("error")))
        identity = response.get("ts")
    elif provider == "discord":
        response = json_request(f"https://discord.com/api/v10/channels/{urllib.parse.quote(str(recipient), safe='')}/messages",
                                {"content": text}, {"Authorization": "Bot " + token})
        identity = response["id"]
    elif provider == "teams":
        response = json_request(f"https://graph.microsoft.com/v1.0/chats/{urllib.parse.quote(str(recipient), safe='')}/messages",
                                {"body": {"contentType": "text", "content": text}}, {"Authorization": "Bearer " + token})
        identity = response["id"]
    elif provider == "whatsapp":
        version = config.get("api_version")
        phone = config.get("phone_number_id")
        if not version or not phone: raise ExecutionError("Configure WhatsApp api_version and phone_number_id")
        response = json_request(f"https://graph.facebook.com/{version}/{phone}/messages", {"messaging_product": "whatsapp", "to": recipient,
            "type": "text", "text": {"body": text}}, {"Authorization": "Bearer " + token})
        identity = response["messages"][0]["id"]
    else:
        import base64
        sid, sender = config.get("account_sid"), config.get("from")
        if not sid or not sender: raise ExecutionError("Configure SMS account_sid and from for Twilio")
        request = urllib.request.Request(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            data=urllib.parse.urlencode({"To": recipient, "From": sender, "Body": text}).encode(),
            headers={"Authorization": "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode()})
        with urllib.request.urlopen(request, timeout=30) as result: response = json.load(result)
        identity = response["sid"]
    return {"accepted_by_provider": True, "provider": provider, "contact": a["contact"], "message_id": identity}


def login_profile(e, a):
    config = e.settings.get("web_login", {}).get("sites", {}).get(a["site"])
    if not isinstance(config, dict): raise ExecutionError("Configure web_login.sites for the exact site: " + a["site"])
    for key in ("url", "username_selector", "password_selector", "submit_selector", "success_selector"):
        if not config.get(key): raise ExecutionError("Web login profile is missing " + key)
    if urllib.parse.urlparse(config["url"]).scheme != "https": raise ExecutionError("Login profiles require an HTTPS URL")
    return config


class BrowserAgent:
    def __init__(self):
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser-agent")
        self.driver = None; self.browser = None

    def login(self, e, a):
        config = login_profile(e, a)
        return self.pool.submit(self._login, config, a).result(timeout=90)

    def _login(self, config, a):
        from playwright.sync_api import sync_playwright
        if not self.driver: self.driver = sync_playwright().start()
        if not self.browser or not self.browser.is_connected():
            self.browser = self.driver.chromium.launch(headless=False, channel=config.get("browser_channel", "chrome"))
        # Isolated browser context: no personal browser profile is read.
        context = self.browser.new_context(); page = context.new_page()
        page.goto(config["url"], wait_until="domcontentloaded", timeout=30000)
        page.locator(config["username_selector"]).fill(a["username"])
        page.locator(config["password_selector"]).fill(a["password"])
        page.locator(config["submit_selector"]).click()
        page.locator(config["success_selector"]).wait_for(state="visible", timeout=30000)
        return {"login_verified": True, "url": page.url, "visible_browser": True}


browser_agent = BrowserAgent()
