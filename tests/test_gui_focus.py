import importlib.util
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from renderdoc_mcp import backend


SERVICES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "src",
    "renderdoc_mcp",
    "qrenderdoc_extension",
    "services.py",
)


def load_services_module():
    spec = importlib.util.spec_from_file_location("renderdoc_mcp_test_services", SERVICES_PATH)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"renderdoc": SimpleNamespace(ReplayOptions=lambda: object())}):
        spec.loader.exec_module(module)
    return module


services = load_services_module()


class FakeEventBrowser:
    def __init__(self, event_ids):
        self.event_ids = set(event_ids)

    def GetAPIEventForEID(self, event_id):
        return SimpleNamespace(eventId=event_id if event_id in self.event_ids else 0)


class FakeMainWindow:
    def __init__(self):
        self.brought_to_front = False

    def BringToFront(self):
        self.brought_to_front = True


class FakeReplay:
    def BlockInvoke(self, callback):
        controller = SimpleNamespace(
            GetAPIProperties=lambda: SimpleNamespace(pipelineType="D3D11")
        )
        callback(controller)


class FakeContext:
    def __init__(self, event_ids=(852,), loaded=True):
        self.loaded = loaded
        self.filename = r"D:\captures\frame.rdc"
        self.browser = FakeEventBrowser(event_ids)
        self.main_window = FakeMainWindow()
        self.calls = []

    def IsCaptureLoaded(self):
        return self.loaded

    def GetCaptureFilename(self):
        return self.filename

    def GetEventBrowser(self):
        return self.browser

    def SetEventID(self, exclude, selected_event_id, event_id, force):
        self.calls.append((exclude, selected_event_id, event_id, force))

    def ShowEventBrowser(self):
        self.calls.append(("show_event_browser",))

    def GetMainWindow(self):
        return self.main_window

    def Replay(self):
        return FakeReplay()

    def LoadCapture(self, capture_path, options, original_path, temporary, local):
        self.filename = capture_path
        self.loaded = True
        self.calls.append(("load", capture_path, original_path, temporary, local))


class GUIFocusTests(unittest.TestCase):
    def test_opens_capture_in_background_without_touching_gui(self):
        ctx = FakeContext(loaded=False)
        capture_path = os.path.abspath(__file__) + ".rdc"

        with mock.patch.object(services.os.path, "isfile", return_value=True):
            result = services.BridgeServices(ctx).open_capture_background(
                {"capture_path": capture_path}
            )

        self.assertTrue(result["background"])
        self.assertEqual(result["source"], "qrenderdoc_bridge_background")
        self.assertEqual([call[0] for call in ctx.calls], ["load"])
        self.assertFalse(ctx.main_window.brought_to_front)

    def test_backend_background_open_never_calls_headless_or_foreground_open(self):
        with mock.patch.object(
            backend.gui_bridge,
            "open_capture_background",
            return_value={"loaded": True},
        ) as background_open, mock.patch.object(
            backend.gui_bridge, "open_capture"
        ) as foreground_open, mock.patch.object(
            backend, "get_worker_client"
        ) as worker_client:
            result = backend.open_capture_background(r"D:\captures\frame.rdc")

        self.assertTrue(result["background"])
        self.assertEqual(backend.current_backend(), "qrenderdoc_bridge")
        background_open.assert_called_once_with(r"D:\captures\frame.rdc")
        foreground_open.assert_not_called()
        worker_client.assert_not_called()

    def test_backend_background_failure_does_not_fallback(self):
        with mock.patch.object(
            backend.gui_bridge,
            "open_capture_background",
            side_effect=backend.gui_bridge.GUIBridgeError("bridge unavailable"),
        ), mock.patch.object(backend.gui_bridge, "open_capture") as foreground_open:
            result = backend.open_capture_background(r"D:\captures\frame.rdc")

        self.assertEqual(result["code"], "QRENDERDOC_BRIDGE_ERROR")
        foreground_open.assert_not_called()

    def test_backend_foreground_open_is_explicit(self):
        with mock.patch.object(
            backend.gui_bridge,
            "open_capture",
            return_value={"loaded": True},
        ) as foreground_open:
            result = backend.open_capture_foreground(r"D:\captures\frame.rdc")

        self.assertFalse(result["background"])
        foreground_open.assert_called_once_with(r"D:\captures\frame.rdc")

    def test_focuses_exact_event_and_brings_gui_forward(self):
        ctx = FakeContext(event_ids=(852,))

        result = services.BridgeServices(ctx).focus_event({"event_id": 852})

        self.assertEqual(result["event_id"], 852)
        self.assertTrue(result["focused"])
        self.assertIn(([], 852, 852, True), ctx.calls)
        self.assertIn(("show_event_browser",), ctx.calls)
        self.assertTrue(ctx.main_window.brought_to_front)

    def test_rejects_event_that_is_not_in_event_browser(self):
        ctx = FakeContext(event_ids=(852,))

        with self.assertRaisesRegex(ValueError, "Event ID 999 not found"):
            services.BridgeServices(ctx).focus_event({"event_id": 999})

        self.assertFalse(any(len(call) == 4 for call in ctx.calls))

    def test_opens_capture_before_focusing_event(self):
        ctx = FakeContext(event_ids=(852,), loaded=False)
        capture_path = os.path.abspath(__file__)

        with mock.patch.object(services.os.path, "isfile", return_value=True):
            result = services.BridgeServices(ctx).open_capture_at_event(
                {"capture_path": capture_path + ".rdc", "event_id": "852"}
            )

        self.assertEqual(ctx.calls[0][0], "load")
        self.assertEqual(ctx.calls[1], ([], 852, 852, True))
        self.assertEqual(result["event_id"], 852)

    def test_requires_positive_integer_event_id(self):
        ctx = FakeContext()

        for event_id in (None, 0, -1, "abc"):
            with self.subTest(event_id=event_id):
                with self.assertRaisesRegex(ValueError, "event_id"):
                    services.BridgeServices(ctx).focus_event({"event_id": event_id})

    def test_backend_uses_gui_after_focusing(self):
        with mock.patch.object(
            backend.gui_bridge,
            "focus_event",
            return_value={"event_id": 852, "focused": True},
        ):
            result = backend.focus_event(852)

        self.assertTrue(result["focused"])
        self.assertEqual(backend.current_backend(), "qrenderdoc_bridge")


if __name__ == "__main__":
    unittest.main()
