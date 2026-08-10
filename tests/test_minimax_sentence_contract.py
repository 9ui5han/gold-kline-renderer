import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import main


def payload_for(segments, target_duration_sec=None):
    text = " ".join(item["text"] for item in segments)
    return main.TTSProxyRequest(
        request_id="minimax-sentence-test",
        text=text,
        speech_text=text,
        narration_json={"segments": segments},
        tts_provider="minimax",
        minimax_voice_id="English_Trustworthy_Man",
        target_duration_sec=target_duration_sec,
    )


class MiniMaxSentenceContractTests(unittest.TestCase):
    def test_parser_keeps_complete_segment_without_splitting_decimal_prices(self):
        units = main.parse_minimax_sentence_units(payload_for([{
            "order": 1,
            "segment_id": "technical_evidence",
            "text": "Gold is near 4,273.04. Momentum remains mixed.",
            "effective_speed": 0.94,
            "pause_after_ms": 350,
        }]))
        self.assertEqual(
            [unit["text"] for unit in units],
            ["Gold is near 4,273.04. Momentum remains mixed."],
        )
        self.assertEqual([unit["speed"] for unit in units], [0.94])
        self.assertEqual([unit["pause_after_ms"] for unit in units], [350])

    def test_parser_preserves_complete_segment_and_end_pause(self):
        units = main.parse_minimax_sentence_units(payload_for([{
            "order": 1,
            "segment_id": "opening",
            "text": "First sentence. Second sentence! Third sentence?",
            "speed": 1.0,
            "pause_after_ms": 300,
        }]))
        self.assertEqual([unit["speed"] for unit in units], [1.0])
        self.assertEqual([unit["pause_after_ms"] for unit in units], [300])
        self.assertEqual(len(units), 1)

    def test_parser_combines_global_and_segment_speed(self):
        payload = payload_for([{
            "order": 1,
            "segment_id": "opening",
            "text": "First sentence. Second sentence.",
            "speed": 1.01,
            "effective_speed": 1.091,
            "pause_after_ms": 300,
        }])
        payload.speed_ratio = 1.08
        units = main.parse_minimax_sentence_units(payload)
        self.assertEqual([unit["speed"] for unit in units], [1.09])

    def test_parser_rejects_inconsistent_effective_speed(self):
        payload = payload_for([{
            "order": 1,
            "segment_id": "opening",
            "text": "Opening sentence.",
            "speed": 1.01,
            "effective_speed": 0.95,
            "pause_after_ms": 300,
        }])
        payload.speed_ratio = 1.08
        with self.assertRaisesRegex(ValueError, "effective_speed"):
            main.parse_minimax_sentence_units(payload)

    def test_parser_rejects_missing_speed_before_paid_requests(self):
        with self.assertRaisesRegex(ValueError, "speed"):
            main.parse_minimax_sentence_units(payload_for([{
                "order": 1,
                "segment_id": "opening",
                "text": "Opening sentence.",
                "pause_after_ms": 300,
            }]))

    def test_segment_request_uses_turbo_speed_and_current_response_path(self):
        response = main.httpx.Response(
            200,
            request=main.httpx.Request("POST", "https://api.302.ai"),
            json={
                "data": {"audio": "https://file.302.ai/minimax.mp3", "status": 2},
                "extra_info": {"audio_length": 1234},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )
        with tempfile.TemporaryDirectory() as directory, patch.object(
            main.httpx, "post", return_value=response
        ) as post, patch.object(main, "download_audio") as download:
            output = Path(directory) / "sentence.mp3"
            main.generate_minimax_tts_segment(
                "Gold is near 4,273.04.",
                "English_Trustworthy_Man",
                0.93,
                output,
            )
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "speech-2.8-turbo")
        self.assertEqual(body["voice_setting"]["speed"], 0.93)
        self.assertEqual(body["language_boost"], "English")
        self.assertEqual(body["output_format"], "url")
        download.assert_called_once_with("https://file.302.ai/minimax.mp3", output)

    def test_wav_normalization_adds_short_fades_at_both_ends(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            main, "run_command"
        ) as run:
            source = Path(directory) / "source.mp3"
            target = Path(directory) / "target.wav"
            main.normalize_audio_to_wav(source, target)

        args = run.call_args.args[0]
        filter_index = args.index("-af")
        self.assertEqual(
            args[filter_index + 1],
            "afade=t=in:st=0:d=0.008,areverse,"
            "afade=t=in:st=0:d=0.008,areverse",
        )
        self.assertEqual(args[-1], str(target))

    def test_segmented_generation_uses_each_speed_and_pause(self):
        payload = payload_for([{
            "order": 1,
            "segment_id": "opening",
            "text": "First sentence. Second sentence.",
            "speed": 1.0,
            "pause_after_ms": 300,
        }])
        calls = []

        def fake_generate(text, voice, speed, output_path):
            calls.append((text, voice, speed))
            output_path.write_bytes(b"audio")

        with tempfile.TemporaryDirectory() as directory, patch.object(
            main, "WORK_DIR", Path(directory)
        ), patch.object(
            main, "generate_minimax_tts_segment", side_effect=fake_generate
        ), patch.object(
            main, "normalize_audio_to_wav",
            side_effect=lambda source, target: target.write_bytes(b"wav"),
        ), patch.object(
            main, "probe_duration", side_effect=[1.2, 1.2, 1.2]
        ), patch.object(
            main, "concatenate_audio_with_pauses",
            side_effect=lambda paths, pauses, target, work: target.write_bytes(b"wav"),
        ) as concatenate:
            output = Path(directory) / "result.wav"
            bounds = main.generate_minimax_segmented_tts(payload, output)
        self.assertEqual([call[2] for call in calls], [1.0])
        self.assertEqual(concatenate.call_args.args[1], [300])
        self.assertEqual(
            [bound["text"] for bound in bounds],
            ["First sentence. Second sentence."],
        )
        self.assertEqual(bounds[0]["start_sec"], 0.0)


if __name__ == "__main__":
    unittest.main()
