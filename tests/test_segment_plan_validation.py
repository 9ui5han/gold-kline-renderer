import json
import unittest

from app.segment_plan_validation import process_segment_plan_step


BUDGET = {
    "target_duration_sec": 100,
    "preferred_min_sec": 90,
    "preferred_max_sec": 110,
    "hard_min_sec": 80,
    "hard_max_sec": 120,
    "min_segments": 4,
    "max_segments": 7,
    "has_relevant_macro": False,
    "section_ratio_policy": {
        "intro": [0.03, 0.06],
        "analysis": [0.15, 0.20],
        "macro": [0.00, 0.10],
        "forecast_total": [0.60, 0.75],
        "outro": [0.03, 0.06],
    },
    "visual_modes": ["chart_intro", "technical_analysis", "scenario_animation", "summary"],
    "camera_motions": ["static_hold", "micro_drift"],
}


def _segment(order, section, role, duration, template, event_type=None):
    events = []
    if event_type:
        events.append({
            "event_id": f"event-{order}",
            "event_type": event_type,
            "start_sec": 0,
            "duration_sec": duration,
            "fact_anchor_ids": ["technical:last_close"],
        })
    return {
        "segment_id": f"segment-{order}",
        "order": order,
        "section": section,
        "planning_role": role,
        "scenario_id": "s1" if section == "primary_path" else None,
        "fact_anchor_ids": ["technical:last_close"],
        "content_goal": "条件观察",
        "duration_target_sec": duration,
        "importance": "normal",
        "speech_style": "normal",
        "visual": {
            "visual_mode": "scenario_animation" if section == "primary_path" else "chart_intro",
            "source_timeframe": "1h",
            "camera_motion": "static_hold",
            "highlight_levels": [],
            "show_volume": False,
            "show_macro_marker": False,
        },
        "scenes": [{
            "scene_id": f"scene-{order}",
            "template_id": template,
            "start_sec": 0,
            "duration_sec": duration,
            "camera_motion": "static_hold",
            "overlay_events": events,
            "transition_out": {
                "type": "hard_cut",
                "duration_ms": 0,
            },
        }],
        "transition_out": {
            "type": "hard_cut",
            "duration_ms": 0,
        },
    }


VALID_PLAN = {
    "schema_version": "video-segment-plan-v1",
    "target_duration_sec": 100,
    "estimated_final_duration_sec": 100,
    "segments": [
        _segment(1, "intro", "opening_hook", 4, "hook_chart", "hook_text"),
        _segment(2, "analysis", "technical_context", 17, "chart_push"),
        _segment(3, "primary_path", "primary_forecast", 75, "path_reveal"),
        _segment(4, "outro", "closing_question", 4, "closing_card", "closing_question"),
    ],
}


CONTEXT = {
    "segment_budget": BUDGET,
    "technical_facts": {"last_close": 4616.9},
    "market_analysis": {},
    "validated_levels": {},
    "structure_paths": {"scenarios": [{"scenario_id": "s1"}]},
    "forecast_framework": {},
    "macro_timing": {"events": []},
}


class SegmentPlanValidationTests(unittest.TestCase):
    def _step(self, candidate, repair_count):
        return process_segment_plan_step(
            candidate,
            CONTEXT["segment_budget"],
            CONTEXT["technical_facts"],
            CONTEXT["market_analysis"],
            CONTEXT["validated_levels"],
            CONTEXT["structure_paths"],
            CONTEXT["forecast_framework"],
            CONTEXT["macro_timing"],
            repair_count,
        )

    def test_valid_plan_passes(self):
        result = self._step(VALID_PLAN, 0)
        self.assertEqual(result["action"], "pass")
        self.assertTrue(result["done"])
        final = json.loads(result["result_json"])
        self.assertTrue(final["segment_plan_valid"])

    def test_invalid_plan_requests_repair(self):
        candidate = json.loads(json.dumps(VALID_PLAN))
        candidate["segments"][-1]["scenes"][-1]["template_id"] = "chart_push"
        result = self._step(candidate, 0)
        self.assertEqual(result["action"], "repair")
        self.assertFalse(result["done"])
        prompt = json.loads(result["repair_prompt_json"])
        self.assertTrue(any("closing_card" in item for item in prompt["validator_errors"]))

    def test_invalid_plan_fails_after_two_repairs(self):
        candidate = json.loads(json.dumps(VALID_PLAN))
        candidate["segments"] = []
        result = self._step(candidate, 2)
        self.assertEqual(result["action"], "fail")
        self.assertTrue(result["done"])
        final = json.loads(result["result_json"])
        self.assertFalse(final["segment_plan_valid"])


if __name__ == "__main__":
    unittest.main()
