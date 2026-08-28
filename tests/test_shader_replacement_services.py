import importlib.util
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock


SERVICES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "src",
    "renderdoc_mcp",
    "qrenderdoc_extension",
    "services.py",
)


class FakeResourceId:
    @staticmethod
    def Null():
        return 0


class FakeShaderStage:
    Pixel = "pixel"


def load_services_module():
    fake_renderdoc = SimpleNamespace(
        ReplayOptions=lambda: object(),
        ResourceId=FakeResourceId,
        ShaderStage=FakeShaderStage,
    )
    spec = importlib.util.spec_from_file_location(
        "renderdoc_mcp_test_replacement_services", SERVICES_PATH
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"renderdoc": fake_renderdoc}):
        spec.loader.exec_module(module)
    return module


services = load_services_module()


class FakeState:
    def GetShader(self, stage):
        return 10


class FakeController:
    def __init__(self):
        self.calls = []

    def SetFrameEvent(self, event_id, force):
        self.calls.append(("set", event_id, force))

    def GetPipelineState(self):
        return FakeState()

    def RemoveReplacement(self, resource_id):
        self.calls.append(("remove", resource_id))


class FakeReplay:
    def __init__(self, controller):
        self.controller = controller

    def BlockInvoke(self, callback):
        callback(self.controller)


class FakeContext:
    def __init__(self, replacement_id=None, replacement_persists=False):
        self.controller = FakeController()
        self.replacement_id = replacement_id
        self.replacement_persists = replacement_persists
        self.calls = []

    def IsCaptureLoaded(self):
        return True

    def Replay(self):
        return FakeReplay(self.controller)

    def IsResourceReplaced(self, resource_id):
        return resource_id == 10 and self.replacement_id is not None

    def GetResourceReplacement(self, resource_id):
        return self.replacement_id if resource_id == 10 else None

    def UnregisterReplacement(self, resource_id):
        self.calls.append(("unregister", resource_id))
        if resource_id == 10:
            self.replacement_id = None

    def RefreshStatus(self):
        self.calls.append(("refresh",))
        if self.replacement_persists:
            self.replacement_id = 30


class ShaderReplacementServiceTests(unittest.TestCase):
    def test_reports_existing_ui_replacement(self):
        ctx = FakeContext(replacement_id=30)

        result = services.BridgeServices(ctx).get_pixel_shader_replacement_status(
            {"event_id": 871}
        )

        self.assertTrue(result["replaced"])
        self.assertEqual(result["original_resource_id"], "10")
        self.assertEqual(result["replacement_resource_id"], "30")

    def test_reset_clears_ui_and_replay_replacements(self):
        ctx = FakeContext(replacement_id=30)

        result = services.BridgeServices(ctx).reset_pixel_shader({"event_id": 871})

        self.assertTrue(result["was_replaced"])
        self.assertFalse(result["replaced"])
        self.assertEqual(result["removed_replacement_resource_id"], "30")
        self.assertIn(("unregister", 10), ctx.calls)
        self.assertIn(("remove", 10), ctx.controller.calls)
        self.assertIn(("set", 870, True), ctx.controller.calls)
        self.assertIn(("set", 871, True), ctx.controller.calls)
        self.assertIn(("refresh",), ctx.calls)

    def test_reset_is_idempotent_without_ui_replacement(self):
        ctx = FakeContext()
        bridge = services.BridgeServices(ctx)

        first = bridge.reset_pixel_shader({"event_id": 871})
        second = bridge.reset_pixel_shader({"event_id": 871})

        self.assertFalse(first["was_replaced"])
        self.assertFalse(second["was_replaced"])
        self.assertNotIn(("unregister", 10), ctx.calls)
        self.assertEqual(
            [call for call in ctx.controller.calls if call == ("remove", 10)],
            [("remove", 10), ("remove", 10)],
        )

    def test_reset_fails_when_ui_replacement_returns(self):
        ctx = FakeContext(replacement_id=30, replacement_persists=True)

        with self.assertRaisesRegex(ValueError, "still registered"):
            services.BridgeServices(ctx).reset_pixel_shader({"event_id": 871})

    def test_validation_resets_before_and_after_failure(self):
        ctx = FakeContext(replacement_id=30)
        bridge = services.BridgeServices(ctx)
        reset_calls = []
        original_reset = bridge._reset_pixel_shader

        def tracking_reset(event_id):
            reset_calls.append(event_id)
            return original_reset(event_id)

        bridge._reset_pixel_shader = tracking_reset
        fake_roundtrip = SimpleNamespace(
            validate_pixel_shader_roundtrip=mock.Mock(
                side_effect=RuntimeError("validation failed")
            )
        )

        with mock.patch.dict(
            sys.modules, {"renderdoc_mcp.shader_roundtrip": fake_roundtrip}
        ):
            with self.assertRaisesRegex(ValueError, "validation failed"):
                bridge.validate_pixel_shader(
                    {
                        "event_id": 871,
                        "hlsl_path": r"D:\shader.hlsl",
                        "output_dir": r"D:\validation",
                    }
                )

        self.assertEqual(reset_calls, [871, 871])
        self.assertIsNone(ctx.replacement_id)

    def test_validation_preserves_original_error_when_cleanup_fails(self):
        ctx = FakeContext(replacement_id=30)
        bridge = services.BridgeServices(ctx)
        reset_calls = 0
        original_reset = bridge._reset_pixel_shader

        def reset_then_fail(event_id):
            nonlocal reset_calls
            reset_calls += 1
            if reset_calls == 1:
                return original_reset(event_id)
            raise RuntimeError("cleanup failed")

        bridge._reset_pixel_shader = reset_then_fail
        fake_roundtrip = SimpleNamespace(
            validate_pixel_shader_roundtrip=mock.Mock(
                side_effect=RuntimeError("validation failed")
            )
        )

        with mock.patch.dict(
            sys.modules, {"renderdoc_mcp.shader_roundtrip": fake_roundtrip}
        ):
            with self.assertRaisesRegex(ValueError, "validation failed"):
                bridge.validate_pixel_shader(
                    {
                        "event_id": 871,
                        "hlsl_path": r"D:\shader.hlsl",
                        "output_dir": r"D:\validation",
                    }
                )


if __name__ == "__main__":
    unittest.main()
