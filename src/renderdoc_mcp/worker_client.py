"""Client for the Python 3.6 RenderDoc worker process."""

import atexit
import json
import os
import subprocess
import sys
import uuid

from renderdoc_mcp.renderdoc_api import error


class WorkerClient:
    """Keeps one RenderDoc worker alive so capture state survives MCP calls."""

    def __init__(self):
        self._process = None

    def call(self, method, params=None):
        process = self._ensure_process()
        request_id = str(uuid.uuid4())
        request = {"id": request_id, "method": method, "params": params or {}}

        try:
            assert process.stdin is not None
            assert process.stdout is not None
            process.stdin.write(json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n")
            process.stdin.flush()
            line = process.stdout.readline()
        except Exception as exc:
            self.shutdown()
            return error(f"RenderDoc worker communication failed: {exc}", "WORKER_IO_ERROR")

        if not line:
            stderr = self._read_stderr(process)
            self.shutdown()
            message = "RenderDoc worker exited unexpectedly"
            if stderr:
                message = f"{message}: {stderr}"
            return error(message, "WORKER_EXITED")

        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            return error(f"Invalid RenderDoc worker response: {exc}", "WORKER_BAD_RESPONSE")

        if response.get("id") not in {request_id, None}:
            return error("RenderDoc worker response id mismatch", "WORKER_BAD_RESPONSE")
        result = response.get("result")
        if isinstance(result, dict):
            return result
        return {"result": result}

    def shutdown(self):
        process = self._process
        self._process = None
        if process is None:
            return

        if process.poll() is None:
            try:
                assert process.stdin is not None
                process.stdin.write(json.dumps({"id": "shutdown", "method": "shutdown", "params": {}}, separators=(",", ":")) + "\n")
                process.stdin.flush()
            except Exception:
                pass
        if process.poll() is None:
            process.terminate()

    def _ensure_process(self):
        if self._process is not None and self._process.poll() is None:
            return self._process

        python36 = _python36_executable()
        env = os.environ.copy()
        src_dir = _src_dir()
        env["PYTHONPATH"] = _prepend_path(env.get("PYTHONPATH", ""), src_dir)
        env["PYTHONIOENCODING"] = "utf-8"

        renderdoc_modules = env.get("RENDERDOC_MODULE_PATH") or r"C:\Program Files\RenderDoc\pymodules"
        env["RENDERDOC_MODULE_PATH"] = renderdoc_modules
        renderdoc_root = os.path.dirname(renderdoc_modules)
        env["PATH"] = _prepend_path(env.get("PATH", ""), renderdoc_modules, renderdoc_root)

        command = [python36, "-m", "renderdoc_mcp.worker"]
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=os.getcwd(),
        )
        return self._process

    @staticmethod
    def _read_stderr(process):
        return _read_stderr(process)


def _python36_executable():
    configured = os.environ.get("RENDERDOC_MCP_PYTHON36")
    if configured:
        return configured

    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python36\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\py.exe"),
        "py",
    ]
    for candidate in candidates:
        if candidate == "py":
            return candidate
        if os.path.isfile(candidate):
            return candidate
    return sys.executable


def _src_dir():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _prepend_path(value, *items):
    existing = [part for part in value.split(os.pathsep) if part]
    prefix = [item for item in items if item and item not in existing]
    return os.pathsep.join(prefix + existing)


def _read_stderr(process):
    stderr = process.stderr
    if stderr is None:
        return ""
    try:
        return stderr.read().strip()
    except Exception:
        return ""


_client = None


def get_worker_client():
    global _client
    if _client is None:
        _client = WorkerClient()
        atexit.register(_client.shutdown)
    return _client
