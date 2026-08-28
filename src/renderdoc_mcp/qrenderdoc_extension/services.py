"""qrenderdoc services used by renderdoc-mcp bridge."""

import os
import sys

import renderdoc as rd


class BridgeServices:
    """Small facade around qrenderdoc CaptureContext."""

    def __init__(self, ctx, activity=None):
        self.ctx = ctx
        self.activity = activity

    def record_activity(self, params):
        """Record an MCP-side result, such as the final image comparison."""
        if self.activity is None:
            raise ValueError("Activity log is not available")
        return self.activity.record_external(
            params.get("operation", "mcp_result"),
            params.get("status", "success"),
            params.get("message", "Completed"),
            params,
            params.get("details"),
        )

    def show_activity_log(self, params=None):
        """Show and raise the docked activity window."""
        if self.activity is None:
            raise ValueError("Activity log is not available")
        from .activity_panel import show_activity_panel

        show_activity_panel(self.ctx, self.activity)
        return {"visible": True, "entries": len(self.activity.snapshot())}

    def get_capture_status(self, params=None):
        if not self.ctx.IsCaptureLoaded():
            return {"loaded": False}

        result = {"loaded": True, "filename": None, "api": None}
        try:
            result["filename"] = self.ctx.GetCaptureFilename()
        except Exception:
            pass

        def callback(controller):
            try:
                result["api"] = str(controller.GetAPIProperties().pipelineType)
            except Exception:
                pass

        self._invoke(callback)
        return result

    def open_capture(self, params):
        capture_path = params.get("capture_path") or params.get("filepath")
        if not capture_path:
            raise ValueError("capture_path is required")
        if not os.path.isfile(capture_path):
            raise ValueError("Capture file not found: %s" % capture_path)
        if not capture_path.lower().endswith(".rdc"):
            raise ValueError("Invalid capture file type: %s" % capture_path)

        self.ctx.LoadCapture(capture_path, rd.ReplayOptions(), capture_path, False, True)
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("Failed to load capture: %s" % capture_path)

        status = self.get_capture_status()
        return {
            "filepath": capture_path,
            "source": "qrenderdoc_bridge",
            "api": status.get("api"),
            "loaded": True,
        }

    def open_capture_background(self, params):
        """Load a capture without selecting an event or activating GUI widgets."""
        result = self.open_capture(params)
        result["source"] = "qrenderdoc_bridge_background"
        result["background"] = True
        return result

    def open_capture_at_event(self, params):
        """Open a capture in qrenderdoc and select an exact event in the GUI."""
        result = self.open_capture(params)
        result.update(self.focus_event(params))
        return result

    def focus_event(self, params):
        """Select an event and make the Event Browser visible to the user."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        event_id = _parse_event_id(params.get("event_id"))
        api_event = self.ctx.GetEventBrowser().GetAPIEventForEID(event_id)
        if int(getattr(api_event, "eventId", 0)) != event_id:
            raise ValueError("Event ID %s not found" % event_id)

        self.ctx.SetEventID([], event_id, event_id, True)
        self.ctx.ShowEventBrowser()
        try:
            self.ctx.GetMainWindow().BringToFront()
        except Exception:
            # Some window managers reject programmatic activation. The EID is
            # still selected and the Event Browser is still made visible.
            pass

        result = {
            "loaded": True,
            "event_id": event_id,
            "focused": True,
        }
        try:
            result["filepath"] = self.ctx.GetCaptureFilename()
        except Exception:
            pass
        return result

    def export_draw_bundle(self, params):
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        _ensure_project_src()
        from renderdoc_mcp.exporter import export_draw_bundle

        event_id = params.get("event_id")
        output_dir = params.get("output_dir")
        if event_id is None:
            raise ValueError("event_id is required")
        if not output_dir:
            raise ValueError("output_dir is required")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                session = BridgeSession(controller)
                result["data"] = export_draw_bundle(
                    session,
                    int(event_id),
                    output_dir,
                    prefix=params.get("prefix", "skin"),
                    mesh_format=params.get("mesh_format", "json"),
                    texture_file_type=params.get("texture_file_type", "png"),
                    texture_stages=params.get("texture_stages"),
                    include_render_targets=params.get("include_render_targets", True),
                    skip_small_textures=params.get("skip_small_textures", True),
                    save_depth=params.get("save_depth", False),
                    max_vertices=params.get("max_vertices", 0),
                )
            except Exception as exc:
                import traceback

                result["error"] = "%s\n%s" % (str(exc), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def export_shader_material(self, params):
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        event_id = params.get("event_id")
        output_dir = params.get("output_dir")
        if event_id is None:
            raise ValueError("event_id is required")
        if not output_dir:
            raise ValueError("output_dir is required")

        _ensure_project_src()
        from renderdoc_mcp.shader_material import export_shader_material, material_summary

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                bundle = export_shader_material(
                    controller,
                    int(event_id),
                    output_dir,
                    prefix=params.get("prefix", "shader"),
                    include_textures=params.get("include_textures", True),
                    include_mesh=params.get("include_mesh", True),
                )
                result["data"] = material_summary(bundle)
            except Exception as exc:
                import traceback

                result["error"] = "%s\n%s" % (str(exc), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def export_resource_asset(self, params):
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        event_id = params.get("event_id")
        output_dir = params.get("output_dir")
        if event_id is None:
            raise ValueError("event_id is required")
        if not output_dir:
            raise ValueError("output_dir is required")

        _ensure_project_src()
        from renderdoc_mcp.resource_export import export_resource_asset

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                session = BridgeSession(controller)
                result["data"] = export_resource_asset(
                    session,
                    int(event_id),
                    output_dir,
                    prefix=params.get("prefix", "asset"),
                    config=params.get("config"),
                    preset_name=params.get("preset_name"),
                    preset_dir=params.get("preset_dir"),
                    texture_file_type=params.get("texture_file_type", "png"),
                    texture_stages=params.get("texture_stages"),
                    include_textures=params.get("include_textures", True),
                    include_render_targets=params.get("include_render_targets", False),
                    skip_small_textures=params.get("skip_small_textures", True),
                    save_depth=params.get("save_depth", False),
                    max_vertices=params.get("max_vertices", 0),
                )
            except Exception as exc:
                import traceback

                result["error"] = "%s\n%s" % (str(exc), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def export_and_apply_pixel_shader(self, params):
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        event_id = params.get("event_id")
        hlsl_path = params.get("hlsl_path")
        output_dir = params.get("output_dir")
        if event_id is None:
            raise ValueError("event_id is required")
        if not hlsl_path:
            raise ValueError("hlsl_path is required")
        if not output_dir:
            raise ValueError("output_dir is required")

        _ensure_project_src()
        from renderdoc_mcp.shader_roundtrip import export_pixel_shader_roundtrip

        self._reset_pixel_shader(event_id)
        result = {"data": None, "error": None, "original_id": None, "new_id": None}

        def callback(controller):
            try:
                data, original_id, new_id = export_pixel_shader_roundtrip(
                    controller,
                    int(event_id),
                    hlsl_path,
                    output_dir,
                )
                result["data"] = data
                result["original_id"] = original_id
                result["new_id"] = new_id
            except Exception as exc:
                import traceback

                result["error"] = "%s\n%s" % (str(exc), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])

        self.ctx.UnregisterReplacement(result["original_id"])
        self.ctx.RegisterReplacement(result["original_id"], result["new_id"])
        return result["data"]

    def get_pixel_shader_replacement_status(self, params):
        """Return qrenderdoc's replacement state for one event's pixel shader."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        event_id = _parse_event_id(params.get("event_id"))
        original_id = self._pixel_shader_id(event_id)
        replaced = bool(self.ctx.IsResourceReplaced(original_id))
        replacement_id = self.ctx.GetResourceReplacement(original_id) if replaced else None
        return {
            "event_id": event_id,
            "original_resource_id": str(original_id),
            "replaced": replaced,
            "replacement_resource_id": str(replacement_id) if replacement_id is not None else None,
        }

    def reset_pixel_shader(self, params):
        """Clear both qrenderdoc UI and replay replacements for one event's PS."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        event_id = _parse_event_id(params.get("event_id"))
        return self._reset_pixel_shader(event_id)

    def validate_pixel_shader(self, params):
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        event_id = params.get("event_id")
        hlsl_path = params.get("hlsl_path")
        output_dir = params.get("output_dir")
        if event_id is None:
            raise ValueError("event_id is required")
        if not hlsl_path:
            raise ValueError("hlsl_path is required")
        if not output_dir:
            raise ValueError("output_dir is required")

        _ensure_project_src()
        from renderdoc_mcp.shader_roundtrip import validate_pixel_shader_roundtrip

        event_id = int(event_id)
        self._reset_pixel_shader(event_id)
        result = {"data": None, "error": None}

        def callback(controller):
            try:
                result["data"] = validate_pixel_shader_roundtrip(
                    controller,
                    event_id,
                    hlsl_path,
                    output_dir,
                    params.get("expected_reset_raw_sha256"),
                    params.get("expected_reset_shader_sha256"),
                    params.get("reference_hlsl_path"),
                )
            except Exception as exc:
                import traceback

                result["error"] = "%s\n%s" % (str(exc), traceback.format_exc())

        validation_error = None
        try:
            self._invoke(callback)
            if result["error"]:
                raise ValueError(result["error"])
            return result["data"]
        except Exception as exc:
            validation_error = exc
            raise
        finally:
            try:
                self._reset_pixel_shader(event_id)
            except Exception:
                if validation_error is None:
                    raise

    def validate_pixel_shader_trace(self, params):
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        required = ("event_id", "hlsl_path", "output_dir", "x", "y")
        missing = [name for name in required if params.get(name) is None]
        if missing:
            raise ValueError("Missing required parameters: %s" % ", ".join(missing))

        _ensure_project_src()
        from renderdoc_mcp.shader_debug import validate_pixel_shader_trace

        event_id = int(params["event_id"])
        self._reset_pixel_shader(event_id)
        result = {"data": None, "error": None}

        def callback(controller):
            try:
                result["data"] = validate_pixel_shader_trace(
                    controller,
                    event_id,
                    params["hlsl_path"],
                    params["output_dir"],
                    int(params["x"]),
                    int(params["y"]),
                    params.get("primitive"),
                    params.get("sample"),
                    params.get("view"),
                    int(params.get("max_steps") or 0),
                )
            except Exception as exc:
                import traceback

                result["error"] = "%s\n%s" % (str(exc), traceback.format_exc())

        validation_error = None
        try:
            self._invoke(callback)
            if result["error"]:
                raise ValueError(result["error"])
            return result["data"]
        except Exception as exc:
            validation_error = exc
            raise
        finally:
            try:
                self._reset_pixel_shader(event_id)
            except Exception:
                if validation_error is None:
                    raise

    def validate_vertex_shader(self, params):
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        required = ("event_id", "hlsl_path", "output_dir")
        missing = [name for name in required if params.get(name) is None]
        if missing:
            raise ValueError("Missing required parameters: %s" % ", ".join(missing))

        _ensure_project_src()
        from renderdoc_mcp.shader_roundtrip import validate_vertex_shader_roundtrip

        event_id = int(params["event_id"])
        result = {"data": None, "error": None}

        def callback(controller):
            try:
                result["data"] = validate_vertex_shader_roundtrip(
                    controller,
                    event_id,
                    params["hlsl_path"],
                    params["output_dir"],
                    params.get("reference_hlsl_path"),
                )
            except Exception as exc:
                import traceback

                result["error"] = "%s\n%s" % (str(exc), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def _pixel_shader_id(self, event_id):
        result = {"resource_id": None, "error": None}

        def callback(controller):
            try:
                controller.SetFrameEvent(int(event_id), True)
                result["resource_id"] = controller.GetPipelineState().GetShader(
                    rd.ShaderStage.Pixel
                )
                if result["resource_id"] == rd.ResourceId.Null():
                    raise ValueError("Event ID %s has no pixel shader" % event_id)
            except Exception as exc:
                result["error"] = str(exc)

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["resource_id"]

    def _reset_pixel_shader(self, event_id):
        original_id = self._pixel_shader_id(event_id)
        was_replaced = bool(self.ctx.IsResourceReplaced(original_id))
        replacement_id = self.ctx.GetResourceReplacement(original_id) if was_replaced else None

        if was_replaced:
            self.ctx.UnregisterReplacement(original_id)

        def callback(controller):
            controller.RemoveReplacement(original_id)
            controller.SetFrameEvent(max(int(event_id) - 1, 0), True)
            controller.SetFrameEvent(int(event_id), True)

        self._invoke(callback)
        try:
            self.ctx.RefreshStatus()
        except Exception:
            pass

        replaced = bool(self.ctx.IsResourceReplaced(original_id))
        if replaced:
            raise ValueError(
                "Pixel shader replacement is still registered after Reset for EID %s"
                % event_id
            )

        return {
            "event_id": int(event_id),
            "original_resource_id": str(original_id),
            "was_replaced": was_replaced,
            "removed_replacement_resource_id": (
                str(replacement_id) if replacement_id is not None else None
            ),
            "replaced": replaced,
        }

    def _invoke(self, callback):
        self.ctx.Replay().BlockInvoke(callback)


class BridgeSession:
    """Adapter with the subset of RenderDocSession used by exporter.py."""

    def __init__(self, controller):
        self._controller = controller
        self._structured_file = controller.GetStructuredFile()
        self._actions = {}
        self._textures = {}
        self._resources = {}
        self._current_event = None
        self._rebuild_indexes()

    @property
    def controller(self):
        return self._controller

    @property
    def structured_file(self):
        return self._structured_file

    def set_event(self, event_id):
        if event_id not in self._actions:
            return {"error": "Event ID %s not found" % event_id, "code": "INVALID_EVENT_ID"}
        self._controller.SetFrameEvent(event_id, True)
        self._current_event = event_id
        return None

    def get_action(self, event_id):
        return self._actions.get(event_id)

    def get_texture(self, resource_id):
        return self._textures.get(resource_id)

    def _rebuild_indexes(self):
        self._index_actions(self._controller.GetRootActions())
        for tex in self._controller.GetTextures():
            key = str(tex.resourceId)
            self._textures[key] = tex
            self._resources[key] = tex.resourceId
        for buf in self._controller.GetBuffers():
            self._resources[str(buf.resourceId)] = buf.resourceId
        for res in self._controller.GetResources():
            self._resources.setdefault(str(res.resourceId), res.resourceId)

    def _index_actions(self, actions):
        for action in actions:
            self._actions[action.eventId] = action
            if action.children:
                self._index_actions(action.children)


def _ensure_project_src():
    src = os.environ.get("RENDERDOC_MCP_SRC", r"D:\_Proj\renderdoc-mcp\src")
    if src and os.path.isdir(src) and src not in sys.path:
        sys.path.insert(0, src)


def _parse_event_id(value):
    if value is None:
        raise ValueError("event_id is required")
    try:
        event_id = int(value)
    except (TypeError, ValueError):
        raise ValueError("event_id must be a positive integer")
    if event_id <= 0:
        raise ValueError("event_id must be a positive integer")
    return event_id
