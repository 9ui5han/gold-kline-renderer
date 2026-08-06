import unittest

from pydantic import ValidationError

from app import main


def _history():
    return [
        {
            "time": f"2026-08-04T00:{index:02d}:00Z",
            "open": 4000 + index,
            "high": 4001 + index,
            "low": 3999 + index,
            "close": 4000.5 + index,
            "volume": 100,
        }
        for index in range(20)
    ]


class RenderRequestContractTests(unittest.TestCase):
    def test_structure_path_mode_rejects_empty_forecast_paths(self):
        with self.assertRaisesRegex(ValidationError, "forecast_paths"):
            main.RenderRequest(
                request_id="empty-paths",
                symbol="XAUUSD",
                timeframe="15m",
                data_as_of="2026-08-04T04:45:00Z",
                historical_candles=_history(),
                analysis_forecast={"trend": "sideways"},
                forecast_paths={},
                narration={"segments": []},
                style={"forecast_mode": "structure_paths"},
            )

    def test_structure_path_mode_accepts_complete_path_contract(self):
        payload = main.RenderRequest(
            request_id="complete-paths",
            symbol="XAUUSD",
            timeframe="15m",
            data_as_of="2026-08-04T04:45:00Z",
            historical_candles=_history(),
            analysis_forecast={"trend": "sideways"},
            forecast_paths={
                "schema_version": "structure-path-v1",
                "as_of": "2026-08-04T04:45:00Z",
                "timeframe": "15m",
                "primary_scenario": "sideways",
                "alternate_scenario": "up",
                "scenarios": [
                    {
                        "scenario_id": "sideways",
                        "path_points": [
                            {"resolved_value": 4000},
                            {"resolved_value": 4001},
                            {"resolved_value": 4000.5},
                        ],
                    },
                    {
                        "scenario_id": "up",
                        "path_points": [
                            {"resolved_value": 4000},
                            {"resolved_value": 4002},
                            {"resolved_value": 4004},
                        ],
                    },
                    {
                        "scenario_id": "down",
                        "path_points": [
                            {"resolved_value": 4000},
                            {"resolved_value": 3998},
                            {"resolved_value": 3996},
                        ],
                    },
                ],
            },
            narration={"segments": []},
            style={"forecast_mode": "structure_paths"},
        )

        self.assertEqual(payload.style.forecast_mode, "structure_paths")


if __name__ == "__main__":
    unittest.main()
