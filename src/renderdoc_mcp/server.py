"""MCP 工具入口。

这里故意只注册少量工具：打开捕获、连接 GUI 当前捕获、导出 mesh、导出纹理、
导出完整 bundle。复杂分析功能都不放进来，保持这个 MCP 专注资源导出。
"""

import atexit
import os

from mcp.server.fastmcp import FastMCP

from renderdoc_mcp import backend
from renderdoc_mcp import gui_bridge
from renderdoc_mcp.renderdoc_api import error, to_json

mcp = FastMCP(
    "renderdoc-mcp",
    instructions=(
        "Minimal RenderDoc MCP. Open an .rdc capture, then export draw-call "
        "textures and decoded VSIn/VSOut mesh data."
    ),
)


@mcp.tool()
def connect_to_gui_capture():
    """打开 RenderDoc GUI 当前加载的 rdc。"""
    try:
        path = gui_bridge.current_capture_path()
    except gui_bridge.GUIBridgeError as exc:
        return to_json(error(str(exc), "GUI_BRIDGE_ERROR"))

    if not path:
        return to_json(error("No capture is currently open in RenderDoc GUI.", "NO_GUI_CAPTURE"))

    result = _open_capture(os.path.normpath(path))
    if "error" not in result:
        result["source"] = "renderdoc_gui"
    return to_json(result)


@mcp.tool()
def open_capture(filepath):
    """按路径打开 rdc。"""
    return to_json(_open_capture(filepath))


@mcp.tool()
def close_capture():
    """关闭当前 capture。"""
    return to_json(backend.close_capture())


def _open_capture(filepath):
    """包装 RenderDoc 加载错误，避免把 Python 堆栈直接暴露给 MCP 调用侧。"""
    return backend.open_capture(filepath)


@mcp.tool()
def export_mesh_stage_data(
    event_id,
    output_path,
    stage="vsout",
    mesh_format="json",
    first_index=0,
    max_vertices=0,
    instance=0,
    view=0,
):
    """导出一个 mesh stage。常用 stage 是 vsin 和 vsout。"""
    return to_json(
        backend.export_mesh_stage_data(
            {
                "event_id": event_id,
                "output_path": output_path,
                "stage": stage,
                "mesh_format": mesh_format,
                "first_index": first_index,
                "max_vertices": max_vertices,
                "instance": instance,
                "view": view,
            },
        )
    )


@mcp.tool()
def export_event_textures(
    event_id,
    output_dir,
    prefix="skin",
    stages=None,
    file_type="png",
    skip_small=True,
    include_render_targets=True,
    save_depth=False,
):
    """导出一个 draw 绑定的纹理。"""
    return to_json(
        backend.export_event_textures(
            {
                "event_id": event_id,
                "output_dir": output_dir,
                "prefix": prefix,
                "stages": stages,
                "file_type": file_type,
                "skip_small": skip_small,
                "include_render_targets": include_render_targets,
                "save_depth": save_depth,
            },
        )
    )


@mcp.tool()
def export_draw_bundle(
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
    """导出 VSIn、VSOut、纹理和 manifest。"""
    return to_json(
        backend.export_draw_bundle(
            {
                "event_id": event_id,
                "output_dir": output_dir,
                "prefix": prefix,
                "mesh_format": mesh_format,
                "texture_file_type": texture_file_type,
                "texture_stages": texture_stages,
                "include_render_targets": include_render_targets,
                "skip_small_textures": skip_small_textures,
                "save_depth": save_depth,
                "max_vertices": max_vertices,
            },
        )
    )


def _cleanup():
    """MCP 进程退出时释放 RenderDoc replay 资源。"""
    backend.shutdown()


atexit.register(_cleanup)


def main():
    """FastMCP 入口。"""
    mcp.run()
