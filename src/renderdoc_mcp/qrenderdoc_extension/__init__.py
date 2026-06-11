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
    _server = BridgeServer(handler)
    _server.start()
    print("[renderdoc-mcp] bridge loaded for RenderDoc %s" % version)


def unregister():
    global _server
    if _server:
        _server.stop()
        _server = None
    print("[renderdoc-mcp] bridge unloaded")
