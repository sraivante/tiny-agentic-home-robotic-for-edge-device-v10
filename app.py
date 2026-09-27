"""Union Command v10 sample lab: local inference UI with a SAMPLE executor.

The executor is a demonstration tool, not a finished product. A risk gate
(--allow-risk) blocks catalog actions above the chosen risk level; the
default "caution" blocks every "critical" action (shutdown, delete, run_command, ...).
"""
from __future__ import annotations

import argparse
import collections
import copy
import json
import os
import secrets
import re
import socket
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.serving import make_server

from categories import category_for
from lab_executor import LabExecutor, ExecutionError, redact
from tinyagent.schema import CATALOG, ROOT


def read_json(path):
    return json.loads(path.read_text("utf-8"))


RISK_LEVELS = {"safe": 0, "caution": 1, "critical": 2}


def create_app(port=8770, files_root=None, predictor_factory=None, allow_risk="caution"):
    if allow_risk not in RISK_LEVELS:
        raise ValueError("allow_risk must be safe, caution or critical")
    app = Flask(__name__, static_folder="web", static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 16384
    manifest = read_json(ROOT / "models.json")
    models = {m["id"]: m for m in manifest["models"]}
    library = read_json(ROOT / "command_library.json")
    token = secrets.token_urlsafe(32)
    predictors = collections.OrderedDict()
    inference_lock, execution_lock, ticket_lock = threading.Lock(), threading.Lock(), threading.Lock()
    tickets = {}
    executors = {key: LabExecutor(Path(files_root or ROOT / "runtime/files") / key) for key in models}
    app.extensions["lab"] = {"executors": executors, "tickets": tickets}
    desktop = next(iter(executors.values())).desktop

    def prune():
        for key in list(tickets):
            if tickets[key]["expires_at"] < time.time() and tickets[key]["state"] != "running":
                del tickets[key]

    @app.before_request
    def local_only():
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if app.testing:
            allowed.add("localhost")
        if request.host not in allowed:
            return jsonify(error="Unrecognized local host"), 403
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and origin not in {"http://" + host for host in allowed}:
                return jsonify(error="Cross-origin requests are not accepted"), 403
            if request.headers.get("X-Lab-Token") != token or not request.is_json:
                return jsonify(error="Missing local session token or JSON body"), 403
            if not isinstance(request.get_json(), dict):
                return jsonify(error="Request body must be a JSON object"), 400

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        return response

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.get("/")
    def index():
        return send_from_directory(ROOT / "web", "index.html")

    @app.get("/api/health")
    def health():
        return jsonify(status="ok", app="union-command-v10-lab", models=len(models), allow_risk=allow_risk)

    @app.get("/api/bootstrap")
    def bootstrap():
        executor = next(iter(executors.values()))
        catalog = []
        for action, spec in CATALOG.items():
            available, reason = executor.availability(action)
            catalog.append(spec | {"category": category_for(action), "live_available": available,
                                   "availability_reason": reason, "implemented": action in executor.adapters,
                                   "executor_platforms": sorted(executor.coverage.get(action, [])),
                                   "requirements": executor.requirements.get(action, {})})
        return jsonify(token=token, models=list(models.values()), catalog=catalog,
                       categories=manifest["categories"], platform=executor.platform,
                       library=library, files_root=str(Path(files_root or ROOT / "runtime/files").resolve()),
                       live_adapters=sum(row["live_available"] for row in catalog), allow_risk=allow_risk,
                       implemented_adapters=len(executor.adapters))

    @app.get("/api/desktop")
    def desktop_state():
        return jsonify(windows=desktop.windows(), target=desktop.target, last_capture=desktop.last_capture)

    @app.post("/api/desktop/target")
    def desktop_target():
        body = request.get_json()
        if set(body) != {"window_id"}: raise ExecutionError("Supply only window_id")
        with execution_lock:
            return jsonify(desktop.select(body["window_id"]))

    @app.get("/api/captures/<name>")
    def capture(name):
        if not re.fullmatch(r"[a-f0-9]{32}\.jpg", name):
            return jsonify(error="Invalid capture name"), 404
        return send_from_directory(ROOT / "runtime/captures", name)

    @app.get("/api/demo")
    def demo():
        run_id = secrets.token_hex(3)
        sequences = {}
        for index, model_id in enumerate(models, 1):
            filename = f"agent-{index}-{run_id}.txt"
            text = "Agentic actions working"
            steps = [
                (f'create file "{filename}"', "create_file", {"path": filename}),
                (f'please open the file "{filename}"', "open_file", {"path": filename}),
                (f'type "{text}"', "type_text", {"text": text}),
                ("save file", "save_file", {}),
                (f'read file "{filename}"', "read_file", {"path": filename}),
            ]
            sequences[model_id] = [{"text": prompt, "action": action, "args": args} for prompt, action, args in steps]
        return jsonify(sequences=sequences, description="Create → open in editor → type → save → verify file")

    @app.post("/api/predict")
    def predict():
        body = request.get_json()
        model_id, text = body.get("model_id"), body.get("text")
        if not isinstance(model_id, str) or model_id not in models:
            raise ExecutionError("Select an available model")
        info = models[model_id]
        if not isinstance(text, str) or not text.strip() or len(text) > info["max_length"]:
            raise ExecutionError(f"Enter a command containing 1 to {info['max_length']} characters")
        with ticket_lock:
            prune()
            if len(tickets) >= 512:
                return jsonify(error="Too many recent requests. Wait for older plans to expire."), 429
        try:
            with inference_lock:
                load_start = time.perf_counter()
                if model_id not in predictors:
                    if predictor_factory is None:
                        from tinyagent.runtime import Predictor
                        model = Predictor(ROOT / info["checkpoint"], threads=2)
                        if model.checkpoint_sha256 != info["sha256"]:
                            raise ExecutionError("Checkpoint checksum differs from the bundled manifest")
                    else:
                        model = predictor_factory(ROOT / info["checkpoint"])
                    predictors[model_id] = model
                    while len(predictors) > 2:
                        predictors.popitem(last=False)
                predictors.move_to_end(model_id)
                load_ms = round((time.perf_counter() - load_start) * 1000, 2)
                prediction = predictors[model_id].predict(text)
        except Exception as exc:
            return jsonify(error=f"Model inference failed: {exc}"), 500
        prediction = copy.deepcopy(prediction)
        errors = prediction.get("validation_errors", [])
        plan, plan_error, ticket_id = None, None, None
        if not errors:
            try:
                plan = executors[model_id].preview(prediction["action"], prediction["args"])
                # plan["risk"] is the executor's effective risk (never lower than the catalog label).
                risk = plan.get("risk", "critical")
                if plan["live_available"] and RISK_LEVELS.get(risk, 2) > RISK_LEVELS[allow_risk]:
                    plan.update(live_available=False, availability_reason=(
                        f"Blocked by the sample risk gate: '{prediction['action']}' is risk '{risk}'. "
                        f"Restart the lab with --allow-risk {risk} to allow it (at your own risk)."))
                if plan["live_available"]:
                    ticket_id = secrets.token_urlsafe(24)
                    with ticket_lock:
                        tickets[ticket_id] = {"model_id": model_id, "plan": copy.deepcopy(plan), "state": "ready",
                                              "expires_at": plan["expires_at"], "result": None}
                else:
                    with executors[model_id].lock:
                        executors[model_id].pending.pop(plan["id"], None)
            except (ValueError, OSError) as exc:
                plan_error = str(exc)
        public_plan = {k: v for k, v in copy.deepcopy(plan).items() if k not in {"id", "required_confirmation"}} if plan else None
        if public_plan:
            for step in public_plan["commands"]:
                env = step.get("environment", {})
                if "TINY_UNION_ARGUMENTS" in env:
                    env["TINY_UNION_ARGUMENTS"] = json.dumps(redact(json.loads(env["TINY_UNION_ARGUMENTS"])))
            public_plan = redact(public_plan)
        return jsonify(model_id=model_id, text=text, prediction=prediction, plan=public_plan,
                       plan_error=plan_error, ticket_id=ticket_id, load_ms=load_ms)

    @app.post("/api/execute")
    def execute():
        body = request.get_json()
        if set(body) != {"ticket_id"} or not isinstance(body["ticket_id"], str):
            raise ExecutionError("Execution accepts only a model-generated ticket_id")
        with ticket_lock:
            prune()
            ticket = tickets.get(body["ticket_id"])
            if not ticket:
                raise ExecutionError("This model plan expired or does not exist. Run the prediction again.")
            if ticket["state"] == "done":
                return jsonify(ticket["result"] | {"replayed": True})
            if ticket["state"] == "running":
                return jsonify(error="This command is already running; it will not execute twice."), 409
            ticket["state"] = "running"
        try:
            with execution_lock:
                result = executors[ticket["model_id"]].execute_automatically(ticket["plan"])
        except ExecutionError as exc:
            result = {"status": "failed", "executed": False, "error": str(exc),
                      "action": ticket["plan"]["action"]}
        except Exception as exc:
            result = {"status": "failed", "executed": None, "execution_state": "unknown",
                      "error": f"Unexpected executor failure; the operation may have started: {exc}",
                      "action": ticket["plan"]["action"]}
        with ticket_lock:
            ticket.update(state="done", result=result)
        return jsonify(result)

    return app


def main():
    parser = argparse.ArgumentParser(description="Union Command v10 local sample lab")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--files-root", type=Path)
    parser.add_argument("--allow-risk", choices=list(RISK_LEVELS), default="caution",
                        help="Highest catalog risk level the sample executor may run (default: caution)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be from 1 to 65535")
    # Fail before constructing the app if another server owns this port.
    with socket.socket() as probe:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            parser.exit(1, f"Port {args.port} is occupied. Try --port {args.port + 1}.\n")
    server = make_server("127.0.0.1", args.port, create_app(args.port, args.files_root, allow_risk=args.allow_risk), threaded=True)
    url = f"http://127.0.0.1:{args.port}"
    print(f"\nUnion Command v10 lab is ready: {url}\nRisk gate: actions up to '{args.allow_risk}' may execute. Sample executor only, not a finished product.\nPress Ctrl+C to stop.\n", flush=True)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
