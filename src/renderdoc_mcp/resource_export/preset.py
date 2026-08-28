"""Preset read/write helpers shared by GUI and MCP."""

import json
import os

from renderdoc_mcp.resource_export.schema import default_preset_dir, normalize_config, safe_preset_name


def preset_path(name, preset_dir=None):
    preset_name = safe_preset_name(name)
    if not preset_name:
        raise ValueError("preset name is empty")
    return os.path.join(preset_dir or default_preset_dir(), preset_name + ".json")


def list_presets(preset_dir=None):
    root = preset_dir or default_preset_dir()
    if not os.path.isdir(root):
        return []
    names = []
    for filename in os.listdir(root):
        if filename.lower().endswith(".json"):
            names.append(os.path.splitext(filename)[0])
    names.sort()
    return names


def load_preset(name, preset_dir=None):
    path = preset_path(name, preset_dir)
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if "config" in payload:
        return normalize_config(payload.get("config"))

    config = dict(payload.get("transform", {}))
    config["attribute_mappings"] = payload.get("mappings", [])
    return normalize_config(config)


def save_preset(name, config, preset_dir=None):
    path = preset_path(name, preset_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"version": 2, "config": normalize_config(config)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path
