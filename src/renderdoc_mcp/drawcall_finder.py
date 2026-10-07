"""Find draw calls that visually belong to one reference subject.

The finder uses known anchor draw calls to locate the subject in screen space,
then ranks nearby draws by PostVS overlap and pipeline similarity.  It also
writes an SVG contact sheet so the candidate meshes can be checked visually.
"""

import html
import json
import math
import os
import struct

from renderdoc_mcp.mesh_decode import get_mesh_stage_data
from renderdoc_mcp.renderdoc_api import rd


def find_drawcalls_by_reference(
    session,
    reference_image_path,
    output_dir,
    anchor_event_ids=None,
    event_start=0,
    event_end=0,
    min_indices=3,
    max_vertices=2048,
    min_overlap=0.2,
):
    """Rank draw calls near anchor events and write a visual contact sheet."""
    reference_image_path = os.path.normpath(reference_image_path)
    output_dir = os.path.normpath(output_dir)
    if not os.path.isfile(reference_image_path):
        raise ValueError("Reference image not found: %s" % reference_image_path)
    if max_vertices <= 0:
        raise ValueError("max_vertices must be positive")
    if min_overlap < 0 or min_overlap > 1:
        raise ValueError("min_overlap must be between 0 and 1")

    anchors = sorted(set(int(value) for value in (anchor_event_ids or [])))
    actions = list(_walk_actions(session.controller.GetRootActions()))
    action_by_event = {int(action.eventId): (action, marker_path) for action, marker_path in actions}
    missing = [event_id for event_id in anchors if event_id not in action_by_event]
    if missing:
        raise ValueError("Anchor event IDs not found: %s" % ", ".join(str(value) for value in missing))

    if anchors:
        event_start = int(event_start or max(1, min(anchors) - 128))
        event_end = int(event_end or max(anchors) + 128)
    else:
        event_start = int(event_start or 1)
        event_end = int(event_end or max(action_by_event or {1: None}))
    if event_start > event_end:
        raise ValueError("event_start must not exceed event_end")

    anchor_rows = []
    for event_id in anchors:
        action, marker_path = action_by_event[event_id]
        anchor_rows.append(
            _analyze_drawcall(session, action, marker_path, max_vertices, is_anchor=True)
        )
    anchor_bbox = _union_bbox([row.get("screen_bbox") for row in anchor_rows])
    anchor_targets = set(row.get("output_signature") for row in anchor_rows)
    anchor_vertex_shaders = set(row.get("vertex_shader") for row in anchor_rows)
    anchor_pixel_shaders = set(row.get("pixel_shader") for row in anchor_rows)

    candidates = []
    skipped = []
    for action, marker_path in actions:
        event_id = int(action.eventId)
        if event_id < event_start or event_id > event_end:
            continue
        if not _is_draw_action(action) or int(getattr(action, "numIndices", 0)) < int(min_indices):
            continue
        if event_id in anchors:
            row = next(item for item in anchor_rows if item["event_id"] == event_id)
        else:
            try:
                row = _analyze_drawcall(session, action, marker_path, max_vertices)
            except Exception as exc:
                skipped.append({"event_id": event_id, "reason": str(exc)})
                continue
        bbox = row.get("screen_bbox")
        if bbox is None:
            skipped.append({"event_id": event_id, "reason": "no_visible_postvs_position"})
            continue

        score = _candidate_score(
            row,
            anchor_bbox,
            anchor_targets,
            anchor_vertex_shaders,
            anchor_pixel_shaders,
            anchors,
        )
        row.update(score)
        if row["is_anchor"] or not anchors or (
            row["output_target_match"] and row["subject_overlap"] >= float(min_overlap)
        ):
            candidates.append(row)

    candidates.sort(key=lambda item: (-item["score"], item["event_id"]))
    os.makedirs(output_dir, exist_ok=True)
    manifest_path = os.path.join(output_dir, "drawcall_candidates.json")
    contact_sheet_path = os.path.join(output_dir, "drawcall_candidates.svg")
    _write_contact_sheet(contact_sheet_path, candidates, anchor_bbox)

    result = {
        "schema": "renderdoc-mcp.drawcall-finder.v1",
        "reference_image": {
            "path": reference_image_path,
            "size": _image_dimensions(reference_image_path),
        },
        "event_range": [event_start, event_end],
        "anchor_event_ids": anchors,
        "anchor_bbox": anchor_bbox,
        "candidate_count": len(candidates),
        "candidates": [_public_row(row) for row in candidates],
        "skipped": skipped,
        "contact_sheet_path": contact_sheet_path,
        "manifest_path": manifest_path,
    }
    with open(manifest_path, "w", encoding="utf-8") as manifest:
        json.dump(result, manifest, ensure_ascii=False, indent=2)
    return result


def _walk_actions(actions, marker_path=()):
    for action in actions:
        name = _action_name(action)
        children = list(getattr(action, "children", None) or [])
        current_path = marker_path
        if children:
            current_path = marker_path + ((name,) if name else ())
        yield action, marker_path
        for child in _walk_actions(children, current_path):
            yield child


def _action_name(action):
    try:
        return str(action.GetName(None))
    except Exception:
        return str(getattr(action, "customName", "") or "")


def _is_draw_action(action):
    try:
        return bool(action.flags & rd.ActionFlags.Drawcall)
    except Exception:
        return int(getattr(action, "numIndices", 0)) > 0


def _analyze_drawcall(session, action, marker_path, max_vertices, is_anchor=False):
    event_id = int(action.eventId)
    error = session.set_event(event_id)
    if error:
        raise ValueError(error.get("error", str(error)))
    state = session.controller.GetPipelineState()
    vertices, meta = get_mesh_stage_data(
        session.controller,
        action,
        "vsout",
        first_index=0,
        max_vertices=int(max_vertices),
        instance=0,
        view=0,
    )
    points = []
    for row in vertices:
        position = _position_value(row)
        point = _screen_point(position)
        if point is not None:
            points.append(point)
    bbox = _bbox(points)
    targets = _output_targets(state)
    depth_target = _depth_target(state)
    mesh_binding = _mesh_binding(state, action)
    return {
        "event_id": event_id,
        "action_name": _safe_action_name(action, session),
        "marker_path": list(marker_path),
        "num_indices": int(getattr(action, "numIndices", 0)),
        "num_instances": int(getattr(action, "numInstances", 0)),
        "index_offset": int(getattr(action, "indexOffset", 0)),
        "vertex_offset": int(getattr(action, "vertexOffset", 0)),
        "base_vertex": int(getattr(action, "baseVertex", 0)),
        "topology": str(state.GetPrimitiveTopology()),
        "vertex_shader": str(state.GetShader(rd.ShaderStage.Vertex)),
        "pixel_shader": str(state.GetShader(rd.ShaderStage.Pixel)),
        "output_targets": targets,
        "depth_target": depth_target,
        "output_signature": "|".join(targets + [depth_target]),
        "mesh_binding": mesh_binding,
        "mesh_signature": _mesh_signature(mesh_binding, action),
        "pixel_resources": _read_only_resources(state, rd.ShaderStage.Pixel),
        "screen_bbox": bbox,
        "screen_center": _bbox_center(bbox),
        "screen_area": _bbox_area(bbox),
        "sampled_vertices": len(points),
        "total_vertices": int(meta.get("total_vertices", 0)),
        "is_anchor": bool(is_anchor),
        "_points": points,
    }


def _safe_action_name(action, session):
    try:
        return str(action.GetName(session.structured_file))
    except Exception:
        return _action_name(action)


def _position_value(row):
    for name, value in row.items():
        if "position" in str(name).lower() and isinstance(value, (list, tuple)):
            return value
    return None


def _screen_point(position):
    if not position or len(position) < 4:
        return None
    x, y, _z, w = [float(value) for value in position[:4]]
    if not all(math.isfinite(value) for value in (x, y, w)) or abs(w) < 1.0e-12:
        return None
    u = (x / w + 1.0) * 0.5
    v = (1.0 - y / w) * 0.5
    if not math.isfinite(u) or not math.isfinite(v):
        return None
    return (u, v)


def _output_targets(state):
    targets = []
    try:
        for target in state.GetOutputTargets():
            resource = str(target.resource)
            if resource not in ("ResourceId::0", "0"):
                targets.append(resource)
    except Exception:
        pass
    return targets


def _depth_target(state):
    try:
        return str(state.GetDepthTarget().resource)
    except Exception:
        return "ResourceId::0"


def _mesh_binding(state, action):
    result = {"index_buffer": None, "vertex_buffers": []}
    try:
        index_buffer = state.GetIBuffer()
        result["index_buffer"] = {
            "resource_id": str(index_buffer.resourceId),
            "byte_offset": int(index_buffer.byteOffset),
            "byte_stride": int(index_buffer.byteStride),
        }
    except Exception:
        pass
    try:
        for slot, vertex_buffer in enumerate(state.GetVBuffers()):
            resource_id = str(vertex_buffer.resourceId)
            if resource_id in ("ResourceId::0", "0"):
                continue
            result["vertex_buffers"].append(
                {
                    "slot": slot,
                    "resource_id": resource_id,
                    "byte_offset": int(vertex_buffer.byteOffset),
                    "byte_stride": int(vertex_buffer.byteStride),
                }
            )
    except Exception:
        pass
    return result


def _mesh_signature(binding, action):
    index_buffer = binding.get("index_buffer") or {}
    vertex_buffers = binding.get("vertex_buffers") or []
    values = [
        index_buffer.get("resource_id", ""),
        str(index_buffer.get("byte_offset", 0)),
        str(index_buffer.get("byte_stride", 0)),
        str(int(getattr(action, "indexOffset", 0))),
        str(int(getattr(action, "numIndices", 0))),
        str(int(getattr(action, "baseVertex", 0))),
    ]
    values.extend(
        "%s:%s:%s:%s"
        % (item["slot"], item["resource_id"], item["byte_offset"], item["byte_stride"])
        for item in vertex_buffers
    )
    return "|".join(values)


def _read_only_resources(state, stage):
    resources = []
    try:
        for binding in state.GetReadOnlyResources(stage):
            resource = str(binding.descriptor.resource)
            if resource not in resources and resource not in ("ResourceId::0", "0"):
                resources.append(resource)
    except Exception:
        pass
    return resources


def _bbox(points):
    if not points:
        return None
    left = max(0.0, min(point[0] for point in points))
    top = max(0.0, min(point[1] for point in points))
    right = min(1.0, max(point[0] for point in points))
    bottom = min(1.0, max(point[1] for point in points))
    if right <= left or bottom <= top:
        return None
    return [left, top, right, bottom]


def _union_bbox(boxes):
    boxes = [box for box in boxes if box]
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _bbox_area(box):
    if not box:
        return 0.0
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _bbox_center(box):
    if not box:
        return None
    return [(box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5]


def _intersection_area(first, second):
    if not first or not second:
        return 0.0
    return max(0.0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0.0, min(first[3], second[3]) - max(first[1], second[1])
    )


def _candidate_score(
    row,
    anchor_bbox,
    anchor_targets,
    anchor_vertex_shaders,
    anchor_pixel_shaders,
    anchors,
):
    candidate_area = _bbox_area(row.get("screen_bbox"))
    anchor_area = _bbox_area(anchor_bbox)
    intersection = _intersection_area(row.get("screen_bbox"), anchor_bbox)
    subject_overlap = intersection / candidate_area if candidate_area else 0.0
    union = candidate_area + anchor_area - intersection
    iou = intersection / union if union else 0.0
    target_match = not anchors or row.get("output_signature") in anchor_targets
    shader_match = (
        row.get("vertex_shader") in anchor_vertex_shaders
        or row.get("pixel_shader") in anchor_pixel_shaders
    )
    if anchors:
        distance = min(abs(row["event_id"] - event_id) for event_id in anchors)
        proximity = 1.0 / (1.0 + distance / 32.0)
    else:
        proximity = 0.0
    score = (
        0.5 * subject_overlap
        + 0.2 * iou
        + 0.15 * float(target_match)
        + 0.1 * float(shader_match)
        + 0.05 * proximity
    )
    return {
        "subject_overlap": round(subject_overlap, 6),
        "bbox_iou": round(iou, 6),
        "output_target_match": bool(target_match),
        "shader_match": bool(shader_match),
        "anchor_event_distance": min((abs(row["event_id"] - value) for value in anchors), default=None),
        "score": round(score, 6),
    }


def _public_row(row):
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _image_dimensions(path):
    with open(path, "rb") as image:
        header = image.read(24)
    if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
        width, height = struct.unpack(">II", header[16:24])
        return [width, height]
    return None


def _write_contact_sheet(path, rows, anchor_bbox):
    columns = 4
    panel_width = 300
    panel_height = 340
    count = max(1, len(rows))
    row_count = int(math.ceil(float(count) / columns))
    width = columns * panel_width
    height = row_count * panel_height
    crop = _expanded_bbox(anchor_bbox) if anchor_bbox else [0.0, 0.0, 1.0, 1.0]
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
        % (width, height, width, height),
        '<rect width="100%%" height="100%%" fill="#111827"/>',
    ]
    for index, row in enumerate(rows):
        left = (index % columns) * panel_width
        top = (index // columns) * panel_height
        parts.append(
            '<rect x="%d" y="%d" width="292" height="332" rx="6" fill="#1f2937" stroke="#4b5563"/>'
            % (left + 4, top + 4)
        )
        title = "EID %s  %s" % (row["event_id"], row.get("action_name", ""))
        parts.append(
            '<text x="%d" y="%d" fill="#f9fafb" font-family="monospace" font-size="13">%s</text>'
            % (left + 12, top + 22, html.escape(title[:38]))
        )
        subtitle = "%s idx  score %.3f" % (
            row.get("num_indices", 0),
            row.get("score", 0.0),
        )
        parts.append(
            '<text x="%d" y="%d" fill="#93c5fd" font-family="monospace" font-size="11">%s</text>'
            % (left + 12, top + 39, html.escape(subtitle))
        )
        points = row.get("_points") or []
        triangles = len(points) // 3
        step = max(1, int(math.ceil(float(triangles) / 300.0)))
        for triangle in range(0, triangles, step):
            polygon = points[triangle * 3 : triangle * 3 + 3]
            mapped = [
                _map_point(point, crop, left + 12, top + 50, panel_width - 24, panel_height - 64)
                for point in polygon
            ]
            value = " ".join("%.2f,%.2f" % point for point in mapped)
            parts.append(
                '<polygon points="%s" fill="#22d3ee" fill-opacity="0.24" stroke="#67e8f9" stroke-width="0.65"/>'
                % value
            )
    parts.append("</svg>")
    with open(path, "w", encoding="utf-8") as svg:
        svg.write("\n".join(parts))


def _expanded_bbox(box):
    width = box[2] - box[0]
    height = box[3] - box[1]
    margin_x = max(width * 0.08, 0.01)
    margin_y = max(height * 0.08, 0.01)
    return [
        max(0.0, box[0] - margin_x),
        max(0.0, box[1] - margin_y),
        min(1.0, box[2] + margin_x),
        min(1.0, box[3] + margin_y),
    ]


def _map_point(point, crop, left, top, width, height):
    crop_width = max(crop[2] - crop[0], 1.0e-12)
    crop_height = max(crop[3] - crop[1], 1.0e-12)
    x = left + (point[0] - crop[0]) / crop_width * width
    y = top + (point[1] - crop[1]) / crop_height * height
    return (x, y)
