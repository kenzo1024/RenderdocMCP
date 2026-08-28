"""Run one pixel-shader roundtrip after qrenderdoc finishes loading a capture."""

import json
import os
import sys
import traceback

import qrenderdoc as qrd


PROJECT_SRC = r"D:\_Proj\renderdoc-mcp\src"
EVENT_ID = 871
HLSL_PATH = r"D:\_Proj\QTAU6\Assets\EndField\终末地逆向\Uber源码比对.dart"
OUTPUT_DIR = r"C:\Users\Administrator\Desktop"
RESULT_PATH = os.path.join(OUTPUT_DIR, "eid871_shader_roundtrip_result.json")

if PROJECT_SRC not in sys.path:
    sys.path.insert(0, PROJECT_SRC)

from renderdoc_mcp.shader_roundtrip import export_pixel_shader_roundtrip


class RoundtripRunner(qrd.CaptureViewer):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.finished = False
        ctx.AddCaptureViewer(self)

    def OnCaptureLoaded(self):
        self.run()

    def OnCaptureClosed(self):
        pass

    def OnSelectedEventChanged(self, event_id):
        pass

    def OnEventChanged(self, event_id):
        pass

    def run(self):
        if self.finished:
            return
        self.finished = True
        payload = {"ok": False, "event_id": EVENT_ID}
        ids = {"original": None, "new": None}

        def replay(controller):
            try:
                result, original_id, new_id = export_pixel_shader_roundtrip(
                    controller,
                    EVENT_ID,
                    HLSL_PATH,
                    OUTPUT_DIR,
                )
                payload.update(result)
                payload["ok"] = True
                ids["original"] = original_id
                ids["new"] = new_id
            except Exception as exc:
                payload["error"] = str(exc)
                payload["traceback"] = traceback.format_exc()

        self.ctx.Replay().BlockInvoke(replay)
        if payload["ok"]:
            self.ctx.UnregisterReplacement(ids["original"])
            self.ctx.RegisterReplacement(ids["original"], ids["new"])
        with open(RESULT_PATH, "w", encoding="utf-8") as result_file:
            json.dump(payload, result_file, ensure_ascii=False, indent=2)


runner = RoundtripRunner(pyrenderdoc)
if pyrenderdoc.IsCaptureLoaded():
    runner.run()
