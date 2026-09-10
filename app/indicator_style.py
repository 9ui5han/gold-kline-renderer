"""Shared indicator-style contract for TOOL-07, TOOL-08, and TOOL-09.

The module is deterministic.  LLM output may reference this contract, but it
cannot select indicators, calculate values, or declare what was narrated.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Iterable


class IndicatorStyleError(ValueError):
    """Raised when an indicator style or its use violates the contract."""


DEFAULT_INDICATOR_PROFILE: dict[str, Any] = {
    "schema_version": "indicator-profile-v1",
    "style_id": "dual_ema_trend",
    "indicator_ids": ["dual_ema"],
    "max_indicators": 2,
    "components": ["ema20", "ema50"],
    "allowed_fact_ids": [
        "ema_alignment",
        "ema_cross",
        "close_vs_ema20",
        "close_vs_ema50",
    ],
    "show_from_role": "primary_forecast",
    "continue_to_end": True,
    "fade_in_ms": 250,
}


INDICATOR_REGISTRY: dict[str, dict[str, Any]] = {
    "dual_ema": {
        "components": ("ema20", "ema50"),
        "allowed_fact_ids": (
            "ema_alignment",
            "ema_cross",
            "close_vs_ema20",
            "close_vs_ema50",
        ),
        "min_closed_candles": 50,
        "renderer": "price_overlay",
    },
}


INDICATOR_ALIAS_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "dual_ema",
        (
            r"\bema\s*20\b",
            r"\bema\s*50\b",
            r"\bemas?\b",
            r"\bexponential\s+moving\s+averages?\b",
            r"\bdual\s+moving\s+averages?\b",
        ),
    ),
    ("macd", (r"\bmacd\b", r"\bmacd\s+histogram\b")),
    ("rsi", (r"\brsi(?:\s*14)?\b", r"\brelative\s+strength\s+index\b")),
    ("bollinger", (r"\bbollinger\s+bands?\b",)),
    ("atr", (r"\batr(?:\s*14)?\b", r"\baverage\s+true\s+range\b")),
    ("kdj", (r"\bkdj\b", r"\bstochastic\b")),
    ("vwap", (r"\bvwap\b", r"\bvolume[- ]weighted\s+average\s+price\b")),
)


ROLE_ALIASES = {
    "primary_wait": "primary_forecast",
    "primary_wait_path": "primary_forecast",
    "primary_conditions": "primary_forecast",
    "primary_path": "primary_forecast",
    "alternate_path": "alternate_forecast",
}

ROLE_ORDER = (
    "opening_hook",
    "technical_context",
    "macro_context",
    "primary_forecast",
    "alternate_forecast",
    "closing_question",
)


def _string_list(value: Any, error_code: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise IndicatorStyleError(error_code)
    return [item.strip() for item in value]


def normalize_indicator_profile(profile: Any) -> dict[str, Any]:
    """Return a validated copy of an ``indicator-profile-v1`` object."""
    if not isinstance(profile, dict) or not profile:
        raise IndicatorStyleError("INDICATOR_PROFILE_REQUIRED")
    if profile.get("schema_version") != "indicator-profile-v1":
        raise IndicatorStyleError("INDICATOR_PROFILE_VERSION_INVALID")

    indicator_ids = _string_list(
        profile.get("indicator_ids"),
        "INDICATOR_IDS_INVALID",
    )
    if len(indicator_ids) > 2:
        raise IndicatorStyleError("INDICATOR_COUNT_EXCEEDED")
    if len(set(indicator_ids)) != len(indicator_ids):
        raise IndicatorStyleError("INDICATOR_ID_DUPLICATE")
    for indicator_id in indicator_ids:
        if indicator_id not in INDICATOR_REGISTRY:
            raise IndicatorStyleError(f"INDICATOR_NOT_REGISTERED:{indicator_id}")

    components = _string_list(
        profile.get("components"),
        "INDICATOR_COMPONENTS_INVALID",
    )
    expected_components = [
        component
        for indicator_id in indicator_ids
        for component in INDICATOR_REGISTRY[indicator_id]["components"]
    ]
    if components != expected_components:
        bad = next(
            (
                component
                for component in components
                if component not in expected_components
            ),
            "set",
        )
        raise IndicatorStyleError(f"INDICATOR_COMPONENT_INVALID:{bad}")

    allowed_fact_ids = _string_list(
        profile.get("allowed_fact_ids"),
        "INDICATOR_FACT_IDS_INVALID",
    )
    expected_facts = {
        fact_id
        for indicator_id in indicator_ids
        for fact_id in INDICATOR_REGISTRY[indicator_id]["allowed_fact_ids"]
    }
    for fact_id in allowed_fact_ids:
        if fact_id not in expected_facts:
            raise IndicatorStyleError(f"INDICATOR_FACT_NOT_ALLOWED:{fact_id}")

    maximum = profile.get("max_indicators")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum != 2:
        raise IndicatorStyleError("MAX_INDICATORS_INVALID")
    if len(indicator_ids) > maximum:
        raise IndicatorStyleError("INDICATOR_COUNT_EXCEEDED")

    normalized = copy.deepcopy(profile)
    normalized["style_id"] = str(profile.get("style_id") or "").strip()
    if not normalized["style_id"]:
        raise IndicatorStyleError("STYLE_ID_REQUIRED")
    normalized["indicator_ids"] = indicator_ids
    normalized["components"] = components
    normalized["allowed_fact_ids"] = allowed_fact_ids
    normalized["show_from_role"] = ROLE_ALIASES.get(
        str(profile.get("show_from_role") or "").strip(),
        str(profile.get("show_from_role") or "").strip(),
    )
    if normalized["show_from_role"] not in ROLE_ORDER:
        raise IndicatorStyleError("INDICATOR_SHOW_ROLE_INVALID")
    if not isinstance(profile.get("continue_to_end"), bool):
        raise IndicatorStyleError("INDICATOR_CONTINUE_INVALID")
    fade_in_ms = profile.get("fade_in_ms")
    if isinstance(fade_in_ms, bool) or not isinstance(fade_in_ms, int):
        raise IndicatorStyleError("INDICATOR_FADE_INVALID")
    if fade_in_ms < 0 or fade_in_ms > 1000:
        raise IndicatorStyleError("INDICATOR_FADE_INVALID")
    return normalized


def default_indicator_profile() -> dict[str, Any]:
    """Return an isolated validated copy of the current fixed style."""
    return normalize_indicator_profile(copy.deepcopy(DEFAULT_INDICATOR_PROFILE))


def canonical_planning_role(role: Any) -> str:
    value = str(role or "").strip()
    return ROLE_ALIASES.get(value, value)


def indicator_ids_for_role(
    profile: Any,
    planning_role: Any,
) -> dict[str, list[str]]:
    """Return narration and render permissions for one segment role."""
    normalized = normalize_indicator_profile(profile)
    role = canonical_planning_role(planning_role)
    if role not in ROLE_ORDER:
        raise IndicatorStyleError(f"PLANNING_ROLE_INVALID:{role or 'empty'}")
    show_role = normalized["show_from_role"]
    role_index = ROLE_ORDER.index(role)
    show_index = ROLE_ORDER.index(show_role)
    enabled = role_index >= show_index
    if not enabled:
        return {"allowed": [], "required": [], "rendered": []}

    indicator_ids = list(normalized["indicator_ids"])
    required = (
        indicator_ids
        if role in {"primary_forecast", "alternate_forecast"}
        else []
    )
    rendered = (
        indicator_ids
        if role != "closing_question" or normalized["continue_to_end"]
        else []
    )
    return {
        "allowed": indicator_ids,
        "required": list(required),
        "rendered": list(rendered),
    }


def _price_relation(price: float, average: float) -> str:
    if abs(price - average) <= 1e-9:
        return "at"
    return "above" if price > average else "below"


def filter_indicator_context(
    technical_contract: Any,
    profile: Any,
) -> dict[str, Any]:
    """Expose only facts for selected indicators from a technical contract."""
    normalized = normalize_indicator_profile(profile)
    if not isinstance(technical_contract, dict):
        raise IndicatorStyleError("TECHNICAL_CONTRACT_REQUIRED")
    if technical_contract.get("schema_version") != "technical-contract-v1":
        raise IndicatorStyleError("TECHNICAL_CONTRACT_VERSION_INVALID")
    indicator_facts = technical_contract.get("indicator_facts")
    if not isinstance(indicator_facts, dict):
        raise IndicatorStyleError("INDICATOR_FACTS_REQUIRED")
    timeframe = str(indicator_facts.get("primary_timeframe") or "").strip()
    timeframes = indicator_facts.get("timeframes")
    facts = timeframes.get(timeframe) if isinstance(timeframes, dict) else None
    if not timeframe or not isinstance(facts, dict):
        raise IndicatorStyleError("PRIMARY_INDICATOR_FACTS_REQUIRED")

    if normalized["indicator_ids"] != ["dual_ema"]:
        unsupported = normalized["indicator_ids"][0] if normalized["indicator_ids"] else "empty"
        raise IndicatorStyleError(f"INDICATOR_NOT_IMPLEMENTED:{unsupported}")
    try:
        closed_count = int(facts["closed_count"])
        last_close = float(facts["last_close"])
        ema20 = float(facts["ema20"])
        ema50 = float(facts["ema50"])
    except (KeyError, TypeError, ValueError) as exc:
        raise IndicatorStyleError("DUAL_EMA_FACTS_INVALID") from exc
    if closed_count < int(INDICATOR_REGISTRY["dual_ema"]["min_closed_candles"]):
        raise IndicatorStyleError("DUAL_EMA_DATA_INSUFFICIENT")

    return {
        "schema_version": "indicator-context-v1",
        "style_id": normalized["style_id"],
        "primary_timeframe": timeframe,
        "indicator_ids": ["dual_ema"],
        "facts": {
            "closed_count": closed_count,
            "last_close": last_close,
            "ema20": ema20,
            "ema50": ema50,
            "ema_alignment": (
                "ema20_above_ema50"
                if ema20 > ema50
                else "ema20_below_ema50"
                if ema20 < ema50
                else "ema20_equal_ema50"
            ),
            "close_vs_ema20": _price_relation(last_close, ema20),
            "close_vs_ema50": _price_relation(last_close, ema50),
        },
    }


def filter_technical_base(technical_contract: Any) -> dict[str, Any]:
    """Return non-indicator market facts safe for planning and narration."""
    if not isinstance(technical_contract, dict):
        raise IndicatorStyleError("TECHNICAL_CONTRACT_REQUIRED")
    if technical_contract.get("schema_version") != "technical-contract-v1":
        raise IndicatorStyleError("TECHNICAL_CONTRACT_VERSION_INVALID")
    indicator_facts = technical_contract.get("indicator_facts")
    if not isinstance(indicator_facts, dict):
        raise IndicatorStyleError("INDICATOR_FACTS_REQUIRED")
    timeframe = str(indicator_facts.get("primary_timeframe") or "").strip()
    timeframes = indicator_facts.get("timeframes")
    primary = timeframes.get(timeframe) if isinstance(timeframes, dict) else None
    if not timeframe or not isinstance(primary, dict):
        raise IndicatorStyleError("PRIMARY_INDICATOR_FACTS_REQUIRED")
    last_candle = indicator_facts.get("last_real_candle")
    return {
        "schema_version": "technical-base-v1",
        "primary_timeframe": timeframe,
        "data_as_of": (
            str(last_candle.get("time") or "")
            if isinstance(last_candle, dict)
            else ""
        ),
        "closed_count": int(primary.get("closed_count") or 0),
        "last_close": float(primary.get("last_close") or 0.0),
        "market_structure": str(primary.get("structure") or ""),
    }


def detect_narrated_indicator_ids(text: Any) -> list[str]:
    """Derive mentioned indicator IDs from narration; never trust LLM claims."""
    source = str(text or "")
    detected: list[str] = []
    for indicator_id, patterns in INDICATOR_ALIAS_PATTERNS:
        if any(re.search(pattern, source, flags=re.IGNORECASE) for pattern in patterns):
            detected.append(indicator_id)
    return detected


def validate_narration_indicators(
    text: Any,
    *,
    allowed_indicator_ids: Iterable[str],
    required_indicator_ids: Iterable[str],
) -> list[str]:
    """Reject unselected indicators and missing indicators required by a role."""
    allowed = list(dict.fromkeys(str(item) for item in allowed_indicator_ids))
    required = list(dict.fromkeys(str(item) for item in required_indicator_ids))
    detected = detect_narrated_indicator_ids(text)
    for indicator_id in detected:
        if indicator_id not in allowed:
            raise IndicatorStyleError(
                f"NARRATION_INDICATOR_FORBIDDEN:{indicator_id}"
            )
    for indicator_id in required:
        if indicator_id not in detected:
            raise IndicatorStyleError(
                f"NARRATION_INDICATOR_REQUIRED:{indicator_id}"
            )
    return detected
