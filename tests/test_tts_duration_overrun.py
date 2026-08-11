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

    def test_accepts_real_eighty_second_audio_with_less_than_five_second_overrun(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            output = Path(directory) / "output.wav"
            commands = []
            with patch("app.main.probe_duration", side_effect=[87.003, 84.469]), patch(
                "app.main.run_command", side_effect=lambda command: commands.append(command)
            ):
                raw, scale = normalize_audio_to_target_duration(source, output, 80.0)
        self.assertEqual(raw, 87.003)
        self.assertAlmostEqual(scale, 84.469 / 87.003)
        self.assertIn("atempo=1.03000000", commands[0])
        self.assertEqual(commands[0][commands[0].index("-t") + 1], "84.469")

    def test_rejects_audio_still_more_than_five_seconds_over_target(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "app.main.probe_duration", return_value=88.0
        ):
            with self.assertRaisesRegex(RuntimeError, "allowed_overrun_sec=5.000"):
                normalize_audio_to_target_duration(
                    Path(directory) / "source.wav",
                    Path(directory) / "output.wav",
                    80.0,
                )

    def test_five_second_overrun_contract_applies_to_all_supported_durations(self):
        for target in (60.0, 80.0, 90.0, 120.0):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                raw_duration = (target + 4.5) * 1.03
                output_duration = target + 4.5
                with patch(
                    "app.main.probe_duration",
                    side_effect=[raw_duration, output_duration],
                ), patch("app.main.run_command"):
                    raw, scale = normalize_audio_to_target_duration(
                        Path(directory) / "source.wav",
                        Path(directory) / "output.wav",
                        target,
                    )
                self.assertAlmostEqual(raw, raw_duration)
                self.assertAlmostEqual(scale, output_duration / raw_duration)

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
