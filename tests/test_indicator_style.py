import unittest

from app.indicator_style import (
    DEFAULT_INDICATOR_PROFILE,
    IndicatorStyleError,
    detect_narrated_indicator_ids,
    filter_indicator_context,
    indicator_ids_for_role,
    normalize_indicator_profile,
    validate_narration_indicators,
)


class IndicatorStyleTests(unittest.TestCase):
    def test_dual_ema_is_one_indicator_family_with_two_components(self):
        profile = normalize_indicator_profile(DEFAULT_INDICATOR_PROFILE)

        self.assertEqual(profile["indicator_ids"], ["dual_ema"])
        self.assertEqual(profile["components"], ["ema20", "ema50"])
        self.assertEqual(len(profile["indicator_ids"]), 1)

    def test_more_than_two_indicator_families_is_rejected(self):
        profile = dict(DEFAULT_INDICATOR_PROFILE)
        profile["indicator_ids"] = ["dual_ema", "macd", "rsi"]

        with self.assertRaisesRegex(
            IndicatorStyleError,
            "INDICATOR_COUNT_EXCEEDED",
        ):
            normalize_indicator_profile(profile)

    def test_indicator_visibility_starts_with_primary_forecast(self):
        profile = normalize_indicator_profile(DEFAULT_INDICATOR_PROFILE)

        self.assertEqual(
            indicator_ids_for_role(profile, "technical_context"),
            {"allowed": [], "required": [], "rendered": []},
        )
        self.assertEqual(
            indicator_ids_for_role(profile, "primary_forecast"),
            {
                "allowed": ["dual_ema"],
                "required": ["dual_ema"],
                "rendered": ["dual_ema"],
            },
        )
        self.assertEqual(
            indicator_ids_for_role(profile, "closing_question"),
            {
                "allowed": ["dual_ema"],
                "required": [],
                "rendered": ["dual_ema"],
            },
        )

    def test_filter_exposes_only_dual_ema_indicator_facts(self):
        technical = {
            "schema_version": "technical-contract-v1",
            "indicator_facts": {
                "schema_version": "indicator-facts-v2",
                "primary_timeframe": "1h",
                "timeframes": {
                    "1h": {
                        "closed_count": 199,
                        "last_close": 4434.88,
                        "ema20": 4444.04,
                        "ema50": 4438.70,
                        "rsi14": 46.88,
                        "atr14": 23.41,
                        "macd": -2.4,
                        "vwap": 4405.94,
                        "structure": "range_or_mixed",
                    }
                },
            },
        }

        result = filter_indicator_context(
            technical,
            DEFAULT_INDICATOR_PROFILE,
        )

        self.assertEqual(result["indicator_ids"], ["dual_ema"])
        self.assertEqual(
            result["facts"],
            {
                "closed_count": 199,
                "last_close": 4434.88,
                "ema20": 4444.04,
                "ema50": 4438.7,
                "ema_alignment": "ema20_above_ema50",
                "close_vs_ema20": "below",
                "close_vs_ema50": "below",
            },
        )
        fact_keys = set(result["facts"])
        for forbidden in ("rsi14", "atr14", "macd", "vwap"):
            self.assertNotIn(forbidden, fact_keys)

    def test_unselected_indicator_in_narration_is_rejected(self):
        text = "EMA20 remains above EMA50, while the MACD histogram expands."

        self.assertEqual(
            detect_narrated_indicator_ids(text),
            ["dual_ema", "macd"],
        )
        with self.assertRaisesRegex(
            IndicatorStyleError,
            "NARRATION_INDICATOR_FORBIDDEN:macd",
        ):
            validate_narration_indicators(
                text,
                allowed_indicator_ids=["dual_ema"],
                required_indicator_ids=["dual_ema"],
            )

    def test_required_dual_ema_must_appear_in_prediction_narration(self):
        with self.assertRaisesRegex(
            IndicatorStyleError,
            "NARRATION_INDICATOR_REQUIRED:dual_ema",
        ):
            validate_narration_indicators(
                "Price remains inside the observation zone.",
                allowed_indicator_ids=["dual_ema"],
                required_indicator_ids=["dual_ema"],
            )


if __name__ == "__main__":
    unittest.main()
