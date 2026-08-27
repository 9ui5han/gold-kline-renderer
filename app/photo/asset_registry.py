import json
from pathlib import Path
from typing import Any


ALLOWED_LICENSES = {"CC0-1.0", "ISC", "PROJECT-OWNED", "UNDRAW-2026"}
ALLOWED_SOURCES = {
    "project_owned",
    "lucide",
    "brand_library",
    "generated_background",
    "undraw",
}

COVER_TOPIC_ASSETS = (
    (("RSI", "MACD", "KDJ", "ATR", "OBV", "指标", "预测"), "undraw_predictive_analytics"),
    (("营收", "收入", "利润", "REVENUE"), "undraw_revenue_analysis"),
    (("数据", "图表", "DATA"), "undraw_visual_data"),
    (("投资", "INVEST"), "undraw_investing"),
    (("预算", "BUDGET"), "undraw_budgeting"),
    (("计划", "PLAN"), "undraw_business_plan"),
)


class AssetRegistry:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        manifest_path = self.root / "assets-v1.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("PHOTO_ASSET_MANIFEST_INVALID") from exc
        if manifest.get("schema_version") != "photo-assets-manifest-v1":
            raise ValueError("PHOTO_ASSET_MANIFEST_VERSION_INVALID")
        assets = manifest.get("assets")
        if not isinstance(assets, dict):
            raise ValueError("PHOTO_ASSET_MANIFEST_ITEMS_INVALID")
        self.assets: dict[str, dict[str, Any]] = assets

    def resolve(self, asset_key: str, asset_type: str) -> dict[str, Any]:
        item = self.assets.get(str(asset_key or "").strip())
        if not isinstance(item, dict):
            raise KeyError("PHOTO_ASSET_NOT_FOUND")
        if item.get("asset_type") != asset_type:
            raise ValueError("PHOTO_ASSET_TYPE_MISMATCH")
        if item.get("source") not in ALLOWED_SOURCES:
            raise ValueError("PHOTO_ASSET_SOURCE_NOT_ALLOWED")
        if item.get("license") not in ALLOWED_LICENSES:
            raise ValueError("PHOTO_ASSET_LICENSE_NOT_ALLOWED")

        target = (self.root / str(item.get("relative_path") or "")).resolve()
        if self.root not in target.parents or not target.is_file():
            raise ValueError("PHOTO_ASSET_PATH_INVALID")
        return {
            "asset_key": str(asset_key),
            "asset_type": asset_type,
            "asset_path": str(target),
            "source": item["source"],
            "license": item["license"],
            "license_file": str(item.get("license_file") or ""),
        }

    def select_cover_asset(self, topic_text: str) -> dict[str, Any]:
        topic = str(topic_text or "").upper()
        selected = "undraw_business_analytics"
        for keywords, asset_key in COVER_TOPIC_ASSETS:
            if any(keyword in topic for keyword in keywords):
                selected = asset_key
                break
        return self.resolve(selected, "background")
