import unittest
import importlib.util
import os
import sys
from types import ModuleType
from unittest import mock


ACTIVITY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "src",
    "renderdoc_mcp",
    "qrenderdoc_extension",
    "activity.py",
)


def load_activity_module():
    spec = importlib.util.spec_from_file_location("renderdoc_mcp_test_activity", ACTIVITY_PATH)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules):
        spec.loader.exec_module(module)
    return module


ActivityStore = load_activity_module().ActivityStore


def load_request_handler_module():
    package_name = "renderdoc_mcp_test_extension"
    package = ModuleType(package_name)
    package.__path__ = []
    activity_module = load_activity_module()

    class FakeServices:
        def __init__(self, ctx, activity):
            self.activity = activity

        def __getattr__(self, name):
            if name == "failing_operation":
                raise AttributeError(name)
            return lambda params: {"operation": name, "event_id": params.get("event_id")}

    services_module = ModuleType(package_name + ".services")
    services_module.BridgeServices = FakeServices
    request_handler_path = os.path.join(os.path.dirname(ACTIVITY_PATH), "request_handler.py")
    spec = importlib.util.spec_from_file_location(
        package_name + ".request_handler",
        request_handler_path,
    )
    module = importlib.util.module_from_spec(spec)
    modules = {
        package_name: package,
        package_name + ".activity": activity_module,
        package_name + ".services": services_module,
    }
    with mock.patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class ActivityStoreTests(unittest.TestCase):
    def test_tracks_successful_operation(self):
        store = ActivityStore(max_entries=3)
        entry_id = store.begin("focus_event", {"event_id": 871})
        result = store.finish(entry_id, {"focused": True})

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["event_id"], 871)
        self.assertEqual(result["message"], "EID focused")

    def test_tracks_errors_and_trims_old_entries(self):
        store = ActivityStore(max_entries=2)
        first = store.begin("open_capture", {"filepath": r"D:\first.rdc"})
        store.finish(first, error="file missing")
        second = store.begin("focus_event", {"event_id": 1})
        store.finish(second, {"focused": True})
        third = store.begin("reset_pixel_shader", {"event_id": 2})
        store.finish(third, {"replaced": False})

        entries = store.snapshot()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["operation"], "focus_event")
        self.assertEqual(entries[1]["operation"], "reset_pixel_shader")

    def test_records_external_comparison_result(self):
        store = ActivityStore()
        result = store.record_external(
            "validate_pixel_shader.result",
            "success",
            "Pixel comparison: ok",
            {"event_id": 871},
            {"status": "ok", "targets": [{"slot": 0}]},
        )

        self.assertEqual(result["event_id"], 871)
        self.assertEqual(result["details"]["status"], "ok")


class RequestActivityTests(unittest.TestCase):
    def test_handler_wraps_bridge_requests(self):
        handler = load_request_handler_module().RequestHandler(object())

        response = handler.handle(
            {"id": "1", "method": "focus_event", "params": {"event_id": 871}}
        )

        self.assertEqual(response["result"]["event_id"], 871)
        entry = handler.activity.snapshot()[0]
        self.assertEqual(entry["operation"], "focus_event")
        self.assertEqual(entry["status"], "success")

    def test_handler_records_unknown_method_as_error(self):
        handler = load_request_handler_module().RequestHandler(object())

        response = handler.handle({"id": "2", "method": "missing", "params": {}})

        self.assertIn("error", response)
        entry = handler.activity.snapshot()[0]
        self.assertEqual(entry["status"], "error")
        self.assertIn("Method not found", entry["message"])


if __name__ == "__main__":
    unittest.main()
