"""RenderDoc GUI bridge 的文件 IPC 客户端。

这个 bridge 是装在 RenderDoc GUI 里的扩展。它通过临时目录里的 request.json /
response.json 通信，让 MCP 可以知道 GUI 当前打开的是哪个 rdc。
"""

import json
import os
import tempfile
import time
import uuid

IPC_DIR = os.path.join(tempfile.gettempdir(), "renderdoc_mcp")
REQUEST_FILE = os.path.join(IPC_DIR, "request.json")
RESPONSE_FILE = os.path.join(IPC_DIR, "response.json")
LOCK_FILE = os.path.join(IPC_DIR, "lock")


class GUIBridgeError(RuntimeError):
    """GUI bridge 不可用、超时或返回错误时抛出。"""

    pass


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

        time.sleep(0.01)
        # 给 RenderDoc 扩展一点 flush 时间，避免刚创建文件就读到半截 JSON。
        with open(RESPONSE_FILE, "r", encoding="utf-8") as f:
            response = json.load(f)
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
