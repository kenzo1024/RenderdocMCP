import csv
import os
import tempfile
import unittest

from renderdoc_mcp.resource_export import csv_export, schema
from renderdoc_mcp.resource_export import preset


class ResourceExportTests(unittest.TestCase):
    def test_writes_reversed_axis_mapped_flipped_uv_csv(self):
        mesh_data = {
            "vsin": [
                {"idx": 0, "ATTRIBUTE0": [1, 2, 3]},
                {"idx": 1, "ATTRIBUTE0": [4, 5, 6]},
                {"idx": 2, "ATTRIBUTE0": [7, 8, 9]},
            ],
            "vsout": [
                {"idx": 0, "TEXCOORD0": [0.25, 0.10]},
                {"idx": 1, "TEXCOORD0": [0.50, 0.20]},
                {"idx": 2, "TEXCOORD0": [0.75, 0.30]},
            ],
        }
        path = os.path.join(tempfile.mkdtemp(), "mesh.csv")

        csv_export.write_asset_csv(mesh_data, path, schema.default_export_config())

        with open(path, encoding="utf-8") as f:
            rows = list(csv.reader(f))

        self.assertEqual(rows[0], ["IDX", "POSITION.x", "POSITION.y", "POSITION.z", "TEXCOORD0.x", "TEXCOORD0.y"])
        self.assertEqual(rows[1], ["0", "2.0", "3.0", "1.0", "0.25", "0.9"])
        self.assertEqual(rows[2], ["2", "8.0", "9.0", "7.0", "0.75", "0.7"])
        self.assertEqual(rows[3], ["1", "5.0", "6.0", "4.0", "0.5", "0.8"])

    def test_preset_roundtrip_normalizes_export_flags(self):
        preset_dir = tempfile.mkdtemp()
        config = schema.default_export_config()

        preset.save_preset("ue default", config, preset_dir)
        loaded = preset.load_preset("ue default", preset_dir)

        self.assertEqual(loaded[schema.AXIS_X], "+Y")
        self.assertTrue(loaded[schema.FLIP_UV_V])
        self.assertTrue(loaded[schema.EXPORT_UV])
        self.assertFalse(loaded[schema.EXPORT_NORMAL])


if __name__ == "__main__":
    unittest.main()
