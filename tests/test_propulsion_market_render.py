import copy
import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.photo.market_chart_renderer import PLOT, render_market_chart


def _bars(count=8):
    return [
        {
            "t": f"2026-08-31 {index:02d}:00:00",
            "o": 4000.0 + index,
            "h": 4002.0 + index,
            "l": 3999.0 + index,
            "c": 4001.0 + index,
            "v": 1000 + index,
        }
        for index in range(count)
    ]


def market_page(page_no=1):
    return {
        "page_no": page_no,
        "concept_term": "propulsion block",
        "direction": "bullish",
        "lesson_type": "setup",
        "visible_kline": _bars(),
        "slice_start": 10, "slice_end": 18,
        "anchor_index": 4, "confirmation_index": 7,
        "as_of": "2026-08-31 07:00:00",
        "bars_closed": True,
        "zones": [
            {"kind": "order_block", "start_index": 1, "end_index": 7,
             "price_low": 4000.0, "price_high": 4001.0, "label": "Order block"},
            {"kind": "propulsion_block", "start_index": 4, "end_index": 7,
             "price_low": 4003.0, "price_high": 4004.0, "label": "Propulsion"},
        ],
        "markers": [
            {"kind": "liquidity_sweep", "index": 2, "price": 4001.0, "reference_index": 0},
            {"kind": "inducement", "index": 5, "price": 4004.0, "reference_index": 3},
        ],
        "rule_version": "pb-edu-v1",
        "chart_mode": "educational_reconstruction",
        "historical_pattern_claim": False,
    }


class PropulsionMarketRenderTests(unittest.TestCase):
    def test_market_chart_uses_a_taller_plot_without_changing_source_coordinates(self):
        self.assertEqual(PLOT, (74, 95, 1014, 650))
        page = market_page()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "market.png"
            result = render_market_chart(
                page,
                output,
                {"market": "XAUUSD", "timeframe": "1h", "input_meta": {}},
                language="en",
            )
            self.assertEqual(Image.open(output).convert("RGB").getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(result["palette"]["background"], "#F7F8FA")
        self.assertEqual(result["coordinate_map"]["zones"][0]["start_index"], 1)
        self.assertEqual(result["coordinate_map"]["markers"][1]["index"], 5)

    def test_invalid_direction_and_marker_price_rejected(self):
        for field,value,error in [('direction','unknown','MARKET_DIRECTION_INVALID')]:
            page=market_page(); page[field]=value
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ValueError,error):
                    render_market_chart(page,Path(tmp)/'bad.png',{'timeframe':'1h'},language='en')
        page=market_page(); page['markers'][0]['price']=3000
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'MARKET_MARKER_PRICE_MISMATCH'):
                render_market_chart(page,Path(tmp)/'bad.png',{'timeframe':'1h'},language='en')

    def test_marker_reference_must_be_earlier_and_in_range(self):
        page=market_page(); page['markers'][0]['reference_index']=-999
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'MARKET_MARKER_REFERENCE_INVALID'):
                render_market_chart(page,Path(tmp)/'bad.png',{'timeframe':'1h'},language='en')

    def test_full_local_pipeline_and_reused_real_chart(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.photo.routes import build_photo_router
        root=Path(os.environ['PROPULSION_V2_ROOT'])
        fixture=runpy.run_path(str(root/'test_analysis.py'),run_name='fixture_module')
        analysis=runpy.run_path(str(root/'02_analyze.py'))
        source,rules=fixture['inputs']()
        source['page_count']=4
        rules['rules'].append(dict(rules['rules'][0],page_no=3))
        a=analysis['main'](json.dumps(source),rules,'[]')
        self.assertTrue(a['tool2_valid'],a)
        copy_pages=[dict(page_no=i,role='cover' if i==1 else 'promo' if i==4 else 'definition',
            en_title='Example',en_body='',zh_translation='示例',text_position='top',
            analysis_page_no=None if i in (1,4) else i) for i in range(1,5)]
        promo = json.dumps({"platform": "telegram", "account": "TikTok111", "cta": "JOIN FREE"})
        plan=runpy.run_path(str(root/'03_plan.py'))['main'](a['trusted_analysis_json'],{'pages':copy_pages},promo,'{"model":"gpt-image-2"}')
        self.assertTrue(plan['tool3_valid'],plan)
        build=runpy.run_path(str(root/'04_build.py'))['main'](plan['trusted_page_plan_json'])
        self.assertTrue(build['build_valid'],build)
        with tempfile.TemporaryDirectory() as tmp:
            app=FastAPI(); app.include_router(build_photo_router(Path(tmp),Path('assets/photo'),'https://example.invalid'))
            client=TestClient(app)
            response=client.post('/v1/photo/charts/render',json=json.loads(build['chart_req_json']))
            self.assertEqual(response.status_code,200,response.text)
            legacy=json.loads(build['chart_req_json']); legacy['content_type']='market'
            legacy_response=client.post('/v1/photo/charts/render',json=legacy)
            self.assertEqual(legacy_response.status_code,422,legacy_response.text)
            assets=response.json()['assets']
            self.assertEqual(len(assets),2)
            self.assertNotEqual(assets[0]['data_fingerprint'],assets[1]['data_fingerprint'])
            self.assertTrue(Path(assets[0]['asset_path']).is_file())
            # These cover URLs are local response fixtures, not real image API calls.
            image_body='{"data":[{"url":"https://example.invalid/test.png"}]}'
            delivery=runpy.run_path(str(root/'04_assemble.py'))['main'](plan['trusted_page_plan_json'],200,response.text,200,image_body,200,image_body)
            self.assertTrue(delivery['tool4_valid'],delivery)
            self.assertEqual(json.loads(delivery['carousel_delivery_json'])['status'],'assets_assembled')
            bad=json.loads(build['chart_req_json']); bad['route_payload']['analysis_pages'][0]['zones'][0]['price_low']-=1
            rejected=client.post('/v1/photo/charts/render',json=bad)
            self.assertEqual(rejected.status_code,422)

    def test_real_market_chart_writes_png_and_exposes_source_coordinates(self):
        page = market_page()
        with tempfile.TemporaryDirectory() as tmp:
            result = render_market_chart(
                page,
                Path(tmp) / "market.png",
                {"market": "XAUUSD", "timeframe": "1h", "input_meta": {"data_timezone": "not_provided"}},
                language="en",
            )
            self.assertEqual(result["source_type"], "educational_reconstruction")
            self.assertEqual(result["data_timezone"], "not_provided")
            self.assertEqual(result["rendered_candle_count"], 8)
            self.assertEqual(result["coordinate_map"]["zones"][0]["start_index"], 1)
            self.assertEqual(result["coordinate_map"]["markers"][1]["index"], 5)
            self.assertTrue(Path(result["asset_path"]).is_file())
            with Image.open(result["asset_path"]) as image:
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(image.size, (1080, 720))

    def test_editorial_style_metadata_does_not_change_data_fingerprint(self):
        page = market_page()
        expected = {
            "market": "XAUUSD",
            "timeframe": "1h",
            "visible_kline": page["visible_kline"],
            "zones": page["zones"],
            "markers": page["markers"],
            "as_of": page["as_of"],
            "rule_version": page["rule_version"],
        }
        expected_fingerprint = __import__("hashlib").sha256(
            json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            result = render_market_chart(
                page,
                Path(tmp) / "styled.png",
                {"market": "XAUUSD", "timeframe": "1h", "input_meta": {}},
                language="en",
            )
        self.assertEqual(result["data_fingerprint"], expected_fingerprint)
        self.assertEqual(result["style_version"], "trading-editorial-v1")
        self.assertEqual(result["palette"]["bullish"], "#D8A12E")
        self.assertEqual(result["palette"]["bearish"], "#123B5D")

    def test_rejects_out_of_bounds_and_unclosed_page_data(self):
        page = market_page()
        page["zones"][0]["end_index"] = 8
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "MARKET_ZONE_INDEX_OUT_OF_RANGE"):
                render_market_chart(page, Path(tmp) / "bad.png", {"timeframe": "1h"}, language="en")

        page = market_page()
        page["bars_closed"] = False
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "MARKET_BARS_NOT_CLOSED"):
                render_market_chart(page, Path(tmp) / "bad.png", {}, language="en")

    def test_rejects_invalid_ohlc_prices_and_noncontinuous_timestamps(self):
        page = market_page()
        page["visible_kline"][3]["h"] = 3999.0
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "MARKET_OHLC_INVALID"):
                render_market_chart(page, Path(tmp) / "bad.png", {}, language="en")

        page = market_page()
        page["visible_kline"][4]["t"] = "2026-08-31 05:30:00"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "MARKET_TIMEFRAME_MISMATCH"):
                render_market_chart(page, Path(tmp) / "bad.png", {"timeframe": "1h"}, language="en")

    def test_rejects_wrong_timeframe_bool_indices_and_tampered_zone(self):
        page = market_page()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "MARKET_TIMEFRAME_MISMATCH"):
                render_market_chart(page, Path(tmp) / "bad.png", {"timeframe": "15m"}, language="en")
        page = market_page()
        page["anchor_index"] = True
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "MARKET_ANCHOR_OR_CONFIRMATION_OUT_OF_RANGE"):
                render_market_chart(page, Path(tmp) / "bad.png", {"timeframe": "1h"}, language="en")
        page = market_page()
        page["zones"][0]["price_high"] = 4001.5
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "MARKET_ZONE_BOUNDARY_MISMATCH"):
                render_market_chart(page, Path(tmp) / "bad.png", {"timeframe": "1h"}, language="en")

    def test_analysis_output_integrates_for_bull_bear_and_checklist(self):
        root = Path(os.environ["PROPULSION_V2_ROOT"])
        analysis = runpy.run_path(str(root / "02_analyze.py"))
        fixtures = runpy.run_path(str(root / "test_analysis.py"), run_name="fixture_module")
        for direction, lesson in (("bullish", "definition"), ("bearish", "definition"), ("unspecified", "checklist")):
            source, rules = fixtures["inputs"](direction, lesson)
            response = analysis["main"](json.dumps(source), rules, "[]")
            self.assertTrue(response["tool2_valid"], response)
            output = json.loads(response["trusted_analysis_json"])
            page = output["analysis_pages"][0]
            with tempfile.TemporaryDirectory() as tmp:
                result = render_market_chart(
                    page, Path(tmp) / "real.png",
                    {"market": output["market"], "timeframe": output["timeframe"], "input_meta": output["input_meta"]},
                    language="en",
                )
            self.assertEqual(result["data_timezone"], "not_provided")
            self.assertTrue(result["data_fingerprint"])

    def test_market_request_validation_requires_matching_unique_pages(self):
        from app.photo.market_chart_renderer import validate_market_request

        request_pages = [{"page_no": 1, "visual_type": "market_chart"}]
        payload = {
            "schema_version": "carousel-route-v2",
            "analysis_mode": "educational_reconstruction",
            "market": "XAUUSD",
            "timeframe": "1h",
            "input_meta": {"timezone": "unknown"},
            "analysis_pages": [market_page()],
        }
        validated = validate_market_request(request_pages, payload)
        self.assertEqual(validated[0]["page_no"], 1)
        duplicate = copy.deepcopy(payload)
        duplicate["analysis_pages"].append(market_page())
        with self.assertRaisesRegex(ValueError, "MARKET_PAGE_NO_DUPLICATE"):
            validate_market_request(request_pages, duplicate)


if __name__ == "__main__":
    unittest.main()
