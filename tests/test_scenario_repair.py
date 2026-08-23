import json
import unittest
from app.scenario_repair import process_scenario_step, validate_candidate


ACTIVE_LEVELS = {
    "authoritative_price_map": {
        "CURRENT": 4616.9,
        "OPEN_UPSIDE": 4625.0,
        "OPEN_DOWNSIDE": 4608.0,
    },
    "active": [],
}

FRAMEWORK = {
    "available": False,
    "direction_state": "insufficient_levels",
    "midpoint_zone": None,
}

VALID_PATHS = {
    "schema_version": "structure-path-v1",
    "primary_scenario": "range",
    "alternate_scenario": None,
    "macro_markers": [],
    "scenarios": [
        {
            "scenario_id": "range",
            "label": "区间观察",
            "condition": "等待价格确认",
            "macro_relation": "none",
            "path_points": [
                {"ref": "CURRENT", "resolved_value": 4616.9, "time_ratio": 0},
                {"ref": "OPEN_UPSIDE", "resolved_value": 4625.0, "time_ratio": 1},
            ],
            "invalidation": "条件失效后停止采用",
            "reason": "仅作条件观察",
            "segment_paths": ["resistance_break"],
        }
    ],
}


class ScenarioRepairTests(unittest.TestCase):
    def test_valid_candidate_returns_string_contracts(self):
        result = validate_candidate(
            VALID_PATHS,
            ACTIVE_LEVELS,
            FRAMEWORK,
            {},
            {},
        )
        self.assertTrue(result["scenario_valid"])
        self.assertEqual(json.loads(result["scenario_errors_json"]), [])
        self.assertEqual(
            json.loads(result["candidate_json"])["schema_version"],
            "structure-path-v1",
        )

    def test_invalid_price_returns_repair_prompt(self):
        candidate = json.loads(json.dumps(VALID_PATHS))
        candidate["scenarios"][0]["path_points"][0]["resolved_value"] = 1
        result = validate_candidate(candidate, ACTIVE_LEVELS, FRAMEWORK, {}, {})
        self.assertFalse(result["scenario_valid"])
        errors = json.loads(result["scenario_errors_json"])
        self.assertTrue(any("resolved_value被修改" in item for item in errors))
        repair_payload = json.loads(result["repair_prompt_json"])
        self.assertEqual(repair_payload["validator_errors"], errors)

    def test_step_requests_repair_then_fails_at_limit(self):
        candidate = json.loads(json.dumps(VALID_PATHS))
        candidate["scenarios"][0]["path_points"][0]["resolved_value"] = 1

        repair = process_scenario_step(
            candidate, ACTIVE_LEVELS, FRAMEWORK, {}, {}, 0
        )
        self.assertEqual(repair["action"], "repair")
        self.assertFalse(repair["done"])
        self.assertEqual(
            json.loads(repair["next_request_base_json"])["repair_count"],
            1,
        )

        failed = process_scenario_step(
            candidate, ACTIVE_LEVELS, FRAMEWORK, {}, {}, 2
        )
        self.assertEqual(failed["action"], "fail")
        self.assertTrue(failed["done"])
        result = json.loads(failed["result_json"])
        self.assertFalse(result["scenario_valid"])

    def test_step_passes_valid_candidate(self):
        result = process_scenario_step(
            VALID_PATHS, ACTIVE_LEVELS, FRAMEWORK, {}, {}, 0
        )
        self.assertEqual(result["action"], "pass")
        self.assertTrue(result["done"])
        final = json.loads(result["result_json"])
        self.assertTrue(final["scenario_valid"])

if __name__ == "__main__":
    unittest.main()
