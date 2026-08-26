from pathlib import Path
from typing import Any

from PIL import Image


def validate_post(photo_plan: dict[str, Any], render_result: dict[str, Any]) -> dict[str, Any]:
    errors = []
    bad_pages = set()
    planned_pages = photo_plan.get("pages") or []
    images = render_result.get("images") or []
    if len(images) != len(planned_pages):
        errors.append({"page_no": 0, "code": "PAGE_COUNT_MISMATCH", "message": "图片数量与计划不一致"})
    image_by_page = {item.get("page_no"): item for item in images if isinstance(item, dict)}
    for page in planned_pages:
        page_no = int(page.get("page_no") or 0)
        item = image_by_page.get(page_no)
        if not item:
            bad_pages.add(page_no)
            errors.append({"page_no": page_no, "code": "PAGE_IMAGE_MISSING", "message": "页面图片不存在"})
            continue
        path = Path(str(item.get("path") or ""))
        if not path.is_file():
            bad_pages.add(page_no)
            errors.append({"page_no": page_no, "code": "PAGE_FILE_MISSING", "message": "页面文件不存在"})
            continue
        with Image.open(path) as image:
            if image.size != (int(item.get("width", 0)), int(item.get("height", 0))):
                bad_pages.add(page_no)
                errors.append({"page_no": page_no, "code": "PAGE_SIZE_INVALID", "message": "图片尺寸不一致"})
        if item.get("layout_overflow") is True:
            bad_pages.add(page_no)
            errors.append({"page_no": page_no, "code": "TEXT_OVERFLOW", "message": "文字超出安全长度"})
        if item.get("render_language") != "en" or item.get("english_contract_valid") is not True:
            bad_pages.add(page_no)
            errors.append({"page_no": page_no, "code": "NON_ENGLISH_RENDER", "message": "最终图片必须使用英文"})
        if item.get("layout_overlap") is True:
            bad_pages.add(page_no)
            errors.append({"page_no": page_no, "code": "LAYOUT_OVERLAP", "message": "人物、图表或文字发生遮挡"})
        if int(item.get("disclaimer_count") or 0) != 1:
            bad_pages.add(page_no)
            errors.append({"page_no": page_no, "code": "DISCLAIMER_COUNT_INVALID", "message": "英文免责声明必须且只能出现一次"})
        if page.get("visual_type") in {"indicator_panel", "zone_diagram", "candlestick_demo", "market_chart"}:
            evidence = item.get("teaching_evidence") if isinstance(item.get("teaching_evidence"), dict) else {}
            if (
                evidence.get("engine_version") != "indicator-teaching-v1" or
                evidence.get("signal_contract_valid") is not True or
                int(evidence.get("ohlc_count") or 0) < 40 or
                not evidence.get("signal_anchors") or
                not evidence.get("data_fingerprint")
            ):
                bad_pages.add(page_no)
                errors.append({"page_no": page_no, "code": "TEACHING_SIGNAL_INVALID", "message": "指标信号没有绑定到有效教学K线"})
        if photo_plan.get("content_type") == "knowledge" and page.get("visual_type") in {"indicator_panel", "candlestick_demo", "market_chart"}:
            if item.get("risk_note_present") is not True:
                bad_pages.add(page_no)
                errors.append({"page_no": page_no, "code": "DEMO_DISCLAIMER_MISSING", "message": "缺少教学示意说明"})
    passed = not errors
    delivery = {
        "photo_job_id": render_result.get("photo_job_id", ""),
        "image_urls": [item.get("url", "") for item in images],
        "image_count": len(images),
    } if passed else {}
    return {
        "schema_version": "photo-qa-v1",
        "passed": passed,
        "bad_pages": sorted(bad_pages),
        "errors": errors,
        "delivery": delivery,
    }
