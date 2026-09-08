import json
import unittest

from app.segment_plan_validation import process_segment_plan_step
from app.visual_fact_catalog import build_visual_fact_catalog


TECHNICAL = {
    "last_close": 4434.88,
    "market_structure": "mixed",
    "technical_summary": "Higher timeframe remains mixed.",
}
LEVELS = {
    "levels": [
        {
            "level_id": "R1",
            "side": "resistance",
            "zone_low": 4448.0,
            "zone_high": 4452.0,
            "center": 4450.0,
        }
    ]
}
PATHS = {
    "scenarios": [
        {
            "scenario_id": "scenario_up",
            "label": "Upside confirmation",
            "condition": "Price breaks above the upside anchor.",
            "invalidation": "Price falls back below the upside anchor.",
            "path_points": [
                {"ref": "CURRENT", "resolved_value": 4434.88, "time_ratio": 0.0},
                {"ref": "OPEN_UPSIDE", "resolved_value": 4452.44, "time_ratio": 1.0},
            ],
        }
    ]
}
FRAMEWORK = {
    "available": True,
    "direction_state": "wait_for_confirmation",
    "limitations": ["Framework is conditional only."],
}
MACRO = {
    "events": [
        {
            "event_id": "fed-rate",
            "scheduled_time_utc": "2026-09-08T18:00:00Z",
            "title": "Fed rate decision",
        }
    ]
}


class VisualFactCatalogTests(unittest.TestCase):
    def test_catalog_copies_validated_values_and_derives_direction(self):
        catalog = build_visual_fact_catalog(
            TECHNICAL, {"technical_summary": "Market summary."}, LEVELS,
            PATHS, FRAMEWORK, MACRO,
        )
        self.assertEqual(catalog["schema_version"], "visual-fact-catalog-v1")
        facts = {item["anchor_id"]: item for item in catalog["facts"]}
        self.assertEqual(facts["technical:last_close"]["price"], 4434.88)
        self.assertEqual(facts["level:R1"]["center_price"], 4450.0)
        self.assertEqual(facts["scenario:scenario_up"]["direction"], "up")
        self.assertEqual(facts["macro:fed-rate"]["scheduled_time_utc"], "2026-09-08T18:00:00Z")

    def test_unknown_market_structure_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "VISUAL_TEXT_ENUM_UNKNOWN"):
            build_visual_fact_catalog(
                {**TECHNICAL, "market_structure": "invented"},
                {}, LEVELS, PATHS, FRAMEWORK, MACRO,
            )

    def test_segment_contract_contains_catalog_without_changing_outer_outputs(self):
        from copy import deepcopy
        from tests.test_segment_plan_validation import BUDGET, CONTEXT, VALID_PLAN

        candidate = deepcopy(VALID_PLAN)
        for segment in candidate["segments"]:
            if segment["section"] != "outro":
                segment["visual"]["camera_motion"] = "micro_drift"
                segment["scenes"][0]["camera_motion"] = "micro_drift"
        result = process_segment_plan_step(
            candidate, BUDGET, CONTEXT["technical_facts"],
            CONTEXT["market_analysis"], CONTEXT["validated_levels"],
            CONTEXT["structure_paths"], CONTEXT["forecast_framework"],
            CONTEXT["macro_timing"], 0,
        )
        self.assertEqual(result["action"], "pass")
        self.assertEqual(set(result), {
            "schema_version", "action", "done", "result_json",
            "repair_prompt_json", "next_request_base_json",
        })
        contract = json.loads(json.loads(result["result_json"])["segment_plan_v1_json"])
        self.assertEqual(contract["visual_fact_catalog"]["schema_version"], "visual-fact-catalog-v1")

    def test_unknown_anchor_requests_repair(self):
        from copy import deepcopy
        from tests.test_segment_plan_validation import BUDGET, CONTEXT, VALID_PLAN

        candidate = deepcopy(VALID_PLAN)
        candidate["segments"][0]["fact_anchor_ids"] = ["level:R9"]
        result = process_segment_plan_step(
            candidate, BUDGET, CONTEXT["technical_facts"],
            CONTEXT["market_analysis"], CONTEXT["validated_levels"],
            CONTEXT["structure_paths"], CONTEXT["forecast_framework"],
            CONTEXT["macro_timing"], 0,
        )
        self.assertEqual(result["action"], "repair")
        errors = json.loads(json.loads(result["result_json"])["segment_errors_json"])
        self.assertTrue(any("未知fact_anchor_id=level:R9" in item for item in errors))

    def test_static_hold_is_only_allowed_for_closing_card(self):
        from copy import deepcopy
        from tests.test_segment_plan_validation import BUDGET, CONTEXT, VALID_PLAN

        candidate = deepcopy(VALID_PLAN)
        candidate["segments"][1]["visual"]["camera_motion"] = "static_hold"
        candidate["segments"][1]["scenes"][0]["camera_motion"] = "static_hold"
        result = process_segment_plan_step(
            candidate, BUDGET, CONTEXT["technical_facts"],
            CONTEXT["market_analysis"], CONTEXT["validated_levels"],
            CONTEXT["structure_paths"], CONTEXT["forecast_framework"],
            CONTEXT["macro_timing"], 0,
        )
        self.assertEqual(result["action"], "repair")
        errors = json.loads(json.loads(result["result_json"])["segment_errors_json"])
        self.assertTrue(any("static_hold仅允许closing_card" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
