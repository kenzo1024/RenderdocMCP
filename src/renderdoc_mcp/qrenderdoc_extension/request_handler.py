"""Request routing for the qrenderdoc bridge."""

import traceback

from .services import BridgeServices


class RequestHandler:
    """Routes IPC requests to qrenderdoc-backed services."""

    def __init__(self, ctx):
        self.services = BridgeServices(ctx)
        self._methods = {
            "ping": self._ping,
            "get_capture_status": self.services.get_capture_status,
            "open_capture": self.services.open_capture,
            "export_draw_bundle": self.services.export_draw_bundle,
        }

    def handle(self, request):
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        try:
            if method not in self._methods:
                return self._error(request_id, -32601, "Method not found: %s" % method)
            return {"id": request_id, "result": self._methods[method](params)}
        except Exception as exc:
            traceback.print_exc()
            return self._error(request_id, -32000, str(exc))

    def _ping(self, params):
        return {"status": "ok", "bridge": "renderdoc-mcp"}

    def _error(self, request_id, code, message):
        return {"id": request_id, "error": {"code": code, "message": message}}
