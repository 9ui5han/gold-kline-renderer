import json
import unittest

from app.indicator_style import DEFAULT_INDICATOR_PROFILE
from app.main import SegmentPlanStepRequest
from app.segment_plan_validation import _rescale_plan_segment, process_segment_plan_step


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
    "edge_duration_policy": {
        "ratio": 0.05,
        "min_sec": 5.5,
        "sections": ["intro", "outro"],
        "reallocation": "average_from_eligible_middle_segments",
        "protected_sections": ["analysis"],
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
            "camera_motion": "static_hold" if section == "outro" else "micro_drift",
            "highlight_levels": [],
            "show_volume": False,
            "show_macro_marker": False,
        },
        "scenes": [{
            "scene_id": f"scene-{order}",
            "template_id": template,
            "start_sec": 0,
            "duration_sec": duration,
            "camera_motion": "static_hold" if section == "outro" else "micro_drift",
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
        _segment(1, "intro", "opening_hook", 5.5, "hook_chart", "hook_text"),
        _segment(2, "analysis", "technical_context", 17, "chart_push"),
        _segment(3, "primary_path", "primary_forecast", 72, "path_reveal"),
        _segment(4, "outro", "closing_question", 5.5, "closing_card", "closing_question"),
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

INDICATOR_CONTEXT = {
    "schema_version": "indicator-context-v1",
    "style_id": "dual_ema_trend",
    "primary_timeframe": "1h",
    "indicator_ids": ["dual_ema"],
    "facts": {
        "closed_count": 80,
        "last_close": 4616.9,
        "ema20": 4612.5,
        "ema50": 4608.0,
        "ema_alignment": "ema20_above_ema50",
        "close_vs_ema20": "above",
        "close_vs_ema50": "above",
    },
}


class SegmentPlanValidationTests(unittest.TestCase):
    def test_rescale_preserves_scene_total_after_three_decimal_rounding(self):
        """The rounded scene timeline must still equal its segment duration."""
        segment = _segment(3, "primary_path", "primary_forecast", 20, "path_reveal")
        segment["scenes"] = [
            {
                **segment["scenes"][0],
                "scene_id": "scene-3a",
                "start_sec": 0,
                "duration_sec": 10,
            },
            {
                **segment["scenes"][0],
                "scene_id": "scene-3b",
                "start_sec": 10,
                "duration_sec": 5,
            },
            {
                **segment["scenes"][0],
                "scene_id": "scene-3c",
                "start_sec": 15,
                "duration_sec": 5,
            },
        ]

        _rescale_plan_segment(segment, 19.375)

        scenes = segment["scenes"]
        self.assertEqual(sum(scene["duration_sec"] for scene in scenes), 19.375)
        self.assertEqual([scene["start_sec"] for scene in scenes], [0.0, 9.688, 14.532])

    def test_one_millisecond_scene_rounding_residual_is_reconciled(self):
        """A one-millisecond rounding residual belongs to the final scene."""
        candidate = json.loads(json.dumps(VALID_PLAN))
        scene = candidate["segments"][2]["scenes"][0]
        candidate["segments"][2]["scenes"] = [
            {
                **scene,
                "scene_id": "scene-3a",
                "start_sec": 0,
                "duration_sec": 36,
            },
            {
                **scene,
                "scene_id": "scene-3b",
                "start_sec": 36,
                "duration_sec": 18,
            },
            {
                **scene,
                "scene_id": "scene-3c",
                "start_sec": 54,
                "duration_sec": 18.001,
            },
        ]

        result = self._step(candidate, 0)

        self.assertEqual(result["action"], "pass")
        contract = json.loads(json.loads(result["result_json"])["segment_plan_v1_json"])
        scenes = contract["segment_plan"]["segments"][2]["scenes"]
        self.assertEqual([scene["duration_sec"] for scene in scenes], [36, 18, 18])
        self.assertEqual([scene["start_sec"] for scene in scenes], [0, 36, 54])

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
            indicator_profile=DEFAULT_INDICATOR_PROFILE,
            indicator_context=INDICATOR_CONTEXT,
        )

    def test_valid_plan_passes(self):
        result = self._step(VALID_PLAN, 0)
        self.assertEqual(result["action"], "pass")
        self.assertTrue(result["done"])
        final = json.loads(result["result_json"])
        self.assertTrue(final["segment_plan_valid"])

    def test_missing_indicator_contract_requests_repair(self):
        """Removing the new authoritative style contract must block TOOL-07."""
        result = process_segment_plan_step(
            VALID_PLAN, CONTEXT["segment_budget"], CONTEXT["technical_facts"],
            CONTEXT["market_analysis"], CONTEXT["validated_levels"],
            CONTEXT["structure_paths"], CONTEXT["forecast_framework"],
            CONTEXT["macro_timing"], 0,
        )

        self.assertEqual(result["action"], "repair")
        errors = json.loads(result["repair_prompt_json"])["validator_errors"]
        self.assertIn("INDICATOR_PROFILE_REQUIRED", errors)

    def test_invalid_indicator_profile_requests_repair(self):
        profile = dict(DEFAULT_INDICATOR_PROFILE)
        profile["indicator_ids"] = ["macd"]
        result = process_segment_plan_step(
            VALID_PLAN, CONTEXT["segment_budget"], CONTEXT["technical_facts"],
            CONTEXT["market_analysis"], CONTEXT["validated_levels"],
            CONTEXT["structure_paths"], CONTEXT["forecast_framework"],
            CONTEXT["macro_timing"], 0,
            indicator_profile=profile,
            indicator_context=INDICATOR_CONTEXT,
        )
        errors = json.loads(result["repair_prompt_json"])["validator_errors"]
        self.assertIn("INDICATOR_NOT_REGISTERED:macd", errors)

    def test_llm_cannot_mutate_indicator_profile(self):
        candidate = json.loads(json.dumps(VALID_PLAN))
        candidate["indicator_profile"] = {
            "schema_version": "indicator-profile-v1",
            "indicator_ids": ["macd"],
        }

        result = self._step(candidate, 0)

        errors = json.loads(result["repair_prompt_json"])["validator_errors"]
        self.assertIn("INDICATOR_PROFILE_MUTATED", errors)

    def test_pre_forecast_indicator_anchor_requests_repair(self):
        candidate = json.loads(json.dumps(VALID_PLAN))
        candidate["segments"][0]["fact_anchor_ids"] = ["indicator:ema20"]
        candidate["segments"][0]["visual"]["indicator_focus"] = True

        result = self._step(candidate, 0)

        errors = json.loads(result["repair_prompt_json"])["validator_errors"]
        self.assertTrue(any("INDICATOR_VISIBILITY_BEFORE_ROLE" in item for item in errors))

    def test_repair_loop_and_final_contract_preserve_indicator_inputs(self):
        candidate = json.loads(json.dumps(VALID_PLAN))
        candidate["segments"][-1]["scenes"][-1]["template_id"] = "chart_push"

        repair = self._step(candidate, 0)
        next_request = json.loads(repair["next_request_base_json"])
        prompt = json.loads(repair["repair_prompt_json"])

        self.assertEqual(next_request["indicator_profile"], DEFAULT_INDICATOR_PROFILE)
        self.assertEqual(next_request["indicator_context"], INDICATOR_CONTEXT)
        self.assertEqual(prompt["indicator_profile"], DEFAULT_INDICATOR_PROFILE)
        self.assertEqual(prompt["indicator_context"], INDICATOR_CONTEXT)

        passed = self._step(VALID_PLAN, 0)
        contract = json.loads(json.loads(passed["result_json"])["segment_plan_v1_json"])
        self.assertEqual(contract["indicator_profile"], DEFAULT_INDICATOR_PROFILE)
        self.assertEqual(contract["indicator_context"], INDICATOR_CONTEXT)

    def test_http_request_model_keeps_indicator_contract_objects(self):
        payload = SegmentPlanStepRequest.model_validate({
            "candidate": VALID_PLAN,
            "segment_budget": BUDGET,
            "technical_facts": CONTEXT["technical_facts"],
            "market_analysis": CONTEXT["market_analysis"],
            "validated_levels": CONTEXT["validated_levels"],
            "structure_paths": CONTEXT["structure_paths"],
            "forecast_framework": CONTEXT["forecast_framework"],
            "macro_timing": CONTEXT["macro_timing"],
            "indicator_profile": DEFAULT_INDICATOR_PROFILE,
            "indicator_context": INDICATOR_CONTEXT,
        })

        self.assertEqual(payload.indicator_profile, DEFAULT_INDICATOR_PROFILE)
        self.assertEqual(payload.indicator_context, INDICATOR_CONTEXT)

    def test_invalid_plan_requests_repair(self):
        candidate = json.loads(json.dumps(VALID_PLAN))
        candidate["segments"][-1]["scenes"][-1]["template_id"] = "chart_push"
        result = self._step(candidate, 0)
        self.assertEqual(result["action"], "repair")
        self.assertFalse(result["done"])
        prompt = json.loads(result["repair_prompt_json"])
        self.assertTrue(any("closing_card" in item for item in prompt["validator_errors"]))

    def test_legacy_quoted_visual_booleans_are_canonicalized(self):
        candidate = json.loads(json.dumps(VALID_PLAN))
        for segment in candidate["segments"]:
            segment["visual"]["show_volume"] = "false"
            segment["visual"]["show_macro_marker"] = "false"
        result = self._step(candidate, 0)
        self.assertEqual(result["action"], "pass")
        final = json.loads(result["result_json"])
        contract = json.loads(final["segment_plan_v1_json"])
        visual = contract["segment_plan"]["segments"][0]["visual"]
        self.assertIs(visual["show_volume"], False)
        self.assertIs(visual["show_macro_marker"], False)

    def test_invalid_visual_boolean_requests_repair(self):
        candidate = json.loads(json.dumps(VALID_PLAN))
        candidate["segments"][0]["visual"]["show_volume"] = "disabled"
        result = self._step(candidate, 0)
        self.assertEqual(result["action"], "repair")
        prompt = json.loads(result["repair_prompt_json"])
        self.assertTrue(
            any("show_volume必须是Boolean" in item for item in prompt["validator_errors"])
        )

    def test_invalid_plan_fails_after_two_repairs(self):
        candidate = json.loads(json.dumps(VALID_PLAN))
        candidate["segments"] = []
        result = self._step(candidate, 2)
        self.assertEqual(result["action"], "fail")
        self.assertTrue(result["done"])
        final = json.loads(result["result_json"])
        self.assertFalse(final["segment_plan_valid"])

    def test_edge_duration_minimum_is_dynamic_and_enforced(self):
        candidate = json.loads(json.dumps(VALID_PLAN))
        candidate["segments"][0]["duration_target_sec"] = 5.4
        candidate["segments"][0]["scenes"][0]["duration_sec"] = 5.4
        candidate["segments"][2]["duration_target_sec"] = 72.1
        candidate["segments"][2]["scenes"][0]["duration_sec"] = 72.1
        result = self._step(candidate, 0)
        self.assertEqual(result["action"], "pass")
        contract = json.loads(json.loads(result["result_json"])["segment_plan_v1_json"])
        segments = contract["segment_plan"]["segments"]
        self.assertEqual(segments[0]["duration_target_sec"], 5.5)
        self.assertEqual(segments[-1]["duration_target_sec"], 5.5)

    def test_edge_reallocation_updates_middle_scenes(self):
        candidate = json.loads(json.dumps(VALID_PLAN))
        candidate["segments"][0]["duration_target_sec"] = 3
        candidate["segments"][0]["scenes"][0]["duration_sec"] = 3
        candidate["segments"][1]["duration_target_sec"] = 10
        candidate["segments"][1]["scenes"][0]["duration_sec"] = 10
        candidate["segments"][2]["duration_target_sec"] = 24
        candidate["segments"][2]["scenes"][0]["duration_sec"] = 24
        candidate["segments"][3] = _segment(
            4, "primary_path", "primary_forecast", 20, "path_reveal"
        )
        candidate["segments"].append(
            _segment(5, "outro", "closing_question", 3, "closing_card", "closing_question")
        )
        candidate["target_duration_sec"] = 60
        candidate["estimated_final_duration_sec"] = 60
        budget = json.loads(json.dumps(BUDGET))
        budget.update({
            "target_duration_sec": 60,
            "preferred_min_sec": 50,
            "preferred_max_sec": 70,
            "hard_min_sec": 40,
            "hard_max_sec": 80,
        })
        result = process_segment_plan_step(
            candidate,
            budget,
            CONTEXT["technical_facts"],
            CONTEXT["market_analysis"],
            CONTEXT["validated_levels"],
            CONTEXT["structure_paths"],
            CONTEXT["forecast_framework"],
            CONTEXT["macro_timing"],
            0,
            indicator_profile=DEFAULT_INDICATOR_PROFILE,
            indicator_context=INDICATOR_CONTEXT,
        )
        self.assertEqual(result["action"], "pass")
        contract = json.loads(json.loads(result["result_json"])["segment_plan_v1_json"])
        durations = [
            segment["duration_target_sec"]
            for segment in contract["segment_plan"]["segments"]
        ]
        self.assertEqual(durations, [5.5, 10.0, 21.5, 17.5, 5.5])
        self.assertEqual(
            contract["segment_plan"]["segments"][2]["scenes"][0]["duration_sec"],
            21.5,
        )

    def test_edge_floor_uses_target_ratio_for_longer_video(self):
        budget = json.loads(json.dumps(BUDGET))
        budget["target_duration_sec"] = 120
        candidate = json.loads(json.dumps(VALID_PLAN))
        candidate["target_duration_sec"] = 120
        candidate["estimated_final_duration_sec"] = 120
        candidate["segments"][0]["duration_target_sec"] = 6
        candidate["segments"][0]["scenes"][0]["duration_sec"] = 6
        candidate["segments"][1]["duration_target_sec"] = 19
        candidate["segments"][1]["scenes"][0]["duration_sec"] = 19
        candidate["segments"][2]["duration_target_sec"] = 89
        candidate["segments"][2]["scenes"][0]["duration_sec"] = 89
        candidate["segments"][3]["duration_target_sec"] = 6
        candidate["segments"][3]["scenes"][0]["duration_sec"] = 6
        result = process_segment_plan_step(
            candidate,
            budget,
            CONTEXT["technical_facts"],
            CONTEXT["market_analysis"],
            CONTEXT["validated_levels"],
            CONTEXT["structure_paths"],
            CONTEXT["forecast_framework"],
            CONTEXT["macro_timing"],
            0,
            indicator_profile=DEFAULT_INDICATOR_PROFILE,
            indicator_context=INDICATOR_CONTEXT,
        )
        self.assertEqual(result["action"], "pass")

    def test_edge_minimum_reports_infeasible_short_target(self):
        budget = json.loads(json.dumps(BUDGET))
        budget["target_duration_sec"] = 12
        candidate = json.loads(json.dumps(VALID_PLAN))
        candidate["target_duration_sec"] = 12
        candidate["estimated_final_duration_sec"] = 12
        candidate["segments"][0]["duration_target_sec"] = 5.5
        candidate["segments"][0]["scenes"][0]["duration_sec"] = 5.5
        candidate["segments"][1]["duration_target_sec"] = 2
        candidate["segments"][1]["scenes"][0]["duration_sec"] = 2
        candidate["segments"][2]["duration_target_sec"] = 2
        candidate["segments"][2]["scenes"][0]["duration_sec"] = 2
        candidate["segments"][3]["duration_target_sec"] = 2.5
        candidate["segments"][3]["scenes"][0]["duration_sec"] = 2.5
        result = process_segment_plan_step(
            candidate,
            budget,
            CONTEXT["technical_facts"],
            CONTEXT["market_analysis"],
            CONTEXT["validated_levels"],
            CONTEXT["structure_paths"],
            CONTEXT["forecast_framework"],
            CONTEXT["macro_timing"],
            0,
        )
        prompt = json.loads(result["repair_prompt_json"])
        self.assertTrue(any("EDGE_DURATION_INFEASIBLE" in item for item in prompt["validator_errors"]))


if __name__ == "__main__":
    unittest.main()
