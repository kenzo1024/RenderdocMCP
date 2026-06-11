"""Backend selection for renderdoc-mcp.

The default backend is the headless Python worker. Some captures fail to open
through renderdoc.pyd but load correctly in qrenderdoc, so we keep a small
fallback backend that talks to the bundled qrenderdoc bridge extension.
"""

from renderdoc_mcp import gui_bridge
from renderdoc_mcp.renderdoc_api import error
from renderdoc_mcp.worker_client import get_worker_client

_backend = "headless"


def current_backend():
    return _backend


def open_capture(filepath):
    """Open capture in headless worker, then fallback to qrenderdoc bridge."""
    global _backend
    headless = get_worker_client().call("open_capture", {"filepath": filepath})
    if "error" not in headless:
        _backend = "headless"
        return headless

    try:
        bridge = gui_bridge.open_capture(filepath)
    except gui_bridge.GUIBridgeError as exc:
        failure = error(str(exc), "QRENDERDOC_BRIDGE_ERROR")
        failure["headless_error"] = headless
        return failure

    _backend = "qrenderdoc_bridge"
    if isinstance(bridge, dict):
        bridge.setdefault("source", "qrenderdoc_bridge")
        bridge["headless_error"] = headless
    return bridge


def close_capture():
    global _backend
    if _backend == "qrenderdoc_bridge":
        _backend = "headless"
        return {"status": "qrenderdoc bridge capture remains managed by qrenderdoc"}
    return get_worker_client().call("close_capture")


def export_draw_bundle(params):
    if _backend == "qrenderdoc_bridge":
        try:
            return gui_bridge.export_draw_bundle(**params)
        except gui_bridge.GUIBridgeError as exc:
            return error(str(exc), "QRENDERDOC_BRIDGE_ERROR")
    return get_worker_client().call("export_draw_bundle", params)


def export_mesh_stage_data(params):
    if _backend == "qrenderdoc_bridge":
        return error(
            "qrenderdoc bridge currently supports export_draw_bundle only.",
            "BRIDGE_UNSUPPORTED_TOOL",
        )
    return get_worker_client().call("export_mesh_stage_data", params)


def export_event_textures(params):
    if _backend == "qrenderdoc_bridge":
        return error(
            "qrenderdoc bridge currently supports export_draw_bundle only.",
            "BRIDGE_UNSUPPORTED_TOOL",
        )
    return get_worker_client().call("export_event_textures", params)


def shutdown():
    get_worker_client().shutdown()
