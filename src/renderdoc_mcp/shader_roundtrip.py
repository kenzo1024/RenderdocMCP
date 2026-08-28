"""Export and replace a pixel shader through RenderDoc's replay controller."""

import hashlib
import json
import os
import re

from renderdoc_mcp.renderdoc_api import rd


def export_pixel_shader_roundtrip(controller, event_id, hlsl_path, output_dir):
    """Export the reset DXBC, compile HLSL, export its DXBC, and apply it."""
    if not os.path.isfile(hlsl_path):
        raise ValueError("HLSL file not found: %s" % hlsl_path)

    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    stage = rd.ShaderStage.Pixel
    controller.SetFrameEvent(event_id, True)
    state = controller.GetPipelineState()
    original_id = state.GetShader(stage)
    if original_id == rd.ResourceId.Null():
        raise ValueError("Event ID %s has no pixel shader" % event_id)

    # Reset first so original.rawBytes is guaranteed to describe capture state.
    controller.RemoveReplacement(original_id)
    controller.SetFrameEvent(event_id, True)
    state = controller.GetPipelineState()
    original = state.GetShaderReflection(stage)
    if original is None:
        raise ValueError("Failed to reflect the original pixel shader")

    original_bytes = bytes(original.rawBytes)
    _validate_dxbc(original_bytes, "Original pixel shader")
    original_path = os.path.join(output_dir, "eid%d_pixel_original_reset.dxbc" % event_id)
    _write_bytes(original_path, original_bytes)

    source = _load_hlsl_source(hlsl_path)

    entry_name = state.GetShaderEntryPoint(stage) or original.entryPoint or "main"
    compile_flags = original.debugInfo.compileFlags
    new_id, compile_log = controller.BuildTargetShader(
        entry_name,
        rd.ShaderEncoding.HLSL,
        source,
        compile_flags,
        stage,
    )
    if new_id == rd.ResourceId.Null():
        raise ValueError("HLSL compilation failed:\n%s" % compile_log)

    try:
        entries = controller.GetShaderEntryPoints(new_id)
        if not entries:
            raise ValueError("Recompiled pixel shader has no entry point")
        recompiled = controller.GetShader(
            state.GetGraphicsPipelineObject(), new_id, entries[0]
        )
        if recompiled is None:
            raise ValueError("Failed to reflect the recompiled pixel shader")

        recompiled_bytes = bytes(recompiled.rawBytes)
        _validate_dxbc(recompiled_bytes, "Recompiled pixel shader")
        recompiled_path = os.path.join(output_dir, "eid%d_pixel_applied.dxbc" % event_id)
        _write_bytes(recompiled_path, recompiled_bytes)

        controller.ReplaceResource(original_id, new_id)
    except Exception:
        controller.FreeTargetResource(new_id)
        raise

    return {
        "event_id": event_id,
        "entry_point": entry_name,
        "original_resource_id": str(original_id),
        "replacement_resource_id": str(new_id),
        "compile_log": str(compile_log),
        "original": _file_result(original_path, original_bytes),
        "recompiled": _file_result(recompiled_path, recompiled_bytes),
    }, original_id, new_id


def validate_pixel_shader_roundtrip(
    controller,
    event_id,
    hlsl_path,
    output_dir,
    expected_reset_raw_sha256=None,
    expected_reset_shader_sha256=None,
    reference_hlsl_path=None,
):
    """Apply a PS temporarily, export both MRT states, then restore the capture."""
    os.makedirs(output_dir, exist_ok=True)
    controller.SetFrameEvent(int(event_id), True)
    original_state = controller.GetPipelineState()
    original_id = original_state.GetShader(rd.ShaderStage.Pixel)
    if original_id == rd.ResourceId.Null():
        raise ValueError("Event ID %s has no pixel shader" % event_id)

    controller.RemoveReplacement(original_id)
    controller.SetFrameEvent(int(event_id), True)
    reset_state = controller.GetPipelineState()
    reset_shader_sha256 = _shader_raw_sha256(reset_state, rd.ShaderStage.Pixel)
    if (
        expected_reset_shader_sha256
        and reset_shader_sha256 != str(expected_reset_shader_sha256).upper()
    ):
        raise ValueError(
            "Reset pixel shader baseline mismatch: expected %s, got %s"
            % (str(expected_reset_shader_sha256).upper(), reset_shader_sha256)
        )
    reset_targets = _save_render_targets(
        controller, reset_state, event_id, output_dir, "reset"
    )
    reset_target_baseline = _assert_reset_target_hashes(
        reset_targets, expected_reset_raw_sha256
    )
    reference_shader = None
    reference_targets = None
    reference_id = None
    replacement_id = None
    try:
        if reference_hlsl_path:
            reference_dir = os.path.join(output_dir, "reference")
            reference_shader, original_id, reference_id = export_pixel_shader_roundtrip(
                controller, event_id, reference_hlsl_path, reference_dir
            )
            controller.SetFrameEvent(int(event_id), True)
            reference_targets = _save_render_targets(
                controller,
                controller.GetPipelineState(),
                event_id,
                output_dir,
                "reference",
            )
            controller.RemoveReplacement(original_id)
            controller.SetFrameEvent(max(int(event_id) - 1, 0), True)
            controller.SetFrameEvent(int(event_id), True)
            controller.FreeTargetResource(reference_id)
            reference_id = None

        candidate_dir = (
            os.path.join(output_dir, "candidate") if reference_hlsl_path else output_dir
        )
        shader_result, original_id, replacement_id = export_pixel_shader_roundtrip(
            controller, event_id, hlsl_path, candidate_dir
        )
        controller.SetFrameEvent(int(event_id), True)
        applied_targets = _save_render_targets(
            controller, controller.GetPipelineState(), event_id, output_dir, "applied"
        )
        targets = {"reset": reset_targets, "applied": applied_targets}
        comparison_pair = ["reset", "applied"]
        if reference_targets is not None:
            targets["reference"] = reference_targets
            comparison_pair = ["reference", "applied"]

        return {
            "event_id": int(event_id),
            "hlsl_path": os.path.normpath(hlsl_path),
            "reference_hlsl_path": (
                os.path.normpath(reference_hlsl_path) if reference_hlsl_path else None
            ),
            "targets": targets,
            "comparison_pair": comparison_pair,
            "reset_baseline": {
                "shader_raw_sha256": reset_shader_sha256,
                "targets": reset_target_baseline,
            },
            "replacement_resource_id": str(replacement_id),
            "reference_shader": reference_shader,
            "shader": shader_result,
        }
    finally:
        controller.RemoveReplacement(original_id)
        controller.SetFrameEvent(int(event_id), True)
        if replacement_id is not None:
            controller.FreeTargetResource(replacement_id)
        if reference_id is not None:
            controller.FreeTargetResource(reference_id)


def export_vertex_shader_roundtrip(controller, event_id, hlsl_path, output_dir):
    """Compile and apply a vertex shader while keeping all work in replay."""
    if not os.path.isfile(hlsl_path):
        raise ValueError("HLSL file not found: %s" % hlsl_path)

    os.makedirs(output_dir, exist_ok=True)
    stage = rd.ShaderStage.Vertex
    controller.SetFrameEvent(int(event_id), True)
    state = controller.GetPipelineState()
    original_id = state.GetShader(stage)
    if original_id == rd.ResourceId.Null():
        raise ValueError("Event ID %s has no vertex shader" % event_id)

    controller.RemoveReplacement(original_id)
    controller.SetFrameEvent(int(event_id), True)
    state = controller.GetPipelineState()
    original = state.GetShaderReflection(stage)
    if original is None:
        raise ValueError("Failed to reflect the original vertex shader")

    original_bytes = bytes(original.rawBytes)
    _validate_dxbc(original_bytes, "Original vertex shader")
    original_path = os.path.join(output_dir, "eid%d_vertex_original_reset.dxbc" % event_id)
    _write_bytes(original_path, original_bytes)

    source = _load_hlsl_source(hlsl_path)
    entry_name = state.GetShaderEntryPoint(stage) or original.entryPoint or "main"
    compile_flags = original.debugInfo.compileFlags
    new_id, compile_log = controller.BuildTargetShader(
        entry_name,
        rd.ShaderEncoding.HLSL,
        source,
        compile_flags,
        stage,
    )
    if new_id == rd.ResourceId.Null():
        raise ValueError("HLSL compilation failed:\n%s" % compile_log)

    try:
        entries = controller.GetShaderEntryPoints(new_id)
        if not entries:
            raise ValueError("Recompiled vertex shader has no entry point")
        recompiled = controller.GetShader(
            state.GetGraphicsPipelineObject(), new_id, entries[0]
        )
        if recompiled is None:
            raise ValueError("Failed to reflect the recompiled vertex shader")

        recompiled_bytes = bytes(recompiled.rawBytes)
        _validate_dxbc(recompiled_bytes, "Recompiled vertex shader")
        recompiled_path = os.path.join(output_dir, "eid%d_vertex_applied.dxbc" % event_id)
        _write_bytes(recompiled_path, recompiled_bytes)
        controller.ReplaceResource(original_id, new_id)
    except Exception:
        controller.FreeTargetResource(new_id)
        raise

    return {
        "event_id": int(event_id),
        "entry_point": entry_name,
        "original_resource_id": str(original_id),
        "replacement_resource_id": str(new_id),
        "compile_log": str(compile_log),
        "original": _file_result(original_path, original_bytes),
        "recompiled": _file_result(recompiled_path, recompiled_bytes),
    }, original_id, new_id


def validate_vertex_shader_roundtrip(
    controller,
    event_id,
    hlsl_path,
    output_dir,
    reference_hlsl_path=None,
):
    """Compare Reset/Applied PostVS bytes and always restore the Reset state."""
    os.makedirs(output_dir, exist_ok=True)
    event_id = int(event_id)
    controller.SetFrameEvent(event_id, True)
    state = controller.GetPipelineState()
    original_id = state.GetShader(rd.ShaderStage.Vertex)
    if original_id == rd.ResourceId.Null():
        raise ValueError("Event ID %s has no vertex shader" % event_id)

    controller.RemoveReplacement(original_id)
    controller.SetFrameEvent(event_id, True)
    reset_snapshot = _save_postvs_snapshot(controller, event_id, output_dir, "reset")
    reference_snapshot = None
    reference_id = None
    replacement_id = None
    try:
        if reference_hlsl_path:
            reference_dir = os.path.join(output_dir, "reference")
            _, original_id, reference_id = export_vertex_shader_roundtrip(
                controller, event_id, reference_hlsl_path, reference_dir
            )
            controller.SetFrameEvent(event_id, True)
            reference_snapshot = _save_postvs_snapshot(
                controller, event_id, output_dir, "reference"
            )
            controller.RemoveReplacement(original_id)
            controller.SetFrameEvent(max(event_id - 1, 0), True)
            controller.SetFrameEvent(event_id, True)
            controller.FreeTargetResource(reference_id)
            reference_id = None

        candidate_dir = os.path.join(output_dir, "candidate") if reference_hlsl_path else output_dir
        shader_result, original_id, replacement_id = export_vertex_shader_roundtrip(
            controller, event_id, hlsl_path, candidate_dir
        )
        controller.SetFrameEvent(event_id, True)
        applied_snapshot = _save_postvs_snapshot(controller, event_id, output_dir, "applied")
        comparison = _compare_postvs_snapshots(
            reference_snapshot or reset_snapshot, applied_snapshot
        )
        return {
            "event_id": event_id,
            "hlsl_path": os.path.normpath(hlsl_path),
            "reference_hlsl_path": os.path.normpath(reference_hlsl_path) if reference_hlsl_path else None,
            "reset": reset_snapshot,
            "reference": reference_snapshot,
            "applied": applied_snapshot,
            "comparison": comparison,
            "shader": shader_result,
            "replacement_resource_id": str(replacement_id),
        }
    finally:
        controller.RemoveReplacement(original_id)
        controller.SetFrameEvent(event_id, True)
        if replacement_id is not None:
            controller.FreeTargetResource(replacement_id)
        if reference_id is not None:
            controller.FreeTargetResource(reference_id)


def _validate_dxbc(data, label):
    if len(data) < 4 or data[:4] != b"DXBC":
        raise ValueError("%s is not a DXBC container" % label)


def _assert_reset_target_hashes(targets, expected_hashes):
    """Stop validation before Apply when Reset MRT bytes are not trusted."""
    if not expected_hashes:
        return {}

    mismatches = []
    results = {}
    for slot, specification in expected_hashes.items():
        slot = str(slot)
        target = targets.get(slot)
        if target is None:
            mismatches.append("RT%s is missing" % slot)
            continue
        if isinstance(specification, dict):
            expected = specification.get("sha256")
            baseline_path = specification.get("raw_path")
            max_differing_bytes = int(specification.get("max_differing_bytes", 0))
        else:
            expected = specification
            baseline_path = None
            max_differing_bytes = 0
        if not expected:
            mismatches.append("RT%s expected SHA-256 is missing" % slot)
            continue

        actual = str(target.get("raw_sha256", "")).upper()
        expected = str(expected).upper()
        target_result = {
            "expected_sha256": expected,
            "actual_sha256": actual,
            "exact_match": actual == expected,
            "accepted": actual == expected,
        }
        if actual != expected and baseline_path and max_differing_bytes >= 0:
            comparison = _compare_raw_files(baseline_path, target.get("raw_path"))
            target_result.update(comparison)
            target_result["max_differing_bytes"] = max_differing_bytes
            target_result["accepted"] = (
                comparison["same_size"]
                and comparison["differing_bytes"] <= max_differing_bytes
            )
        results[slot] = target_result
        if not target_result["accepted"]:
            detail = "RT%s expected %s, got %s" % (slot, expected, actual)
            if "differing_bytes" in target_result:
                detail += ", differing bytes %s > %s" % (
                    target_result["differing_bytes"],
                    max_differing_bytes,
                )
            mismatches.append(detail)

    if mismatches:
        raise ValueError("Reset render-target baseline mismatch: %s" % "; ".join(mismatches))
    return results


def _shader_raw_sha256(state, stage):
    reflection = state.GetShaderReflection(stage)
    if reflection is None:
        raise ValueError("Failed to reflect the Reset pixel shader")
    data = bytes(reflection.rawBytes)
    _validate_dxbc(data, "Reset pixel shader")
    return hashlib.sha256(data).hexdigest().upper()


def _compare_raw_files(expected_path, actual_path):
    if not expected_path or not os.path.isfile(expected_path):
        raise ValueError("Reset baseline file not found: %s" % expected_path)
    if not actual_path or not os.path.isfile(actual_path):
        raise ValueError("Reset output file not found: %s" % actual_path)

    expected_size = os.path.getsize(expected_path)
    actual_size = os.path.getsize(actual_path)
    differing_bytes = 0
    with open(expected_path, "rb") as expected_file, open(actual_path, "rb") as actual_file:
        while True:
            expected_chunk = expected_file.read(1024 * 1024)
            actual_chunk = actual_file.read(1024 * 1024)
            if not expected_chunk and not actual_chunk:
                break
            differing_bytes += sum(
                left != right for left, right in zip(expected_chunk, actual_chunk)
            )
            differing_bytes += abs(len(expected_chunk) - len(actual_chunk))

    return {
        "baseline_raw_path": os.path.normpath(expected_path),
        "same_size": expected_size == actual_size,
        "differing_bytes": differing_bytes,
    }


def _save_postvs_snapshot(controller, event_id, output_dir, prefix):
    """Persist the complete VSOut buffer before it is rounded for JSON export."""
    mesh = controller.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
    raw = bytes(
        controller.GetBufferData(
            mesh.vertexResourceId,
            mesh.vertexByteOffset,
            mesh.vertexByteSize,
        )
    )
    raw_path = os.path.join(output_dir, "eid%d_%s_vsout.bin" % (event_id, prefix))
    metadata_path = os.path.join(
        output_dir, "eid%d_%s_vsout.json" % (event_id, prefix)
    )
    metadata = {
        "event_id": int(event_id),
        "prefix": prefix,
        "resource_id": str(mesh.vertexResourceId),
        "vertex_byte_offset": int(mesh.vertexByteOffset),
        "vertex_byte_size": int(mesh.vertexByteSize),
        "vertex_byte_stride": int(mesh.vertexByteStride),
        "num_indices": int(mesh.numIndices),
        "raw_path": os.path.normpath(raw_path),
        "raw_size": len(raw),
        "raw_sha256": hashlib.sha256(raw).hexdigest().upper(),
    }
    _write_bytes(raw_path, raw)
    with open(metadata_path, "w", encoding="utf-8", newline="\n") as output_file:
        json.dump(metadata, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")
    metadata["metadata_path"] = os.path.normpath(metadata_path)
    return metadata


def _compare_postvs_snapshots(reference, candidate):
    comparison = _compare_raw_files(reference["raw_path"], candidate["raw_path"])
    comparison["reference_sha256"] = reference["raw_sha256"]
    comparison["candidate_sha256"] = candidate["raw_sha256"]
    comparison["exact_match"] = (
        comparison["same_size"] and comparison["differing_bytes"] == 0
    )
    return comparison


def _load_hlsl_source(path, include_stack=None):
    """Inline local quoted includes because BuildTargetShader receives bytes only."""
    include_stack = include_stack or []
    path = os.path.abspath(path)
    if path in include_stack:
        chain = " -> ".join(include_stack + [path])
        raise ValueError("Circular HLSL include: %s" % chain)
    with open(path, "r", encoding="utf-8-sig") as source_file:
        source = source_file.read()

    stack = include_stack + [path]
    base_dir = os.path.dirname(path)
    include_pattern = re.compile(r'^\s*#include\s+"([^"]+)"\s*$', re.MULTILINE)

    def replace_include(match):
        include_path = os.path.normpath(os.path.join(base_dir, match.group(1)))
        if not os.path.isfile(include_path):
            raise ValueError("Local HLSL include not found: %s" % include_path)
        return _load_hlsl_source(include_path, stack).decode("utf-8")

    expanded = include_pattern.sub(replace_include, source)
    return expanded.encode("utf-8")


def _save_render_targets(controller, state, event_id, output_dir, prefix):
    targets = {}
    texture_descriptions = {
        str(texture.resourceId): texture for texture in controller.GetTextures()
    }
    for slot, target in enumerate(state.GetOutputTargets()):
        resource_id = target.resource
        if resource_id == rd.ResourceId.Null():
            continue
        path = os.path.join(
            output_dir, "eid%d_%s_rt%d.png" % (event_id, prefix, slot)
        )
        raw_path = os.path.join(
            output_dir, "eid%d_%s_rt%d.bin" % (event_id, prefix, slot)
        )
        raw = bytes(controller.GetTextureData(resource_id, rd.Subresource()))
        _write_bytes(raw_path, raw)
        save = rd.TextureSave()
        save.resourceId = resource_id
        save.destType = rd.FileType.PNG
        save.mip = 0
        save.slice.sliceIndex = 0
        save.alpha = rd.AlphaMapping.Preserve
        controller.SaveTexture(save, path)
        texture = texture_descriptions.get(str(resource_id))
        targets[str(slot)] = {
            "slot": slot,
            "resource_id": str(resource_id),
            "path": os.path.normpath(path),
            "size": os.path.getsize(path),
            "raw_path": os.path.normpath(raw_path),
            "raw_size": len(raw),
            "raw_sha256": hashlib.sha256(raw).hexdigest().upper(),
            "width": int(getattr(texture, "width", 0)),
            "height": int(getattr(texture, "height", 0)),
            "format": _format_name(texture),
        }
    return targets


def _format_name(texture):
    if texture is None:
        return None
    try:
        return str(texture.format.Name())
    except Exception:
        return str(getattr(texture, "format", ""))


def _write_bytes(path, data):
    with open(path, "wb") as output_file:
        output_file.write(data)


def _file_result(path, data):
    return {
        "path": os.path.normpath(path),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest().upper(),
        "header": data[:4].decode("ascii"),
    }
