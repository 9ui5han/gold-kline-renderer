from pathlib import Path
from typing import Any

from PIL import Image


def _layout_regions_valid(item: dict[str, Any]) -> bool:
    regions = item.get("layout_regions")
    if not isinstance(regions, dict):
        return False
    required = {"header", "title", "body", "footer"}
    if item.get("chart_present") is True:
        required.add("chart")
    if item.get("checklist_present") is True:
        required.add("checklist")
    if item.get("layout_template") == "summary":
        required.add("summary")
    if item.get("character_present") is True:
        required.add("character")
    width, height = int(item.get("width") or 0), int(item.get("height") or 0)
    for name in required:
        bounds = regions.get(name)
        if (
            not isinstance(bounds, (list, tuple)) or len(bounds) != 4 or
            not all(isinstance(value, (int, float)) for value in bounds) or
            bounds[0] < 0 or bounds[1] < 0 or
            bounds[0] >= bounds[2] or bounds[1] >= bounds[3] or
            bounds[2] > width or bounds[3] > height
        ):
            return False
    return True


def validate_post(
    photo_plan: dict[str, Any],
    render_result: dict[str, Any],
    expected_language: str = "zh-CN",
) -> dict[str, Any]:
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
            errors.append({"page_no": page_no, "code": "LAYOUT_OVERFLOW", "message": "页面内容超出安全区域"})
        language_valid = (
            item.get("render_language") == expected_language
            and item.get("copy_contract_valid", item.get("chinese_contract_valid")) is True
        )
        if expected_language == "zh-CN":
            language_valid = language_valid and item.get("chinese_contract_valid") is True
        if not language_valid:
            bad_pages.add(page_no)
            errors.append({
                "page_no": page_no,
                "code": (
                    "NON_CHINESE_RENDER"
                    if expected_language == "zh-CN"
                    else "LANGUAGE_RENDER_MISMATCH"
                ),
                "message": f"页面语言必须为{expected_language}",
            })
        if item.get("layout_overlap") is True:
            bad_pages.add(page_no)
            errors.append({"page_no": page_no, "code": "LAYOUT_OVERLAP", "message": "人物、图表或文字发生遮挡"})
        if not _layout_regions_valid(item):
            bad_pages.add(page_no)
            errors.append({"page_no": page_no, "code": "LAYOUT_REGIONS_MISSING", "message": "缺少有效页面布局区域"})
        if int(item.get("disclaimer_count") or 0) != 1:
            bad_pages.add(page_no)
            errors.append({"page_no": page_no, "code": "DISCLAIMER_COUNT_INVALID", "message": "教学示意说明必须且只能出现一次"})
        role = str(page.get("page_role") or "")
        visual_type = str(page.get("visual_type") or "")
        if (role == "cover" or visual_type == "cover_illustration") and item.get("topic_visual_present") is not True:
            bad_pages.add(page_no)
            errors.append({"page_no": page_no, "code": "COVER_TOPIC_VISUAL_MISSING", "message": "封面缺少与主题相关的主视觉"})
        if role in {"checklist", "mistakes"} or visual_type == "checklist":
            if item.get("checklist_present") is not True or int(item.get("checklist_item_count") or 0) < 1:
                bad_pages.add(page_no)
                errors.append({"page_no": page_no, "code": "CHECKLIST_CONTENT_MISSING", "message": "检查清单主体为空"})
            if item.get("chart_present") is not True:
                bad_pages.add(page_no)
                errors.append({"page_no": page_no, "code": "CHECKLIST_CHART_MISSING", "message": "检查清单缺少教学K线图"})
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
