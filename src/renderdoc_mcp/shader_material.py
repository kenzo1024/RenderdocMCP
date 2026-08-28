"""Export the raw material needed to reconstruct a draw-call shader.

The export deliberately keeps DXBC and RenderDoc's disassembly as the source of
truth.  It does not pass through a third-party HLSL decompiler.
"""

import base64
import hashlib
import json
import os

from renderdoc_mcp.renderdoc_api import rd, safe_filename, shader_stages


def export_shader_material(
    controller,
    event_id,
    output_dir,
    prefix="shader",
    include_textures=True,
    include_mesh=True,
):
    """Export VS/PS bytecode, disassembly, reflection, bindings and pipeline state."""
    os.makedirs(output_dir, exist_ok=True)
    controller.SetFrameEvent(int(event_id), True)
    state = controller.GetPipelineState()
    pipeline_id = state.GetGraphicsPipelineObject()
    bundle = {
        "schema": "renderdoc-mcp.shader-material.v1",
        "event_id": int(event_id),
        "pipeline_type": _value(getattr(controller.GetAPIProperties(), "pipelineType", None)),
        "stages": {},
        "pipeline": _pipeline_state(controller, state),
        "action": _action_metadata(controller, event_id),
        "buffers": {},
    }

    for stage_name in ("vertex", "pixel"):
        stage = shader_stages()[stage_name]
        bundle["stages"][stage_name] = _export_stage(
            controller,
            state,
            pipeline_id,
            int(event_id),
            stage_name,
            stage,
            output_dir,
            prefix,
            bundle["buffers"],
        )

    if include_textures:
        bundle["textures"] = _export_textures(
            controller, state, output_dir, prefix, int(event_id)
        )
    if include_mesh:
        bundle["mesh"] = _export_mesh(controller, output_dir, prefix, int(event_id))

    manifest_path = os.path.join(output_dir, "%s_eid%d_material.json" % (safe_filename(prefix), event_id))
    bundle["manifest_path"] = os.path.normpath(manifest_path)
    with open(manifest_path, "w", encoding="utf-8") as manifest:
        json.dump(bundle, manifest, ensure_ascii=False, indent=2)
    return bundle


def material_summary(bundle):
    """Return the small response sent over IPC after the full manifest is saved."""
    return {
        "schema": bundle.get("schema"),
        "event_id": bundle.get("event_id"),
        "manifest_path": bundle.get("manifest_path"),
        "stages": {
            name: {
                "bound": value.get("bound"),
                "dxbc": value.get("dxbc"),
                "asm": value.get("asm"),
                "buffer_binding_count": len(value.get("buffer_bindings", [])),
            }
            for name, value in bundle.get("stages", {}).items()
        },
        "buffer_count": len(bundle.get("buffers", {})),
        "texture_count": bundle.get("textures", {}).get("exported_count", 0),
        "mesh": {
            name: value.get("vertex_count")
            for name, value in bundle.get("mesh", {}).items()
            if isinstance(value, dict)
        },
    }


def _export_stage(
    controller,
    state,
    pipeline_id,
    event_id,
    stage_name,
    stage,
    output_dir,
    prefix,
    exported_buffers,
):
    shader_id = state.GetShader(stage)
    if _is_null(shader_id):
        return {"bound": False, "stage": stage_name}
    reflection = state.GetShaderReflection(stage)
    if reflection is None:
        return {"bound": True, "stage": stage_name, "resource_id": str(shader_id), "reflection": None}

    stem = "%s_eid%d_%s" % (safe_filename(prefix), event_id, stage_name)
    raw = bytes(reflection.rawBytes)
    dxbc_path = os.path.join(output_dir, stem + ".dxbc")
    asm_path = os.path.join(output_dir, stem + ".asm")
    _write_bytes(dxbc_path, raw)
    disassembly = controller.DisassembleShader(pipeline_id, reflection, "")
    _write_text(asm_path, str(disassembly))

    result = {
        "bound": True,
        "stage": stage_name,
        "resource_id": str(shader_id),
        "entry_point": str(getattr(reflection, "entryPoint", "") or state.GetShaderEntryPoint(stage) or "main"),
        "encoding": _value(getattr(reflection, "encoding", None)),
        "dxbc": _file_result(dxbc_path, raw),
        "asm": {"path": os.path.normpath(asm_path), "sha256": _sha256_text(disassembly)},
        "reflection": _reflection(reflection),
        "bindings": _bindings(state, stage),
        "constant_buffer_data": _constant_buffer_data(controller, state, pipeline_id, shader_id, stage, reflection),
    }
    result["buffer_bindings"] = _export_stage_buffers(
        controller,
        state,
        reflection,
        stage,
        stage_name,
        event_id,
        output_dir,
        prefix,
        exported_buffers,
    )
    return result


def _export_stage_buffers(
    controller,
    state,
    reflection,
    stage,
    stage_name,
    event_id,
    output_dir,
    prefix,
    exported_buffers,
):
    result = []
    resources = list(reflection.readOnlyResources)
    for used in state.GetReadOnlyResources(stage):
        index = int(used.access.index)
        if index >= len(resources) or bool(getattr(resources[index], "isTexture", False)):
            continue

        descriptor = used.descriptor
        resource_id = descriptor.resource
        if _is_null(resource_id):
            continue

        resource_key = str(resource_id)
        if resource_key not in exported_buffers:
            offset = int(getattr(descriptor, "byteOffset", 0))
            size = int(getattr(descriptor, "byteSize", 0))
            raw = bytes(controller.GetBufferData(resource_id, offset, size))
            filename = "%s_eid%d_buffer_%s.bin" % (
                safe_filename(prefix),
                event_id,
                safe_filename(resource_key.replace("::", "_")),
            )
            path = os.path.join(output_dir, filename)
            _write_bytes(path, raw)
            stride = int(getattr(descriptor, "elementByteSize", 0))
            exported_buffers[resource_key] = {
                "resource_id": resource_key,
                "path": os.path.normpath(path),
                "byte_offset": offset,
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest().upper(),
                "element_byte_size": stride,
                "element_count": len(raw) // stride if stride > 0 else None,
            }

        result.append(
            {
                "stage": stage_name,
                "binding_index": index,
                "name": str(getattr(resources[index], "name", "")),
                "resource_id": resource_key,
                "array_element": int(getattr(used.access, "arrayElement", 0)),
            }
        )
    return result


def _reflection(reflection):
    return {
        "input_signature": [_signature(x) for x in reflection.inputSignature],
        "output_signature": [_signature(x) for x in reflection.outputSignature],
        "constant_blocks": [_constant_block(x) for x in reflection.constantBlocks],
        "samplers": [_struct(x, ["name", "fixedBindNumber", "fixedBindSetOrSpace", "bindArraySize"]) for x in reflection.samplers],
        "read_only_resources": [_resource(x) for x in reflection.readOnlyResources],
        "read_write_resources": [_resource(x) for x in reflection.readWriteResources],
    }


def _constant_buffer_data(controller, state, pipeline_id, shader_id, stage, reflection):
    result = []
    for index, block in enumerate(reflection.constantBlocks):
        item = {"index": index, "name": str(getattr(block, "name", "")), "byte_size": int(getattr(block, "byteSize", 0))}
        try:
            used = state.GetConstantBlock(stage, index, 0)
            desc = used.descriptor
            item["binding"] = _used_descriptor(used)
            resource = desc.resource
            if not _is_null(resource):
                declared_size = int(getattr(block, "byteSize", 0))
                bound_size = int(getattr(desc, "byteSize", 0))
                read_size = declared_size if bound_size <= 0 else min(declared_size or bound_size, bound_size)
                raw = bytes(controller.GetBufferData(resource, int(desc.byteOffset), read_size))
                item["raw"] = {"encoding": "base64", "byte_offset": int(desc.byteOffset), "data": base64.b64encode(raw).decode("ascii")}
                item["variables"] = [_shader_variable(x) for x in controller.GetCBufferVariableContents(
                    pipeline_id, shader_id, stage, str(getattr(reflection, "entryPoint", "") or "main"), index,
                    resource, int(desc.byteOffset), int(desc.byteSize)
                )]
        except Exception as exc:
            item["error"] = str(exc)
        result.append(item)
    return result


def _bindings(state, stage):
    return {
        "constant_blocks": [_used_descriptor(x) for x in state.GetConstantBlocks(stage)],
        "read_only_resources": [_used_descriptor(x) for x in state.GetReadOnlyResources(stage)],
        "samplers": [_used_descriptor(x) for x in state.GetSamplers(stage)],
        "read_write_resources": [_used_descriptor(x) for x in state.GetReadWriteResources(stage)],
    }


def _pipeline_state(controller, state):
    data = {
        "topology": _value(state.GetPrimitiveTopology()),
        "viewports": [],
        "scissors": [],
        "vertex_inputs": [_vertex_input(x) for x in state.GetVertexInputs()],
        "vertex_buffers": [_struct(x, ["resourceId", "byteOffset", "byteStride", "byteSize"]) for x in state.GetVBuffers()],
        "index_buffer": _struct(state.GetIBuffer(), ["resourceId", "byteOffset", "byteStride", "byteSize"]),
        "rasterized_stream": state.GetRasterizedStream(),
        "output_targets": [_descriptor(x) for x in state.GetOutputTargets()],
        "depth_target": _descriptor(state.GetDepthTarget()),
        "color_blends": [_struct(x, ["enabled", "logicOperationEnabled", "logicOperation", "writeMask", "colorBlend", "alphaBlend"]) for x in state.GetColorBlends()],
        "stencil_faces": [_struct(x, ["failOperation", "depthFailOperation", "passOperation", "function", "reference", "compareMask", "writeMask"]) for x in state.GetStencilFaces()],
    }
    try:
        d3d11 = controller.GetD3D11PipelineState()
        rasterizer = getattr(d3d11, "rasterizer", None)
        output_merger = getattr(d3d11, "outputMerger", None)
        data["viewports"] = [_struct(x, ["x", "y", "width", "height", "minDepth", "maxDepth"]) for x in getattr(rasterizer, "viewports", [])]
        data["scissors"] = [_struct(x, ["x", "y", "width", "height", "enabled"]) for x in getattr(rasterizer, "scissors", [])]
        data["d3d11"] = {
            "rasterizer": _rasterizer_state(getattr(rasterizer, "state", None)),
            "depth_stencil": _depth_stencil_state(getattr(output_merger, "depthStencilState", None)),
            "blend": _blend_state(getattr(output_merger, "blendState", None)),
            "depth_read_only": bool(getattr(output_merger, "depthReadOnly", False)),
            "stencil_read_only": bool(getattr(output_merger, "stencilReadOnly", False)),
        }
    except Exception as exc:
        data["d3d11_error"] = str(exc)
    return data


def _action_metadata(controller, event_id):
    for action in _walk(controller.GetRootActions()):
        if int(action.eventId) == int(event_id):
            return _struct(action, ["eventId", "actionId", "flags", "numIndices", "numInstances", "vertexOffset", "indexOffset", "baseVertex", "instanceOffset", "copyDestination", "copySource", "durationMicroseconds"])
    return {"event_id": int(event_id), "error": "action_not_found"}


def _export_textures(controller, state, output_dir, prefix, event_id):
    """Keep texture export optional; metadata remains in bindings regardless."""
    try:
        from renderdoc_mcp.exporter import export_event_textures
        session = _MaterialSession(controller)
        return export_event_textures(session, event_id, os.path.join(output_dir, safe_filename(prefix) + "_textures"), prefix, ["vertex", "pixel"], "png", False, True, False)
    except Exception as exc:
        return {"error": str(exc)}


def _export_mesh(controller, output_dir, prefix, event_id):
    try:
        from renderdoc_mcp.exporter import export_mesh_stage

        session = _MaterialSession(controller)
        result = {}
        for stage in ("vsin", "vsout"):
            path = os.path.join(
                output_dir,
                "%s_eid%d_%s.json" % (safe_filename(prefix), event_id, stage),
            )
            result[stage] = export_mesh_stage(
                session, event_id, path, stage, "json", max_vertices=0
            )
        return result
    except Exception as exc:
        return {"error": str(exc)}


def _signature(value):
    return _struct(value, ["varName", "semanticName", "semanticIdxName", "semanticIndex", "perPrimitiveRate", "regIndex", "systemValue", "varType", "regChannelMask", "channelUsedMask", "needSemanticIndex", "compCount", "stream"])


def _constant_block(value):
    return _struct(value, ["name", "variables", "fixedBindNumber", "fixedBindSetOrSpace", "bindArraySize", "byteSize", "bufferBacked", "inlineDataBytes", "compileConstants"], {"variables": lambda x: [_constant(x) for x in x]})


def _constant(value):
    return _struct(value, ["name", "byteOffset", "bitFieldOffset", "bitFieldSize", "defaultValue", "type"], {"type": _constant_type})


def _constant_type(value):
    return _struct(value, ["name", "members", "flags", "pointerTypeID", "elements", "arrayByteStride", "baseType", "rows", "columns", "matrixByteStride"], {"members": lambda x: [_constant(y) for y in x]})


def _resource(value):
    return _struct(value, ["textureType", "descriptorType", "name", "variableType", "fixedBindNumber", "fixedBindSetOrSpace", "bindArraySize", "isTexture", "hasSampler", "isInputAttachment", "isReadOnly"])


def _vertex_input(value):
    data = _struct(value, ["name", "vertexBuffer", "byteOffset", "perInstance", "instanceRate", "genericEnabled", "used"])
    data["format"] = _format(getattr(value, "format", None))
    if getattr(value, "genericEnabled", False):
        data["generic_value"] = _value(getattr(value, "genericValue", None))
    return data


def _used_descriptor(value):
    return {"access": _struct(value.access, ["stage", "type", "index", "arrayElement", "descriptorStore", "byteOffset", "byteSize", "staticallyUnused"]), "descriptor": _descriptor(value.descriptor), "sampler": _struct(value.sampler, ["object", "type", "addressU", "addressV", "addressW", "compareFunction", "filter", "maxAnisotropy", "maxLOD", "minLOD", "mipBias", "borderColorType", "creationTimeConstant"])}


def _descriptor(value):
    if value is None:
        return None
    return _struct(value, ["type", "flags", "format", "resource", "secondary", "view", "byteOffset", "byteSize", "counterByteOffset", "bufferStructCount", "elementByteSize", "minLODClamp", "firstSlice", "numSlices", "firstMip", "numMips", "textureType"], {"format": _format})


def _rasterizer_state(value):
    return _struct(value, ["resourceId", "fillMode", "cullMode", "frontCCW", "depthBias", "depthBiasClamp", "slopeScaledDepthBias", "depthClip", "scissorEnable", "multisampleEnable", "antialiasedLines", "forcedSampleCount", "conservativeRasterization"])


def _depth_stencil_state(value):
    if value is None:
        return None
    result = _struct(value, ["resourceId", "depthEnable", "depthFunction", "depthWrites", "stencilEnable"])
    result["front_face"] = _stencil_face(getattr(value, "frontFace", None))
    result["back_face"] = _stencil_face(getattr(value, "backFace", None))
    return result


def _stencil_face(value):
    return _struct(value, ["failOperation", "depthFailOperation", "passOperation", "function", "reference", "compareMask", "writeMask"])


def _blend_state(value):
    if value is None:
        return None
    result = _struct(value, ["resourceId", "alphaToCoverage", "independentBlend", "blendFactor", "sampleMask"])
    result["targets"] = [_color_blend(x) for x in getattr(value, "blends", [])]
    return result


def _color_blend(value):
    if value is None:
        return None
    result = _struct(value, ["enabled", "logicOperationEnabled", "logicOperation", "writeMask"])
    result["color"] = _blend_equation(getattr(value, "colorBlend", None))
    result["alpha"] = _blend_equation(getattr(value, "alphaBlend", None))
    return result


def _blend_equation(value):
    return _struct(value, ["source", "destination", "operation"])


def _shader_variable(value):
    result = _struct(value, ["name", "rows", "columns", "type", "flags", "members"])
    result["value"] = _shader_value(value)
    if hasattr(value, "members"):
        result["members"] = [_shader_variable(x) for x in value.members]
    return result


def _shader_value(variable):
    count = max(int(getattr(variable, "rows", 0)) * int(getattr(variable, "columns", 0)), 1)
    value = getattr(variable, "value", None)
    type_name = str(getattr(variable, "type", "")).lower()
    fields = (
        (("float",), "f32v"),
        (("double",), "f64v"),
        (("uint",), "u32v"),
        (("sint", "int"), "s32v"),
        (("ushort",), "u16v"),
        (("sshort", "short"), "s16v"),
        (("ubyte",), "u8v"),
        (("sbyte", "byte"), "s8v"),
    )
    for names, field in fields:
        if any(name in type_name for name in names) and hasattr(value, field):
            return [_value(x) for x in list(getattr(value, field))[:count]]
    return _value(value)


def _format(value):
    if value is None:
        return None
    result = _struct(value, ["compByteWidth", "compCount", "compType", "type"])
    try:
        result["name"] = str(value.Name())
    except Exception:
        pass
    return result


def _struct(value, fields, transforms=None):
    if value is None:
        return None
    transforms = transforms or {}
    result = {}
    for field in fields:
        try:
            item = getattr(value, field)
            result[field] = transforms[field](item) if field in transforms and callable(transforms[field]) else _value(item)
        except Exception:
            continue
    return result


def _value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (list, tuple)):
        return [_value(x) for x in value]
    try:
        return str(value)
    except Exception:
        return repr(value)


def _safe_call(value, method, *args):
    try:
        return getattr(value, method)(*args)
    except Exception:
        return None


def _walk(actions):
    for action in actions:
        yield action
        children = getattr(action, "children", None)
        if children:
            yield from _walk(children)


class _MaterialSession:
    """Minimal exporter adapter shared by GUI and headless replay."""

    def __init__(self, controller):
        self.controller = controller
        self.structured_file = controller.GetStructuredFile()
        self._actions = {int(action.eventId): action for action in _walk(controller.GetRootActions())}
        self._textures = {str(texture.resourceId): texture for texture in controller.GetTextures()}

    def set_event(self, event_id):
        if int(event_id) not in self._actions:
            return {"error": "Event ID %s not found" % event_id, "code": "INVALID_EVENT_ID"}
        self.controller.SetFrameEvent(int(event_id), True)
        return None

    def get_action(self, event_id):
        return self._actions.get(int(event_id))

    def get_texture(self, resource_id):
        return self._textures.get(str(resource_id))


def _is_null(resource_id):
    try:
        return resource_id == rd.ResourceId.Null()
    except Exception:
        return str(resource_id) in ("", "ResourceId::Null", "0")


def _write_bytes(path, data):
    with open(path, "wb") as output_file:
        output_file.write(data)


def _write_text(path, data):
    with open(path, "w", encoding="utf-8", newline="") as output_file:
        output_file.write(data)


def _sha256_text(data):
    return hashlib.sha256(str(data).encode("utf-8")).hexdigest().upper()


def _file_result(path, data):
    return {"path": os.path.normpath(path), "size": len(data), "sha256": hashlib.sha256(data).hexdigest().upper(), "header": data[:4].decode("ascii", errors="replace")}
