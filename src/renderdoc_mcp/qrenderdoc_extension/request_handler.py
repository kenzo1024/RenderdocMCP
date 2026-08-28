"""Request routing for the qrenderdoc bridge."""

import traceback

from .services import BridgeServices
from .activity import ActivityStore


class RequestHandler:
    """Routes IPC requests to qrenderdoc-backed services."""

    def __init__(self, ctx):
        self.activity = ActivityStore()
        self.services = BridgeServices(ctx, self.activity)
        self._methods = {
            "ping": self._ping,
            "get_capture_status": self.services.get_capture_status,
            "open_capture": self.services.open_capture,
            "open_capture_background": self.services.open_capture_background,
            "open_capture_at_event": self.services.open_capture_at_event,
            "focus_event": self.services.focus_event,
            "export_draw_bundle": self.services.export_draw_bundle,
            "export_resource_asset": self.services.export_resource_asset,
            "export_shader_material": self.services.export_shader_material,
            "export_and_apply_pixel_shader": self.services.export_and_apply_pixel_shader,
            "get_pixel_shader_replacement_status": self.services.get_pixel_shader_replacement_status,
            "reset_pixel_shader": self.services.reset_pixel_shader,
            "validate_pixel_shader": self.services.validate_pixel_shader,
            "validate_vertex_shader": self.services.validate_vertex_shader,
            "validate_pixel_shader_trace": self.services.validate_pixel_shader_trace,
            "record_activity": self.services.record_activity,
            "show_activity_log": self.services.show_activity_log,
        }

    def handle(self, request):
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        if method == "record_activity":
            try:
                return {"id": request_id, "result": self.services.record_activity(params)}
            except Exception as exc:
                return self._error(request_id, -32000, str(exc))

        activity_id = self.activity.begin(method, params) if method != "ping" else None
        try:
            if method not in self._methods:
                message = "Method not found: %s" % method
                if activity_id:
                    self.activity.finish(activity_id, error=message)
                return self._error(request_id, -32601, message)
            result = self._methods[method](params)
            if activity_id:
                self.activity.finish(activity_id, result=result)
            return {"id": request_id, "result": result}
        except Exception as exc:
            traceback.print_exc()
            if activity_id:
                self.activity.finish(activity_id, error=exc)
            return self._error(request_id, -32000, str(exc))

    def _ping(self, params):
        return {"status": "ok", "bridge": "renderdoc-mcp"}

    def _error(self, request_id, code, message):
        return {"id": request_id, "error": {"code": code, "message": message}}
