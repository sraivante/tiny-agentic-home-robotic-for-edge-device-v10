import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from lab_executor import LabExecutor
from tinyagent.schema import CATALOG, ROOT, validate_command


class FakePredictor:
    output = {"action": "get_time", "args": {}}

    def __init__(self, checkpoint):
        self.checkpoint = checkpoint

    def predict(self, text):
        value = copy.deepcopy(self.output)
        return value | {"confidence": .99, "alternatives": [], "latency_ms": 1,
                        "validation_errors": validate_command(value["action"], value["args"]),
                        "target": value["action"], "model_epoch": 2}


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="command-lab-tests-")
        FakePredictor.output = {"action": "get_time", "args": {}}
        self.app = create_app(files_root=self.temp.name, predictor_factory=FakePredictor)
        self.app.testing = True
        self.client = self.app.test_client()
        self.bootstrap = self.client.get("/api/bootstrap").get_json()
        self.headers = {"X-Lab-Token": self.bootstrap["token"]}
        self.model_id = self.bootstrap["models"][0]["id"]

    def tearDown(self):
        self.temp.cleanup()

    def predict(self, **extra):
        response = self.client.post("/api/predict", json={"model_id": self.model_id, "text": "time please", **extra}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def execute(self, prediction, **extra):
        return self.client.post("/api/execute", json={"ticket_id": prediction["ticket_id"], **extra}, headers=self.headers)

    def test_all_models_have_all_category_actions_and_valid_examples(self):
        library = self.bootstrap["library"]
        self.assertEqual(len(self.bootstrap["models"]), 1)
        for model in self.bootstrap["models"]:
            self.assertEqual(set(model["actions"]), set(CATALOG))
            self.assertEqual(set(library[model["dataset_id"]]), set(CATALOG))
            for action, rows in library[model["dataset_id"]].items():
                self.assertTrue(rows, action)
                for row in rows:
                    self.assertFalse(validate_command(row["action"], row["args"]), row)

    def test_model_output_executes_without_manual_confirmation(self):
        result = self.predict()
        self.assertEqual(result["prediction"]["action"], "get_time")
        self.assertNotIn("required_confirmation", result["plan"])
        executed = self.execute(result).get_json()
        self.assertEqual(executed["status"], "completed")
        self.assertTrue(executed["executed"])

    def test_file_effect_is_model_specific_and_replay_does_not_execute_twice(self):
        FakePredictor.output = {"action": "create_file", "args": {"path": "original.txt"}}
        prediction = self.predict()
        self.assertEqual(self.execute(prediction).get_json()["status"], "completed")
        replay = self.execute(prediction).get_json()
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["status"], "completed")
        self.assertTrue((Path(self.temp.name) / self.model_id / "original.txt").is_file())

    def test_client_cannot_replace_predicted_action_or_arguments(self):
        prediction = self.predict()
        self.assertEqual(self.execute(prediction, action="shutdown").status_code, 400)
        self.assertEqual(self.execute(prediction, args={"path": "changed.txt"}).status_code, 400)
        self.assertEqual(self.execute(prediction).get_json()["action"], "get_time")

    def test_invalid_and_unavailable_predictions_have_no_execution_ticket(self):
        for output in [{"action": "set_volume", "args": {"value": 101}}, {"action": "factory_reset", "args": {}}]:
            FakePredictor.output = output
            prediction = self.predict()
            self.assertIsNone(prediction["ticket_id"])

    def test_risk_gate_blocks_critical_actions_by_default(self):
        self.assertEqual(self.bootstrap["allow_risk"], "caution")
        FakePredictor.output = {"action": "shutdown", "args": {}}
        prediction = self.predict()
        self.assertIsNone(prediction["ticket_id"])
        self.assertIn("risk gate", prediction["plan"]["availability_reason"])

    def test_risk_gate_can_be_opened_explicitly(self):
        app = create_app(files_root=self.temp.name, predictor_factory=FakePredictor, allow_risk="critical")
        app.testing = True
        client = app.test_client()
        token = client.get("/api/bootstrap").get_json()["token"]
        FakePredictor.output = {"action": "delete_file", "args": {"path": "missing.txt"}}
        body = client.post("/api/predict", json={"model_id": self.model_id, "text": "delete"},
                           headers={"X-Lab-Token": token}).get_json()
        self.assertNotIn("risk gate", (body["plan"] or {}).get("availability_reason", ""))

    def test_file_escape_fails_in_executor(self):
        FakePredictor.output = {"action": "create_file", "args": {"path": "../escape.txt"}}
        result = self.execute(self.predict()).get_json()
        self.assertEqual(result["status"], "failed")
        self.assertFalse((Path(self.temp.name) / "escape.txt").exists())

    def test_unexpected_failure_does_not_claim_no_operation_happened(self):
        executor = self.app.extensions["lab"]["executors"][self.model_id]
        prediction = self.predict()
        with patch.object(executor, "execute_automatically", side_effect=TypeError("unexpected")):
            result = self.execute(prediction).get_json()
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["executed"])
        self.assertEqual(result["execution_state"], "unknown")

    def test_foreign_host_origin_and_missing_token_are_rejected(self):
        self.assertEqual(self.client.get("/api/bootstrap", headers={"Host": "evil.example"}).status_code, 403)
        body = {"model_id": self.model_id, "text": "time"}
        self.assertEqual(self.client.post("/api/predict", json=body).status_code, 403)
        self.assertEqual(self.client.post("/api/predict", json=body, headers=self.headers | {"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/predict", json=[], headers=self.headers).status_code, 400)

    def test_invalid_model_and_command_are_rejected_before_inference(self):
        for body in [{"model_id": "../secret", "text": "hi"}, {"model_id": self.model_id, "text": " "},
                     {"model_id": self.model_id, "text": "x" * 257}]:
            self.assertEqual(self.client.post("/api/predict", json=body, headers=self.headers).status_code, 400)

    def test_concurrent_duplicate_ticket_does_not_repeat_side_effect(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        executor = self.app.extensions["lab"]["executors"][self.model_id]

        def operation(args):
            calls.append(1)
            entered.set()
            release.wait(5)
            return "done"

        executor.add("get_time", operation, "Test operation")
        prediction = self.predict()
        responses = []

        def first():
            with self.app.test_client() as client:
                responses.append(client.post("/api/execute", json={"ticket_id": prediction["ticket_id"]}, headers=self.headers))

        worker = threading.Thread(target=first)
        worker.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.execute(prediction).status_code, 409)
        finally:
            release.set()
            worker.join(5)
        self.assertEqual(calls, [1])
        self.assertEqual(responses[0].get_json()["status"], "completed")


class NativePlanTests(unittest.TestCase):
    def test_native_plan_is_exactly_the_executed_argv(self):
        with tempfile.TemporaryDirectory(prefix="command-lab-native-") as root:
            executor = LabExecutor(root)
            plan = executor.preview("run_command", {"command": "echo sample"})
            with patch.object(executor, "command", return_value={"stdout": "sample"}) as command:
                result = executor.execute_automatically(plan)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(command.call_args.args[0], plan["commands"][0]["argv"])
                self.assertEqual(command.call_args.args[1], 20)


if __name__ == "__main__":
    unittest.main()
