"""Write mapped resource asset CSV files."""

import csv
import os

from renderdoc_mcp.resource_export import mapping, schema, transform


def write_asset_csv(mesh_data, output_path, config):
    config = schema.normalize_config(config)
    error = schema.validate_config(config)
    if error:
        raise ValueError(error)

    rows = source_rows(mesh_data)
    if not rows:
        raise ValueError("no mesh rows")

    mappings = config[schema.ATTRIBUTE_MAPPINGS]
    headers = [item[schema.TARGET_COLUMN] for item in mappings]
    order = transform.row_order(len(rows), config)

    output_path = os.path.normpath(output_path)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row_index in order:
            line = []
            for item in mappings:
                source = item[schema.SOURCE_STAGE]
                source_column = item[schema.SOURCE_COLUMN]
                line.append(mapping.resolve_value(mesh_data.get(source, [])[row_index], source_column))
            transform.apply_transforms(line, headers, config)
            writer.writerow(line)

    return {"output_path": output_path, "row_count": len(order), "headers": headers}


def source_rows(mesh_data):
    if mesh_data.get(schema.VSIN):
        return mesh_data[schema.VSIN]
    return mesh_data.get(schema.VSOUT, [])
