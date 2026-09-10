import unittest

from app.main import VideoOptions
from app.segment_renderer import VideoSpec, _dynamic_ffmpeg_command, _smoothstep
from app.video_composer import ComposeVideoConfig


class VideoQualitySettingsTests(unittest.TestCase):
    def test_all_video_defaults_use_smooth_60_fps(self):
        self.assertEqual(VideoOptions().fps, 60)
        self.assertEqual(VideoSpec().fps, 60)
        self.assertEqual(ComposeVideoConfig().fps, 60)

    def test_dynamic_encoder_keeps_canvas_and_uses_high_quality_h264(self):
        command = _dynamic_ffmpeg_command(
            width=1080,
            height=1920,
            fps=60,
            duration=4.0,
            head_handle_sec=0.25,
            audio_path="/tmp/audio.mp3",
            output_path="/tmp/output.mp4",
        )

        self.assertIn("-s", command)
        self.assertEqual(command[command.index("-s") + 1], "1080x1920")
        self.assertEqual(command[command.index("-r") + 1], "60")
        self.assertEqual(command[command.index("-preset") + 1], "medium")
        self.assertEqual(command[command.index("-crf") + 1], "18")
        self.assertIn("adelay=250|250,apad", command)
        self.assertNotIn("scale=", " ".join(command))

    def test_candle_reveal_uses_smoothstep_easing(self):
        self.assertEqual(_smoothstep(0.0), 0.0)
        self.assertEqual(_smoothstep(1.0), 1.0)
        self.assertLess(_smoothstep(0.25), 0.25)
        self.assertGreater(_smoothstep(0.75), 0.75)


if __name__ == "__main__":
    unittest.main()
