"""Use working installed dependencies, or provision an isolated local environment."""
from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from download_model import ensure_model  # noqa: E402  (standard library only)


def ready(python):
    check = ("import torch,flask,psutil,numpy,transformers,tokenizers,PIL; "
             "assert transformers.__version__ == '4.56.2'; "
             "assert tokenizers.__version__ == '0.22.1'; "
             "assert tuple(map(int,torch.__version__.split('+')[0].split('.')[:2])) >= (2,6); "
             "from transformers import BertModel; " + ("import pywinauto,pycaw" if os.name == "nt" else ""))
    try:
        return subprocess.run([str(python), "-c", check], cwd=ROOT, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except OSError:
        return False


def main():
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 or newer is required. Python 3.11–3.13 is recommended.")
    os.chdir(ROOT)
    local = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python = local if local.exists() else Path(sys.executable)
    if not ready(python):
        if python != Path(sys.executable) and ready(sys.executable):
            python = Path(sys.executable)
        else:
            print("Preparing .venv for the Union Command v10 lab. First setup requires internet access.", flush=True)
            if not local.exists():
                venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")
            python = local
            subprocess.run([str(python), "-m", "pip", "install", "torch>=2.6,<3",
                            "--index-url", "https://download.pytorch.org/whl/cpu"], check=True)
            subprocess.run([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], check=True)
            if not ready(python):
                raise SystemExit("Dependency verification failed. See setup output above.")
    ensure_model(quiet=True)  # downloads the 96 MB checkpoint from Hugging Face once, SHA-256 verified
    environment = os.environ.copy()
    environment.update(PYTHONUTF8="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", USE_TF="0", USE_FLAX="0")
    print("Starting the Union Command v10 sample lab with " + str(python), flush=True)
    try:
        return subprocess.call([str(python), str(ROOT / "app.py"), *sys.argv[1:]], cwd=ROOT, env=environment)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
