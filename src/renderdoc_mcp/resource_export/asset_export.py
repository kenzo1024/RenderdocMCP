"""High-level resource asset export shared by MCP and GUI."""

import json
import os

from renderdoc_mcp.exporter import export_event_textures
from renderdoc_mcp.mesh_decode import get_mesh_stage_data, jsonify_vertices
from renderdoc_mcp.renderdoc_api import error, safe_filename
from renderdoc_mcp.resource_export import csv_export, fbx_export, mapping, preset, schema


def export_resource_asset(
    session,
    event_id,
    output_dir,
    prefix="asset",
    config=None,
    preset_name=None,
    preset_dir=None,
    texture_file_type="png",
    texture_stages=None,
    include_textures=True,
    include_render_targets=False,
    skip_small_textures=True,
    save_depth=False,
    max_vertices=0,
):
    """Export mapped CSV/FBX plus optional textures and manifest."""
    err = session.set_event(event_id)
    if err:
        return err

    action = session.get_action(event_id)
    if action is None:
        return error("Event ID %s not found" % event_id, "INVALID_EVENT_ID")

    try:
        export_config = load_config(config, preset_name, preset_dir)
        mesh_data, mesh_exports = decode_mesh_data(session, action, event_id, max_vertices)
        if not config and not preset_name:
            export_config = mapping.auto_config(mesh_data[schema.VSIN], mesh_data[schema.VSOUT])

        validation_error = schema.validate_config(export_config)
        if validation_error:
            return error(validation_error, "INVALID_EXPORT_CONFIG")

        bundle_dir = os.path.normpath(os.path.join(output_dir, "%s_eid_%s" % (safe_filename(prefix), event_id)))
        os.makedirs(bundle_dir, exist_ok=True)
        base = os.path.join(bundle_dir, safe_filename(prefix) + "_mesh")
        csv_result = csv_export.write_asset_csv(mesh_data, base + ".csv", export_config)
        fbx_result = fbx_export.export_fbx(csv_result["output_path"], base + ".fbx", export_config)

        texture_result = None
        if include_textures:
            texture_dir = os.path.join(bundle_dir, safe_filename(prefix) + "_textures")
            texture_result = export_event_textures(
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

        manifest = {
            "schema": "renderdoc-mcp.resource-asset.v1",
            "event_id": event_id,
            "output_dir": bundle_dir,
            "action_name": action.GetName(session.structured_file),
            "num_indices": action.numIndices,
            "num_instances": action.numInstances,
            "preset_name": preset_name,
            "config": export_config,
            "mesh_sources": mesh_exports,
            "csv": csv_result,
            "fbx": fbx_result,
            "textures": texture_result,
        }
        manifest_path = os.path.join(bundle_dir, safe_filename(prefix) + "_asset_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        return {
            "event_id": event_id,
            "bundle_dir": bundle_dir,
            "manifest_path": manifest_path,
            "csv": csv_result,
            "fbx": fbx_result,
            "textures": texture_result,
        }
    except Exception as exc:
        return error(str(exc), "RESOURCE_ASSET_EXPORT_ERROR")


def load_config(config, preset_name, preset_dir):
    if preset_name:
        return preset.load_preset(preset_name, preset_dir)
    return schema.normalize_config(config)


def decode_mesh_data(session, action, event_id, max_vertices):
    mesh_data = {}
    exports = {}
    for stage_name in (schema.VSIN, schema.VSOUT):
        vertices, meta = get_mesh_stage_data(
            session.controller,
            action,
            stage_name,
            max_vertices=max_vertices,
        )
        rows = jsonify_vertices(vertices)
        mesh_data[stage_name] = rows
        exports[stage_name] = {
            "event_id": event_id,
            "stage": stage_name,
            "vertex_count": len(rows),
            "headers": mapping.mesh_headers(rows),
        }
        exports[stage_name].update(meta)
    return mesh_data, exports
