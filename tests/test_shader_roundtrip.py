import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from renderdoc_mcp import shader_roundtrip


class FakeResourceId:
    @staticmethod
    def Null():
        return 0


class FakeShaderStage:
    Pixel = "pixel"


class FakeShaderEncoding:
    HLSL = "hlsl"


class FakeState:
    def __init__(self, reflection):
        self.reflection = reflection

    def GetShader(self, stage):
        return 10

    def GetShaderReflection(self, stage):
        return self.reflection

    def GetShaderEntryPoint(self, stage):
        return "main"

    def GetGraphicsPipelineObject(self):
        return 20


class FakeController:
    def __init__(self, original, recompiled, compile_result=(30, "")):
        self.state = FakeState(original)
        self.recompiled = recompiled
        self.compile_result = compile_result
        self.calls = []

    def SetFrameEvent(self, event_id, force):
        self.calls.append(("set", event_id, force))

    def GetPipelineState(self):
        return self.state

    def RemoveReplacement(self, resource_id):
        self.calls.append(("reset", resource_id))

    def BuildTargetShader(self, *args):
        self.calls.append(("build", args))
        return self.compile_result

    def GetShaderEntryPoints(self, resource_id):
        return ["entry"]

    def GetShader(self, pipeline_id, resource_id, entry):
        return self.recompiled

    def ReplaceResource(self, original_id, new_id):
        self.calls.append(("replace", original_id, new_id))

    def FreeTargetResource(self, resource_id):
        self.calls.append(("free", resource_id))


def reflection(data):
    return SimpleNamespace(
        rawBytes=data,
        entryPoint="main",
        debugInfo=SimpleNamespace(compileFlags="flags"),
    )


def fake_renderdoc():
    fake = SimpleNamespace(
        ResourceId=FakeResourceId,
        ShaderStage=FakeShaderStage,
        ShaderEncoding=FakeShaderEncoding,
    )
    return mock.patch.object(shader_roundtrip, "rd", fake)


class ShaderRoundtripTests(unittest.TestCase):
    def test_validates_candidate_against_reference_hlsl(self):
        with tempfile.TemporaryDirectory() as output_dir, fake_renderdoc():
            controller = FakeController(
                reflection(b"DXBC-original"), reflection(b"DXBC-new")
            )
            targets = [
                {"0": {"raw_sha256": "RESET"}},
                {"0": {"raw_sha256": "REFERENCE"}},
                {"0": {"raw_sha256": "CANDIDATE"}},
            ]
            shader_exports = [
                ({"compile_log": ""}, 10, 30),
                ({"compile_log": ""}, 10, 40),
            ]

            with mock.patch.object(
                shader_roundtrip,
                "_save_render_targets",
                side_effect=targets,
            ), mock.patch.object(
                shader_roundtrip,
                "export_pixel_shader_roundtrip",
                side_effect=shader_exports,
            ):
                result = shader_roundtrip.validate_pixel_shader_roundtrip(
                    controller,
                    871,
                    r"D:\candidate.hlsl",
                    output_dir,
                    expected_reset_shader_sha256=None,
                    reference_hlsl_path=r"D:\reference.hlsl",
                )

            self.assertEqual(result["comparison_pair"], ["reference", "applied"])
            self.assertEqual(result["targets"]["reference"]["0"]["raw_sha256"], "REFERENCE")
            self.assertEqual(result["targets"]["applied"]["0"]["raw_sha256"], "CANDIDATE")
            self.assertIn(("free", 30), controller.calls)
            self.assertIn(("free", 40), controller.calls)

    def test_accepts_matching_reset_target_hashes(self):
        targets = {"0": {"raw_sha256": "ABC123"}}

        shader_roundtrip._assert_reset_target_hashes(targets, {"0": "abc123"})

    def test_rejects_mismatched_reset_target_hashes(self):
        targets = {"0": {"raw_sha256": "BAD"}}

        with self.assertRaisesRegex(ValueError, "RT0 expected GOOD, got BAD"):
            shader_roundtrip._assert_reset_target_hashes(targets, {"0": "GOOD"})

    def test_rejects_missing_reset_target(self):
        with self.assertRaisesRegex(ValueError, "RT1 is missing"):
            shader_roundtrip._assert_reset_target_hashes({}, {"1": "EXPECTED"})

    def test_accepts_small_reset_raw_variance(self):
        with tempfile.TemporaryDirectory() as output_dir:
            baseline_path = os.path.join(output_dir, "baseline.bin")
            actual_path = os.path.join(output_dir, "actual.bin")
            with open(baseline_path, "wb") as baseline_file:
                baseline_file.write(b"abcd")
            with open(actual_path, "wb") as actual_file:
                actual_file.write(b"abXd")

            result = shader_roundtrip._assert_reset_target_hashes(
                {"0": {"raw_sha256": "ACTUAL", "raw_path": actual_path}},
                {
                    "0": {
                        "sha256": "EXPECTED",
                        "raw_path": baseline_path,
                        "max_differing_bytes": 1,
                    }
                },
            )

            self.assertTrue(result["0"]["accepted"])
            self.assertEqual(result["0"]["differing_bytes"], 1)

    def test_rejects_reset_raw_variance_over_limit(self):
        with tempfile.TemporaryDirectory() as output_dir:
            baseline_path = os.path.join(output_dir, "baseline.bin")
            actual_path = os.path.join(output_dir, "actual.bin")
            with open(baseline_path, "wb") as baseline_file:
                baseline_file.write(b"abcd")
            with open(actual_path, "wb") as actual_file:
                actual_file.write(b"aXXd")

            with self.assertRaisesRegex(ValueError, "differing bytes 2 > 1"):
                shader_roundtrip._assert_reset_target_hashes(
                    {"0": {"raw_sha256": "ACTUAL", "raw_path": actual_path}},
                    {
                        "0": {
                            "sha256": "EXPECTED",
                            "raw_path": baseline_path,
                            "max_differing_bytes": 1,
                        }
                    },
                )

    def test_inlines_local_hlsl_includes(self):
        with tempfile.TemporaryDirectory() as output_dir:
            include_path = os.path.join(output_dir, "core.hlsl")
            source_path = os.path.join(output_dir, "entry.hlsl")
            with open(include_path, "w", encoding="utf-8") as include_file:
                include_file.write("float4 Core() { return 1; }\n")
            with open(source_path, "w", encoding="utf-8") as source_file:
                source_file.write('#include "core.hlsl"\nfloat4 main() : SV_Target { return Core(); }\n')

            source = shader_roundtrip._load_hlsl_source(source_path).decode("utf-8")

            self.assertIn("float4 Core()", source)
            self.assertNotIn("#include", source)

    def test_exports_and_applies_both_dxbc_files(self):
        with tempfile.TemporaryDirectory() as output_dir, fake_renderdoc():
            hlsl_path = os.path.join(output_dir, "shader.hlsl")
            with open(hlsl_path, "wb") as hlsl_file:
                hlsl_file.write(b"void main() {}")
            controller = FakeController(
                reflection(b"DXBC-original"), reflection(b"DXBC-new")
            )

            result, original_id, new_id = shader_roundtrip.export_pixel_shader_roundtrip(
                controller, 871, hlsl_path, output_dir
            )

            self.assertEqual(original_id, 10)
            self.assertEqual(new_id, 30)
            with open(result["original"]["path"], "rb") as shader_file:
                self.assertEqual(shader_file.read(), b"DXBC-original")
            with open(result["recompiled"]["path"], "rb") as shader_file:
                self.assertEqual(shader_file.read(), b"DXBC-new")
            self.assertEqual(controller.calls[-1], ("replace", 10, 30))

    def test_compile_failure_leaves_shader_reset(self):
        with tempfile.TemporaryDirectory() as output_dir, fake_renderdoc():
            hlsl_path = os.path.join(output_dir, "shader.hlsl")
            with open(hlsl_path, "wb") as hlsl_file:
                hlsl_file.write(b"invalid")
            controller = FakeController(
                reflection(b"DXBC-original"), None, compile_result=(0, "compile error")
            )

            with self.assertRaisesRegex(ValueError, "compile error"):
                shader_roundtrip.export_pixel_shader_roundtrip(
                    controller, 871, hlsl_path, output_dir
                )

            self.assertIn(("reset", 10), controller.calls)
            self.assertFalse(any(call[0] == "replace" for call in controller.calls))

    def test_invalid_recompiled_container_is_not_applied(self):
        with tempfile.TemporaryDirectory() as output_dir, fake_renderdoc():
            hlsl_path = os.path.join(output_dir, "shader.hlsl")
            with open(hlsl_path, "wb") as hlsl_file:
                hlsl_file.write(b"void main() {}")
            controller = FakeController(reflection(b"DXBC-original"), reflection(b"bad"))

            with self.assertRaisesRegex(ValueError, "not a DXBC"):
                shader_roundtrip.export_pixel_shader_roundtrip(
                    controller, 871, hlsl_path, output_dir
                )

            self.assertIn(("free", 30), controller.calls)
            self.assertFalse(any(call[0] == "replace" for call in controller.calls))


if __name__ == "__main__":
    unittest.main()
