import copy
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import main


AUTH = {"Authorization": "Bearer photo-test-token"}


def photo_plan() -> dict:
    return {
        "schema_version": "photo-plan-v1",
        "post_title": "3分钟看懂RSI",
        "content_type": "knowledge",
        "page_count": 1,
        "pages": [{
            "page_no": 1,
            "page_role": "cover",
            "title": "3分钟看懂RSI",
            "body": "RSI用来观察近期上涨和下跌力量的相对强弱。",
            "key_message": "RSI观察相对强弱，不直接预测涨跌",
            "visual_type": "indicator_panel",
            "visual_focus": "rsi_high_zone",
            "required_elements": ["RSI曲线", "70参考线", "30参考线"],
            "annotations": [],
            "asset_requests": [
                {
                    "asset_type": "character",
                    "asset_key": "teacher_front",
                    "purpose": "封面讲师",
                    "required": True,
                },
                {
                    "asset_type": "icon",
                    "asset_key": "search",
                    "purpose": "观察提示",
                    "required": False,
                },
            ],
            "risk_note": "教学示意图｜不代表实时行情",
        }],
    }


class PhotoRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.TOKEN = "photo-test-token"
        cls.api = TestClient(main.app)

    @classmethod
    def tearDownClass(cls):
        cls.api.close()

    def test_photo_routes_are_registered_and_protected(self):
        expected = {
            "/v1/photo/charts/render",
            "/v1/photo/assets/resolve",
            "/v1/photo/render-post",
            "/v1/photo/validate",
            "/v1/photo/repair",
            "/v1/photo/jobs/{photo_job_id}",
        }
        paths = set(main.app.openapi()["paths"])
        self.assertTrue(expected.issubset(paths))
        response = self.api.post("/v1/photo/charts/render", json={})
        self.assertEqual(response.status_code, 401)

    def test_market_chart_is_rejected_until_real_market_renderer_exists(self):
        response = self.api.post(
            "/v1/photo/charts/render",
            headers=AUTH,
            json={
                "schema_version": "photo-chart-request-v1",
                "content_type": "market",
                "pages": [{
                    "page_no": 1,
                    "visual_type": "market_chart",
                    "visual_focus": "latest_market",
                    "required_elements": ["真实K线"],
                    "annotations": [],
                    "risk_note": "",
                }],
                "route_payload": {
                    "schema_version": "photo-route-v1",
                    "route_name": "market",
                },
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("PHOTO_MARKET_CHART_NOT_IMPLEMENTED", response.text)

    def test_explicit_unknown_indicator_plugin_is_rejected(self):
        response = self.api.post(
            "/v1/photo/charts/render",
            headers=AUTH,
            json={
                "schema_version": "photo-chart-request-v1",
                "content_type": "knowledge",
                "pages": [{
                    "page_no": 1,
                    "visual_type": "indicator_panel",
                    "visual_focus": "custom indicator",
                    "required_elements": [],
                    "annotations": [],
                    "risk_note": "Educational illustration",
                    "teaching_spec": {
                        "indicator_id": "unknown_magic",
                        "indicator_kind": "oscillator",
                        "lesson_goal": "basic_use"
                    }
                }],
                "route_payload": {"schema_version": "photo-route-v1", "route_name": "knowledge"},
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("INDICATOR_NOT_REGISTERED:unknown_magic", response.text)

        for indicator_id in ("rsi_magic", "ictx"):
            body = response.request.content
            import json
            payload = json.loads(body)
            payload["pages"][0]["teaching_spec"]["indicator_id"] = indicator_id
            retry = self.api.post("/v1/photo/charts/render", headers=AUTH, json=payload)
            self.assertEqual(retry.status_code, 422)
            self.assertIn(f"INDICATOR_NOT_REGISTERED:{indicator_id}", retry.text)

    def test_unsupported_lesson_goal_is_rejected(self):
        base = {
            "schema_version": "photo-chart-request-v1",
            "content_type": "knowledge",
            "pages": [{
                "page_no": 1, "visual_type": "indicator_panel", "visual_focus": "lesson",
                "required_elements": [], "annotations": [], "risk_note": "Educational illustration",
                "teaching_spec": {"indicator_id": "rsi", "indicator_kind": "oscillator", "lesson_goal": "overbought_intro"},
            }],
            "route_payload": {"schema_version": "photo-route-v1", "route_name": "knowledge"},
        }
        response = self.api.post("/v1/photo/charts/render", headers=AUTH, json=base)
        self.assertEqual(response.status_code, 422)
        self.assertIn("LESSON_GOAL_NOT_SUPPORTED", response.text)

    def test_generic_rsi_lesson_goal_renders_through_http_contract(self):
        response = self.api.post(
            "/v1/photo/charts/render",
            headers=AUTH,
            json={
                "schema_version": "photo-chart-request-v1",
                "content_type": "knowledge",
                "pages": [{
                    "page_no": 2,
                    "visual_type": "indicator_panel",
                    "visual_focus": "RSI range overview",
                    "required_elements": ["RSI", "30", "70"],
                    "annotations": [],
                    "risk_note": "教学示意图｜不代表实时行情",
                    "teaching_spec": {
                        "indicator_id": "rsi",
                        "indicator_kind": "oscillator",
                        "lesson_goal": "overview",
                    },
                }],
                "route_payload": {
                    "schema_version": "photo-route-v1",
                    "route_name": "knowledge",
                    "topic_text": "RSI指标是什么意思？如何使用？",
                },
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        asset = response.json()["assets"][0]
        self.assertEqual(asset["indicator_id"], "rsi")
        self.assertEqual(asset["render_language"], "zh-CN")
        self.assertTrue(asset["signal_contract_valid"])
        self.assertTrue(Path(asset["asset_path"]).is_file())

    def test_chart_language_is_forwarded_to_renderer(self):
        response = self.api.post(
            "/v1/photo/charts/render",
            headers=AUTH,
            json={
                "schema_version": "photo-chart-request-v1",
                "content_type": "knowledge",
                "language": "en",
                "pages": [{
                    "page_no": 2,
                    "visual_type": "indicator_panel",
                    "visual_focus": "RSI range overview",
                    "required_elements": ["RSI", "30", "70"],
                    "annotations": [],
                    "risk_note": "Educational illustration",
                    "teaching_spec": {
                        "indicator_id": "rsi",
                        "indicator_kind": "oscillator",
                        "lesson_goal": "overview",
                    },
                }],
                "route_payload": {
                    "schema_version": "photo-route-v1",
                    "route_name": "knowledge",
                    "topic_text": "RSI tutorial",
                },
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        asset = response.json()["assets"][0]
        self.assertEqual(asset["render_language"], "en")

    def test_chart_language_rejects_unknown_value_at_http_boundary(self):
        response = self.api.post(
            "/v1/photo/charts/render",
            headers=AUTH,
            json={
                "schema_version": "photo-chart-request-v1",
                "content_type": "knowledge",
                "language": "fr",
                "pages": [],
                "route_payload": {
                    "schema_version": "photo-route-v1",
                    "route_name": "knowledge",
                },
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_duplicate_teaching_charts_are_rejected_before_downstream_tools(self):
        duplicate_page = {
            "visual_type": "indicator_panel",
            "visual_focus": "RSI oversold recovery",
            "required_elements": ["RSI", "30"],
            "annotations": [],
            "risk_note": "教学示意图｜不代表实时行情",
            "teaching_spec": {
                "indicator_id": "rsi",
                "indicator_kind": "oscillator",
                "lesson_goal": "state_b",
            },
        }
        response = self.api.post(
            "/v1/photo/charts/render",
            headers=AUTH,
            json={
                "schema_version": "photo-chart-request-v1",
                "content_type": "knowledge",
                "pages": [
                    {"page_no": 3, **duplicate_page},
                    {"page_no": 4, **duplicate_page},
                ],
                "route_payload": {
                    "schema_version": "photo-route-v1",
                    "route_name": "knowledge",
                    "topic_text": "RSI tutorial",
                },
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("DUPLICATE_TEACHING_CHART", response.text)

    def test_rsi_four_page_teaching_path_returns_distinct_assets(self):
        definitions = (
            (2, "indicator_panel", "overview"),
            (3, "indicator_panel", "state_a"),
            (4, "indicator_panel", "state_b"),
            (6, "candlestick_demo", "worked_example"),
        )
        pages = [{
            "page_no": page_no,
            "visual_type": visual_type,
            "visual_focus": lesson_goal,
            "required_elements": ["RSI"],
            "annotations": [],
            "risk_note": "教学示意图｜不代表实时行情",
            "teaching_spec": {
                "indicator_id": "rsi",
                "indicator_kind": "oscillator",
                "lesson_goal": lesson_goal,
            },
        } for page_no, visual_type, lesson_goal in definitions]
        response = self.api.post(
            "/v1/photo/charts/render",
            headers=AUTH,
            json={
                "schema_version": "photo-chart-request-v1",
                "content_type": "knowledge",
                "pages": pages,
                "route_payload": {
                    "schema_version": "photo-route-v1",
                    "route_name": "knowledge",
                    "topic_text": "RSI指标是什么意思？如何使用？",
                },
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        assets = response.json()["assets"]
        self.assertEqual([item["page_no"] for item in assets], [2, 3, 4, 6])
        self.assertEqual(len({item["data_fingerprint"] for item in assets}), 4)
        self.assertTrue(all(item["signal_contract_valid"] for item in assets))

    def test_missing_required_asset_is_rejected(self):
        response = self.api.post(
            "/v1/photo/assets/resolve",
            headers=AUTH,
            json={
                "schema_version": "photo-asset-request-v1",
                "requests": [{
                    "page_no": 1,
                    "asset_type": "character",
                    "asset_key": "teacher_missing",
                    "purpose": "required",
                    "required": True,
                }],
                "chart_assets": [],
                "allowed_sources": ["project_owned"],
                "allow_paid_assets": False,
                "allow_unknown_license": False,
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("PHOTO_ASSET_NOT_FOUND", response.text)

    def test_render_rejects_missing_chinese_copy_without_shared_fallback(self):
        invalid_plan = photo_plan()
        invalid_plan["pages"][0]["title"] = ""
        invalid_plan["pages"][0]["body"] = ""
        response = self.api.post(
            "/v1/photo/render-post",
            headers=AUTH,
            json={
                "schema_version": "photo-render-request-v1",
                "photo_request_id": "photo-missing-copy-001",
                "canvas": {"width": 1080, "height": 1080},
                "theme_id": "finance_education_v1",
                "platform": "tiktok",
                "photo_plan": invalid_plan,
                "chart_assets": {"schema_version": "photo-chart-v1", "assets": []},
                "visual_assets": {"schema_version": "photo-assets-v1", "assets": []},
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("PAGE_1_CHINESE_COPY_REQUIRED", response.text)

    def test_render_post_accepts_optional_english_release_mode(self):
        plan = photo_plan()
        plan["post_title"] = "WHAT IS RSI?"
        plan["pages"][0].update({
            "title": "What is RSI?",
            "body": "Learn how the Relative Strength Index works.",
            "key_message": "Understand RSI before using it.",
            "visual_type": "cover_illustration",
            "visual_focus": "RSI indicator",
            "required_elements": ["RSI", "price chart"],
            "risk_note": "Educational illustration | Not real-time market data",
        })
        response = self.api.post(
            "/v1/photo/render-post",
            headers=AUTH,
            json={
                "schema_version": "photo-render-request-v1",
                "photo_request_id": "photo-english-release-001",
                "language": "en",
                "canvas": {"width": 1080, "height": 1080},
                "theme_id": "finance_education_v1",
                "platform": "tiktok",
                "photo_plan": plan,
                "chart_assets": {"schema_version": "photo-chart-v1", "assets": []},
                "visual_assets": {"schema_version": "photo-assets-v1", "assets": []},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        image = response.json()["images"][0]
        self.assertEqual(image["render_language"], "en")
        self.assertEqual(image["rendered_title"], "WHAT IS RSI?")
        self.assertEqual(image["typography_metrics"]["font_family"], "Montserrat")

    def test_repair_rejects_financial_fact_changes(self):
        for error_code in ("PRICE_MISMATCH", "FORECAST_CONDITION_MISMATCH", "TEACHING_SIGNAL_INVALID"):
            with self.subTest(error_code=error_code):
                response = self.api.post(
                    "/v1/photo/repair",
                    headers=AUTH,
                    json={
                        "schema_version": "photo-repair-request-v1",
                        "photo_plan": photo_plan(),
                        "render_result": {
                            "schema_version": "photo-render-v1",
                            "status": "completed",
                            "photo_job_id": "photo-fact-test",
                            "images": [],
                            "error": "",
                        },
                        "bad_pages": [1],
                        "errors": [{"page_no": 1, "code": error_code, "message": "fact"}],
                        "repair_count": 1,
                        "allowed_repairs": ["text_overflow"],
                        "protected_fields": ["price", "forecast_condition"],
                    },
                )
                self.assertEqual(response.status_code, 422)
                self.assertIn("PHOTO_FACT_REBUILD_REQUIRED", response.text)

    def test_complete_photo_flow_does_not_touch_video_or_macro_state(self):
        original_jobs = copy.deepcopy(main.JOBS)
        original_tts_jobs = copy.deepcopy(main.TTS_JOBS)
        original_macro_cache = copy.deepcopy(main.MACRO_STATUS_CACHE)
        usage_existed = main.MACRO_WORKFLOW_USAGE_PATH.exists()
        usage_mtime = (
            main.MACRO_WORKFLOW_USAGE_PATH.stat().st_mtime_ns
            if usage_existed else None
        )

        chart_response = self.api.post(
            "/v1/photo/charts/render",
            headers=AUTH,
            json={
                "schema_version": "photo-chart-request-v1",
                "content_type": "knowledge",
                "pages": [{
                    "page_no": 1,
                    "visual_type": "indicator_panel",
                    "visual_focus": "rsi_high_zone",
                    "required_elements": ["RSI曲线", "70参考线", "30参考线"],
                    "annotations": [],
                    "risk_note": "教学示意图｜不代表实时行情",
                }],
                "route_payload": {
                    "schema_version": "photo-route-v1",
                    "route_name": "knowledge",
                    "teaching_demo_only": True,
                },
            },
        )
        self.assertEqual(chart_response.status_code, 200, chart_response.text)
        charts = chart_response.json()
        self.assertEqual(charts["schema_version"], "photo-chart-v1")
        self.assertEqual(len(charts["assets"]), 1)
        self.assertTrue(Path(charts["assets"][0]["asset_path"]).is_file())

        asset_response = self.api.post(
            "/v1/photo/assets/resolve",
            headers=AUTH,
            json={
                "schema_version": "photo-asset-request-v1",
                "requests": [
                    {"page_no": 1, "asset_type": "character", "asset_key": "teacher_front", "purpose": "封面讲师", "required": True},
                    {"page_no": 1, "asset_type": "icon", "asset_key": "search", "purpose": "观察提示", "required": False},
                ],
                "chart_assets": charts["assets"],
                "allowed_sources": ["project_owned", "lucide", "brand_library", "generated_background"],
                "allow_paid_assets": False,
                "allow_unknown_license": False,
            },
        )
        self.assertEqual(asset_response.status_code, 200, asset_response.text)
        visuals = asset_response.json()
        self.assertEqual({item["license"] for item in visuals["assets"]}, {"PROJECT-OWNED", "ISC"})

        render_response = self.api.post(
            "/v1/photo/render-post",
            headers=AUTH,
            json={
                "schema_version": "photo-render-request-v1",
                "photo_request_id": "photo-test-flow-001",
                "canvas": {"width": 1080, "height": 1080},
                "theme_id": "finance_education_v1",
                "platform": "tiktok",
                "photo_plan": photo_plan(),
                "chart_assets": charts,
                "visual_assets": visuals,
            },
        )
        self.assertEqual(render_response.status_code, 200, render_response.text)
        rendered = render_response.json()
        self.assertEqual(rendered["schema_version"], "photo-render-v1")
        self.assertEqual(rendered["status"], "completed")
        self.assertEqual(len(rendered["images"]), 1)
        self.assertEqual(rendered["images"][0]["render_language"], "zh-CN")
        self.assertEqual(rendered["images"][0]["rendered_title"], "3分钟看懂RSI")
        self.assertEqual(
            rendered["images"][0]["rendered_disclaimer"],
            "教学示意图｜不代表实时行情",
        )
        page_path = Path(rendered["images"][0]["path"])
        self.assertTrue(page_path.is_file())
        self.assertIn("photo-work", str(page_path))

        qa_response = self.api.post(
            "/v1/photo/validate",
            headers=AUTH,
            json={
                "schema_version": "photo-qa-request-v1",
                "photo_plan": photo_plan(),
                "render_result": rendered,
                "checks": ["page_count", "page_order", "text_overflow", "mobile_readability", "demo_disclaimer"],
            },
        )
        self.assertEqual(qa_response.status_code, 200, qa_response.text)
        qa = qa_response.json()
        self.assertEqual(qa["schema_version"], "photo-qa-v1")
        self.assertTrue(qa["passed"], qa)

        repair_response = self.api.post(
            "/v1/photo/repair",
            headers=AUTH,
            json={
                "schema_version": "photo-repair-request-v1",
                "photo_plan": photo_plan(),
                "render_result": rendered,
                "bad_pages": [1],
                "errors": [{"page_no": 1, "code": "TEXT_OVERFLOW", "message": "test"}],
                "repair_count": 1,
                "allowed_repairs": ["text_overflow", "font_size", "spacing", "overlap", "missing_annotation", "icon_path", "demo_disclaimer"],
                "protected_fields": ["price", "time", "symbol", "timeframe", "indicator_value", "market_direction", "forecast_condition", "invalidation"],
            },
        )
        self.assertEqual(repair_response.status_code, 200, repair_response.text)
        repaired = repair_response.json()
        self.assertEqual(repaired["status"], "completed")
        self.assertTrue(repaired["images"][0]["topic_visual_present"])
        self.assertTrue(repaired["images"][0]["character_present"])

        job_response = self.api.get(
            f"/v1/photo/jobs/{rendered['photo_job_id']}", headers=AUTH
        )
        self.assertEqual(job_response.status_code, 200, job_response.text)
        self.assertEqual(job_response.json()["photo_job_id"], rendered["photo_job_id"])

        self.assertEqual(main.JOBS, original_jobs)
        self.assertEqual(main.TTS_JOBS, original_tts_jobs)
        self.assertEqual(main.MACRO_STATUS_CACHE, original_macro_cache)
        self.assertEqual(main.MACRO_WORKFLOW_USAGE_PATH.exists(), usage_existed)
        if usage_existed:
            self.assertEqual(
                main.MACRO_WORKFLOW_USAGE_PATH.stat().st_mtime_ns,
                usage_mtime,
            )


if __name__ == "__main__":
    unittest.main()
