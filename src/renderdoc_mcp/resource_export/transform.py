"""Geometry transform helpers for asset export."""

from renderdoc_mcp.resource_export import schema


def row_order(count, config):
    rows = list(range(count))
    if config.get(schema.FACE_WINDING) != schema.FACE_WINDING_REVERSE:
        return rows
    for index in range(0, len(rows) - 2, 3):
        rows[index + 1], rows[index + 2] = rows[index + 2], rows[index + 1]
    return rows


def apply_transforms(values, headers, config):
    apply_axis_transform(values, headers, config)
    apply_uv_transform(values, headers, config)


def apply_axis_transform(values, headers, config):
    axis_map = {
        "x": config.get(schema.AXIS_X, "+X"),
        "y": config.get(schema.AXIS_Y, "+Y"),
        "z": config.get(schema.AXIS_Z, "+Z"),
    }
    for semantic_name in ("POSITION", "NORMAL", "TANGENT"):
        indices = semantic_indices(headers, semantic_name)
        if len(indices) < 3:
            continue
        source = [safe_float(values[indices[0]]), safe_float(values[indices[1]]), safe_float(values[indices[2]])]
        result = [
            axis_value(axis_map.get("x"), source),
            axis_value(axis_map.get("y"), source),
            axis_value(axis_map.get("z"), source),
        ]
        for index, value in zip(indices[:3], result):
            values[index] = value


def apply_uv_transform(values, headers, config):
    if not config.get(schema.FLIP_UV_V, True):
        return
    for index, header in enumerate(headers):
        if is_uv_v_header(header):
            values[index] = 1.0 - safe_float(values[index])


def semantic_indices(headers, semantic_name):
    prefix = semantic_name.upper() + "."
    return [index for index, header in enumerate(headers) if str(header).upper().startswith(prefix)]


def is_uv_v_header(header):
    parts = str(header).strip().upper().replace("_", ".").split(".")
    if len(parts) < 2:
        return False
    return parts[0].startswith("TEXCOORD") and parts[-1] in ("Y", "V")


def axis_value(axis_name, source):
    axis_name = str(axis_name or "").upper()
    sign = -1.0 if axis_name.startswith("-") else 1.0
    axis = axis_name[-1:] if axis_name else "X"
    component = 0
    if axis == "Y":
        component = 1
    elif axis == "Z":
        component = 2
    return sign * source[component]


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return 0.0
