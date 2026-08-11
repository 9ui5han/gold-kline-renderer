import ast
import unittest
from pathlib import Path
from typing import Any


SOURCE = Path("app/main.py").resolve()
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
FUNCTIONS = {
    node.name: node
    for node in TREE.body
    if isinstance(node, ast.FunctionDef)
    and node.name in {"resolve_history_end_sec", "build_scene_intervals"}
}
MODULE = ast.Module(body=list(FUNCTIONS.values()), type_ignores=[])
namespace = {"Any": Any}
exec(compile(ast.fix_missing_locations(MODULE), str(SOURCE), "exec"), namespace)
resolve_history_end_sec = namespace["resolve_history_end_sec"]
build_scene_intervals = namespace["build_scene_intervals"]


def payload(cues=None):
    return {
        "style": {"theme": "light_tradingview"},
        "narration": {"subtitle_cues": cues or []},
        "timeline": {
            "history_ratio": 0.20,
            "history_render_fps": 10,
            "steady_render_fps": 5,
        },
    }


class TimelineRenderIntervalTests(unittest.TestCase):
    def test_freezes_at_aligned_technical_evidence_start(self):
        data = payload([
            {"segment_id": "technical_evidence", "start_sec": 8, "end_sec": 18}
        ])
        self.assertEqual(resolve_history_end_sec(data, 120), 8)

    def test_falls_back_to_twenty_percent(self):
        self.assertEqual(resolve_history_end_sec(payload(), 120), 24)

    def test_history_is_ten_fps_and_later_is_five_fps(self):
        intervals = build_scene_intervals(payload(), 10)
        history_end = 2.0
        early = [end - start for start, end in intervals if end <= history_end]
        later = [end - start for start, end in intervals if start >= history_end]
        self.assertTrue(early)
        self.assertTrue(later)
        self.assertTrue(all(abs(value - 0.1) < 1e-6 for value in early))
        self.assertTrue(all(value <= 0.2 + 1e-6 for value in later))


if __name__ == "__main__":
    unittest.main()
