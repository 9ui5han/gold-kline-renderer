from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ContentType = Literal["knowledge", "market", "forecast"]
AllowedAssetSource = Literal[
    "project_owned",
    "lucide",
    "brand_library",
    "generated_background",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PhotoTeachingSpec(StrictModel):
    indicator_id: str = Field(min_length=1, max_length=40)
    indicator_kind: Literal["oscillator", "overlay", "price_structure", "pattern"]
    lesson_goal: str = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def indicator_kind_matches_plugin(self) -> "PhotoTeachingSpec":
        normalized = self.indicator_id.lower()
        generic_goals = {
            "overview", "state_a", "state_b", "components", "setup", "worked_example",
        }
        rsi_goals = generic_goals | {
            "range_overview", "oversold_recovery", "overbought_reversal",
        }
        ict_goals = {
            "bullish_order_block", "bearish_order_block",
            "bullish_liquidity_sweep", "bearish_liquidity_sweep",
            "bullish_fvg", "bearish_fvg", "bullish_bos", "bearish_bos",
        } | generic_goals
        if normalized in {"rsi", "rsi_14"} and self.indicator_kind != "oscillator":
            raise ValueError("INDICATOR_KIND_MISMATCH")
        if normalized in {"rsi", "rsi_14"} and self.lesson_goal not in rsi_goals:
            raise ValueError("LESSON_GOAL_NOT_SUPPORTED")
        if normalized in {"ict", "ict_structure"} and self.indicator_kind != "price_structure":
            raise ValueError("INDICATOR_KIND_MISMATCH")
        if normalized in {"ict", "ict_structure"} and self.lesson_goal not in ict_goals:
            raise ValueError("LESSON_GOAL_NOT_SUPPORTED")
        return self


class PhotoChartPage(StrictModel):
    page_no: int = Field(ge=1, le=10)
    visual_type: str = Field(min_length=1, max_length=40)
    visual_focus: str = Field(default="", max_length=100)
    required_elements: list[str] = Field(default_factory=list, max_length=12)
    annotations: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    risk_note: str = Field(default="", max_length=120)
    teaching_spec: PhotoTeachingSpec | None = None


class PhotoChartRequest(StrictModel):
    schema_version: Literal["photo-chart-request-v1"]
    content_type: ContentType
    pages: list[PhotoChartPage] = Field(default_factory=list, max_length=10)
    route_payload: dict[str, Any]


class PhotoAssetItem(StrictModel):
    page_no: int = Field(ge=1, le=10)
    asset_type: Literal["character", "icon", "logo", "background"]
    asset_key: str = Field(min_length=1, max_length=80)
    purpose: str = Field(default="", max_length=120)
    required: bool = False


class PhotoAssetRequest(StrictModel):
    schema_version: Literal["photo-asset-request-v1"]
    requests: list[PhotoAssetItem] = Field(default_factory=list, max_length=60)
    chart_assets: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    allowed_sources: list[AllowedAssetSource]
    allow_paid_assets: Literal[False] = False
    allow_unknown_license: Literal[False] = False

    @field_validator("allowed_sources")
    @classmethod
    def sources_must_not_be_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("PHOTO_ASSET_SOURCES_EMPTY")
        return value


class PhotoCanvas(StrictModel):
    width: int = Field(default=1080, ge=720, le=2160)
    height: int = Field(default=1080, ge=720, le=2160)


class PhotoRenderRequest(StrictModel):
    schema_version: Literal["photo-render-request-v1"]
    photo_request_id: str = Field(pattern=r"^photo-[A-Za-z0-9][A-Za-z0-9_-]{2,80}$")
    canvas: PhotoCanvas
    theme_id: Literal["finance_education_v1"]
    platform: Literal["tiktok", "instagram"]
    photo_plan: dict[str, Any]
    chart_assets: dict[str, Any]
    visual_assets: dict[str, Any]

    @model_validator(mode="after")
    def validate_contract_versions(self) -> "PhotoRenderRequest":
        if self.photo_plan.get("schema_version") != "photo-plan-v1":
            raise ValueError("PHOTO_PLAN_VERSION_INVALID")
        if self.chart_assets.get("schema_version") != "photo-chart-v1":
            raise ValueError("PHOTO_CHART_VERSION_INVALID")
        if self.visual_assets.get("schema_version") != "photo-assets-v1":
            raise ValueError("PHOTO_ASSETS_VERSION_INVALID")
        return self


class PhotoQaRequest(StrictModel):
    schema_version: Literal["photo-qa-request-v1"]
    photo_plan: dict[str, Any]
    render_result: dict[str, Any]
    checks: list[str] = Field(default_factory=list, max_length=20)


class PhotoRepairRequest(StrictModel):
    schema_version: Literal["photo-repair-request-v1"]
    photo_plan: dict[str, Any]
    render_result: dict[str, Any]
    bad_pages: list[int] = Field(min_length=1, max_length=10)
    errors: list[dict[str, Any]] = Field(default_factory=list, max_length=30)
    repair_count: Literal[1]
    allowed_repairs: list[str] = Field(default_factory=list, max_length=20)
    protected_fields: list[str] = Field(default_factory=list, max_length=20)
