"""导出一个 draw call 的 mesh 和纹理。

这个模块不负责打开 rdc，只接收已经打开好的 RenderDocSession。
这样职责比较清楚：session 管生命周期，exporter 管落盘格式。
"""

import csv
import json
import os

from renderdoc_mcp.mesh_decode import get_mesh_stage_data, jsonify_vertices
from renderdoc_mcp.renderdoc_api import (
    error,
    file_types,
    rd,
    safe_filename,
    shader_stages,
    texture_desc_to_dict,
)


def export_mesh_stage(
    session,
    event_id,
    output_path,
    stage,
    file_format="json",
    first_index=0,
    max_vertices=0,
    instance=0,
    view=0,
):
    """导出单个 mesh stage 到 JSON 或 CSV。"""
    err = session.set_event(event_id)
    if err:
        return err

    action = session.get_action(event_id)
    if action is None:
        return error(f"Event ID {event_id} not found", "INVALID_EVENT_ID")

    try:
        # 真正的 VSIn/VSOut 解码都在 mesh_decode.py，这里只负责写文件。
        vertices, stage_meta = get_mesh_stage_data(
            session.controller,
            action,
            stage,
            first_index=first_index,
            max_vertices=max_vertices,
            instance=instance,
            view=view,
        )
    except Exception as exc:
        return error(f"Failed to decode {stage}: {exc}")

    meta = {
        # meta 会写进 JSON，方便后续知道这批 mesh 来自哪个 EID。
        "event_id": event_id,
        "stage": stage.lower(),
        "action_name": action.GetName(session.structured_file),
        "num_indices": action.numIndices,
        "num_instances": action.numInstances,
        "first_index": first_index,
        "instance": instance,
        "view": view,
    }
    meta.update(stage_meta)
    return _write_mesh(vertices, output_path, file_format, meta)


def export_event_textures(
    session,
    event_id,
    output_dir,
    prefix,
    stages=None,
    file_type="png",
    skip_small=True,
    include_render_targets=True,
    save_depth=False,
):
    """导出当前 draw 绑定的 shader 纹理和可选 RT。"""
    err = session.set_event(event_id)
    if err:
        return err

    available_file_types = file_types()
    if file_type.lower() not in available_file_types:
        return error(f"Unsupported texture file type: {file_type}")

    os.makedirs(output_dir, exist_ok=True)
    # SetFrameEvent 后拿到的 PipelineState 就是这个 EID 的绑定状态。
    state = session.controller.GetPipelineState()
    stages = stages or ["vertex", "pixel"]
    exported = []
    skipped = []

    for stage_name in stages:
        stage_enum = shader_stages().get(stage_name.lower())
        if stage_enum is None:
            skipped.append({"stage": stage_name, "reason": "unknown_stage"})
            continue
        if stage_enum == rd.ShaderStage.Compute:
            # 这个工具面向图形 draw call，compute 资源暂不混进来。
            skipped.append({"stage": stage_name, "reason": "compute_not_supported"})
            continue
        _export_stage_textures(session, state, stage_name, stage_enum, output_dir, prefix, file_type, skip_small, exported, skipped)

    if include_render_targets:
        _export_render_targets(session, state, event_id, output_dir, prefix, file_type, save_depth, exported, skipped)

    return {
        "event_id": event_id,
        "output_dir": os.path.normpath(output_dir),
        "exported": exported,
        "exported_count": len(exported),
        "skipped": skipped,
        "skipped_count": len(skipped),
    }


def export_draw_bundle(
    session,
    event_id,
    output_dir,
    prefix="skin",
    mesh_format="json",
    texture_file_type="png",
    texture_stages=None,
    include_render_targets=True,
    skip_small_textures=True,
    save_depth=False,
    max_vertices=0,
):
    """一次导出 VSIn、VSOut、纹理和 manifest。

    默认目录名和文件名都带 prefix，比如 skin_eid_852/skin_vsin.json。
    """
    action = session.get_action(event_id)
    if action is None:
        return error(f"Event ID {event_id} not found", "INVALID_EVENT_ID")

    bundle_dir = os.path.normpath(os.path.join(output_dir, f"{safe_filename(prefix)}_eid_{event_id}"))
    texture_dir = os.path.join(bundle_dir, f"{safe_filename(prefix)}_textures")
    os.makedirs(bundle_dir, exist_ok=True)

    exports = {}
    errors = []
    for stage in ("vsin", "vsout"):
        # 用户明确要求保留 VSIn 和 VSOut，这里固定作为 bundle 的核心产物。
        path = os.path.join(bundle_dir, f"{safe_filename(prefix)}_{stage}.{mesh_format}")
        result = export_mesh_stage(session, event_id, path, stage, mesh_format, max_vertices=max_vertices)
        if "error" in result:
            item = {"stage": stage}
            item.update(result)
            errors.append(item)
        else:
            exports[stage] = result

    tex_result = export_event_textures(
        session,
        event_id,
        texture_dir,
        prefix,
        stages=texture_stages,
        file_type=texture_file_type,
        skip_small=skip_small_textures,
        include_render_targets=include_render_targets,
        save_depth=save_depth,
    )
    if "error" in tex_result:
        errors.append({"textures": tex_result})
    else:
        exports["textures"] = tex_result

    manifest = {
        # manifest 是给资产处理脚本看的目录索引，不需要重新扫描文件名。
        "event_id": event_id,
        "output_dir": bundle_dir,
        "action_name": action.GetName(session.structured_file),
        "num_indices": action.numIndices,
        "num_instances": action.numInstances,
        "exports": exports,
        "errors": errors,
    }
    manifest_path = os.path.join(bundle_dir, f"{safe_filename(prefix)}_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return {
        "event_id": event_id,
        "bundle_dir": bundle_dir,
        "manifest_path": manifest_path,
        "exports": exports,
        "errors": errors,
    }


def _write_mesh(vertices, output_path, file_format, meta):
    """把 mesh 行数据写成 JSON 或 CSV。"""
    fmt = file_format.lower()
    if fmt not in {"json", "csv"}:
        return error("mesh_format must be json or csv", "INVALID_ARGUMENT")

    output_path = os.path.normpath(output_path)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    rows = jsonify_vertices(vertices)

    if fmt == "json":
        with open(output_path, "w", encoding="utf-8") as f:
            payload = dict(meta)
            payload["vertices"] = rows
            json.dump(payload, f, ensure_ascii=False, indent=2)
    else:
        _write_mesh_csv(rows, output_path)

    return {
        "output_path": output_path,
        "format": fmt,
        "vertex_count": len(rows),
        "event_id": meta["event_id"],
        "stage": meta["stage"],
    }


def _write_mesh_csv(rows, output_path):
    """CSV 列名按实际属性动态生成。"""
    fieldnames = ["vtx", "idx"]
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            for key, value in list(flat.items()):
                if isinstance(value, list):
                    flat[key] = ";".join(str(item) for item in value)
            writer.writerow(flat)


def _export_stage_textures(session, state, stage_name, stage_enum, output_dir, prefix, file_type, skip_small, exported, skipped):
    """导出某个 shader stage 的只读资源纹理。"""
    reflection = state.GetShaderReflection(stage_enum)
    if reflection is None:
        return

    bindings = _readonly_bindings_by_index(state, stage_enum)
    for index, resource in enumerate(reflection.readOnlyResources):
        for binding in bindings.get(index, []):
            tex = session.get_texture(str(binding.descriptor.resource))
            if tex is None:
                continue
            if skip_small and tex.width <= 4 and tex.height <= 4:
                # 很多引擎会绑定 1x1/4x4 占位纹理，默认跳过减少噪声。
                skipped.append({
                    "stage": stage_name,
                    "binding_index": index,
                    "name": resource.name,
                    "resource_id": str(binding.descriptor.resource),
                    "reason": "small_texture",
                })
                continue
            filename = f"{prefix}_{stage_name}_t{index}_{resource.name}_{tex.width}x{tex.height}.{file_type.lower()}"
            output_path = os.path.join(output_dir, safe_filename(filename))
            _save_texture(session, tex.resourceId, output_path, file_type)
            exported.append({
                "type": "shader_resource",
                "stage": stage_name,
                "binding_index": index,
                "name": resource.name,
                "resource_id": str(tex.resourceId),
                "texture": texture_desc_to_dict(tex),
                "output_path": output_path,
            })


def _readonly_bindings_by_index(state, stage_enum):
    """把绑定表按 slot 分组，方便和 shader reflection 对上名字。"""
    try:
        bindings = state.GetReadOnlyResources(stage_enum)
    except Exception:
        return {}

    by_index = {}
    for binding in bindings:
        by_index.setdefault(binding.access.index, []).append(binding)
    return by_index


def _export_render_targets(session, state, event_id, output_dir, prefix, file_type, save_depth, exported, skipped):
    """导出当前 draw 的 color render target。"""
    for slot, target in enumerate(state.GetOutputTargets()):
        if int(target.resource) == 0:
            continue
        tex = session.get_texture(str(target.resource))
        if tex is None:
            continue
        filename = f"{prefix}_rt_color{slot}_eid{event_id}_{tex.width}x{tex.height}.{file_type.lower()}"
        output_path = os.path.join(output_dir, safe_filename(filename))
        _save_texture(session, target.resource, output_path, file_type)
        exported.append({
            "type": "render_target",
            "slot": slot,
            "resource_id": str(target.resource),
            "texture": texture_desc_to_dict(tex),
            "output_path": output_path,
        })

    if save_depth:
        _export_depth_target(session, state, event_id, output_dir, prefix, file_type, exported, skipped)


def _export_depth_target(session, state, event_id, output_dir, prefix, file_type, exported, skipped):
    """可选导出 depth target。某些格式 SaveTexture 可能失败，失败交给调用栈处理。"""
    try:
        target = state.GetDepthTarget()
    except Exception as exc:
        skipped.append({"type": "depth_target", "reason": str(exc)})
        return

    if int(target.resource) == 0:
        return
    tex = session.get_texture(str(target.resource))
    size = f"{tex.width}x{tex.height}" if tex else "unknown"
    filename = f"{prefix}_rt_depth_eid{event_id}_{size}.{file_type.lower()}"
    output_path = os.path.join(output_dir, safe_filename(filename))
    _save_texture(session, target.resource, output_path, file_type)
    exported.append({
        "type": "depth_target",
        "resource_id": str(target.resource),
        "texture": texture_desc_to_dict(tex) if tex else None,
        "output_path": output_path,
    })


def _save_texture(session, resource_id, output_path, file_type):
    """调用 RenderDoc SaveTexture 写图片。"""
    save = rd.TextureSave()
    save.resourceId = resource_id
    save.destType = file_types()[file_type.lower()]
    save.mip = 0
    save.slice.sliceIndex = 0
    save.alpha = rd.AlphaMapping.Preserve
    session.controller.SaveTexture(save, output_path)
