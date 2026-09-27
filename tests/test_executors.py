import copy
import os
import tempfile
import unittest
from unittest.mock import patch, Mock

from lab_executor import LabExecutor, ExecutionError
from tinyagent.schema import CATALOG
from executors.desktop import Desktop
from executors.hardware import address, byte, alt
from executors.linux import block_device


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agentic-executors-")
        self.e = LabExecutor(self.temp.name)

    def tearDown(self): self.temp.cleanup()

    def test_every_catalog_action_has_a_callable_executor(self):
        self.assertEqual(set(CATALOG), set(self.e.adapters))
        for action, adapter in self.e.adapters.items():
            self.assertTrue(callable(adapter.function), action)
            self.assertTrue(adapter.platforms, action)

    def test_missing_profile_is_not_an_executable_plan(self):
        self.e.settings.pop("run_script", None)
        self.assertFalse(self.e.preview("run_script", {})["live_available"])
        self.e.settings.pop("factory_reset", None)
        self.assertFalse(self.e.preview("factory_reset", {})["live_available"])

    def test_no_keyboard_input_without_a_target(self):
        with patch.object(self.e.desktop, "target", None), patch.object(self.e.desktop, "_input") as send:
            plan = self.e.preview("type_text", {"text": "must not appear"})
            self.assertFalse(plan["live_available"])
            with self.assertRaises(ExecutionError): self.e.execute_automatically(plan)
            send.assert_not_called()

    def test_repeat_reuses_the_last_successful_model_arguments(self):
        first = self.e.execute_automatically(self.e.preview("calculate", {"expression": "12 + 8"}))
        repeated = self.e.execute_automatically(self.e.preview("repeat_last", {}))
        self.assertEqual(first["status"], "completed")
        self.assertEqual(repeated["result"]["result"], first["result"])

    def test_unknown_and_clarify_do_not_claim_device_execution(self):
        for action in ("unknown", "clarify"):
            result = self.e.execute_automatically(self.e.preview(action, {}))
            self.assertFalse(result["executed"])
            self.assertIn(result["status"], {"no_action", "needs_input"})

    def test_native_plan_keeps_the_original_argument_environment(self):
        if self.e.platform != "windows": self.skipTest("Windows PowerShell adapter")
        plan = self.e.preview("set_timezone", {"tz": "UTC"})
        original = copy.deepcopy(plan["commands"][0])
        self.e.settings["set_timezone"] = {"unexpected_change": True}
        with patch.object(self.e, "command", return_value={"stdout": "ok"}) as run:
            result = self.e.execute_automatically(plan)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(run.call_args.args[0], original["argv"])
        self.assertEqual(run.call_args.args[2]["TINY_UNION_ARGUMENTS"], original["environment"]["TINY_UNION_ARGUMENTS"])

    def test_hardware_values_cannot_become_extra_cli_options(self):
        self.assertEqual(address("0x27"), "0x27")
        self.assertEqual(byte(255), "0xff")
        self.assertEqual(alt("alt4"), "a4")
        for fn, value in [(address, "0x27; reboot"), (byte, 256), (alt, "a4 --help"), (block_device, "/dev/sda;reboot")]:
            with self.assertRaises(ValueError): fn(value)

    @unittest.skipUnless(os.name == "nt", "Windows input API")
    def test_unicode_text_is_sent_literally_not_as_shortcuts(self):
        desktop = Desktop()
        with patch.object(desktop, "focus", return_value={"id": "123"}), patch.object(desktop, "_input") as send, patch("executors.desktop.time.sleep"):
            desktop.type_text("A{ENTER}ह")
        units = [call.kwargs["scan"] for call in send.call_args_list[::2]]
        self.assertEqual(bytes(sum(([v & 255, v >> 8] for v in units), [])).decode("utf-16-le"), "A{ENTER}ह")


if __name__ == "__main__": unittest.main()
