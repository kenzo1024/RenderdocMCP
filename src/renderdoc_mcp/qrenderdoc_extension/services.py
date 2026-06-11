"""qrenderdoc services used by renderdoc-mcp bridge."""

import os
import sys

import renderdoc as rd


class BridgeServices:
    """Small facade around qrenderdoc CaptureContext."""

    def __init__(self, ctx):
        self.ctx = ctx

    def get_capture_status(self, params=None):
        if not self.ctx.IsCaptureLoaded():
            return {"loaded": False}

        result = {"loaded": True, "filename": None, "api": None}
        try:
            result["filename"] = self.ctx.GetCaptureFilename()
        except Exception:
            pass

        def callback(controller):
            try:
                result["api"] = str(controller.GetAPIProperties().pipelineType)
            except Exception:
                pass

        self._invoke(callback)
        return result

    def open_capture(self, params):
        capture_path = params.get("capture_path") or params.get("filepath")
        if not capture_path:
            raise ValueError("capture_path is required")
        if not os.path.isfile(capture_path):
            raise ValueError("Capture file not found: %s" % capture_path)
        if not capture_path.lower().endswith(".rdc"):
            raise ValueError("Invalid capture file type: %s" % capture_path)

        self.ctx.LoadCapture(capture_path, rd.ReplayOptions(), capture_path, False, True)
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("Failed to load capture: %s" % capture_path)

        status = self.get_capture_status()
        return {
            "filepath": capture_path,
            "source": "qrenderdoc_bridge",
            "api": status.get("api"),
            "loaded": True,
        }

    def export_draw_bundle(self, params):
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        _ensure_project_src()
        from renderdoc_mcp.exporter import export_draw_bundle

        event_id = params.get("event_id")
        output_dir = params.get("output_dir")
        if event_id is None:
            raise ValueError("event_id is required")
        if not output_dir:
            raise ValueError("output_dir is required")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                session = BridgeSession(controller)
                result["data"] = export_draw_bundle(
                    session,
                    int(event_id),
                    output_dir,
                    prefix=params.get("prefix", "skin"),
                    mesh_format=params.get("mesh_format", "json"),
                    texture_file_type=params.get("texture_file_type", "png"),
                    texture_stages=params.get("texture_stages"),
                    include_render_targets=params.get("include_render_targets", True),
                    skip_small_textures=params.get("skip_small_textures", True),
                    save_depth=params.get("save_depth", False),
                    max_vertices=params.get("max_vertices", 0),
                )
            except Exception as exc:
                import traceback

                result["error"] = "%s\n%s" % (str(exc), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def _invoke(self, callback):
        self.ctx.Replay().BlockInvoke(callback)


class BridgeSession:
    """Adapter with the subset of RenderDocSession used by exporter.py."""

    def __init__(self, controller):
        self._controller = controller
        self._structured_file = controller.GetStructuredFile()
        self._actions = {}
        self._textures = {}
        self._resources = {}
        self._current_event = None
        self._rebuild_indexes()

    @property
    def controller(self):
        return self._controller

    @property
    def structured_file(self):
        return self._structured_file

    def set_event(self, event_id):
        if event_id not in self._actions:
            return {"error": "Event ID %s not found" % event_id, "code": "INVALID_EVENT_ID"}
        self._controller.SetFrameEvent(event_id, True)
        self._current_event = event_id
        return None

    def get_action(self, event_id):
        return self._actions.get(event_id)

    def get_texture(self, resource_id):
        return self._textures.get(resource_id)

    def _rebuild_indexes(self):
        self._index_actions(self._controller.GetRootActions())
        for tex in self._controller.GetTextures():
            key = str(tex.resourceId)
            self._textures[key] = tex
            self._resources[key] = tex.resourceId
        for buf in self._controller.GetBuffers():
            self._resources[str(buf.resourceId)] = buf.resourceId
        for res in self._controller.GetResources():
            self._resources.setdefault(str(res.resourceId), res.resourceId)

    def _index_actions(self, actions):
        for action in actions:
            self._actions[action.eventId] = action
            if action.children:
                self._index_actions(action.children)


def _ensure_project_src():
    src = os.environ.get("RENDERDOC_MCP_SRC", r"D:\_Proj\renderdoc-mcp\src")
    if src and os.path.isdir(src) and src not in sys.path:
        sys.path.insert(0, src)
