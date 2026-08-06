import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import main


def alignment_response(request: httpx.Request, status: int, payload=None):
    return httpx.Response(
        status,
        request=request,
        json=payload or {},
        headers={"content-type": "application/json"},
    )


class TtsAlignmentTests(unittest.TestCase):
    def test_ai302_headers_normalize_bearer_prefix_and_whitespace(self):
        with patch.object(main, "AI302_API_KEY", "  Bearer test-key \n"):
            headers = main.ai302_headers()

        self.assertEqual(headers["Authorization"], "Bearer test-key")

    def test_elevenlabs_request_uses_configured_documented_model(self):
        payload = main.TTSProxyRequest(
            request_id="model-test",
            text="A short test sentence.",
            tts_provider="elevenlabs",
        )
        request = httpx.Request(
            "POST",
            "https://api.302.ai/elevenlabs/text-to-speech/test-voice",
        )
        response = httpx.Response(
            200,
            request=request,
            json={"url": "https://example.com/test.mp3"},
        )

        with patch.object(main, "ELEVENLABS_MODEL_ID", "eleven_multilingual_v2"), patch.object(
            main.httpx,
            "post",
            return_value=response,
        ) as post, patch.object(main, "download_audio"):
            with tempfile.TemporaryDirectory() as directory:
                main.generate_elevenlabs_tts(
                    payload,
                    Path(directory) / "test.mp3",
                )

        self.assertEqual(
            post.call_args.kwargs["json"]["model_id"],
            "eleven_multilingual_v2",
        )

    def test_elevenlabs_request_uses_v3_by_default(self):
        payload = main.TTSProxyRequest(
            request_id="v3-default-test",
            text="A short test sentence.",
            tts_provider="elevenlabs",
        )
        request = httpx.Request(
            "POST",
            "https://api.302.ai/elevenlabs/text-to-speech/test-voice",
        )
        response = httpx.Response(
            200,
            request=request,
            json={"url": "https://example.com/test.mp3"},
        )

        with patch.object(main.httpx, "post", return_value=response) as post, patch.object(
            main, "download_audio"
        ):
            with tempfile.TemporaryDirectory() as directory:
                main.generate_elevenlabs_tts(
                    payload,
                    Path(directory) / "test.mp3",
                )

        self.assertEqual(post.call_args.kwargs["json"]["model_id"], "eleven_v3")

    def test_elevenlabs_rejects_empty_narration_object(self):
        payload = main.TTSProxyRequest(
            request_id="empty-narration",
            text="A valid narration.",
            narration_json="{}",
            tts_provider="elevenlabs",
        )

        with self.assertRaisesRegex(ValueError, "narration_json\\.segments不能为空"):
            main.parse_elevenlabs_segments(payload)

    def test_keeps_primary_segments_separate_for_fallback_boundaries(self):
        chunks = main.build_elevenlabs_narrative_chunks(
            [
                {"text": "Opening.", "section": "opening", "pause_after_ms": 320},
                {"text": "Levels.", "section": "evidence", "pause_after_ms": 240},
                {
                    "text": "Lower range.",
                    "section": "primary",
                    "pause_after_ms": 460,
                },
                {
                    "text": "Upper range.",
                    "section": "primary",
                    "pause_after_ms": 300,
                },
                {
                    "text": "Primary path.",
                    "section": "primary",
                    "pause_after_ms": 560,
                },
                {
                    "text": "Alternate path.",
                    "section": "alternate",
                    "pause_after_ms": 380,
                },
            ]
        )

        self.assertEqual(
            [item["text"] for item in chunks],
            [
                "Opening.",
                "Levels.",
                "Lower range.",
                "Upper range.",
                "Primary path.",
                "Alternate path.",
            ],
        )

    def test_builds_fallback_cues_from_elevenlabs_segment_bounds(self):
        cues = main.build_segment_boundary_subtitle_cues(
            [
                {
                    "text": "First sentence.",
                    "start_sec": 0.0,
                    "end_sec": 9.5,
                },
                {
                    "text": "Second sentence.",
                    "start_sec": 9.8,
                    "end_sec": 18.0,
                },
            ],
            18.2,
            "First sentence. Second sentence.",
        )

        self.assertEqual(
            [cue["text"] for cue in cues],
            ["First sentence.", "Second sentence."],
        )
        self.assertEqual(cues[0]["start_sec"], 0.0)
        self.assertEqual(cues[0]["end_sec"], 9.5)
        self.assertEqual(cues[1]["start_sec"], 9.8)
        self.assertEqual(cues[1]["end_sec"], 18.0)

    def test_retry_on_503_then_returns_cues(self):
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "audio.wav"
            audio_path.write_bytes(b"wav")
            calls = []

            def fake_post(*args, **kwargs):
                calls.append(1)
                request = httpx.Request("POST", args[0])
                if len(calls) == 1:
                    return alignment_response(request, 503)
                return alignment_response(
                    request,
                    200,
                    {
                        "segments": [
                            {
                                "words": [
                                    {"word": "gold", "start": 0.0, "end": 0.5},
                                    {"word": "rises", "start": 0.6, "end": 1.0},
                                    {"word": "today", "start": 1.1, "end": 1.6},
                                ]
                            }
                        ]
                    },
                )

            with patch.object(main, "ALIGNMENT_MAX_ATTEMPTS", 2), patch.object(
                main, "ALIGNMENT_RETRY_BASE_SEC", 0
            ), patch.object(main.httpx, "post", side_effect=fake_post):
                cues, language = main.align_audio_with_source_text(
                    audio_path,
                    2.0,
                    "Gold rises today.",
                )

            self.assertEqual(len(calls), 2)
            self.assertEqual(language, "en")
            self.assertEqual(cues[0]["text"], "Gold rises today.")
            self.assertGreater(cues[0]["end_sec"], cues[0]["start_sec"])

    def test_elevenlabs_uses_segment_boundaries_when_alignment_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            media_dir = Path(directory) / "media"
            work_dir = Path(directory) / "work"
            media_dir.mkdir()

            payload = main.TTSProxyRequest(
                request_id="fallback-test",
                text="First sentence. Second sentence.",
                narration_json={
                    "segments": [
                        {
                            "text": "First sentence.",
                            "pause_after_ms": 300,
                        },
                        {
                            "text": "Second sentence.",
                            "pause_after_ms": 0,
                        },
                    ]
                },
                tts_provider="elevenlabs",
            )

            with patch.object(main, "AI302_API_KEY", "test-key"), patch.object(
                main, "MEDIA_DIR", media_dir
            ), patch.object(
                main, "WORK_DIR", work_dir
            ), patch.object(
                main,
                "generate_elevenlabs_segmented_tts",
                return_value=[
                    {
                        "text": "First sentence.",
                        "start_sec": 0.0,
                        "end_sec": 1.2,
                    },
                    {
                        "text": "Second sentence.",
                        "start_sec": 1.5,
                        "end_sec": 2.7,
                    },
                ],
            ), patch.object(main, "probe_duration", return_value=2.7), patch.object(
                main,
                "align_audio_with_source_text",
                side_effect=RuntimeError(
                    "SOURCE_TEXT_ALIGNMENT_REQUEST_FAILED: 503 Service Unavailable"
                ),
            ):
                result = main.create_tts_audio(payload)

            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["subtitle_alignment_valid"])
            self.assertEqual(
                result["alignment_method"],
                "elevenlabs_segment_boundary_fallback",
            )
            self.assertEqual(
                [cue["text"] for cue in result["subtitle_cues"]],
                ["First sentence.", "Second sentence."],
            )

    def test_rejects_subtitle_text_mismatch(self):
        with self.assertRaisesRegex(RuntimeError, "TEXT_MISMATCH"):
            main.validate_subtitle_cues(
                [{"start_sec": 0, "end_sec": 1, "text": "Wrong text"}],
                2,
                "Expected text",
            )

    def test_rejects_overlapping_cues(self):
        with self.assertRaisesRegex(RuntimeError, "CUE_OVERLAP"):
            main.validate_subtitle_cues(
                [
                    {"start_sec": 0, "end_sec": 1.2, "text": "First"},
                    {"start_sec": 1.0, "end_sec": 2, "text": "Second"},
                ],
                2,
                "First Second",
            )


if __name__ == "__main__":
    unittest.main()
