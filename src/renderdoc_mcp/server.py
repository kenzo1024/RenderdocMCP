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
        "Minimal RenderDoc MCP. Use open_capture with mode=background for "
        "automated work so qrenderdoc is never focused; use foreground only "
        "when the user explicitly requests visual inspection."
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
def open_capture(filepath, mode="auto"):
    """按路径打开 rdc；mode 可选 auto、background 或 foreground。"""
    filepath = os.path.normpath(filepath)
    if mode == "background":
        return to_json(backend.open_capture_background(filepath))
    if mode == "foreground":
        return to_json(backend.open_capture_foreground(filepath))
    if mode != "auto":
        return to_json(error("mode must be auto, background, or foreground", "INVALID_MODE"))
    return to_json(_open_capture(filepath))


@mcp.tool()
def open_capture_background(filepath):
    """通过 qrenderdoc bridge 后台打开 rdc，不显示、聚焦或激活窗口。"""
    return to_json(backend.open_capture_background(os.path.normpath(filepath)))


@mcp.tool()
def open_capture_at_event(filepath, event_id):
    """在 RenderDoc GUI 中打开指定 rdc，并在 Event Browser 聚焦到 EID。"""
    return to_json(backend.open_capture_at_event(os.path.normpath(filepath), event_id))


@mcp.tool()
def focus_event(event_id):
    """在 RenderDoc GUI 当前打开的 capture 中聚焦到 EID。"""
    return to_json(backend.focus_event(event_id))


@mcp.tool()
def show_activity_log():
    """显示或前置 RenderDoc MCP Activity 底部面板。"""
    try:
        return to_json(gui_bridge.show_activity_log())
    except gui_bridge.GUIBridgeError as exc:
        return to_json(error(str(exc), "GUI_BRIDGE_ERROR"))


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


@mcp.tool()
def export_resource_asset(
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
    """按字段映射和变换规则导出 CSV、FBX、纹理和 manifest。"""
    return to_json(
        backend.export_resource_asset(
            {
                "event_id": event_id,
                "output_dir": output_dir,
                "prefix": prefix,
                "config": config,
                "preset_name": preset_name,
                "preset_dir": preset_dir,
                "texture_file_type": texture_file_type,
                "texture_stages": texture_stages,
                "include_textures": include_textures,
                "include_render_targets": include_render_targets,
                "skip_small_textures": skip_small_textures,
                "save_depth": save_depth,
                "max_vertices": max_vertices,
            },
        )
    )


@mcp.tool()
def export_shader_material(
    event_id,
    output_dir,
    prefix="shader",
    include_textures=True,
    include_mesh=True,
):
    """导出 VS/PS 原始 DXBC、ASM、反射、绑定、常量和管线状态。"""
    return to_json(
        backend.export_shader_material(
            {
                "event_id": event_id,
                "output_dir": output_dir,
                "prefix": prefix,
                "include_textures": include_textures,
                "include_mesh": include_mesh,
            },
        )
    )


@mcp.tool()
def export_and_apply_pixel_shader(event_id, hlsl_path, output_dir):
    """导出 Reset 状态的 PS DXBC，编译并应用 HLSL，再导出新 DXBC。"""
    try:
        return to_json(
            gui_bridge.export_and_apply_pixel_shader(
                event_id,
                os.path.normpath(hlsl_path),
                os.path.normpath(output_dir),
            )
        )
    except gui_bridge.GUIBridgeError as exc:
        return to_json(error(str(exc), "GUI_BRIDGE_ERROR"))


@mcp.tool()
def get_pixel_shader_replacement_status(event_id):
    """查询指定 EID 的 Pixel Shader 是否仍被 qrenderdoc 替换。"""
    try:
        return to_json(gui_bridge.get_pixel_shader_replacement_status(event_id))
    except gui_bridge.GUIBridgeError as exc:
        return to_json(error(str(exc), "GUI_BRIDGE_ERROR"))


@mcp.tool()
def reset_pixel_shader(event_id):
    """同时清理 qrenderdoc UI 和 ReplayController 的 Pixel Shader Replacement。"""
    try:
        return to_json(gui_bridge.reset_pixel_shader(event_id))
    except gui_bridge.GUIBridgeError as exc:
        return to_json(error(str(exc), "GUI_BRIDGE_ERROR"))


@mcp.tool()
def validate_pixel_shader(
    event_id,
    hlsl_path,
    output_dir,
    threshold=1,
    expected_reset_raw_sha256=None,
    expected_reset_shader_sha256=None,
    reference_hlsl_path=None,
):
    """导出 Reset/Apply MRT；可用 reference_hlsl_path 直接比较两份 HLSL。"""
    try:
        result = gui_bridge.validate_pixel_shader(
            event_id,
            os.path.normpath(hlsl_path),
            os.path.normpath(output_dir),
            expected_reset_raw_sha256,
            expected_reset_shader_sha256,
            os.path.normpath(reference_hlsl_path) if reference_hlsl_path else None,
        )
        from renderdoc_mcp.pixel_compare import compare_roundtrip_targets

        result["comparison"] = compare_roundtrip_targets(result, threshold)
        comparison = result["comparison"]
        targets = comparison.get("targets", [])
        differing_pixels = sum(item.get("differing_pixels", 0) for item in targets)
        max_channel_delta = max(
            [item.get("max_channel_delta", 0) for item in targets] or [0]
        )
        try:
            gui_bridge.record_activity(
                "validate_pixel_shader.result",
                "success" if comparison.get("status") == "ok" else "error",
                "%s target(s), %s differing pixel(s), max delta %s"
                % (len(targets), differing_pixels, max_channel_delta),
                event_id=event_id,
                details=comparison,
            )
        except gui_bridge.GUIBridgeError:
            pass
        return to_json(result)
    except gui_bridge.GUIBridgeError as exc:
        return to_json(error(str(exc), "GUI_BRIDGE_ERROR"))


@mcp.tool()
def validate_pixel_shader_trace(
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
    """导出指定像素在 Reset/Apply 两个 PS 下的动态指令和寄存器 Trace。"""
    try:
        return to_json(
            gui_bridge.validate_pixel_shader_trace(
                event_id,
                os.path.normpath(hlsl_path),
                os.path.normpath(output_dir),
                x,
                y,
                primitive,
                sample,
                view,
                max_steps,
            )
        )
    except gui_bridge.GUIBridgeError as exc:
        return to_json(error(str(exc), "GUI_BRIDGE_ERROR"))


def _cleanup():
    """MCP 进程退出时释放 RenderDoc replay 资源。"""
    backend.shutdown()


atexit.register(_cleanup)


def main():
    """FastMCP 入口。"""
    mcp.run()
