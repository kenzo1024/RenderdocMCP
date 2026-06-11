"""RenderDoc API 加载和少量公共工具。

这个文件只做“底座”：
- 惰性加载 RenderDoc 的 Python 模块，避免 MCP 启动时就因为 ABI 不匹配崩掉。
- 提供 shader stage、文件格式、mesh stage 的名字到 RenderDoc 枚举的转换。
- 提供统一 JSON、错误结构、文件名清洗、纹理描述序列化。
"""

import json
import os
import sys
from typing import Any


def load_renderdoc():
    """加载 RenderDoc 的 Python 模块。

    RenderDoc 的 renderdoc.pyd 对 Python 版本很敏感，比如 RenderDoc 自带
    Python 3.6 扩展时，就不能用 Python 3.10 直接 import。这里把错误包装成
    RuntimeError，让 MCP 工具可以返回清楚的错误信息。
    """
    if "renderdoc" in sys.modules:
        return sys.modules["renderdoc"]

    module_path = os.environ.get("RENDERDOC_MODULE_PATH", "")
    if module_path:
        # pyd 所在目录需要进入 sys.path；Windows DLL 搜索还需要 add_dll_directory。
        if module_path not in sys.path:
            sys.path.insert(0, module_path)
        if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
            os.add_dll_directory(module_path)
            parent = os.path.dirname(module_path)
            if parent:
                os.add_dll_directory(parent)

    try:
        import renderdoc  # noqa: PLC0415
    except Exception as exc:
        raise RuntimeError(
            "Cannot load RenderDoc Python module. Set RENDERDOC_MODULE_PATH and "
            "make sure Python ABI matches RenderDoc's renderdoc.pyd."
        ) from exc

    return renderdoc


class LazyRenderDoc:
    """RenderDoc 模块代理。

    用 `rd.SomeApi` 的写法访问时才真正 import renderdoc。这样 server.py 可以
    在普通 Python 环境中被导入，只有打开 rdc/导出资源时才要求 RenderDoc 环境正确。
    """

    def __init__(self) -> None:
        self._module = None

    def module(self):
        if self._module is None:
            self._module = load_renderdoc()
        return self._module

    def __getattr__(self, name):
        return getattr(self.module(), name)


rd = LazyRenderDoc()


def shader_stages():
    """把 MCP 参数里的 stage 字符串转成 RenderDoc ShaderStage。"""
    return {
        "vertex": rd.ShaderStage.Vertex,
        "hull": rd.ShaderStage.Hull,
        "domain": rd.ShaderStage.Domain,
        "geometry": rd.ShaderStage.Geometry,
        "pixel": rd.ShaderStage.Pixel,
        "compute": rd.ShaderStage.Compute,
    }


def file_types():
    """把输出文件扩展名转成 RenderDoc SaveTexture 需要的 FileType。"""
    return {
        "png": rd.FileType.PNG,
        "jpg": rd.FileType.JPG,
        "bmp": rd.FileType.BMP,
        "tga": rd.FileType.TGA,
        "hdr": rd.FileType.HDR,
        "exr": rd.FileType.EXR,
        "dds": rd.FileType.DDS,
    }


def mesh_stages():
    """把 mesh stage 名字转成 RenderDoc MeshDataStage。"""
    return {
        "vsin": rd.MeshDataStage.VSIn,
        "vsout": rd.MeshDataStage.VSOut,
        "gsout": rd.MeshDataStage.GSOut,
        "taskout": rd.MeshDataStage.TaskOut,
        "meshout": rd.MeshDataStage.MeshOut,
    }

VS_INPUT_STAGE = "vsin"


def error(message, code="API_ERROR"):
    """所有 MCP 工具统一返回这个错误形状，方便调用侧判断。"""
    return {"error": message, "code": code}


def to_json(value):
    """FastMCP 工具返回字符串，所以统一压成 JSON 字符串。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def safe_filename(value):
    """把资源名转成 Windows 可落盘的文件名。"""
    bad_chars = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in bad_chars else ch for ch in value)
    return cleaned.strip().strip(".") or "unnamed"


def texture_desc_to_dict(tex):
    """把 RenderDoc TextureDescription 变成 manifest 里可读的普通 dict。"""
    return {
        "resource_id": str(tex.resourceId),
        "width": tex.width,
        "height": tex.height,
        "depth": tex.depth,
        "array_size": tex.arraysize,
        "mips": tex.mips,
        "format": str(tex.format.Name()),
        "ms_samples": tex.msSamp,
        "byte_size": getattr(tex, "byteSize", None),
    }
