"""Common schema helpers for resource asset export."""

import copy
import os
import re
import tempfile


VSIN = "vsin"
VSOUT = "vsout"

TARGET_COLUMN = "target_column"
SOURCE_STAGE = "source_stage"
SOURCE_COLUMN = "source_column"
ATTRIBUTE_MAPPINGS = "attribute_mappings"

FACE_WINDING = "face_winding"
FACE_WINDING_KEEP = "keep"
FACE_WINDING_REVERSE = "reverse"
AXIS_X = "axis_x"
AXIS_Y = "axis_y"
AXIS_Z = "axis_z"
FLIP_UV_V = "flip_uv_v"

EXPORT_NORMAL = "export_normal"
EXPORT_TANGENT = "export_tangent"
EXPORT_UV = "export_uv"
EXPORT_UV2 = "export_uv2"
EXPORT_UV3 = "export_uv3"

TARGET_COLUMNS = [
    "IDX",
    "POSITION.x",
    "POSITION.y",
    "POSITION.z",
    "NORMAL.x",
    "NORMAL.y",
    "NORMAL.z",
    "TANGENT.x",
    "TANGENT.y",
    "TANGENT.z",
    "TANGENT.w",
    "TEXCOORD0.x",
    "TEXCOORD0.y",
    "TEXCOORD1.x",
    "TEXCOORD1.y",
    "TEXCOORD2.x",
    "TEXCOORD2.y",
]

REQUIRED_COLUMNS = ["IDX", "POSITION.x", "POSITION.y", "POSITION.z"]

OPTIONAL_GROUPS = {
    EXPORT_NORMAL: ["NORMAL.x", "NORMAL.y", "NORMAL.z"],
    EXPORT_TANGENT: ["TANGENT.x", "TANGENT.y", "TANGENT.z", "TANGENT.w"],
    EXPORT_UV: ["TEXCOORD0.x", "TEXCOORD0.y"],
    EXPORT_UV2: ["TEXCOORD1.x", "TEXCOORD1.y"],
    EXPORT_UV3: ["TEXCOORD2.x", "TEXCOORD2.y"],
}

HEADER_ALIAS_MAP = {
    "IDX": ["IDX", "Index"],
    "POSITION": ["POSITION", "ATTRIBUTE0", "SV_Position", "SV_POSITION", "gl_Position"],
    "NORMAL": ["NORMAL"],
    "TANGENT": ["TANGENT"],
    "TEXCOORD0": ["TEXCOORD0", "TEXCOORD", "UV0", "UV"],
    "TEXCOORD1": ["TEXCOORD1", "UV1"],
    "TEXCOORD2": ["TEXCOORD2", "UV2"],
}


def default_export_config():
    """Return a practical UE-to-Unity default config."""
    mappings = [
        {TARGET_COLUMN: "IDX", SOURCE_STAGE: VSIN, SOURCE_COLUMN: "idx"},
        {TARGET_COLUMN: "POSITION.x", SOURCE_STAGE: VSIN, SOURCE_COLUMN: "ATTRIBUTE0.x"},
        {TARGET_COLUMN: "POSITION.y", SOURCE_STAGE: VSIN, SOURCE_COLUMN: "ATTRIBUTE0.y"},
        {TARGET_COLUMN: "POSITION.z", SOURCE_STAGE: VSIN, SOURCE_COLUMN: "ATTRIBUTE0.z"},
        {TARGET_COLUMN: "TEXCOORD0.x", SOURCE_STAGE: VSOUT, SOURCE_COLUMN: "TEXCOORD0.x"},
        {TARGET_COLUMN: "TEXCOORD0.y", SOURCE_STAGE: VSOUT, SOURCE_COLUMN: "TEXCOORD0.y"},
    ]
    config = {
        ATTRIBUTE_MAPPINGS: mappings,
        FACE_WINDING: FACE_WINDING_REVERSE,
        AXIS_X: "+Y",
        AXIS_Y: "+Z",
        AXIS_Z: "+X",
        FLIP_UV_V: True,
    }
    apply_export_flags(config)
    return config


def normalize_config(config):
    """Fill missing fields and keep the config JSON-friendly."""
    result = default_export_config()
    if config:
        result.update(copy.deepcopy(config))
    result[ATTRIBUTE_MAPPINGS] = list(result.get(ATTRIBUTE_MAPPINGS) or [])
    apply_export_flags(result)
    return result


def apply_export_flags(config):
    targets = [item.get(TARGET_COLUMN, "") for item in config.get(ATTRIBUTE_MAPPINGS, [])]
    for key, columns in OPTIONAL_GROUPS.items():
        config[key] = all(column in targets for column in columns)


def validate_config(config):
    mappings = config.get(ATTRIBUTE_MAPPINGS) or []
    if not mappings:
        return "no mapping rows"

    targets = [item.get(TARGET_COLUMN, "") for item in mappings]
    duplicates = sorted(set([target for target in targets if targets.count(target) > 1]))
    if duplicates:
        return "duplicate target columns: %s" % ", ".join(duplicates)

    missing_required = [column for column in REQUIRED_COLUMNS if column not in targets]
    if missing_required:
        return "missing required columns: %s" % ", ".join(missing_required)

    for item in mappings:
        if not item.get(SOURCE_STAGE):
            return "empty source stage for %s" % item.get(TARGET_COLUMN, "")
        if not item.get(SOURCE_COLUMN):
            return "empty source column for %s" % item.get(TARGET_COLUMN, "")

    for columns in OPTIONAL_GROUPS.values():
        selected = [column for column in columns if column in targets]
        if selected and len(selected) != len(columns):
            missing = [column for column in columns if column not in targets]
            return "incomplete optional group, missing: %s" % ", ".join(missing)

    return ""


def normalize_header(header):
    return str(header).replace(" ", "").replace("_", "").upper()


def split_component(name):
    if "." not in str(name):
        return str(name), None
    semantic, component = str(name).rsplit(".", 1)
    return semantic, component.lower()


def safe_preset_name(name):
    preset_name = str(name or "").strip()
    preset_name = re.sub(r'[<>:"/\\|?*]', "_", preset_name)
    return preset_name.strip(". ")


def default_preset_dir():
    root = os.environ.get("RENDERDOC_MCP_PRESET_DIR")
    if root:
        return os.path.normpath(root)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "qrenderdoc", "extensions", "renderdoc_mcp_bridge", "presets")
    return os.path.join(tempfile.gettempdir(), "renderdoc_mcp", "resource_presets")
