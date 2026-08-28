import os
import tempfile
import unittest

from PIL import Image

from renderdoc_mcp.pixel_compare import compare_roundtrip_targets


class PixelCompareTests(unittest.TestCase):
    def test_uses_requested_comparison_pair(self):
        with tempfile.TemporaryDirectory() as output_dir:
            reference_path = os.path.join(output_dir, "reference.png")
            applied_path = os.path.join(output_dir, "applied.png")
            Image.new("RGBA", (1, 1), (1, 2, 3, 255)).save(reference_path)
            Image.new("RGBA", (1, 1), (1, 2, 3, 255)).save(applied_path)

            result = compare_roundtrip_targets(
                {
                    "comparison_pair": ["reference", "applied"],
                    "targets": {
                        "reference": {"0": {"slot": 0, "path": reference_path}},
                        "applied": {"0": {"slot": 0, "path": applied_path}},
                    },
                }
            )

            self.assertEqual(result["comparison_pair"], ["reference", "applied"])
            self.assertEqual(result["targets"][0]["differing_pixels"], 0)

    def test_reports_identical_and_different_pixels(self):
        with tempfile.TemporaryDirectory() as output_dir:
            reset_path = os.path.join(output_dir, "reset.png")
            applied_path = os.path.join(output_dir, "applied.png")
            reset_raw_path = os.path.join(output_dir, "reset.bin")
            applied_raw_path = os.path.join(output_dir, "applied.bin")
            Image.new("RGBA", (2, 1), (10, 20, 30, 255)).save(reset_path)
            applied = Image.new("RGBA", (2, 1), (10, 20, 30, 255))
            applied.putpixel((1, 0), (12, 20, 30, 255))
            applied.save(applied_path)
            with open(reset_raw_path, "wb") as raw_file:
                raw_file.write(b"\x00\x01")
            with open(applied_raw_path, "wb") as raw_file:
                raw_file.write(b"\x00\x02")

            result = compare_roundtrip_targets(
                {
                    "targets": {
                        "reset": {"0": {"slot": 0, "path": reset_path, "raw_path": reset_raw_path}},
                        "applied": {"0": {"slot": 0, "path": applied_path, "raw_path": applied_raw_path}},
                    }
                },
                threshold=1,
            )

            target = result["targets"][0]
            self.assertEqual(target["status"], "ok")
            self.assertEqual(target["differing_pixels"], 1)
            self.assertEqual(target["max_channel_delta"], 2)
            self.assertEqual(target["raw"]["differing_bytes"], 1)
            self.assertFalse(target["raw"]["exact_match"])
            self.assertTrue(os.path.isfile(target["diff_path"]))
            self.assertEqual(target["candidate_pixels"][0]["x"], 1)
            self.assertEqual(target["candidate_pixels"][0]["y"], 0)


if __name__ == "__main__":
    unittest.main()
