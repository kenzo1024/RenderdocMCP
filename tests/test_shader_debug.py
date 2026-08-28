import unittest

from renderdoc_mcp.shader_debug import compare_pixel_traces


def state(step, instruction, disassembly):
    return {
        "step": step,
        "next_instruction": instruction,
        "next_disassembly": disassembly,
        "changes": [],
    }


class ShaderDebugCompareTests(unittest.TestCase):
    def test_reports_first_dynamic_opcode_divergence(self):
        reset = {
            "states": [state(0, 0, "mov r0, v0"), state(1, 1, "iadd r1, r0, l(1)")],
            "final_outputs": {},
        }
        applied = {
            "states": [
                state(0, 0, "mov r0, v0"),
                state(1, 1, "itof r1, r0"),
                state(2, 2, "iadd r1, r1, l(1)"),
            ],
            "final_outputs": {},
        }

        result = compare_pixel_traces(reset, applied)

        divergence = result["first_opcode_divergence"]
        self.assertEqual(divergence["reset_range"], [1, 1])
        self.assertEqual(divergence["applied_range"], [1, 2])

    def test_compares_final_outputs_as_raw_bits(self):
        reset = {
            "states": [],
            "final_outputs": {"o0": {"raw_u32": [1, 2, 3, 4]}},
        }
        applied = {
            "states": [],
            "final_outputs": {"o0": {"raw_u32": [1, 2, 9, 4]}},
        }

        result = compare_pixel_traces(reset, applied)

        self.assertFalse(result["final_outputs"][0]["exact_raw_match"])


if __name__ == "__main__":
    unittest.main()
