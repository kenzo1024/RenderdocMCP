"""File-based IPC server used inside qrenderdoc."""

import json
import os
import re
import tempfile
import traceback

IPC_NAMESPACE_ENV = "RENDERDOC_MCP_IPC_NAMESPACE"


def _ipc_dir():
    root = os.path.join(tempfile.gettempdir(), "renderdoc_mcp")
    namespace = os.environ.get(IPC_NAMESPACE_ENV, "").strip()
    if not namespace:
        return root
    safe_namespace = re.sub(r"[^A-Za-z0-9_.-]+", "_", namespace).strip("._")
    return os.path.join(root, safe_namespace or "default")


IPC_DIR = _ipc_dir()
REQUEST_FILE = os.path.join(IPC_DIR, "request.json")
RESPONSE_FILE = os.path.join(IPC_DIR, "response.json")
RESPONSE_TEMP_FILE = os.path.join(IPC_DIR, "response.tmp.json")
LOCK_FILE = os.path.join(IPC_DIR, "lock")


class BridgeServer:
    """Polls request.json and writes response.json."""

    def __init__(self, handler, ctx):
        self.handler = handler
        self.ctx = ctx
        self._running = False
        if not os.path.isdir(IPC_DIR):
            os.makedirs(IPC_DIR)

    def start(self):
        self._running = True
        self._cleanup_files()
        self._schedule_next()
        print("[renderdoc-mcp] bridge IPC: %s" % IPC_DIR)

    def stop(self):
        self._running = False
        self._cleanup_files()

    def _poll_request(self):
        if not self._running:
            return

        try:
            if not os.path.exists(REQUEST_FILE) or os.path.exists(LOCK_FILE):
                return
            with open(REQUEST_FILE, "r", encoding="utf-8") as f:
                request = json.load(f)
            try:
                os.remove(REQUEST_FILE)
            except FileNotFoundError:
                return
            response = self.handler.handle(request)
            self._write_response(response)
        except Exception as exc:
            traceback.print_exc()
            try:
                response = {
                    "id": None,
                    "error": {"code": -32603, "message": str(exc)},
                }
                self._write_response(response)
            except Exception:
                pass
        finally:
            if self._running:
                self._schedule_next()

    def _schedule_next(self):
        self.ctx.DelayedCallback(100, self._poll_request)

    def _cleanup_files(self):
        for path in (REQUEST_FILE, RESPONSE_FILE, RESPONSE_TEMP_FILE, LOCK_FILE):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

    def _write_response(self, response):
        with open(RESPONSE_TEMP_FILE, "w", encoding="utf-8") as response_file:
            json.dump(response, response_file, ensure_ascii=False, separators=(",", ":"))
            response_file.flush()
            os.fsync(response_file.fileno())
        os.replace(RESPONSE_TEMP_FILE, RESPONSE_FILE)
