"""Map VSIn/VSOut decoded rows to FBX CSV columns."""

from renderdoc_mcp.resource_export import schema


def mesh_headers(rows):
    headers = ["vtx", "idx"]
    for row in rows or []:
        for key, value in row.items():
            add_header(headers, key, value)
    return headers


def add_header(headers, key, value):
    if isinstance(value, (list, tuple)):
        for index in range(len(value)):
            item = "%s.%s" % (key, component_name(index))
            if item not in headers:
                headers.append(item)
        return
    if key not in headers:
        headers.append(key)


def component_name(index):
    return ["x", "y", "z", "w"][index] if index < 4 else str(index)


def resolve_value(row, source_column):
    semantic, component = schema.split_component(source_column)
    if component is None:
        return row.get(semantic)

    value = row.get(semantic)
    if isinstance(value, (list, tuple)):
        index = component_index(component)
        if index is not None and index < len(value):
            return value[index]

    return row.get(source_column)


def component_index(component):
    component = str(component).lower()
    if component in ("x", "r", "u"):
        return 0
    if component in ("y", "g", "v"):
        return 1
    if component in ("z", "b"):
        return 2
    if component in ("w", "a"):
        return 3
    try:
        return int(component)
    except Exception:
        return None


def auto_config(vsin_rows, vsout_rows):
    config = schema.default_export_config()
    headers = {
        schema.VSIN: mesh_headers(vsin_rows),
        schema.VSOUT: mesh_headers(vsout_rows),
    }
    for item in config[schema.ATTRIBUTE_MAPPINGS]:
        source = item[schema.SOURCE_STAGE]
        current = item[schema.SOURCE_COLUMN]
        if current in headers.get(source, []):
            continue
        replacement = find_header(headers.get(source, []), item[schema.TARGET_COLUMN])
        if replacement:
            item[schema.SOURCE_COLUMN] = replacement
    return config


def find_header(headers, target_column):
    parts = target_column.split(".", 1)
    semantic = parts[0]
    suffix = "." + parts[1] if len(parts) > 1 else ""
    aliases = schema.HEADER_ALIAS_MAP.get(semantic, [semantic])
    candidates = [schema.normalize_header(alias + suffix) for alias in aliases]
    for header in headers:
        if schema.normalize_header(header) in candidates:
            return header
    return None
