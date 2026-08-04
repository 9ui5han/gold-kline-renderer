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
