import unittest

from app.kline_precision import normalize_kline_numbers


class KlinePrecisionTests(unittest.TestCase):
    def test_rounds_kline_prices_indicators_and_forecast_anchors(self):
        payload = {
            "last_close": 4434.876,
            "indicator_facts": {
                "timeframes": {
                    "1h": {
                        "open": 4423.987,
                        "high": 4442.555,
                        "low": 4422.604,
                        "close": 4434.876,
                        "volume": 6117.456,
                        "ema20": 4444.0351,
                        "atr14": 23.4091,
                        "rsi14": 46.8817,
                        "vwap": 4405.9376,
                        "closed_count": 199,
                    }
                }
            },
            "authoritative_price_map": {
                "CURRENT": 4434.876,
                "OPEN_UPSIDE": 4452.4382686525,
            },
            "path_points": [
                {"resolved_value": 4452.4382686525, "time_ratio": 0.6},
            ],
            "duration_target_sec": 3.333,
            "repair_count": 1,
        }

        normalized = normalize_kline_numbers(payload)

        self.assertEqual(normalized["last_close"], 4434.88)
        candle = normalized["indicator_facts"]["timeframes"]["1h"]
        self.assertEqual(candle["open"], 4423.99)
        self.assertEqual(candle["high"], 4442.56)
        self.assertEqual(candle["low"], 4422.6)
        self.assertEqual(candle["close"], 4434.88)
        self.assertEqual(candle["volume"], 6117.46)
        self.assertEqual(candle["ema20"], 4444.04)
        self.assertEqual(candle["atr14"], 23.41)
        self.assertEqual(candle["rsi14"], 46.88)
        self.assertEqual(candle["vwap"], 4405.94)
        self.assertEqual(candle["closed_count"], 199)
        self.assertEqual(
            normalized["authoritative_price_map"]["OPEN_UPSIDE"], 4452.44
        )
        self.assertEqual(normalized["path_points"][0]["resolved_value"], 4452.44)
        self.assertEqual(normalized["path_points"][0]["time_ratio"], 0.6)
        self.assertEqual(normalized["duration_target_sec"], 3.333)
        self.assertEqual(normalized["repair_count"], 1)

    def test_rounds_decimal_prices_inside_kline_narrative_fields(self):
        payload = {
            "technical_summary": "Last close was 4434.876 and ATR was 23.4091.",
            "condition": "Price must hold above 4452.4382686525.",
            "duration_target_sec": 3.333,
        }

        normalized = normalize_kline_numbers(payload)

        self.assertEqual(
            normalized["technical_summary"],
            "Last close was 4434.88 and ATR was 23.41.",
        )
        self.assertEqual(
            normalized["condition"],
            "Price must hold above 4452.44.",
        )
        self.assertEqual(normalized["duration_target_sec"], 3.333)


if __name__ == "__main__":
    unittest.main()
