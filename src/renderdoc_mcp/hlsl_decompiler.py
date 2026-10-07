"""Decompile DXBC to HLSL with 3Dmigoto's cmd_Decompiler.

RenderDoc can be configured to call the same executable from
Shader Viewer -> Custom Tool. This module calls it directly, so MCP does not
depend on that UI setting.
"""

import os
import shutil
import subprocess

DEFAULT_EXE = r"C:\Program Files\RenderDoc\cmd_Decompiler.exe"


def decompiler_executable():
    configured = os.environ.get("RENDERDOC_HLSL_DECOMPILER", "").strip()
    return configured or DEFAULT_EXE


def decompile_dxbc(dxbc_path, output_hlsl=None, timeout=180):
    """Run cmd_Decompiler.exe -D and return the HLSL path."""
    exe = decompiler_executable()
    if not os.path.isfile(exe):
        raise FileNotFoundError(
            "3Dmigoto decompiler not found: %s. Set RENDERDOC_HLSL_DECOMPILER."
            % exe
        )
    dxbc_path = os.path.abspath(dxbc_path)
    if not os.path.isfile(dxbc_path):
        raise FileNotFoundError("DXBC not found: %s" % dxbc_path)

    produced = os.path.splitext(dxbc_path)[0] + ".hlsl"
    if os.path.exists(produced):
        os.remove(produced)

    completed = subprocess.run(
        [exe, "-D", dxbc_path],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=os.path.dirname(dxbc_path),
    )
    if not os.path.isfile(produced):
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            "cmd_Decompiler produced no HLSL (exit %s): %s"
            % (completed.returncode, detail[:2000])
        )

    destination = os.path.abspath(output_hlsl) if output_hlsl else produced
    if os.path.normcase(destination) != os.path.normcase(produced):
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copyfile(produced, destination)
    with open(destination, "r", encoding="utf-8", errors="replace") as handle:
        header = handle.readline().strip()
    return {
        "hlsl_path": destination,
        "dxbc_path": dxbc_path,
        "decompiler": exe,
        "exit_code": completed.returncode,
        "header": header,
        "byte_size": os.path.getsize(destination),
    }


def decompile_event_pixel_shader(event_id, output_dir, prefix="ps"):
    """Export the event's pixel DXBC through the GUI bridge, then decompile it."""
    from renderdoc_mcp import gui_bridge

    os.makedirs(output_dir, exist_ok=True)
    summary = gui_bridge.export_shader_material(
        int(event_id),
        output_dir,
        prefix=prefix,
        include_textures=False,
        include_mesh=False,
    )
    if isinstance(summary, dict) and summary.get("error"):
        raise RuntimeError(str(summary["error"]))
    pixel = (summary.get("stages") or {}).get("pixel") or {}
    dxbc = (pixel.get("dxbc") or {}).get("path")
    if not dxbc:
        raise RuntimeError("Pixel DXBC was not exported for EID %s" % event_id)
    result = decompile_dxbc(dxbc)
    result["event_id"] = int(event_id)
    result["dxbc"] = pixel.get("dxbc")
    return result
