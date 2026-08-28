"""RenderDoc GUI bridge 的文件 IPC 客户端。

这个 bridge 是装在 RenderDoc GUI 里的扩展。它通过临时目录里的 request.json /
response.json 通信，让 MCP 可以知道 GUI 当前打开的是哪个 rdc。
"""

import json
import os
import re
import tempfile
import time
import uuid

IPC_NAMESPACE_ENV = "RENDERDOC_MCP_IPC_NAMESPACE"


class GUIBridgeError(RuntimeError):
    """GUI bridge 不可用、超时或返回错误时抛出。"""

    pass


def _ipc_dir():
    root = os.path.join(tempfile.gettempdir(), "renderdoc_mcp")
    namespace = os.environ.get(IPC_NAMESPACE_ENV, "").strip()
    if not namespace:
        return root
    safe_namespace = re.sub(r"[^A-Za-z0-9_.-]+", "_", namespace).strip("._")
    if not safe_namespace:
        raise GUIBridgeError("Invalid RenderDoc MCP IPC namespace")
    return os.path.join(root, safe_namespace)


IPC_DIR = _ipc_dir()
REQUEST_FILE = os.path.join(IPC_DIR, "request.json")
RESPONSE_FILE = os.path.join(IPC_DIR, "response.json")
LOCK_FILE = os.path.join(IPC_DIR, "lock")


def is_available() -> bool:
    """只检查 IPC 目录是否存在，不代表 RenderDoc 一定有 rdc 打开。"""
    return os.path.isdir(IPC_DIR)


def call(method, params=None, timeout=30.0):
    """向 GUI bridge 发一个请求并等待响应。"""
    if not is_available():
        raise GUIBridgeError(f"RenderDoc GUI bridge IPC directory not found: {IPC_DIR}")

    request = {"id": str(uuid.uuid4()), "method": method, "params": params or {}}

    if os.path.exists(RESPONSE_FILE):
        # 避免读到上一次遗留的响应。
        os.remove(RESPONSE_FILE)

    # lock 文件用于告诉 RenderDoc 扩展：request.json 还没写完，先别读。
    with open(LOCK_FILE, "w", encoding="utf-8") as f:
        f.write("lock")
    with open(REQUEST_FILE, "w", encoding="utf-8") as f:
        json.dump(request, f)
    os.remove(LOCK_FILE)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not os.path.exists(RESPONSE_FILE):
            time.sleep(0.05)
            continue

        try:
            with open(RESPONSE_FILE, "r", encoding="utf-8") as f:
                response = json.load(f)
        except (json.JSONDecodeError, OSError):
            # 兼容尚未重启的旧扩展：它可能在文件创建后继续写入。
            time.sleep(0.05)
            continue
        os.remove(RESPONSE_FILE)

        if "error" in response:
            err = response["error"]
            raise GUIBridgeError(f"[{err.get('code', '?')}] {err.get('message', err)}")
        return response.get("result")

    raise GUIBridgeError(f"RenderDoc GUI bridge request timed out: {method}")


def current_capture_path():
    """返回 RenderDoc GUI 当前打开的 rdc 路径；没有打开时返回 None。"""
    status = call("get_capture_status")
    if not status or not status.get("loaded"):
        return None
    return status.get("filename") or None


def open_capture(capture_path):
    """让 RenderDoc GUI bridge 打开指定 rdc。"""
    return call("open_capture", {"capture_path": capture_path}, timeout=60.0)


def open_capture_background(capture_path):
    """让 bridge 加载指定 rdc，但不显示、聚焦或激活 GUI。"""
    return call(
        "open_capture_background",
        {"capture_path": capture_path},
        timeout=60.0,
    )


def open_capture_at_event(capture_path, event_id):
    """让 RenderDoc GUI 打开指定 rdc，并在 Event Browser 选中 EID。"""
    return call(
        "open_capture_at_event",
        {"capture_path": capture_path, "event_id": event_id},
        timeout=60.0,
    )


def focus_event(event_id):
    """让 RenderDoc GUI 在当前 capture 中选中 EID。"""
    return call("focus_event", {"event_id": event_id})


def record_activity(operation, status, message, event_id=None, details=None):
    """Write an MCP-side result into the qrenderdoc activity window."""
    return call(
        "record_activity",
        {
            "operation": operation,
            "status": status,
            "message": message,
            "event_id": event_id,
            "details": details or {},
        },
    )


def show_activity_log():
    """Show and raise the RenderDoc MCP Activity dock."""
    return call("show_activity_log")


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
    """通过 RenderDoc GUI bridge 导出 draw bundle。"""
    return call(
        "export_draw_bundle",
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
        timeout=300.0,
    )


def export_shader_material(
    event_id,
    output_dir,
    prefix="shader",
    include_textures=True,
    include_mesh=True,
):
    """通过 RenderDoc GUI bridge 导出 Shader 原材料包。"""
    return call(
        "export_shader_material",
        {
            "event_id": event_id,
            "output_dir": output_dir,
            "prefix": prefix,
            "include_textures": include_textures,
            "include_mesh": include_mesh,
        },
        timeout=300.0,
    )


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
    """通过 RenderDoc GUI bridge 导出映射后的资产包。"""
    return call(
        "export_resource_asset",
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
        timeout=600.0,
    )


def export_and_apply_pixel_shader(event_id, hlsl_path, output_dir):
    """Export reset DXBC, compile HLSL, export the new DXBC, and apply it."""
    return call(
        "export_and_apply_pixel_shader",
        {
            "event_id": event_id,
            "hlsl_path": hlsl_path,
            "output_dir": output_dir,
        },
        timeout=300.0,
    )


def get_pixel_shader_replacement_status(event_id):
    """Return qrenderdoc's replacement state for one event's pixel shader."""
    return call("get_pixel_shader_replacement_status", {"event_id": event_id})


def reset_pixel_shader(event_id):
    """Clear both qrenderdoc UI and replay replacements for one event's PS."""
    return call("reset_pixel_shader", {"event_id": event_id})


def validate_pixel_shader(
    event_id,
    hlsl_path,
    output_dir,
    expected_reset_raw_sha256=None,
    expected_reset_shader_sha256=None,
    reference_hlsl_path=None,
):
    """Apply a PS temporarily, compare MRT snapshots, and restore Reset state."""
    return call(
        "validate_pixel_shader",
        {
            "event_id": event_id,
            "hlsl_path": hlsl_path,
            "output_dir": output_dir,
            "expected_reset_raw_sha256": expected_reset_raw_sha256,
            "expected_reset_shader_sha256": expected_reset_shader_sha256,
            "reference_hlsl_path": reference_hlsl_path,
        },
        timeout=300.0,
    )


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
    """Debug one pixel before/after applying HLSL and restore Reset state."""
    return call(
        "validate_pixel_shader_trace",
        {
            "event_id": event_id,
            "hlsl_path": hlsl_path,
            "output_dir": output_dir,
            "x": x,
            "y": y,
            "primitive": primitive,
            "sample": sample,
            "view": view,
            "max_steps": max_steps,
        },
        timeout=600.0,
    )


def validate_vertex_shader(
    event_id,
    hlsl_path,
    output_dir,
    reference_hlsl_path=None,
):
    """Apply a VS temporarily, compare PostVS bytes, and restore Reset."""
    return call(
        "validate_vertex_shader",
        {
            "event_id": event_id,
            "hlsl_path": hlsl_path,
            "output_dir": output_dir,
            "reference_hlsl_path": reference_hlsl_path,
        },
        timeout=600.0,
    )
