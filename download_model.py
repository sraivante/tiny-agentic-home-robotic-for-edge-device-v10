"""Download the Union Command v10 checkpoint from Hugging Face and verify its SHA-256.

Standard library only, so it works before any dependency is installed:
    python download_model.py
"""
from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ID = "sraivante/union-command-minilm-v10"
FILENAME = "models/a100_minilm_v10_quoted/best.pt"
SHA256 = "c91d44e4a687152cd65d86d56cfc0e453a60a1afe14f8c395e8af058f2fc4d5b"
SIZE = 96200101
URL = f"https://huggingface.co/{REPO_ID}/resolve/main/{FILENAME}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_model(quiet: bool = False) -> Path:
    target = ROOT / FILENAME
    if target.exists() and target.stat().st_size == SIZE and sha256(target) == SHA256:
        if not quiet:
            print(f"Model already present and verified: {target}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    print(f"Downloading Union Command v10 ({SIZE / 1e6:.1f} MB) from {URL}", flush=True)
    request = urllib.request.Request(URL, headers={"User-Agent": "union-command-v10-downloader"})
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
        done = 0
        while True:
            block = response.read(1 << 20)
            if not block:
                break
            out.write(block)
            done += len(block)
            if sys.stdout.isatty():
                print(f"\r  {done / 1e6:6.1f} / {SIZE / 1e6:.1f} MB", end="", flush=True)
    if sys.stdout.isatty():
        print()
    actual = sha256(partial)
    if actual != SHA256:
        partial.unlink(missing_ok=True)
        raise SystemExit(f"Checksum mismatch: expected {SHA256}, got {actual}. Nothing was installed.")
    os.replace(partial, target)
    print(f"Verified SHA-256 and saved: {target}")
    return target


if __name__ == "__main__":
    ensure_model()
