"""In-memory activity history for the qrenderdoc bridge."""

import copy
import os
import threading
import time
import uuid
from datetime import datetime


class ActivityStore:
    """Keep a bounded, UI-friendly history of bridge operations."""

    def __init__(self, max_entries=200):
        self.max_entries = max(1, int(max_entries))
        self._entries = []
        self._active = {}
        self._listeners = []
        self._lock = threading.RLock()

    def begin(self, operation, params=None):
        params = params or {}
        entry = {
            "id": str(uuid.uuid4()),
            "timestamp": _timestamp(),
            "operation": str(operation),
            "status": "running",
            "filepath": _capture_name(params.get("capture_path") or params.get("filepath")),
            "event_id": _event_id(params.get("event_id")),
            "message": "Running",
            "duration_ms": None,
            "details": {},
            "_started": time.perf_counter(),
        }
        with self._lock:
            self._entries.append(entry)
            self._active[entry["id"]] = entry
            self._trim()
        self._notify()
        return entry["id"]

    def finish(self, entry_id, result=None, error=None):
        with self._lock:
            entry = self._active.pop(entry_id, None)
            if entry is None:
                return None
            entry["duration_ms"] = int((time.perf_counter() - entry["_started"]) * 1000)
            entry.pop("_started", None)
            if error:
                entry["status"] = "error"
                entry["message"] = _short_text(error)
                entry["details"] = {"error": _short_text(error, 2000)}
            elif isinstance(result, dict) and result.get("error"):
                entry["status"] = "error"
                entry["message"] = _short_text(result.get("error"))
                entry["details"] = _summarize(result)
            else:
                entry["status"] = "success"
                entry["message"] = _success_message(entry["operation"], result)
                entry["details"] = _summarize(result)
            self._trim()
            result = copy.deepcopy(entry)
        self._notify()
        return result

    def record_external(self, operation, status, message, params=None, details=None):
        """Record a result produced outside the bridge, e.g. MCP pixel compare."""
        params = params or {}
        entry = {
            "id": str(uuid.uuid4()),
            "timestamp": _timestamp(),
            "operation": str(operation),
            "status": str(status),
            "filepath": _capture_name(params.get("capture_path") or params.get("filepath")),
            "event_id": _event_id(params.get("event_id")),
            "message": _short_text(message),
            "duration_ms": None,
            "details": _summarize(details or {}),
        }
        with self._lock:
            self._entries.append(entry)
            self._trim()
            result = copy.deepcopy(entry)
        self._notify()
        return result

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._entries)

    def clear(self):
        with self._lock:
            self._entries[:] = []
            self._active.clear()
        self._notify()

    def add_listener(self, callback):
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback):
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _trim(self):
        overflow = len(self._entries) - self.max_entries
        if overflow > 0:
            del self._entries[:overflow]

    def _notify(self):
        with self._lock:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback()
            except Exception:
                pass


def _timestamp():
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _capture_name(value):
    if not value:
        return None
    return os.path.basename(str(value))


def _event_id(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return str(value) if value else None


def _short_text(value, limit=240):
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _success_message(operation, result):
    if operation == "validate_pixel_shader":
        if isinstance(result, dict):
            pair = result.get("comparison_pair", ["reset", "applied"])
            return "Reset/Apply snapshots complete (%s -> %s)" % (pair[0], pair[1])
        return "Validation complete"
    if operation == "open_capture_at_event":
        return "Capture opened and EID focused"
    if operation == "focus_event":
        return "EID focused"
    if operation == "export_and_apply_pixel_shader":
        return "Pixel shader applied"
    if operation == "reset_pixel_shader":
        return "Pixel shader reset"
    return "Completed"


def _summarize(value, depth=0):
    if depth > 2:
        return "..."
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"targets", "comparison", "shader", "reference_shader"}:
                result[key] = _summarize(item, depth + 1)
            elif isinstance(item, (str, int, float, bool)) or item is None:
                result[key] = _short_text(item, 500) if isinstance(item, str) else item
        return result
    if isinstance(value, list):
        return [_summarize(item, depth + 1) for item in value[:20]]
    return _short_text(value, 500)
