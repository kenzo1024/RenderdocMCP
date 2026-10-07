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


def open_capture_background(filepath):
    """Load a capture through qrenderdoc without showing or focusing its UI."""
    global _backend
    try:
        result = gui_bridge.open_capture_background(filepath)
    except gui_bridge.GUIBridgeError as exc:
        return error(str(exc), "QRENDERDOC_BRIDGE_ERROR")

    _backend = "qrenderdoc_bridge"
    if isinstance(result, dict):
        result.setdefault("source", "qrenderdoc_bridge_background")
        result["background"] = True
    return result


def open_capture_foreground(filepath):
    """Open a capture through the qrenderdoc GUI bridge for manual inspection."""
    global _backend
    try:
        result = gui_bridge.open_capture(filepath)
    except gui_bridge.GUIBridgeError as exc:
        return error(str(exc), "QRENDERDOC_BRIDGE_ERROR")

    _backend = "qrenderdoc_bridge"
    if isinstance(result, dict):
        result.setdefault("source", "qrenderdoc_bridge")
        result["background"] = False
    return result


def open_capture_at_event(filepath, event_id):
    """Open a capture in qrenderdoc and keep later exports on the GUI backend."""
    global _backend
    try:
        result = gui_bridge.open_capture_at_event(filepath, event_id)
    except gui_bridge.GUIBridgeError as exc:
        return error(str(exc), "QRENDERDOC_BRIDGE_ERROR")

    _backend = "qrenderdoc_bridge"
    if isinstance(result, dict):
        result.setdefault("source", "qrenderdoc_bridge")
    return result


def focus_event(event_id):
    """Focus the GUI capture and keep later exports on the GUI backend."""
    global _backend
    try:
        result = gui_bridge.focus_event(event_id)
    except gui_bridge.GUIBridgeError as exc:
        return error(str(exc), "QRENDERDOC_BRIDGE_ERROR")

    _backend = "qrenderdoc_bridge"
    return result


def capture_mesh_viewer(event_id, output_path):
    """Ask the open qrenderdoc window for a Mesh Viewer screenshot."""
    global _backend
    try:
        result = gui_bridge.capture_mesh_viewer(event_id, output_path)
    except gui_bridge.GUIBridgeError as exc:
        return error(str(exc), "QRENDERDOC_BRIDGE_ERROR")

    _backend = "qrenderdoc_bridge"
    return result


def describe_event(params):
    return get_worker_client().call("describe_event", params)


def find_draws_by_texture(params):
    return get_worker_client().call("find_draws_by_texture", params)


def export_present(params):
    if _backend == "qrenderdoc_bridge":
        try:
            return gui_bridge.export_present(**params)
        except gui_bridge.GUIBridgeError as exc:
            return error(str(exc), "QRENDERDOC_BRIDGE_ERROR")
    return get_worker_client().call("export_present", params)


def export_present_overlay(params):
    if _backend == "qrenderdoc_bridge":
        try:
            return gui_bridge.export_present_overlay(**params)
        except gui_bridge.GUIBridgeError as exc:
            return error(str(exc), "QRENDERDOC_BRIDGE_ERROR")
    return get_worker_client().call("export_present_overlay", params)


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


def find_drawcalls_by_reference(params):
    if _backend == "qrenderdoc_bridge":
        try:
            return gui_bridge.find_drawcalls_by_reference(**params)
        except gui_bridge.GUIBridgeError as exc:
            return error(str(exc), "QRENDERDOC_BRIDGE_ERROR")
    return get_worker_client().call("find_drawcalls_by_reference", params)


def export_shader_material(params):
    if _backend == "qrenderdoc_bridge":
        try:
            return gui_bridge.export_shader_material(**params)
        except gui_bridge.GUIBridgeError as exc:
            return error(str(exc), "QRENDERDOC_BRIDGE_ERROR")
    return get_worker_client().call("export_shader_material", params)


def export_resource_asset(params):
    if _backend == "qrenderdoc_bridge":
        try:
            return gui_bridge.export_resource_asset(**params)
        except gui_bridge.GUIBridgeError as exc:
            return error(str(exc), "QRENDERDOC_BRIDGE_ERROR")
    return get_worker_client().call("export_resource_asset", params)


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
