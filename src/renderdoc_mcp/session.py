"""RenderDoc 捕获会话。

这个类只管理一件事：打开一个 rdc，并缓存常用索引。
导出逻辑不放在这里，避免 session 变成大杂烩。
"""

import os
from typing import Any

from renderdoc_mcp.renderdoc_api import error, rd


class RenderDocSession:
    """一个进程内只维护一个 ReplayController。

    RenderDoc 的 ReplayController 是后续所有操作的入口：切 EID、取 pipeline、
    读 buffer、保存纹理都靠它。
    """

    def __init__(self) -> None:
        self._initialized = False
        self._cap = None
        self._controller = None
        self._filepath = None
        self._structured_file = None
        self._current_event = None
        self._actions = {}
        self._resources = {}
        self._textures = {}

    @property
    def controller(self):
        return self._controller

    @property
    def structured_file(self):
        return self._structured_file

    @property
    def current_event(self):
        return self._current_event

    @property
    def is_open(self) -> bool:
        return self._controller is not None

    def require_open(self):
        """工具函数的前置检查：没有打开 rdc 时直接返回统一错误。"""
        if self.is_open:
            return None
        return error("No capture is open. Call open_capture first.", "NO_CAPTURE_OPEN")

    def open(self, filepath: str) -> dict:
        """打开 rdc，并建立 action/resource/texture 索引。"""
        filepath = os.path.normpath(filepath)
        if not os.path.isfile(filepath):
            return error(f"Capture file not found: {filepath}", "FILE_NOT_FOUND")

        self._init_replay()
        if self.is_open:
            # 一次只处理一个捕获，换文件前先释放旧 ReplayController。
            self.close()

        cap = rd.OpenCaptureFile()
        result = cap.OpenFile(filepath, "", None)
        if result != rd.ResultCode.Succeeded:
            cap.Shutdown()
            return error(f"Failed to open capture: {result}")

        if not cap.LocalReplaySupport():
            cap.Shutdown()
            return error("Capture cannot be replayed on this machine")

        result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if result != rd.ResultCode.Succeeded:
            cap.Shutdown()
            return error(f"Failed to create replay controller: {result}")

        self._cap = cap
        self._controller = controller
        self._filepath = filepath
        self._structured_file = controller.GetStructuredFile()
        self._current_event = None
        self._rebuild_indexes()

        return {
            "filepath": filepath,
            "api": cap.DriverName(),
            "actions": len(self._actions),
            "textures": len(self._textures),
            "resources": len(self._resources),
        }

    def close(self) -> dict:
        """释放 RenderDoc replay 资源，避免文件句柄和 GPU replay 状态残留。"""
        if not self.is_open:
            return {"status": "no capture open"}

        filepath = self._filepath
        self._controller.Shutdown()
        self._cap.Shutdown()
        self._cap = None
        self._controller = None
        self._filepath = None
        self._structured_file = None
        self._current_event = None
        self._actions.clear()
        self._resources.clear()
        self._textures.clear()
        return {"status": "closed", "filepath": filepath}

    def shutdown(self) -> None:
        """进程退出时调用，关闭 capture 并 ShutdownReplay。"""
        if self.is_open:
            self.close()
        if self._initialized:
            rd.ShutdownReplay()
            self._initialized = False

    def set_event(self, event_id):
        """切到指定 EID。

        RenderDoc 的 pipeline、纹理绑定、PostVS 数据都依赖当前 frame event。
        """
        if event_id not in self._actions:
            return error(f"Event ID {event_id} not found", "INVALID_EVENT_ID")
        self._controller.SetFrameEvent(event_id, True)
        self._current_event = event_id
        return None

    def get_action(self, event_id: int):
        return self._actions.get(event_id)

    def resolve_resource_id(self, resource_id: str):
        return self._resources.get(resource_id)

    def get_texture(self, resource_id: str):
        return self._textures.get(resource_id)

    def _init_replay(self) -> None:
        """RenderDoc replay 全局初始化，只做一次。"""
        if not self._initialized:
            rd.InitialiseReplay(rd.GlobalEnvironment(), [])
            self._initialized = True

    def _rebuild_indexes(self) -> None:
        """把 RenderDoc 的对象按 resourceId/eventId 缓存起来。

        导出纹理时需要从绑定里的 resourceId 快速找到 TextureDescription。
        """
        self._actions.clear()
        self._resources.clear()
        self._textures.clear()

        self._index_actions(self._controller.GetRootActions())
        for tex in self._controller.GetTextures():
            key = str(tex.resourceId)
            self._textures[key] = tex
            self._resources[key] = tex.resourceId
        for buf in self._controller.GetBuffers():
            self._resources[str(buf.resourceId)] = buf.resourceId
        for res in self._controller.GetResources():
            self._resources.setdefault(str(res.resourceId), res.resourceId)

    def _index_actions(self, actions) -> None:
        """递归展开 action tree，方便按 event_id 直接查 draw call。"""
        for action in actions:
            self._actions[action.eventId] = action
            if action.children:
                self._index_actions(action.children)


_session = None


def get_session() -> RenderDocSession:
    """MCP server 生命周期内共享同一个 session。"""
    global _session
    if _session is None:
        _session = RenderDocSession()
    return _session
