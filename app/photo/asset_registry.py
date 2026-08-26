import json
from pathlib import Path
from typing import Any


ALLOWED_LICENSES = {"CC0-1.0", "ISC", "PROJECT-OWNED"}
ALLOWED_SOURCES = {
    "project_owned",
    "lucide",
    "brand_library",
    "generated_background",
}


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
