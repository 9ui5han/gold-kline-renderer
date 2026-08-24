import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

class V72VideoJobsTests(unittest.TestCase):
    def test_new_routes_are_registered(self):
        os.environ.setdefault("RENDER_SERVICE_TOKEN", "test-token-123456789")
        from app.main import app

        paths = app.openapi()["paths"]
        self.assertIn("/v1/segment-render-jobs", paths)
        self.assertIn("/v1/segment-render-jobs/await", paths)
        self.assertIn("/v1/segment-render-jobs/{job_id}", paths)
        self.assertIn("/v1/tool-09/segments/render-await", paths)
        self.assertIn("/v1/tool-09/segments/finalize", paths)
        self.assertIn("/v1/compose-jobs", paths)
        self.assertIn("/v1/compose-jobs/{job_id}", paths)

    def test_job_store_is_idempotent_and_rejects_conflict(self):
        from app.job_store import IdempotencyConflict, JobStore

        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            first, created = store.create_or_get("request-1", {"value": 1})
            again, created_again = store.create_or_get("request-1", {"value": 1})
            self.assertTrue(created)
            self.assertFalse(created_again)
            self.assertEqual(first["job_id"], again["job_id"])
            with self.assertRaises(IdempotencyConflict):
                store.create_or_get("request-1", {"value": 2})

    def test_compose_audio_trim_skips_head_handle(self):
        from app.video_composer import _audio_trim_bounds

        self.assertEqual(_audio_trim_bounds(0.25, 10.0), (0.25, 10.25))

    def test_segment_request_rejects_scene_duration_mismatch(self):
        from app.segment_renderer import SegmentRenderRequest, _dump, _validate_payload
        from fastapi import HTTPException

        payload = {
            "request_id": "gold-abc-seg-01-visual-r0",
            "master_request_id": "gold-abc",
            "segment_id": "seg_01",
            "order": 1,
            "symbol": "XAUUSD",
            "timeframe": "1h",
            "data_as_of": "2026-08-21T00:00:00Z",
            "base_duration_sec": 4.1,
            "head_handle_sec": 0,
            "tail_handle_sec": 0.3,
            "render_duration_sec": 4.4,
            "audio_url": "https://example.invalid/audio.mp3",
            "historical_candles": [
                {"time": f"2026-08-{i:02d}T00:00:00Z", "open": 1, "high": 2, "low": 0.5, "close": 1.5}
                for i in range(1, 21)
            ],
            "visual_timeline": {
                "schema_version": "visual-timeline-v1",
                "segment_id": "seg_01",
                "base_duration_sec": 4.1,
                "fps": 30,
                "scenes": [{"scene_id": "s1", "start_sec": 0, "end_sec": 4.2}],
                "camera_plan": [{"event_id": "c1", "start_sec": 0, "end_sec": 4.0, "motion": "static_hold", "focus_target": "full_chart", "zoom_from": 1, "zoom_to": 1}],
                "overlay_plan": [],
            },
            "video": {"width": 1080, "height": 1920, "fps": 30, "format": "mp4"},
            "fallback_policy": {"on_motion_failure": "static_hold"},
        }
        request = SegmentRenderRequest.model_validate(payload)
        with self.assertRaises(HTTPException) as caught:
            _validate_payload(_dump(request))
        self.assertEqual(caught.exception.detail["code"], "SCENE_BOUNDS_INVALID")

    def test_wait_for_segment_render_job_reports_completed_and_timeout(self):
        from app import segment_renderer

        completed = {
            "job_id": "completed-job",
            "request_id": "request-completed",
            "status": "completed",
            "created_at": "2026-08-23T00:00:00Z",
            "updated_at": "2026-08-23T00:00:01Z",
            "payload": {"segment_id": "seg_01", "order": 1},
            "result": {"video_url": "https://example.invalid/segment.mp4"},
            "error": None,
        }
        waiting = {
            "job_id": "waiting-job",
            "request_id": "request-waiting",
            "status": "rendering",
            "created_at": "2026-08-23T00:00:00Z",
            "updated_at": "2026-08-23T00:00:01Z",
            "payload": {"segment_id": "seg_02", "order": 2},
            "result": None,
            "error": None,
        }
        with patch.object(segment_renderer.STORE, "get", return_value=completed):
            result = segment_renderer.wait_for_segment_render_job("completed-job")
        self.assertEqual(result["wait_status"], "completed")
        self.assertEqual(result["job"]["video_url"], "https://example.invalid/segment.mp4")

        with (
            patch.object(segment_renderer.STORE, "get", return_value=waiting),
            patch.object(segment_renderer, "SEGMENT_RENDER_AWAIT_TIMEOUT_SEC", 0),
        ):
            timed_out = segment_renderer.wait_for_segment_render_job("waiting-job")
        self.assertEqual(timed_out["wait_status"], "timeout")
        self.assertEqual(timed_out["error_code"], "RENDER_WAIT_TIMEOUT")

    def test_await_route_reuses_existing_job_before_waiting(self):
        from app import segment_renderer

        request = segment_renderer.SegmentRenderRequest.model_construct()
        existing = {"job_id": "existing-job", "status": "rendering"}
        with (
            patch.object(
                segment_renderer,
                "_create_or_reuse_segment_render_job",
                return_value=(existing, False),
            ),
            patch.object(
                segment_renderer,
                "wait_for_segment_render_job",
                return_value={"wait_status": "completed", "job": {"job_id": "existing-job"}},
            ) as wait_for_job,
        ):
            result = segment_renderer.create_and_await_segment_render_job(request)

        self.assertEqual(result["wait_status"], "completed")
        wait_for_job.assert_called_once_with("existing-job")

    def test_tool09_render_adapter_preserves_master_request_id(self):
        from app import segment_renderer

        payload = segment_renderer.Tool09SegmentRequest.model_validate({
            "schema_version": "tool09-segment-request-v1",
            "master_request_id": "gold-master-01",
            "market_input": {},
            "segment_item": {"segment_id": "seg_01", "order": 1},
        })
        completed = {
            "wait_status": "completed",
            "job": {
                "status": "completed",
                "video_url": "https://example.invalid/seg_01.mp4",
                "thumbnail_url": "",
                "base_duration_sec": 5.0,
                "head_handle_sec": 0.0,
                "tail_handle_sec": 0.0,
                "render_duration_sec": 5.0,
                "probe_valid": True,
                "kline_main_visual_present": True,
                "degraded": False,
            },
        }
        with (
            patch.object(segment_renderer, "_tool09_render_request", return_value=object()),
            patch.object(segment_renderer, "create_and_await_segment_render_job", return_value=completed),
        ):
            result = segment_renderer.tool09_render_and_await(payload)

        self.assertTrue(result["segment_result_valid"])
        self.assertEqual(result["master_request_id"], "gold-master-01")
        self.assertEqual(result["rendered_segment"]["master_request_id"], "gold-master-01")

    def test_tool09_models_reject_empty_master_request_id(self):
        from app.segment_renderer import Tool09FinalizeRequest, Tool09SegmentRequest
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            Tool09SegmentRequest.model_validate({
                "schema_version": "tool09-segment-request-v1",
                "master_request_id": "",
                "market_input": {},
                "segment_item": {},
            })
        with self.assertRaises(ValidationError):
            Tool09FinalizeRequest.model_validate({
                "schema_version": "tool09-collection-request-v1",
                "master_request_id": "",
                "rendered_segments": [],
                "market_input": {},
                "segment_media": {},
            })
        with self.assertRaises(ValidationError):
            Tool09SegmentRequest.model_validate({
                "schema_version": "tool09-segment-request-v1",
                "master_request_id": "   ",
                "market_input": {},
                "segment_item": {},
            })

    def test_tool09_finalize_requires_matching_master_request_id(self):
        import json
        from app.segment_renderer import Tool09FinalizeRequest, tool09_finalize

        base = {
            "schema_version": "tool09-collection-request-v1",
            "master_request_id": "gold-master-01",
            "market_input": {"schema_version": "market-input-contract-v1"},
            "segment_media": {
                "schema_version": "segment-media-contract-v1",
                "segment_media_inputs": [{"segment_id": "seg_01"}],
            },
        }
        rendered = {
            "master_request_id": "gold-master-01",
            "segment_id": "seg_01",
            "order": 1,
            "status": "completed",
            "video_url": "https://example.invalid/seg_01.mp4",
            "probe_valid": True,
        }
        success = tool09_finalize(Tool09FinalizeRequest.model_validate({**base, "rendered_segments": [rendered]}))
        self.assertTrue(success["segment_render_valid"])
        self.assertEqual(success["master_request_id"], "gold-master-01")
        self.assertEqual(json.loads(success["rendered_v1_json"])["master_request_id"], "gold-master-01")

        mismatch = tool09_finalize(Tool09FinalizeRequest.model_validate({
            **base,
            "rendered_segments": [{**rendered, "master_request_id": "different-master"}],
        }))
        self.assertFalse(mismatch["segment_render_valid"])
        self.assertIn("MASTER_ID_MISMATCH", mismatch["render_errors_json"])

    def test_compose_request_rejects_unsafe_values(self):
        from app.video_composer import ComposeRequest
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            ComposeRequest.model_validate({
                "request_id": "compose-1",
                "segments": [],
                "expected_final_duration_sec": -1,
                "narration_timeline_sec": 0,
                "duration_tolerance_sec": -1,
                "fallback_policy": {},
                "video": {"width": 0, "height": 0, "fps": 0, "format": "avi"},
            })


if __name__ == "__main__":
    unittest.main()
