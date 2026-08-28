"""qrenderdoc extension for renderdoc-mcp.

This extension runs inside qrenderdoc and exposes a tiny file-based IPC bridge
for cases where the headless renderdoc Python API cannot open a capture.
"""

from .bridge_server import BridgeServer
from .request_handler import RequestHandler

_server = None


def register(version, ctx):
    global _server
    handler = RequestHandler(ctx)
    _server = BridgeServer(handler, ctx)
    _server.start()
    _register_export_panel(ctx)
    _register_activity_panel(ctx, handler)
    print("[renderdoc-mcp] bridge loaded for RenderDoc %s" % version)


def unregister():
    global _server
    if _server:
        _server.stop()
        _server = None
    print("[renderdoc-mcp] bridge unloaded")


def _register_export_panel(ctx):
    try:
        import qrenderdoc as qrd

        from .export_panel import show_export_panel

        ctx.Extensions().RegisterPanelMenu(
            qrd.PanelMenu.MeshPreview,
            ["RenderDoc MCP", "Export Resource Asset"],
            lambda pyrenderdoc, data: show_export_panel(pyrenderdoc),
        )
    except Exception as exc:
        print("[renderdoc-mcp] export panel registration failed: %s" % exc)


def _register_activity_panel(ctx, handler):
    try:
        import qrenderdoc as qrd

        from .activity_panel import show_activity_panel

        ctx.Extensions().RegisterWindowMenu(
            qrd.WindowMenu.Tools,
            ["RenderDoc MCP", "Activity Log"],
            lambda pyrenderdoc, data: show_activity_panel(pyrenderdoc, handler.activity),
        )
    except Exception as exc:
        print("[renderdoc-mcp] activity panel registration failed: %s" % exc)
