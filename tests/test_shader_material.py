import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from renderdoc_mcp import shader_material


class FakeResourceId:
    @staticmethod
    def Null():
        return 0


class FakeStage:
    Vertex = "vertex"
    Pixel = "pixel"


def reflection(stage, raw, resources=None):
    return SimpleNamespace(
        rawBytes=raw,
        entryPoint="main",
        encoding="DXBC",
        inputSignature=[],
        outputSignature=[],
        constantBlocks=[],
        samplers=[],
        readOnlyResources=resources or [],
        readWriteResources=[],
        stage=stage,
    )


class FakeState:
    def __init__(self, resources=None, used_resources=None):
        self.used_resources = used_resources or []
        self.reflections = {
            FakeStage.Vertex: reflection(FakeStage.Vertex, b"DXBC-vertex", resources),
            FakeStage.Pixel: reflection(FakeStage.Pixel, b"DXBC-pixel", resources),
        }

    def GetGraphicsPipelineObject(self):
        return 99

    def GetShader(self, stage):
        return 10 if stage == FakeStage.Vertex else 20

    def GetShaderReflection(self, stage):
        return self.reflections[stage]

    def GetShaderEntryPoint(self, stage):
        return "main"

    def GetConstantBlocks(self, stage):
        return []

    def GetReadOnlyResources(self, stage):
        return self.used_resources

    def GetSamplers(self, stage):
        return []

    def GetReadWriteResources(self, stage):
        return []

    def GetPrimitiveTopology(self):
        return "TriangleList"

    def GetVertexInputs(self):
        return []

    def GetVBuffers(self):
        return []

    def GetIBuffer(self):
        return SimpleNamespace(resourceId=0, byteOffset=0, byteStride=0, byteSize=0)

    def GetRasterizedStream(self):
        return 0

    def GetOutputTargets(self):
        return []

    def GetDepthTarget(self):
        return SimpleNamespace(resource=0)

    def GetColorBlends(self):
        return []

    def GetStencilFaces(self):
        return []


class FakeController:
    def __init__(self, resources=None, used_resources=None, buffer_data=b""):
        self.state = FakeState(resources, used_resources)
        self.buffer_data = buffer_data

    def SetFrameEvent(self, event_id, force):
        self.event_id = event_id

    def GetPipelineState(self):
        return self.state

    def GetAPIProperties(self):
        return SimpleNamespace(pipelineType="D3D11")

    def DisassembleShader(self, pipeline, reflection, target):
        return "%s_5_0\nret" % reflection.stage

    def GetRootActions(self):
        return [SimpleNamespace(eventId=871, children=[], numIndices=3, numInstances=1)]

    def GetD3D11PipelineState(self):
        rasterizer = SimpleNamespace(viewports=[], scissors=[], state=SimpleNamespace(cullMode="Back"))
        output = SimpleNamespace(depthStencilState=None, blendState=None, depthReadOnly=False, stencilReadOnly=False)
        return SimpleNamespace(rasterizer=rasterizer, outputMerger=output)

    def GetBufferData(self, resource_id, offset, size):
        return self.buffer_data[offset:offset + size] if size else self.buffer_data[offset:]


class ShaderMaterialTests(unittest.TestCase):
    def test_exports_vertex_and_pixel_source_material(self):
        fake_rd = SimpleNamespace(ResourceId=FakeResourceId, ShaderStage=FakeStage)
        stages = {"vertex": FakeStage.Vertex, "pixel": FakeStage.Pixel}
        with tempfile.TemporaryDirectory() as output_dir, mock.patch.object(shader_material, "rd", fake_rd), mock.patch.object(shader_material, "shader_stages", return_value=stages):
            result = shader_material.export_shader_material(
                FakeController(), 871, output_dir, prefix="eid871",
                include_textures=False, include_mesh=False
            )

            self.assertEqual(result["schema"], "renderdoc-mcp.shader-material.v1")
            self.assertTrue(os.path.isfile(result["stages"]["vertex"]["dxbc"]["path"]))
            self.assertTrue(os.path.isfile(result["stages"]["pixel"]["asm"]["path"]))
            with open(result["manifest_path"], encoding="utf-8") as manifest:
                saved = json.load(manifest)
            self.assertEqual(saved["manifest_path"], result["manifest_path"])
            self.assertEqual(saved["stages"]["vertex"]["dxbc"]["header"], "DXBC")

    def test_exports_and_deduplicates_structured_buffers(self):
        fake_rd = SimpleNamespace(ResourceId=FakeResourceId, ShaderStage=FakeStage)
        stages = {"vertex": FakeStage.Vertex, "pixel": FakeStage.Pixel}
        resources = [SimpleNamespace(name="lights", isTexture=False)]
        descriptor = SimpleNamespace(
            resource=77,
            byteOffset=0,
            byteSize=32,
            elementByteSize=16,
        )
        access = SimpleNamespace(index=0, arrayElement=0)
        used = SimpleNamespace(access=access, descriptor=descriptor)
        controller = FakeController(resources, [used], bytes(range(32)))

        with tempfile.TemporaryDirectory() as output_dir, mock.patch.object(shader_material, "rd", fake_rd), mock.patch.object(shader_material, "shader_stages", return_value=stages), mock.patch.object(shader_material, "_bindings", return_value={}):
            result = shader_material.export_shader_material(
                controller,
                871,
                output_dir,
                prefix="eid871",
                include_textures=False,
                include_mesh=False,
            )

            self.assertEqual(len(result["buffers"]), 1)
            buffer = result["buffers"]["77"]
            self.assertEqual(buffer["element_count"], 2)
            self.assertEqual(len(result["stages"]["vertex"]["buffer_bindings"]), 1)
            self.assertEqual(len(result["stages"]["pixel"]["buffer_bindings"]), 1)
            with open(buffer["path"], "rb") as data_file:
                self.assertEqual(data_file.read(), bytes(range(32)))

            summary = shader_material.material_summary(result)
            self.assertEqual(summary["buffer_count"], 1)
            self.assertNotIn("reflection", summary["stages"]["pixel"])


if __name__ == "__main__":
    unittest.main()
