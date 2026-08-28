"""Export and compare RenderDoc pixel shader debug traces."""

import difflib
import json
import math
import os
import re

from renderdoc_mcp.renderdoc_api import rd
from renderdoc_mcp.shader_roundtrip import export_pixel_shader_roundtrip


def validate_pixel_shader_trace(
    controller,
    event_id,
    hlsl_path,
    output_dir,
    x,
    y,
    primitive=None,
    sample=None,
    view=None,
    max_steps=0,
):
    """Debug one pixel before and after applying HLSL, then restore Reset state."""
    os.makedirs(output_dir, exist_ok=True)
    event_id = int(event_id)
    controller.SetFrameEvent(event_id, True)
    state = controller.GetPipelineState()
    original_id = state.GetShader(rd.ShaderStage.Pixel)
    if original_id == rd.ResourceId.Null():
        raise ValueError("Event ID %s has no pixel shader" % event_id)

    controller.RemoveReplacement(original_id)
    controller.SetFrameEvent(event_id, True)
    history, selected_primitive = _select_pixel_primitive(
        controller, state, event_id, x, y, primitive
    )
    selected_primitive = primitive if primitive is not None else selected_primitive
    original_path = os.path.join(output_dir, "eid%d_pixel_trace_reset.json" % event_id)
    applied_path = os.path.join(output_dir, "eid%d_pixel_trace_applied.json" % event_id)
    original = export_pixel_trace(
        controller, event_id, x, y, original_path, selected_primitive, sample, view, max_steps
    )

    replacement_id = None
    try:
        shader, original_id, replacement_id = export_pixel_shader_roundtrip(
            controller, event_id, hlsl_path, output_dir
        )
        controller.SetFrameEvent(event_id, True)
        applied = export_pixel_trace(
            controller, event_id, x, y, applied_path, selected_primitive, sample, view, max_steps
        )
        comparison = compare_pixel_traces(original, applied)
        result = {
            "event_id": event_id,
            "pixel": {"x": int(x), "y": int(y)},
            "selection": {
                "primitive": selected_primitive,
                "sample": sample,
                "view": view,
                "primitive_source": "explicit" if primitive is not None else "pixel_history",
            },
            "pixel_history": history,
            "shader": shader,
            "traces": {
                "reset": _trace_file_result(original_path, original),
                "applied": _trace_file_result(applied_path, applied),
            },
            "comparison": comparison,
        }
        summary_path = os.path.join(
            output_dir, "eid%d_pixel_trace_comparison.json" % event_id
        )
        _write_json(summary_path, result)
        result["comparison_path"] = os.path.normpath(summary_path)
        return result
    finally:
        controller.RemoveReplacement(original_id)
        controller.SetFrameEvent(event_id, True)
        if replacement_id is not None:
            controller.FreeTargetResource(replacement_id)


def export_pixel_trace(
    controller,
    event_id,
    x,
    y,
    output_path,
    primitive=None,
    sample=None,
    view=None,
    max_steps=0,
):
    """Run DebugPixel and serialize the dynamic DXBC execution trace."""
    controller.SetFrameEvent(int(event_id), True)
    state = controller.GetPipelineState()
    reflection = state.GetShaderReflection(rd.ShaderStage.Pixel)
    if reflection is None:
        raise ValueError("Failed to reflect the pixel shader")
    debug_info = getattr(reflection, "debugInfo", None)
    if debug_info is not None and not getattr(debug_info, "debuggable", True):
        raise ValueError(
            "Pixel shader is not debuggable: %s"
            % getattr(debug_info, "debugStatus", "unknown reason")
        )

    inputs = rd.DebugPixelInputs()
    no_preference = rd.ReplayController.NoPreference
    inputs.primitive = no_preference if primitive is None else int(primitive)
    inputs.sample = no_preference if sample is None else int(sample)
    inputs.view = no_preference if view is None else int(view)
    trace = controller.DebugPixel(int(x), int(y), inputs)
    if trace is None or trace.debugger is None:
        if trace is not None:
            controller.FreeTrace(trace)
        raise ValueError("DebugPixel failed at (%s, %s)" % (x, y))

    try:
        disassembly = controller.DisassembleShader(
            state.GetGraphicsPipelineObject(), reflection, ""
        )
        lines = str(disassembly).splitlines()
        instruction_info = _instruction_info(trace.instInfo, lines)
        states = []
        truncated = False
        while True:
            more = controller.ContinueDebug(trace.debugger)
            if not more:
                break
            for debug_state in more:
                states.append(_debug_state(debug_state, instruction_info))
                if max_steps and len(states) >= int(max_steps):
                    truncated = True
                    break
            if truncated:
                break

        payload = {
            "event_id": int(event_id),
            "pixel": {"x": int(x), "y": int(y)},
            "selection": {
                "primitive": int(inputs.primitive),
                "sample": int(inputs.sample),
                "view": int(inputs.view),
            },
            "stage": str(trace.stage),
            "shader_resource_id": str(state.GetShader(rd.ShaderStage.Pixel)),
            "debuggable": True,
            "truncated": truncated,
            "state_count": len(states),
            "inputs": [_variable(item) for item in trace.inputs],
            "states": states,
            "final_outputs": _final_variables(states, "o"),
        }
        _write_json(output_path, payload)
        return payload
    finally:
        controller.FreeTrace(trace)


def _select_pixel_primitive(controller, state, event_id, x, y, primitive):
    """Use PixelHistory to avoid tracing a different overlapping fragment."""
    targets = state.GetOutputTargets()
    if not targets or targets[0].resource == rd.ResourceId.Null():
        raise ValueError("EID %s has no color target for PixelHistory" % event_id)
    history = controller.PixelHistory(
        targets[0].resource,
        int(x),
        int(y),
        rd.Subresource(),
        rd.CompType.Typeless,
    )
    serialized = [_pixel_modification(item) for item in history]
    if primitive is not None:
        return serialized, int(primitive)

    candidates = [
        item
        for item, raw in zip(serialized, history)
        if int(item["event_id"]) == int(event_id)
        and not bool(item["unbound_ps"])
        and bool(raw.Passed())
    ]
    if not candidates:
        return serialized, None
    return serialized, int(candidates[0]["primitive_id"])


def _pixel_modification(item):
    return {
        "event_id": int(item.eventId),
        "frag_index": int(item.fragIndex),
        "primitive_id": int(item.primitiveID),
        "unbound_ps": bool(item.unboundPS),
        "passed": bool(item.Passed()),
        "sample_masked": bool(item.sampleMasked),
        "backface_culled": bool(item.backfaceCulled),
        "shader_discarded": bool(item.shaderDiscarded),
        "depth_test_failed": bool(item.depthTestFailed),
        "stencil_test_failed": bool(item.stencilTestFailed),
        "shader_out": _modification_value(item.shaderOut),
        "pre_mod": _modification_value(item.preMod),
        "post_mod": _modification_value(item.postMod),
    }


def _modification_value(value):
    return {
        "float": [_json_float(item) for item in value.col.floatValue],
        "uint": [int(item) for item in value.col.uintValue],
        "depth": _json_float(value.depth),
        "stencil": int(value.stencil),
    }


def compare_pixel_traces(original, applied):
    """Compare dynamic opcode paths and final output register bit patterns."""
    original_ops = [_opcode(item.get("next_disassembly")) for item in original["states"]]
    applied_ops = [_opcode(item.get("next_disassembly")) for item in applied["states"]]
    matcher = difflib.SequenceMatcher(None, original_ops, applied_ops, autojunk=False)
    first_divergence = None
    matching_steps = 0
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        if tag == "equal":
            matching_steps += left_end - left_start
            continue
        first_divergence = {
            "kind": tag,
            "reset_range": [left_start, left_end],
            "applied_range": [right_start, right_end],
            "reset_context": _state_context(original["states"], left_start),
            "applied_context": _state_context(applied["states"], right_start),
        }
        break

    original_outputs = original.get("final_outputs", {})
    applied_outputs = applied.get("final_outputs", {})
    output_names = sorted(set(original_outputs) | set(applied_outputs))
    output_comparison = []
    for name in output_names:
        left = original_outputs.get(name)
        right = applied_outputs.get(name)
        output_comparison.append(
            {
                "name": name,
                "exact_raw_match": bool(
                    left is not None
                    and right is not None
                    and left.get("raw_u32") == right.get("raw_u32")
                ),
                "reset": left,
                "applied": right,
            }
        )

    return {
        "reset_state_count": len(original["states"]),
        "applied_state_count": len(applied["states"]),
        "matching_opcode_steps": matching_steps,
        "opcode_similarity": matcher.ratio(),
        "first_opcode_divergence": first_divergence,
        "final_outputs": output_comparison,
    }


def _instruction_info(items, disassembly_lines):
    result = {}
    for item in items:
        line = item.lineInfo
        disassembly_line = int(getattr(line, "disassemblyLine", 0))
        text = ""
        if 0 < disassembly_line <= len(disassembly_lines):
            text = disassembly_lines[disassembly_line - 1].strip()
        result[int(item.instruction)] = {
            "disassembly": text,
            "disassembly_line": disassembly_line,
            "file_index": int(getattr(line, "fileIndex", -1)),
            "source_line": int(getattr(line, "lineStart", 0)),
        }
    return result


def _debug_state(state, instruction_info):
    instruction = int(state.nextInstruction)
    info = _closest_instruction_info(instruction_info, instruction)
    return {
        "step": int(state.stepIndex),
        "next_instruction": instruction,
        "next_disassembly": info.get("disassembly", ""),
        "disassembly_line": info.get("disassembly_line", 0),
        "source_file_index": info.get("file_index", -1),
        "source_line": info.get("source_line", 0),
        "flags": str(state.flags),
        "callstack": [str(item) for item in state.callstack],
        "changes": [
            {"before": _variable(change.before), "after": _variable(change.after)}
            for change in state.changes
        ],
    }


def _closest_instruction_info(items, instruction):
    matches = [key for key in items if key <= instruction]
    return items[max(matches)] if matches else {}


def _variable(variable):
    count = min(max(int(variable.rows) * int(variable.columns), 1), 16)
    raw = [int(item) for item in variable.value.u32v[:count]]
    return {
        "name": str(variable.name),
        "type": str(variable.type),
        "rows": int(variable.rows),
        "columns": int(variable.columns),
        "raw_u32": raw,
        "hex": ["0x%08X" % item for item in raw],
        "s32": [int(item) for item in variable.value.s32v[:count]],
        "f32": [_json_float(item) for item in variable.value.f32v[:count]],
        "members": [_variable(item) for item in variable.members],
    }


def _json_float(value):
    value = float(value)
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    return value


def _final_variables(states, prefix):
    variables = {}
    for state in states:
        for change in state["changes"]:
            after = change["after"]
            name = after["name"]
            if name.startswith(prefix):
                variables[name] = after
    return variables


def _opcode(disassembly):
    if not disassembly:
        return ""
    text = re.sub(r"^\s*\d+\s*:\s*", "", disassembly).strip()
    return text.split(None, 1)[0] if text else ""


def _state_context(states, index, radius=2):
    start = max(index - radius, 0)
    end = min(index + radius + 1, len(states))
    return [
        {
            "index": current,
            "step": states[current].get("step"),
            "instruction": states[current].get("next_instruction"),
            "disassembly": states[current].get("next_disassembly"),
        }
        for current in range(start, end)
    ]


def _trace_file_result(path, trace):
    return {
        "path": os.path.normpath(path),
        "size": os.path.getsize(path),
        "state_count": trace["state_count"],
        "truncated": trace["truncated"],
    }


def _write_json(path, payload):
    with open(path, "w", encoding="utf-8") as output_file:
        json.dump(payload, output_file, ensure_ascii=False, indent=2, allow_nan=False)
