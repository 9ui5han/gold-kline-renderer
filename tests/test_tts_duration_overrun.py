import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import normalize_audio_to_target_duration


class TtsDurationOverrunTests(unittest.TestCase):
    def test_accepts_half_second_overrun_after_maximum_natural_tempo(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            output = Path(directory) / "output.wav"
            commands = []
            with patch("app.main.probe_duration", side_effect=[82.941, 80.525]), patch(
                "app.main.run_command", side_effect=lambda command: commands.append(command)
            ):
                raw, scale = normalize_audio_to_target_duration(source, output, 80.0)
        self.assertEqual(raw, 82.941)
        self.assertAlmostEqual(scale, 80.525 / 82.941)
        self.assertIn("atempo=1.03000000", commands[0])
        self.assertEqual(commands[0][commands[0].index("-t") + 1], "80.525")

    def test_rejects_audio_still_more_than_three_seconds_over_target(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "app.main.probe_duration", return_value=86.0
        ):
            with self.assertRaisesRegex(RuntimeError, "allowed_overrun_sec=3.000"):
                normalize_audio_to_target_duration(
                    Path(directory) / "source.wav",
                    Path(directory) / "output.wav",
                    80.0,
                )

    def test_in_range_audio_still_normalizes_exactly_to_target(self):
        with tempfile.TemporaryDirectory() as directory:
            commands = []
            with patch("app.main.probe_duration", side_effect=[82.0, 80.0]), patch(
                "app.main.run_command", side_effect=lambda command: commands.append(command)
            ):
                normalize_audio_to_target_duration(
                    Path(directory) / "source.wav",
                    Path(directory) / "output.wav",
                    80.0,
                )
        self.assertEqual(commands[0][commands[0].index("-t") + 1], "80.000")


if __name__ == "__main__":
    unittest.main()
