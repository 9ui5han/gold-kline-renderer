import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import normalize_audio_to_target_duration


class TtsDurationOverrunTests(unittest.TestCase):
    def test_accepts_real_short_audio_with_natural_slowdown_and_underrun(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            output = Path(directory) / "output.wav"
            commands = []
            with patch("app.main.probe_duration", side_effect=[75.0, 77.320]), patch(
                "app.main.run_command", side_effect=lambda command: commands.append(command)
            ):
                raw, scale = normalize_audio_to_target_duration(
                    source,
                    output,
                    80.0,
                    duration_tolerance_sec=3.0,
                )
        self.assertEqual(raw, 75.0)
        self.assertAlmostEqual(scale, 77.320 / 75.0)
        self.assertIn("atempo=0.97000000", commands[0])
        self.assertEqual(commands[0][commands[0].index("-t") + 1], "77.320")

    def test_rejects_audio_still_more_than_three_seconds_under_target(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "app.main.probe_duration", return_value=74.0
        ):
            with self.assertRaisesRegex(RuntimeError, "allowed_drift_sec=3.000"):
                normalize_audio_to_target_duration(
                    Path(directory) / "source.wav",
                    Path(directory) / "output.wav",
                    80.0,
                )

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
            with self.assertRaisesRegex(RuntimeError, "allowed_drift_sec=3.000"):
                normalize_audio_to_target_duration(
                    Path(directory) / "source.wav",
                    Path(directory) / "output.wav",
                    80.0,
                )

    def test_rejects_audio_still_more_than_three_seconds_over_target_after_tempo_clamp(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "app.main.probe_duration", return_value=88.0
        ):
            with self.assertRaisesRegex(RuntimeError, "allowed_drift_sec=3.000"):
                normalize_audio_to_target_duration(
                    Path(directory) / "source.wav",
                    Path(directory) / "output.wav",
                    80.0,
                )

    def test_request_tolerance_contract_applies_to_all_supported_durations(self):
        for target in (60.0, 80.0, 90.0, 120.0):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                raw_duration = (target + 2.8) * 1.03
                output_duration = target + 2.8
                with patch(
                    "app.main.probe_duration",
                    side_effect=[raw_duration, output_duration],
                ), patch("app.main.run_command"):
                    raw, scale = normalize_audio_to_target_duration(
                        Path(directory) / "source.wav",
                        Path(directory) / "output.wav",
                        target,
                        duration_tolerance_sec=3.0,
                    )
                self.assertAlmostEqual(raw, raw_duration)
                self.assertAlmostEqual(scale, output_duration / raw_duration)

    def test_request_tolerance_rejects_audio_beyond_the_dynamic_window(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "app.main.probe_duration", return_value=(80.0 + 3.1) * 1.03
        ):
            with self.assertRaisesRegex(RuntimeError, "allowed_drift_sec=3.000"):
                normalize_audio_to_target_duration(
                    Path(directory) / "source.wav",
                    Path(directory) / "output.wav",
                    80.0,
                    duration_tolerance_sec=3.0,
                )

    def test_current_sixty_second_failure_is_rejected_by_main_workflow_tolerance(self):
        """65.201 秒原音频经 1.03 倍自然加速后为 63.302 秒，仍超出 60±3。"""
        with tempfile.TemporaryDirectory() as directory, patch(
            "app.main.probe_duration", return_value=65.201
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                r"clamped=63\.302.*allowed_drift_sec=3\.000",
            ):
                normalize_audio_to_target_duration(
                    Path(directory) / "source.wav",
                    Path(directory) / "output.wav",
                    60.0,
                    duration_tolerance_sec=3.0,
                )

    def test_request_tolerance_accepts_underrun_for_all_supported_durations(self):
        for target in (60.0, 80.0, 90.0, 120.0):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                output_duration = target - 2.8
                raw_duration = output_duration * 0.97
                with patch(
                    "app.main.probe_duration",
                    side_effect=[raw_duration, output_duration],
                ), patch("app.main.run_command"):
                    raw, scale = normalize_audio_to_target_duration(
                        Path(directory) / "source.wav",
                        Path(directory) / "output.wav",
                        target,
                        duration_tolerance_sec=3.0,
                    )
                self.assertAlmostEqual(raw, raw_duration)
                self.assertAlmostEqual(scale, output_duration / raw_duration)

    def test_legacy_request_without_tolerance_keeps_three_second_default(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "app.main.probe_duration", return_value=(80.0 + 3.1) * 1.03
        ):
            with self.assertRaisesRegex(RuntimeError, "allowed_drift_sec=3.000"):
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
