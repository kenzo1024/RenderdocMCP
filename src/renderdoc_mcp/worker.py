"""Python 3.6 RenderDoc worker.

MCP server stays on modern Python for the mcp package, while this worker runs
with RenderDoc's Python ABI and owns the ReplayController session.
"""

import json
import os
import sys
import traceback

from renderdoc_mcp.exporter import (
    export_draw_bundle,
    export_event_textures,
    export_mesh_stage,
)
from renderdoc_mcp.renderdoc_api import error
from renderdoc_mcp.resource_export import export_resource_asset
from renderdoc_mcp.session import get_session
from renderdoc_mcp.shader_material import export_shader_material, material_summary
from renderdoc_mcp.shader_roundtrip import validate_vertex_shader_roundtrip


def _call(method, params):
    session = get_session()

    if method == "open_capture":
        try:
            return session.open(params["filepath"])
        except RuntimeError as exc:
            return error(str(exc), "RENDERDOC_LOAD_ERROR")

    if method == "close_capture":
        return session.close()

    if method == "shutdown":
        session.shutdown()
        return {"status": "shutdown"}

    err = session.require_open()
    if err:
        return err

    if method == "export_mesh_stage_data":
        return export_mesh_stage(
            session,
            params["event_id"],
            params["output_path"],
            params.get("stage", "vsout"),
            file_format=params.get("mesh_format", "json"),
            first_index=params.get("first_index", 0),
            max_vertices=params.get("max_vertices", 0),
            instance=params.get("instance", 0),
            view=params.get("view", 0),
        )

    if method == "export_event_textures":
        return export_event_textures(
            session,
            params["event_id"],
            params["output_dir"],
            params.get("prefix", "skin"),
            stages=params.get("stages"),
            file_type=params.get("file_type", "png"),
            skip_small=params.get("skip_small", True),
            include_render_targets=params.get("include_render_targets", True),
            save_depth=params.get("save_depth", False),
        )

    if method == "export_draw_bundle":
        return export_draw_bundle(
            session,
            params["event_id"],
            params["output_dir"],
            prefix=params.get("prefix", "skin"),
            mesh_format=params.get("mesh_format", "json"),
            texture_file_type=params.get("texture_file_type", "png"),
            texture_stages=params.get("texture_stages"),
            include_render_targets=params.get("include_render_targets", True),
            skip_small_textures=params.get("skip_small_textures", True),
            save_depth=params.get("save_depth", False),
            max_vertices=params.get("max_vertices", 0),
        )

    if method == "export_resource_asset":
        return export_resource_asset(
            session,
            params["event_id"],
            params["output_dir"],
            prefix=params.get("prefix", "asset"),
            config=params.get("config"),
            preset_name=params.get("preset_name"),
            preset_dir=params.get("preset_dir"),
            texture_file_type=params.get("texture_file_type", "png"),
            texture_stages=params.get("texture_stages"),
            include_textures=params.get("include_textures", True),
            include_render_targets=params.get("include_render_targets", False),
            skip_small_textures=params.get("skip_small_textures", True),
            save_depth=params.get("save_depth", False),
            max_vertices=params.get("max_vertices", 0),
        )

    if method == "export_shader_material":
        err = session.set_event(params["event_id"])
        if err:
            return err
        bundle = export_shader_material(
            session.controller,
            params["event_id"],
            params["output_dir"],
            prefix=params.get("prefix", "shader"),
            include_textures=params.get("include_textures", True),
            include_mesh=params.get("include_mesh", True),
        )
        return material_summary(bundle)

    if method == "validate_vertex_shader":
        return validate_vertex_shader_roundtrip(
            session.controller,
            params["event_id"],
            params["hlsl_path"],
            params["output_dir"],
            params.get("reference_hlsl_path"),
        )

    return error("Unknown worker method: {}".format(method), "UNKNOWN_METHOD")


def _write_response(response):
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
            result = _call(request.get("method"), request.get("params") or {})
            _write_response({"id": request.get("id"), "result": result})
        except Exception as exc:
            debug = os.environ.get("RENDERDOC_MCP_WORKER_DEBUG") == "1"
            payload = error(str(exc), "WORKER_ERROR")
            if debug:
                payload["traceback"] = traceback.format_exc()
            _write_response({"id": None, "result": payload})


if __name__ == "__main__":
    main()
