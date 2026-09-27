"""Owned background operations; launched with explicit, JSON-encoded inputs."""
import json
import subprocess
import sys
import time

if __name__ == "__main__":
    mode, args = sys.argv[1], json.loads(sys.argv[2])
    if mode == "watch":
        while True:
            result = subprocess.run(args["argv"], timeout=30, capture_output=True, text=True)
            print(json.dumps({"time": time.time(), "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}), flush=True)
            time.sleep(args["interval"])
    elif mode == "stress":
        stop = time.monotonic() + args["seconds"]
        value = 1
        while time.monotonic() < stop:
            value = (value * 1234567 + 891) % 987654321
        print(json.dumps({"finished": True, "seconds": args["seconds"]}))
