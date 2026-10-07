"""查询捕获里的绘制和绑定。

这里只回答资产分析会反复问的三件事：一笔 EID 绑了哪些纹理、
哪些绘制用了某个贴图名、Present 的交换链在哪。不在这里写文档。
"""

import os

from renderdoc_mcp.exporter import _save_texture, export_bound_texture
from renderdoc_mcp.mesh_decode import get_mesh_stage_data
from renderdoc_mcp.renderdoc_api import (
    error,
    rd,
    safe_filename,
    shader_stages,
    texture_desc_to_dict,
    texture_kind,
    texture_size_label,
)


def normalize_name_parts(value):
    """把逗号字符串或列表收成用于匹配的贴图名片段。"""
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.split(",")
    else:
        parts = value
    return [str(part).strip() for part in parts if str(part).strip()]


def name_matches(name, parts):
    lowered = (name or "").lower()
    return any(part.lower() in lowered for part in parts)


def describe_event(session, event_id, output_dir=None, file_type="png"):
    """列出这一笔着色器绑定的每一张纹理，尺寸和格式以显存为准。"""
    err = session.set_event(event_id)
    if err:
        return err

    action = session.get_action(event_id)
    state = session.controller.GetPipelineState()
    names = _resource_names(session.controller)
    bindings = []
    for stage_name in ("vertex", "pixel"):
        stage_enum = shader_stages()[stage_name]
        bindings.extend(_stage_bindings(session, state, stage_name, stage_enum, names))

    saved = []
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        for item in bindings:
            tex = session.get_texture(item["resource_id"])
            if tex is None or int(tex.width) < 1 or int(tex.height) < 1:
                continue
            basename = safe_filename(
                "eid{}_{}_t{}_{}_{}".format(
                    event_id,
                    item["stage"],
                    item["slot"],
                    item["name"] or item["resource_id"],
                    texture_size_label(tex),
                )
            )
            try:
                result = export_bound_texture(session, tex, output_dir, basename, file_type)
            except Exception as exc:
                item["save_error"] = str(exc)
                continue
            item["output_path"] = result["output_path"]
            item["kind"] = result["kind"]
            saved.append(result["output_path"])

    return {
        "event_id": int(event_id),
        "num_indices": int(action.numIndices) if action is not None else None,
        "num_instances": int(action.numInstances) if action is not None else None,
        "triangles": int(action.numIndices) // 3 if action is not None else None,
        "outputs": _output_sizes(session, state),
        "bindings": bindings,
        "binding_count": len(bindings),
        "mesh": _mesh_bounds(session, action),
        "saved": saved,
    }


def find_draws_by_texture(session, name_parts, event_start=0, event_end=0):
    """找出像素着色器贴图名里含这些片段的绘制。"""
    parts = normalize_name_parts(name_parts)
    if not parts:
        return error("name_parts is required", "INVALID_ARGUMENT")

    names = _resource_names(session.controller)
    matches = []
    for action in _walk_actions(session.controller.GetRootActions()):
        if not (action.flags & rd.ActionFlags.Drawcall):
            continue
        if int(action.numIndices) <= 0:
            continue
        event_id = int(action.eventId)
        if event_start and event_id < int(event_start):
            continue
        if event_end and event_id > int(event_end):
            continue
        err = session.set_event(event_id)
        if err:
            continue
        state = session.controller.GetPipelineState()
        bound = _stage_bindings(session, state, "pixel", rd.ShaderStage.Pixel, names)
        hit = [item for item in bound if name_matches(item["name"], parts)]
        if not hit:
            continue
        matches.append({
            "event_id": event_id,
            "num_indices": int(action.numIndices),
            "num_instances": int(action.numInstances),
            "triangles": int(action.numIndices) // 3,
            "outputs": _output_sizes(session, state),
            "matched": hit,
            "binding_count": len(bound),
        })

    return {"name_parts": parts, "count": len(matches), "draws": matches}


def export_present(session, output_dir):
    """导出最后一次 Present 的交换链画面。"""
    if not output_dir:
        return error("output_dir is required", "INVALID_ARGUMENT")

    present_id = last_present_event_id(session.controller)
    if present_id is None:
        return error("No Present action in this capture", "NO_PRESENT")

    err = session.set_event(present_id)
    if err:
        return err

    os.makedirs(output_dir, exist_ok=True)
    saved = []
    for tex in swapchain_textures(session):
        filename = "present_eid{}_{}x{}.png".format(present_id, int(tex.width), int(tex.height))
        output_path = os.path.join(output_dir, filename)
        _save_texture(session, tex.resourceId, output_path, "png")
        saved.append({
            "output_path": output_path,
            "width": int(tex.width),
            "height": int(tex.height),
            "format": _format_name(tex),
        })

    if not saved:
        return error("Present EID {} has no swapchain texture".format(present_id), "NO_SWAPCHAIN")
    return {"event_id": present_id, "saved": saved}


def normalize_overlay_name(value):
    """把 Texture Viewer overlay 名字收成可查表的键。"""
    text = str(value or "drawcall").strip().lower()
    return text.replace("_", "").replace("-", "").replace(" ", "")


def overlay_enum(name):
    """把 MCP 参数转成 RenderDoc DebugOverlay。"""
    key = normalize_overlay_name(name)
    mapping = {
        "drawcall": rd.DebugOverlay.Drawcall,
        "wireframe": rd.DebugOverlay.Wireframe,
        "depth": rd.DebugOverlay.Depth,
        "stencil": rd.DebugOverlay.Stencil,
        "backfacecull": rd.DebugOverlay.BackfaceCull,
        "viewportscissor": rd.DebugOverlay.ViewportScissor,
        "quadoverdraw": rd.DebugOverlay.QuadOverdrawDraw,
        "quadoverdrawdraw": rd.DebugOverlay.QuadOverdrawDraw,
        "quadoverdrawpass": rd.DebugOverlay.QuadOverdrawPass,
        "trianglesize": rd.DebugOverlay.TriangleSizeDraw,
        "trianglesizedraw": rd.DebugOverlay.TriangleSizeDraw,
        "trianglesizepass": rd.DebugOverlay.TriangleSizePass,
    }
    if key not in mapping:
        return error(
            "Unknown overlay '{}'. Use drawcall or wireframe.".format(name),
            "INVALID_OVERLAY",
        )
    return mapping[key]


def last_present_event_id(controller):
    present_ids = []
    for action in _walk_actions(controller.GetRootActions()):
        if action.flags & rd.ActionFlags.Present:
            present_ids.append(int(action.eventId))
    return present_ids[-1] if present_ids else None


def swapchain_textures(session):
    found = []
    for tex in session.controller.GetTextures():
        if int(tex.creationFlags) & int(rd.TextureCategory.SwapBuffer):
            found.append(tex)
    return found


def first_color_target(session):
    """当前 EID 的第一张有效 color target，和 Texture Viewer 默认看的那张一致。"""
    state = session.controller.GetPipelineState()
    try:
        targets = state.GetOutputTargets()
    except Exception:
        return None, None
    for target in targets:
        if int(target.resource) == 0:
            continue
        tex = session.get_texture(str(target.resource))
        if tex is None:
            continue
        return target.resource, tex
    return None, None


def export_present_overlay(
    session,
    event_id,
    output_dir,
    overlay="drawcall",
    crop=True,
    pad=80,
):
    """用 Texture Viewer 的 DebugOverlay 标出一笔绘制，再叠到最后一次 Present。

    当前表格里的 Present 部件图以前是本地脚本把 VSOut 投到屏幕再 PIL 描线，
    没有走 RenderDoc overlay。这里按 Texture Viewer 同一条 ReplayOutput 路径
    生成 overlay，再缩放到最终交换链画面上融合。
    """
    if event_id is None:
        return error("event_id is required", "INVALID_ARGUMENT")
    if not output_dir:
        return error("output_dir is required", "INVALID_ARGUMENT")

    overlay_value = overlay_enum(overlay)
    if isinstance(overlay_value, dict) and overlay_value.get("error"):
        return overlay_value
    overlay_key = normalize_overlay_name(overlay)

    present_id = last_present_event_id(session.controller)
    if present_id is None:
        return error("No Present action in this capture", "NO_PRESENT")

    err = session.set_event(int(event_id))
    if err:
        return err
    action = session.get_action(int(event_id))
    if action is None:
        return error("Event ID {} not found".format(event_id), "INVALID_EVENT_ID")

    color_id, color_tex = first_color_target(session)
    if color_id is None:
        return error(
            "EID {} has no color target for Texture Viewer overlay".format(event_id),
            "NO_COLOR_TARGET",
        )

    err = session.set_event(present_id)
    if err:
        return err
    present_texes = swapchain_textures(session)
    if not present_texes:
        return error("Present EID {} has no swapchain texture".format(present_id), "NO_SWAPCHAIN")
    present_tex = present_texes[0]

    os.makedirs(output_dir, exist_ok=True)
    present_path = os.path.join(
        output_dir,
        "present_eid{}_{}x{}.png".format(present_id, int(present_tex.width), int(present_tex.height)),
    )
    _save_texture(session, present_tex.resourceId, present_path, "png")

    err = session.set_event(int(event_id))
    if err:
        return err

    overlay_path = os.path.join(
        output_dir,
        "overlay_{}_eid{}.png".format(overlay_key, int(event_id)),
    )
    overlay_err = _save_debug_overlay(session, color_id, overlay_value, overlay_path)
    if overlay_err:
        return overlay_err

    fused_path = os.path.join(
        output_dir,
        "present_overlay_{}_eid{}.png".format(overlay_key, int(event_id)),
    )
    crop_path = os.path.join(
        output_dir,
        "present_overlay_{}_eid{}_crop.png".format(overlay_key, int(event_id)),
    )
    composed = composite_overlay_onto_present(
        present_path,
        overlay_path,
        fused_path,
        crop_path=crop_path if crop else None,
        pad=int(pad),
    )
    if composed.get("error"):
        composed["present_path"] = os.path.normpath(present_path)
        composed["overlay_path"] = os.path.normpath(overlay_path)
        return composed

    return {
        "event_id": int(event_id),
        "present_event_id": present_id,
        "overlay": overlay_key,
        "present_path": os.path.normpath(present_path),
        "overlay_path": os.path.normpath(overlay_path),
        "fused_path": os.path.normpath(fused_path),
        "crop_path": composed.get("crop_path"),
        "crop": composed.get("crop"),
        "color_target": {
            "width": int(color_tex.width),
            "height": int(color_tex.height),
            "format": _format_name(color_tex),
        },
        "present": {
            "width": int(present_tex.width),
            "height": int(present_tex.height),
            "format": _format_name(present_tex),
        },
        "scaled": [
            int(color_tex.width),
            int(color_tex.height),
        ] != [
            int(present_tex.width),
            int(present_tex.height),
        ],
    }


def _save_debug_overlay(session, color_id, overlay_value, overlay_path):
    """走 Texture Viewer 同一条 ReplayOutput DebugOverlay 路径。"""
    output = None
    try:
        output = session.controller.CreateOutput(
            rd.CreateHeadlessWindowingData(100, 100),
            rd.ReplayOutputType.Texture,
        )
        if output is None:
            return error("CreateOutput failed for Texture Viewer overlay", "OVERLAY_OUTPUT")

        display = rd.TextureDisplay()
        display.resourceId = color_id
        display.subresource.sample = 0
        display.overlay = overlay_value
        output.SetTextureDisplay(display)
        output.Display()

        overlay_id = output.GetDebugOverlayTexID()
        if overlay_id is None or int(overlay_id) == 0:
            return error("GetDebugOverlayTexID returned an empty overlay", "NO_OVERLAY_TEXTURE")
        _save_texture(session, overlay_id, overlay_path, "png")
        return None
    except Exception as exc:
        return error("Failed to render Texture Viewer overlay: {}".format(exc), "OVERLAY_RENDER")
    finally:
        if output is not None:
            try:
                output.Shutdown()
            except Exception:
                pass


def mask_overlay_highlight(overlay_image, highlight_alpha=160):
    """只留下 Texture Viewer overlay 的高亮像素，背景半透明黑丢掉。"""
    alpha_out = max(0, min(255, int(highlight_alpha)))
    pixels = overlay_image.load()
    width, height = overlay_image.size
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            if alpha < 200:
                pixels[x, y] = (0, 0, 0, 0)
            else:
                pixels[x, y] = (red, green, blue, alpha_out)
    return overlay_image


def crop_box_from_overlay(overlay_image, pad=80, max_width=1600, max_height=1000):
    """按高亮包围盒裁切；太大时改成靠近画面下方的窗口，避免整屏进表。"""
    bbox = overlay_image.split()[-1].getbbox()
    if bbox is None:
        return None
    img_w, img_h = overlay_image.size
    pad = max(0, int(pad))
    x0 = max(0, bbox[0] - pad)
    y0 = max(0, bbox[1] - pad)
    x1 = min(img_w, bbox[2] + pad)
    y1 = min(img_h, bbox[3] + pad)
    max_width = min(int(max_width), img_w)
    max_height = min(int(max_height), img_h)
    width = x1 - x0
    height = y1 - y0
    if width > max_width or height > max_height:
        cx = (x0 + x1) // 2
        cy = y0 + int((y1 - y0) * 0.62)
        half_w = min(width, max_width) // 2
        half_h = min(height, max_height) // 2
        x0 = max(0, min(img_w - min(width, max_width), cx - half_w))
        y0 = max(0, min(img_h - min(height, max_height), cy - half_h))
        x1 = min(img_w, x0 + min(width, max_width))
        y1 = min(img_h, y0 + min(height, max_height))
    return [x0, y0, x1, y1]


def composite_overlay_onto_present(
    present_path,
    overlay_path,
    fused_path,
    crop_path=None,
    pad=80,
    highlight_alpha=160,
    max_width=1600,
    max_height=1000,
):
    """把 overlay 缩放到 Present 尺寸后做 alpha 合成，可选按高亮裁切。"""
    try:
        from PIL import Image
    except ImportError:
        return error(
            "Pillow is required to fuse Texture Viewer overlay onto Present",
            "COMPOSITE_NEEDS_PIL",
        )

    present = Image.open(present_path).convert("RGBA")
    overlay = Image.open(overlay_path).convert("RGBA")
    if overlay.size != present.size:
        overlay = overlay.resize(present.size, Image.NEAREST)
    overlay = mask_overlay_highlight(overlay, highlight_alpha=highlight_alpha)
    fused = Image.alpha_composite(present, overlay)
    fused.convert("RGB").save(fused_path)

    crop_box = None
    saved_crop = None
    if crop_path:
        crop_box = crop_box_from_overlay(
            overlay,
            pad=pad,
            max_width=max_width,
            max_height=max_height,
        )
        if crop_box is None:
            return error("Overlay highlight is empty; nothing to crop", "EMPTY_OVERLAY")
        fused.crop(tuple(crop_box)).convert("RGB").save(crop_path)
        saved_crop = os.path.normpath(crop_path)

    return {
        "fused_path": os.path.normpath(fused_path),
        "crop_path": saved_crop,
        "crop": crop_box,
    }


def _resource_names(controller):
    names = {}
    for res in controller.GetResources():
        names[str(res.resourceId)] = res.name or ""
    return names


def _stage_bindings(session, state, stage_name, stage_enum, names):
    try:
        bound = state.GetReadOnlyResources(stage_enum)
    except Exception:
        return []

    rows = []
    seen = set()
    for binding in bound:
        resource_id = str(binding.descriptor.resource)
        if resource_id in seen or int(binding.descriptor.resource) == 0:
            continue
        tex = session.get_texture(resource_id)
        if tex is None:
            continue
        seen.add(resource_id)
        rows.append({
            "stage": stage_name,
            "slot": int(binding.access.index),
            "name": names.get(resource_id, ""),
            "resource_id": resource_id,
            "width": int(tex.width),
            "height": int(tex.height),
            "depth": int(getattr(tex, "depth", 1) or 1),
            "kind": texture_kind(tex),
            "mips": int(tex.mips),
            "format": _format_name(tex),
            "texture": texture_desc_to_dict(tex),
        })
    rows.sort(key=lambda item: (item["stage"], item["slot"]))
    return rows


def _output_sizes(session, state):
    sizes = []
    try:
        targets = state.GetOutputTargets()
    except Exception:
        return sizes
    for target in targets:
        if int(target.resource) == 0:
            continue
        tex = session.get_texture(str(target.resource))
        if tex is None:
            continue
        sizes.append("{}x{}".format(int(tex.width), int(tex.height)))
    return sizes


def _mesh_bounds(session, action):
    if action is None:
        return None
    try:
        vertices, _meta = get_mesh_stage_data(session.controller, action, "vsin", instance=0)
    except Exception as exc:
        return {"error": str(exc)}

    positions = []
    uvs = []
    for row in vertices:
        for key, value in row.items():
            upper = key.upper()
            if "POSITION" in upper and _is_vec(value, 3):
                positions.append((float(value[0]), float(value[1]), float(value[2])))
            elif "TEXCOORD0" in upper and _is_vec(value, 2):
                uvs.append((float(value[0]), float(value[1])))
    if not positions:
        return {"error": "no POSITION"}

    xs = [item[0] for item in positions]
    ys = [item[1] for item in positions]
    zs = [item[2] for item in positions]
    unique = set((round(item[0], 3), round(item[1], 3), round(item[2], 3)) for item in positions)
    result = {
        "unique_positions": len(unique),
        "size": [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)],
    }
    if uvs:
        us = [item[0] for item in uvs]
        vs = [item[1] for item in uvs]
        result["uv0"] = [min(us), min(vs), max(us), max(vs)]
    return result


def _format_name(tex):
    try:
        return str(tex.format.Name())
    except Exception:
        return str(tex.format)


def _is_vec(value, count):
    return isinstance(value, (list, tuple)) and len(value) >= count


def _walk_actions(actions):
    for action in actions:
        yield action
        children = action.children
        if children:
            for child in _walk_actions(children):
                yield child
