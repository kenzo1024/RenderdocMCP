"""File-based IPC server used inside qrenderdoc."""

import json
import os
import tempfile
import traceback

from PySide2.QtCore import QObject, QTimer

IPC_DIR = os.path.join(tempfile.gettempdir(), "renderdoc_mcp")
REQUEST_FILE = os.path.join(IPC_DIR, "request.json")
RESPONSE_FILE = os.path.join(IPC_DIR, "response.json")
LOCK_FILE = os.path.join(IPC_DIR, "lock")


class BridgeServer(QObject):
    """Polls request.json and writes response.json."""

    def __init__(self, handler, parent=None):
        super(BridgeServer, self).__init__(parent)
        self.handler = handler
        self._timer = None
        self._running = False
        if not os.path.isdir(IPC_DIR):
            os.makedirs(IPC_DIR)

    def start(self):
        self._running = True
        self._cleanup_files()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_request)
        self._timer.start(100)
        print("[renderdoc-mcp] bridge IPC: %s" % IPC_DIR)

    def stop(self):
        self._running = False
        if self._timer:
            self._timer.stop()
            self._timer = None
        self._cleanup_files()

    def _poll_request(self):
        if not self._running:
            return
        if not os.path.exists(REQUEST_FILE) or os.path.exists(LOCK_FILE):
            return

        try:
            with open(REQUEST_FILE, "r", encoding="utf-8") as f:
                request = json.load(f)
            os.remove(REQUEST_FILE)
            response = self.handler.handle(request)
            with open(RESPONSE_FILE, "w", encoding="utf-8") as f:
                json.dump(response, f, ensure_ascii=False, separators=(",", ":"))
        except Exception as exc:
            traceback.print_exc()
            try:
                response = {
                    "id": None,
                    "error": {"code": -32603, "message": str(exc)},
                }
                with open(RESPONSE_FILE, "w", encoding="utf-8") as f:
                    json.dump(response, f, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                pass

    def _cleanup_files(self):
        for path in (REQUEST_FILE, RESPONSE_FILE, LOCK_FILE):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
