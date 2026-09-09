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
        self.assertIn("/v1/tool-09/segments/submit", paths)
        self.assertIn("/v1/tool-09/render-batches", paths)
        self.assertIn("/v1/tool-09/render-batches/{batch_job_id}", paths)
        self.assertIn("/v1/tool-09/segments/finalize", paths)
        self.assertIn("/v1/compose-jobs", paths)
        self.assertIn("/v1/compose-jobs/{job_id}", paths)

    def test_composed_video_is_served_under_media_url(self):
        from fastapi.testclient import TestClient
        from app import main

        file_name = "test-composed-video.mp4"
        composed_dir = main.DATA_DIR / "composed"
        composed_dir.mkdir(parents=True, exist_ok=True)
        video_path = composed_dir / file_name
        video_path.write_bytes(b"test video")
        self.addCleanup(video_path.unlink, missing_ok=True)

        response = TestClient(main.app).get(f"/media/composed/{file_name}")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, b"test video")

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

    def test_job_store_uses_configured_job_prefix(self):
        from app.job_store import JobStore

        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory), job_prefix="t9bj_")
            job, created = store.create_or_get("batch-request-1", {"value": 1})

        self.assertTrue(created)
        self.assertTrue(job["job_id"].startswith("t9bj_"))

    def test_data_retention_removes_only_expired_generated_files(self):
        from app import segment_renderer

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            old_file = media / "old.mp4"
            old_file.write_text("old", encoding="utf-8")
            fresh_file = media / "fresh.mp4"
            fresh_file.write_text("fresh", encoding="utf-8")
            old_time = segment_renderer.time.time() - (16 * 86400)
            os.utime(old_file, (old_time, old_time))

            with patch.object(segment_renderer, "DATA_DIR", root), patch.object(
                segment_renderer, "MEDIA_DIR", media
            ), patch.object(segment_renderer, "DATA_RETENTION_DAYS", 15):
                removed = segment_renderer._cleanup_expired_data()

            self.assertEqual(removed, 1)
            self.assertFalse(old_file.exists())
            self.assertTrue(fresh_file.exists())

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

    def test_tool09_request_preserves_dynamic_visual_plan(self):
        from app import segment_renderer

        candles = [
            {
                "time": f"2026-09-08T{i:02d}:00:00Z",
                "open": 4400 + i,
                "high": 4402 + i,
                "low": 4398 + i,
                "close": 4401 + i,
            }
            for i in range(20)
        ]
        payload = segment_renderer.Tool09SegmentRequest.model_validate({
            "schema_version": "tool09-segment-request-v1",
            "master_request_id": "gold-dynamic-01",
            "market_input": {
                "schema_version": "market-input-contract-v1",
                "data_as_of": "2026-09-08T00:00:00Z",
                "normalized_market": {
                    "symbol": "XAUUSD.I",
                    "timeframes": {"1h": {"closed_bars": candles}},
                },
                "job_config": {
                    "forecast": {"timeframe": "1h"},
                    "video": {"width": 320, "height": 320, "fps": 30},
                },
            },
            "segment_item": {
                "segment_id": "seg_01",
                "order": 1,
                "visual": {
                    "visual_mode": "scenario_animation",
                    "source_timeframe": "1h",
                    "camera_motion": "slow_zoom_in",
                    "highlight_levels": ["R1"],
                    "show_volume": False,
                    "show_macro_marker": False,
                },
                "scenes": [
                    {
                        "scene_id": "scene_01",
                        "template_id": "path_reveal",
                        "start_sec": 0.0,
                        "duration_sec": 1.0,
                        "camera_motion": "slow_zoom_in",
                        "overlay_events": [
                            {
                                "event_id": "path_01",
                                "event_type": "scenario_path",
                                "start_sec": 0.2,
                                "duration_sec": 0.6,
                                "fact_anchor_ids": ["scenario:up"],
                            }
                        ],
                    },
                    {
                        "scene_id": "scene_02",
                        "template_id": "closing_card",
                        "start_sec": 1.0,
                        "duration_sec": 1.0,
                        "camera_motion": "static_hold",
                        "overlay_events": [],
                    },
                ],
                "resolved_visual_facts": [
                    {
                        "anchor_id": "level:R1",
                        "fact_type": "price_zone",
                        "center_price": 4410.0,
                        "lower_price": 4409.0,
                        "upper_price": 4411.0,
                        "display_text": "R1",
                    },
                    {
                        "anchor_id": "scenario:up",
                        "fact_type": "scenario_path",
                        "path_points": [
                            {"price": 4401.0, "time_ratio": 0.0},
                            {"price": 4412.0, "time_ratio": 1.0},
                        ],
                    },
                ],
                "audio": {
                    "url": "https://example.invalid/audio.wav",
                    "duration_sec": 2.0,
                },
                "duration_validation": {"valid": True},
                "continuous_chart": {
                    "schema_version": "continuous-chart-v1",
                    "mode": "rolling_left",
                    "global_start_sec": 2.0,
                    "global_duration_sec": 10.0,
                    "window_candles": 70,
                },
                "transition_out": {"type": "fade", "duration_ms": 250},
            },
        })

        request = segment_renderer._tool09_render_request(payload)

        self.assertEqual(len(request.visual_timeline["scenes"]), 2)
        self.assertEqual(len(request.visual_timeline["camera_plan"]), 2)
        self.assertEqual(len(request.visual_timeline["overlay_plan"]), 1)
        self.assertEqual(request.visual_facts[0]["anchor_id"], "level:R1")
        self.assertEqual(request.visual_timeline["scenes"][1]["start_sec"], 1.0)
        self.assertEqual(request.visual_timeline["continuous_chart"]["global_start_sec"], 2.0)

    def test_tool09_request_rejects_missing_visual_plan(self):
        from app import segment_renderer
        from fastapi import HTTPException

        payload = segment_renderer.Tool09SegmentRequest.model_validate({
            "schema_version": "tool09-segment-request-v1",
            "master_request_id": "gold-dynamic-missing-plan",
            "market_input": {"schema_version": "market-input-contract-v1"},
            "segment_item": {
                "segment_id": "seg_01",
                "order": 1,
                "audio": {"url": "https://example.invalid/audio.wav", "duration_sec": 1.0},
                "duration_validation": {"valid": True},
            },
        })

        with self.assertRaises(HTTPException) as caught:
            segment_renderer._tool09_render_request(payload)

        self.assertEqual(caught.exception.detail["code"], "VISUAL_PLAN_REQUIRED")

    def test_dynamic_frame_changes_as_timeline_progresses(self):
        from app import segment_renderer

        candles = [
            {"open": 100 + i, "high": 102 + i, "low": 98 + i, "close": 101 + i}
            for i in range(20)
        ]
        timeline = {
            "base_duration_sec": 2.0,
            "scenes": [{
                "scene_id": "scene_01",
                "start_sec": 0.0,
                "end_sec": 2.0,
                "duration_sec": 2.0,
                "camera_motion": "slow_zoom_in",
            }],
            "camera_plan": [{
                "event_id": "camera_01",
                "start_sec": 0.0,
                "end_sec": 2.0,
                "motion": "slow_zoom_in",
            }],
            "overlay_plan": [],
        }

        first = segment_renderer._render_dynamic_frame(
            candles, 160, 160, timeline, [], 0.0,
        )
        last = segment_renderer._render_dynamic_frame(
            candles, 160, 160, timeline, [], 1.9,
        )

        self.assertNotEqual(first, last)
        self.assertEqual(len(first), 160 * 160 * 3)

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

    def test_tool09_submit_returns_job_ticket_without_waiting(self):
        from app import segment_renderer

        payload = segment_renderer.Tool09SegmentRequest.model_validate({
            "schema_version": "tool09-segment-request-v1",
            "master_request_id": "gold-master-async-01",
            "market_input": {},
            "segment_item": {"segment_id": "seg_01", "order": 1},
        })
        request = segment_renderer.SegmentRenderRequest.model_construct(
            request_id="gold-master-async-01-seg-01",
        )
        created = {
            "job_id": "srj_async_01",
            "request_id": "gold-master-async-01-seg-01",
            "status": "queued",
        }
        with (
            patch.object(segment_renderer, "_tool09_render_request", return_value=request),
            patch.object(
                segment_renderer,
                "_create_or_reuse_segment_render_job",
                return_value=(created, True),
            ),
        ):
            result = segment_renderer.tool09_submit(payload)

        self.assertEqual(result["schema_version"], "tool09-submit-result-v1")
        self.assertEqual(result["job_id"], "srj_async_01")
        self.assertEqual(result["status"], "queued")
        self.assertFalse(result["done"])

    def test_tool09_batch_status_reports_pending_without_waiting(self):
        from app import segment_renderer

        payload = segment_renderer.Tool09BatchRequest.model_validate({
            "schema_version": "tool09-batch-request-v1",
            "master_request_id": "gold-master-batch-01",
            "segment_job_ids": ["srj_01", "srj_02"],
            "market_input": {"schema_version": "market-input-contract-v1"},
            "segment_media": {
                "schema_version": "segment-media-contract-v1",
                "segment_media_inputs": [
                    {"segment_id": "seg_01"},
                    {"segment_id": "seg_02"},
                ],
            },
        })
        batch = {
            "job_id": "t9bj_pending_01",
            "request_id": "tool09-batch-gold-master-batch-01",
            "status": "queued",
            "payload": payload.model_dump(),
        }
        pending = {
            "job_id": "srj_01",
            "request_id": "request-01",
            "status": "rendering",
            "created_at": "2026-09-07T00:00:00Z",
            "updated_at": "2026-09-07T00:00:01Z",
            "payload": {"segment_id": "seg_01", "order": 1},
            "result": None,
            "error": None,
        }
        completed = {
            **pending,
            "job_id": "srj_02",
            "status": "completed",
            "payload": {"segment_id": "seg_02", "order": 2},
            "result": {"video_url": "https://example.invalid/seg_02.mp4"},
        }
        with (
            patch.object(segment_renderer.TOOL09_BATCH_STORE, "get", return_value=batch),
            patch.object(segment_renderer.STORE, "get", side_effect=[pending, completed]),
            patch.object(segment_renderer, "_start_render_worker"),
        ):
            result = segment_renderer.tool09_batch_status("t9bj_pending_01")

        self.assertEqual(result["batch_job_id"], "t9bj_pending_01")
        self.assertEqual(result["status"], "rendering")
        self.assertFalse(result["done"])
        self.assertEqual(result["completed_count"], 1)
        self.assertEqual(result["total_count"], 2)

    def test_existing_incomplete_render_job_is_restarted(self):
        from app import segment_renderer

        request = segment_renderer.SegmentRenderRequest.model_construct(
            request_id="request-restart-01",
        )
        existing = {"job_id": "srj_restart_01", "status": "rendering"}
        with (
            patch.object(segment_renderer, "_validate_payload"),
            patch.object(
                segment_renderer.STORE,
                "create_or_get",
                return_value=(existing, False),
            ),
            patch.object(segment_renderer, "_start_render_worker") as start_worker,
        ):
            job, created = segment_renderer._create_or_reuse_segment_render_job(request)

        self.assertFalse(created)
        self.assertEqual(job["job_id"], "srj_restart_01")
        start_worker.assert_called_once_with("srj_restart_01")

    def test_tool09_batch_status_packages_final_contract_after_all_jobs_finish(self):
        import json
        from app import segment_renderer

        payload = segment_renderer.Tool09BatchRequest.model_validate({
            "schema_version": "tool09-batch-request-v1",
            "master_request_id": "gold-master-batch-final-01",
            "segment_job_ids": ["srj_final_01"],
            "market_input": {"schema_version": "market-input-contract-v1"},
            "segment_media": {
                "schema_version": "segment-media-contract-v1",
                "segment_media_inputs": [{"segment_id": "seg_01"}],
            },
        })
        batch = {
            "job_id": "t9bj_final_01",
            "status": "rendering",
            "payload": payload.model_dump(),
            "result": None,
        }
        completed = {
            "job_id": "srj_final_01",
            "status": "completed",
            "payload": {"segment_id": "seg_01", "order": 1},
            "result": {
                "video_url": "https://example.invalid/seg_01.mp4",
                "base_duration_sec": 5,
                "head_handle_sec": 0,
                "tail_handle_sec": 0,
                "render_duration_sec": 5,
                "probe_valid": True,
                "kline_main_visual_present": True,
                "degraded": False,
            },
            "error": None,
        }
        with (
            patch.object(segment_renderer.TOOL09_BATCH_STORE, "get", return_value=batch),
            patch.object(segment_renderer.TOOL09_BATCH_STORE, "update"),
            patch.object(segment_renderer.STORE, "get", return_value=completed),
        ):
            result = segment_renderer.tool09_batch_status("t9bj_final_01")

        self.assertTrue(result["done"])
        self.assertTrue(result["segment_render_valid"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["schema_version"], "tool09-batch-status-v1")
        self.assertTrue(json.loads(result["rendered_v1_json"])["segment_render_valid"])

    def test_tool09_batch_status_preserves_segment_transition(self):
        from app.segment_renderer import _tool09_rendered_from_job

        rendered = _tool09_rendered_from_job("gold-master-transition", {
            "status": "completed",
            "payload": {
                "segment_id": "seg_01",
                "order": 1,
                "transition_out": {"type": "fade", "duration_ms": 300},
            },
            "result": {
                "video_url": "https://example.invalid/seg_01.mp4",
                "probe_valid": True,
                "kline_main_visual_present": True,
            },
        })

        self.assertEqual(
            rendered["transition_out"],
            {"type": "fade", "duration_ms": 300},
        )

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
