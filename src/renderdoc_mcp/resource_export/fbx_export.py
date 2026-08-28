"""CSV to FBX wrapper."""

import csv
import os
import shutil
import subprocess
import tempfile

from renderdoc_mcp.resource_export import schema


def default_converter_path():
    local = os.path.join(os.path.dirname(__file__), "bin", "RenderdocCSVToFBX.exe")
    if os.path.exists(local):
        return local
    legacy = r"D:\_Proj\RenderdocResourceExporter\RenderdocResourceExporter\fbx_res\RenderdocCSVToFBX.exe"
    return legacy


def export_fbx(csv_path, fbx_path, config, converter_path=None):
    converter = converter_path or default_converter_path()
    if not os.path.exists(converter):
        raise ValueError("RenderdocCSVToFBX.exe not found: %s" % converter)

    fbx_path = os.path.normpath(fbx_path)
    os.makedirs(os.path.dirname(fbx_path) or ".", exist_ok=True)
    if os.path.exists(fbx_path):
        os.remove(fbx_path)

    work_dir = tempfile.mkdtemp(prefix="renderdoc_mcp_fbx_")
    try:
        temp_csv = os.path.join(work_dir, "mesh.csv")
        write_converter_csv(csv_path, temp_csv)
        args = [
            converter,
            temp_csv,
            flag(config, schema.EXPORT_NORMAL),
            flag(config, schema.EXPORT_TANGENT),
            flag(config, schema.EXPORT_UV),
            flag(config, schema.EXPORT_UV2),
            flag(config, schema.EXPORT_UV3),
        ]
        result = subprocess.run(
            args,
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        if result.returncode != 0:
            raise RuntimeError("RenderdocCSVToFBX.exe failed: %s\n%s" % (result.stdout, result.stderr))
        temp_fbx = os.path.splitext(temp_csv)[0] + ".fbx"
        if not os.path.exists(temp_fbx):
            raise RuntimeError("FBX file was not created by converter")
        shutil.copyfile(temp_fbx, fbx_path)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return {"output_path": fbx_path, "converter_path": os.path.normpath(converter)}


def write_converter_csv(source_csv, output_csv):
    """The legacy converter expects IDX to be dense and zero-based."""
    with open(source_csv, "r", encoding="utf-8", newline="") as src:
        rows = list(csv.reader(src))
    if not rows:
        raise ValueError("CSV is empty: %s" % source_csv)

    idx_column = 0
    headers = rows[0]
    for index, header in enumerate(headers):
        if str(header).strip().upper() == "IDX":
            idx_column = index
            break

    with open(output_csv, "w", encoding="utf-8", newline="") as dst:
        writer = csv.writer(dst)
        writer.writerow(headers)
        for row_index, row in enumerate(rows[1:]):
            item = list(row)
            if idx_column < len(item):
                item[idx_column] = str(row_index)
            writer.writerow(item)


def flag(config, key):
    return "1" if config.get(key) else "0"
