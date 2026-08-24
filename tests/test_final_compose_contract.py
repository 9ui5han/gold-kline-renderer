import json
import unittest
from unittest.mock import patch


def _start_payload(master_request_id="gold-contract-01"):
    market = {
        "schema_version": "market-input-contract-v1",
        "symbol": "XAUUSD",
    }
    rendered = {
        "schema_version": "rendered-segments-contract-v1",
        "master_request_id": master_request_id,
        "segment_render_valid": True,
        "segment_render_errors": [],
        "rendered_segments": [
            {
                "segment_id": "seg_01",
                "order": 1,
                "base_duration_sec": 4.0,
                "head_handle_sec": 0.0,
                "tail_handle_sec": 0.0,
                "actual_render_duration_sec": 4.0,
                "video": {
                    "url": "https://example.invalid/seg_01.mp4",
                    "duration_sec": 4.0,
                },
                "probe_valid": True,
                "kline_main_visual_present": True,
                "degraded": False,
                "degradation_code": "",
                "transition_out": {"type": "hard_cut", "duration_ms": 0},
            }
        ],
    }
    return {
        "market_input_v1_json": json.dumps(market),
        "rendered_v1_json": json.dumps(rendered),
        "master_request_id": master_request_id,
    }


class FinalComposeContractTests(unittest.TestCase):
    def test_models_reject_whitespace_master_request_id(self):
        from app.video_composer import FinalComposeStartRequest, FinalComposeStepRequest
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            FinalComposeStartRequest.model_validate(
                _start_payload(master_request_id="   ")
            )
        with self.assertRaises(ValidationError):
            FinalComposeStepRequest.model_validate({
                "job_id": "job-01",
                "master_request_id": "   ",
            })

    def test_routes_are_registered_and_protected_by_parent_router(self):
        from app.main import app

        paths = app.openapi()["paths"]
        self.assertIn("/v1/final-compose-jobs/start", paths)
        self.assertIn("/v1/final-compose-jobs/step", paths)
        protected = {
            route.path: route
            for route in app.routes
            if route.path in {
                "/v1/final-compose-jobs/start",
                "/v1/final-compose-jobs/step",
            }
        }
        self.assertTrue(protected["/v1/final-compose-jobs/start"].dependant.dependencies)
        self.assertTrue(protected["/v1/final-compose-jobs/step"].dependant.dependencies)

    def test_tool10_request_is_adapted_to_existing_compose_contract(self):
        from app.video_composer import FinalComposeStartRequest, _compose_request_from_tool10

        payload = FinalComposeStartRequest.model_validate(_start_payload())
        request = _compose_request_from_tool10(payload)
        self.assertEqual(request.request_id, "gold-contract-01-final-compose")
        self.assertEqual(request.segments[0].segment_id, "seg_01")
        self.assertEqual(request.narration_timeline_sec, 4.0)

    def test_start_and_step_preserve_master_request_id(self):
        from app import video_composer

        start_payload = video_composer.FinalComposeStartRequest.model_validate(
            _start_payload()
        )
        queued = {
            "job_id": "job-01",
            "request_id": "gold-contract-01-final-compose",
            "status": "queued",
        }
        completed = {
            **queued,
            "status": "completed",
            "result": {
                "video_url": "https://example.invalid/final.mp4",
                "duration_sec": 4.0,
                "narration_timeline_sec": 4.0,
                "degradation_records": [],
                "rebuild_segment_ids": [],
                "requested_transitions": [],
                "applied_transitions": [],
                "actual_overlap_sec": 0.0,
            },
        }
        with (
            patch.object(video_composer, "create_compose_job", return_value=queued),
            patch.object(video_composer.JOB_STORE, "get", return_value=queued),
        ):
            start = video_composer.start_final_compose_job(start_payload)
        self.assertEqual(start["master_request_id"], "gold-contract-01")
        poll = json.loads(start["request_json"])
        self.assertEqual(poll["master_request_id"], "gold-contract-01")

        with patch.object(video_composer.JOB_STORE, "get", return_value=completed):
            step = video_composer.step_final_compose_job(
                video_composer.FinalComposeStepRequest.model_validate(poll)
            )
        self.assertEqual(step["action"], "pass")
        result = json.loads(step["result_json"])
        inner = json.loads(result["final_result_v1_json"])
        self.assertEqual(result["master_request_id"], "gold-contract-01")
        self.assertEqual(inner["master_request_id"], "gold-contract-01")
        self.assertTrue(result["final_valid"])

    def test_step_rejects_master_request_id_mismatch(self):
        from app import video_composer
        from fastapi import HTTPException

        job = {
            "job_id": "job-01",
            "request_id": "gold-original-final-compose",
            "status": "processing",
        }
        request = video_composer.FinalComposeStepRequest(
            job_id="job-01",
            master_request_id="gold-other",
        )
        with patch.object(video_composer.JOB_STORE, "get", return_value=job):
            with self.assertRaises(HTTPException) as caught:
                video_composer.step_final_compose_job(request)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail, "MASTER_REQUEST_ID_MISMATCH")

    def test_failed_job_returns_a_parseable_failure_contract(self):
        from app import video_composer

        failed = {
            "job_id": "job-failed",
            "request_id": "gold-contract-01-final-compose",
            "status": "failed",
            "result": {"rebuild_segment_ids": ["seg_01"]},
            "error": {"code": "SEGMENT_REBUILD_REQUIRED"},
        }
        request = video_composer.FinalComposeStepRequest(
            job_id="job-failed",
            master_request_id="gold-contract-01",
        )
        with patch.object(video_composer.JOB_STORE, "get", return_value=failed):
            step = video_composer.step_final_compose_job(request)
        result = json.loads(step["result_json"])
        self.assertEqual(step["action"], "fail")
        self.assertTrue(step["done"])
        self.assertFalse(result["final_valid"])
        self.assertEqual(result["rebuild_segment_ids"], ["seg_01"])
        self.assertEqual(
            json.loads(result["final_errors_json"]),
            ["SEGMENT_REBUILD_REQUIRED"],
        )


if __name__ == "__main__":
    unittest.main()
