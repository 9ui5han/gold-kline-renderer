import json
import tempfile
import unittest
from pathlib import Path


class PhotoModelsStoreAssetsTests(unittest.TestCase):
    def test_docker_image_packages_photo_assets(self):
        project_root = Path(__file__).resolve().parents[1]
        dockerfile = (project_root / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY assets/photo ./assets/photo", dockerfile)

    def test_photo_font_renders_distinct_chinese_glyphs(self):
        from app.photo.chart_renderer import _font

        selected = _font(42)
        first = bytes(selected.getmask("指标"))
        second = bytes(selected.getmask("测试"))
        self.assertNotEqual(first, second)

    def test_production_manifest_contains_original_teacher_and_lucide_icons(self):
        from app.photo.asset_registry import AssetRegistry

        root = Path(__file__).resolve().parents[1] / "assets" / "photo"
        registry = AssetRegistry(root)

        for pose in (
            "teacher_front",
            "teacher_point_left",
            "teacher_point_right",
            "teacher_warning",
            "teacher_thinking",
        ):
            item = registry.resolve(pose, "character")
            self.assertEqual(item["license"], "PROJECT-OWNED")
            path = Path(item["asset_path"])
            if pose == "teacher_front":
                from PIL import Image
                with Image.open(path) as image:
                    self.assertEqual(image.mode, "RGBA")
                    self.assertNotEqual(image.getchannel("A").getextrema(), (255, 255))
            else:
                svg = path.read_text(encoding="utf-8")
                self.assertIn("viewBox=\"0 0 600 900\"", svg)
                self.assertNotIn("Open Peeps", svg)
                self.assertNotIn("Humaaans", svg)

        for icon in (
            "search",
            "arrow-right",
            "bookmark",
            "hand-pointer",
            "triangle-alert",
            "lightbulb",
            "circle-check",
            "circle-x",
        ):
            item = registry.resolve(icon, "icon")
            self.assertEqual(item["license"], "ISC")
            self.assertTrue(Path(item["asset_path"]).is_file())

        self.assertTrue((root / "licenses" / "lucide" / "LICENSE.txt").is_file())
        self.assertTrue(
            (root / "licenses" / "project-owned" / "CHARACTER-NOTICE.md").is_file()
        )

    def test_chart_request_rejects_unknown_schema(self):
        from app.photo.models import PhotoChartRequest

        with self.assertRaises(ValueError):
            PhotoChartRequest.model_validate({
                "schema_version": "wrong-version",
                "content_type": "knowledge",
                "pages": [],
                "route_payload": {},
            })

    def test_chart_request_accepts_generic_and_legacy_rsi_goals(self):
        from app.photo.models import PhotoChartRequest

        for lesson_goal in ("overview", "state_a", "state_b", "worked_example", "range_overview"):
            with self.subTest(lesson_goal=lesson_goal):
                request = PhotoChartRequest.model_validate({
                    "schema_version": "photo-chart-request-v1",
                    "content_type": "knowledge",
                    "pages": [{
                        "page_no": 2,
                        "visual_type": "indicator_panel",
                        "teaching_spec": {
                            "indicator_id": "rsi",
                            "indicator_kind": "oscillator",
                            "lesson_goal": lesson_goal,
                        },
                    }],
                    "route_payload": {"topic_text": "RSI tutorial"},
                })
                self.assertEqual(request.pages[0].teaching_spec.lesson_goal, lesson_goal)

    def test_chart_request_rejects_unknown_rsi_goal(self):
        from app.photo.models import PhotoChartRequest

        with self.assertRaises(ValueError):
            PhotoChartRequest.model_validate({
                "schema_version": "photo-chart-request-v1",
                "content_type": "knowledge",
                "pages": [{
                    "page_no": 2,
                    "visual_type": "indicator_panel",
                    "teaching_spec": {
                        "indicator_id": "rsi",
                        "indicator_kind": "oscillator",
                        "lesson_goal": "not_a_real_goal",
                    },
                }],
                "route_payload": {"topic_text": "RSI tutorial"},
            })

    def test_asset_request_rejects_paid_and_unknown_sources(self):
        from app.photo.models import PhotoAssetRequest

        with self.assertRaises(ValueError):
            PhotoAssetRequest.model_validate({
                "schema_version": "photo-asset-request-v1",
                "requests": [],
                "chart_assets": [],
                "allowed_sources": ["unknown-stock-site"],
                "allow_paid_assets": False,
                "allow_unknown_license": False,
            })

    def test_store_uses_only_photo_work_directory(self):
        from app.photo.store import PhotoStore

        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            store = PhotoStore(data_dir)
            job_dir = store.job_dir("photo-abc123")

            self.assertEqual(job_dir, data_dir / "photo-work" / "photo-abc123")
            self.assertNotIn("macro", str(job_dir))
            self.assertNotIn("video", str(job_dir))
            self.assertNotIn("tts", str(job_dir))

    def test_store_rejects_non_photo_job_id(self):
        from app.photo.store import PhotoStore

        with tempfile.TemporaryDirectory() as directory:
            store = PhotoStore(Path(directory))
            with self.assertRaises(ValueError):
                store.job_dir("video-abc")

    def test_registry_returns_only_allowed_licenses(self):
        from app.photo.asset_registry import AssetRegistry

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "characters").mkdir(parents=True)
            (root / "icons").mkdir(parents=True)
            (root / "characters" / "teacher_front.svg").write_text(
                "<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8"
            )
            (root / "icons" / "search.svg").write_text(
                "<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8"
            )
            manifest = {
                "schema_version": "photo-assets-manifest-v1",
                "assets": {
                    "teacher_front": {
                        "asset_type": "character",
                        "relative_path": "characters/teacher_front.svg",
                        "source": "project_owned",
                        "license": "PROJECT-OWNED",
                        "license_file": "licenses/project-owned/CHARACTER-NOTICE.md",
                    },
                    "search": {
                        "asset_type": "icon",
                        "relative_path": "icons/search.svg",
                        "source": "lucide",
                        "license": "ISC",
                        "license_file": "licenses/lucide/LICENSE.txt",
                    },
                },
            }
            (root / "assets-v1.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            registry = AssetRegistry(root)
            teacher = registry.resolve("teacher_front", "character")
            search = registry.resolve("search", "icon")

            self.assertEqual(teacher["license"], "PROJECT-OWNED")
            self.assertEqual(search["license"], "ISC")
            self.assertTrue(Path(teacher["asset_path"]).is_file())
            self.assertTrue(Path(search["asset_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
