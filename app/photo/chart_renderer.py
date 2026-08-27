import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageColor, ImageDraw, ImageFont

from .indicator_engine import resolve_teaching_scene

CYAN, INK, MUTED = "#32C4EA", "#17212B", "#6B7785"
GRID, RED = "#D9E0E6", "#E99AA5"
BLUE_FILL, RED_FILL = "#D9F3FA", "#FBE3E6"
MONTSERRAT_PATH = (
    Path(__file__).resolve().parents[2]
    / "assets" / "photo" / "fonts" / "montserrat"
    / "Montserrat-VariableFont_wght.ttf"
)
LINE_RENDERER = "supersampled_catmull_rom"
LINE_SUPERSAMPLE = 4
PLOT_LEFT = 0
PLOT_RIGHT_SAFETY = 0
LABEL_LEFT = 44
LABEL_RIGHT_SAFETY = 12
# Baseline was .60; .40 is exactly one-third narrower while leaving enough
# horizontal breathing room for a denser, full-width candle sequence.
CANDLE_BODY_WIDTH = 10.0
ANNOTATION_ALPHA = 150

SEMANTIC_LABELS: dict[str, dict[str, str]] = {
    "zh-CN": {
        "rsi_range_overview": "RSI区间总览",
        "rsi_overbought_reversal": "RSI超买转弱示例",
        "rsi_oversold_recovery": "RSI超卖回升示例",
        "rsi_worked_example": "完整示例：指标信号到价格确认",
        "indicator_condition": "指标条件出现",
        "rsi_trigger": "RSI触发",
        "price_confirmation": "价格确认",
        "rsi_caption": "RSI（14）— 与价格K线使用同一时间轴",
        "overbought_zone": "超买区",
        "oversold_zone": "超卖区",
        "indicator_overview": "指标总览",
        "state_a": "状态一",
        "state_b": "状态二",
        "components": "组成部分",
        "setup": "使用条件",
        "worked_example": "完整示例",
        "bullish_cross": "向上交叉",
        "bearish_cross": "向下交叉",
        "bullish_alignment": "多头排列",
        "bearish_alignment": "空头排列",
        "volatility_measure": "波动率总览",
        "volatility_expansion": "波动扩大",
        "volatility_contraction": "波动收缩",
        "three_bands": "三条轨道",
        "band_expansion": "轨道扩张",
        "band_contraction": "轨道收缩",
        "cumulative_volume": "累计成交量",
        "bullish_confirmation": "上涨确认",
        "bearish_confirmation": "下跌确认",
        "range_overview": "区间总览",
        "three_lines": "三线组成",
        "cross_with_price_confirmation": "交叉与价格确认",
        "true_range_and_average": "真实波幅与平均值",
        "risk_distance_context": "风险距离参考",
        "upper_middle_lower": "上中下轨组成",
        "touch_and_confirmation": "触轨与价格确认",
        "line_and_price": "均线与价格",
        "fast_and_slow_lines": "快慢均线",
        "cross_and_retest": "交叉与回测",
        "price_and_obv": "价格与OBV",
        "divergence_check": "背离检查",
        "indicator_line": "指标线",
        "fast_line": "快线",
        "slow_line": "慢线",
        "signal_line": "信号线",
        "histogram": "柱状差值",
        "middle_band": "中轨",
        "upper_band": "上轨",
        "lower_band": "下轨",
        "k_stochastic": "K随机线",
        "d_stochastic": "D随机线",
        "j_stochastic": "J随机线",
        "atr": "ATR波动",
        "obv": "OBV能量潮",
        "ict_title": "ICT结构｜根据演示K线计算",
        "bearish_order_block": "看跌订单块",
        "bullish_order_block": "看涨订单块",
        "fair_value_gap": "公允价值缺口",
        "break_of_structure": "结构突破",
        "liquidity_sweep": "流动性扫损",
        "retest": "回测",
        "indicator_rsi": "RSI",
        "indicator_kdj": "KDJ",
        "indicator_macd": "MACD",
        "indicator_bollinger": "布林带",
        "indicator_moving_average": "移动平均线",
        "indicator_atr": "ATR",
        "indicator_obv": "OBV",
        "indicator_ict": "ICT结构",
    },
    "en": {
        "rsi_range_overview": "RSI range overview",
        "rsi_overbought_reversal": "RSI overbought reversal",
        "rsi_oversold_recovery": "RSI oversold recovery",
        "rsi_worked_example": "Worked example: signal to price confirmation",
        "indicator_condition": "Indicator condition",
        "rsi_trigger": "RSI trigger",
        "price_confirmation": "Price confirmation",
        "rsi_caption": "RSI (14) — shares the price time axis",
        "overbought_zone": "Overbought zone",
        "oversold_zone": "Oversold zone",
        "indicator_overview": "Indicator overview",
        "state_a": "State A",
        "state_b": "State B",
        "components": "Components",
        "setup": "Setup",
        "worked_example": "Worked example",
        "bullish_cross": "Bullish crossover",
        "bearish_cross": "Bearish crossover",
        "bullish_alignment": "Bullish alignment",
        "bearish_alignment": "Bearish alignment",
        "volatility_measure": "Volatility overview",
        "volatility_expansion": "Volatility expansion",
        "volatility_contraction": "Volatility contraction",
        "three_bands": "Three bands",
        "band_expansion": "Band expansion",
        "band_contraction": "Band contraction",
        "cumulative_volume": "Cumulative volume",
        "bullish_confirmation": "Bullish confirmation",
        "bearish_confirmation": "Bearish confirmation",
        "range_overview": "Range overview",
        "three_lines": "Three-line structure",
        "cross_with_price_confirmation": "Crossover with price confirmation",
        "true_range_and_average": "True range and its average",
        "risk_distance_context": "Risk-distance context",
        "upper_middle_lower": "Upper, middle, and lower bands",
        "touch_and_confirmation": "Band touch and price confirmation",
        "line_and_price": "Average line and price",
        "fast_and_slow_lines": "Fast and slow averages",
        "cross_and_retest": "Crossover and retest",
        "price_and_obv": "Price and on-balance volume",
        "divergence_check": "Divergence check",
        "indicator_line": "Indicator line",
        "fast_line": "Fast line",
        "slow_line": "Slow line",
        "signal_line": "Signal line",
        "histogram": "Histogram",
        "middle_band": "Middle band",
        "upper_band": "Upper band",
        "lower_band": "Lower band",
        "k_stochastic": "K stochastic",
        "d_stochastic": "D stochastic",
        "j_stochastic": "J stochastic",
        "atr": "ATR volatility",
        "obv": "On-balance volume",
        "ict_title": "ICT structure from demonstration price candles",
        "bearish_order_block": "Bearish order block",
        "bullish_order_block": "Bullish order block",
        "fair_value_gap": "Fair value gap",
        "break_of_structure": "Break of structure",
        "liquidity_sweep": "Liquidity sweep",
        "retest": "Retest",
        "indicator_rsi": "RSI",
        "indicator_kdj": "KDJ",
        "indicator_macd": "MACD",
        "indicator_bollinger": "Bollinger Bands",
        "indicator_moving_average": "Moving Average",
        "indicator_atr": "ATR",
        "indicator_obv": "OBV",
        "indicator_ict": "ICT Structure",
    },
}


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc" if bold else "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _english_font(size: int, weight: int = 400) -> ImageFont.ImageFont:
    """Load the bundled commercial-safe Montserrat variable font."""
    try:
        font = ImageFont.truetype(str(MONTSERRAT_PATH), size=size)
        if hasattr(font, "set_variation_by_axes"):
            font.set_variation_by_axes([float(weight)])
        return font
    except (OSError, ValueError):
        return _font(size, bold=weight >= 600)


def _is_english(language: str) -> bool:
    return str(language or "").lower().startswith("en")


def _label(key: str, language: str) -> str:
    catalog = SEMANTIC_LABELS["en" if _is_english(language) else "zh-CN"]
    return catalog.get(key, key.replace("_", " "))


def _indicator_name(indicator_id: str, language: str) -> str:
    return _label(f"indicator_{str(indicator_id).lower()}", language)


def _plot_box(width: int, top: int, bottom: int) -> tuple[int, int, int, int]:
    return (PLOT_LEFT, top, width - PLOT_RIGHT_SAFETY, bottom)


def _language_font(language: str, size: int, bold: bool = False) -> ImageFont.ImageFont:
    return _english_font(size, 650 if bold else 450) if _is_english(language) else _font(size, bold)


def _boxes_overlap(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> bool:
    return not (
        left[2] <= right[0] or right[2] <= left[0]
        or left[3] <= right[1] or right[3] <= left[1]
    )


class _ChartLayout:
    def __init__(self, width: int, candle_count: int) -> None:
        self.width = width
        self.plot_left = PLOT_LEFT
        self.plot_right = width - PLOT_RIGHT_SAFETY
        self.label_left = LABEL_LEFT
        self.label_right = width - LABEL_RIGHT_SAFETY
        self.candle_pitch = (
            (self.plot_right - self.plot_left) / candle_count if candle_count else 0.0
        )
        self.bounds: dict[str, list[dict[str, Any]]] = {
            "title": [], "legend": [], "y_axis": [], "annotation": [], "caption": [],
        }
        self.rendered_labels: list[str] = []

    def add_label(
        self,
        kind: str,
        text: str,
        box: tuple[int, int, int, int],
        **metadata: Any,
    ) -> dict[str, Any]:
        item = {"text": text, "bounds": list(box), **metadata}
        self.bounds[kind].append(item)
        self.rendered_labels.append(text)
        return item

    def occupied(self) -> list[tuple[int, int, int, int]]:
        return [
            tuple(item["bounds"])
            for items in self.bounds.values()
            for item in items
        ]

    def overlap_pairs(self) -> list[dict[str, Any]]:
        flattened = [
            (kind, item)
            for kind, items in self.bounds.items()
            for item in items
        ]
        collisions = []
        for index, (kind, item) in enumerate(flattened):
            for other_kind, other in flattened[index + 1:]:
                if _boxes_overlap(tuple(item["bounds"]), tuple(other["bounds"])):
                    collisions.append({
                        "first": kind, "first_text": item["text"],
                        "second": other_kind, "second_text": other["text"],
                    })
        return collisions

    def metadata(self, price_edges: tuple[int, int], indicator_edges: tuple[int, int]) -> dict[str, Any]:
        collisions = self.overlap_pairs()
        return {
            "plot_left": self.plot_left,
            "plot_right": self.plot_right,
            "plot_edges": [self.plot_left, self.plot_right],
            "label_left": self.label_left,
            "label_right": self.label_right,
            "candle_pitch": self.candle_pitch,
            "candle_body_width": CANDLE_BODY_WIDTH,
            "candle_body_width_ratio": (
                CANDLE_BODY_WIDTH / self.candle_pitch if self.candle_pitch else 0.0
            ),
            "left_plot_border": False,
            "right_plot_border": False,
            "price_plot_edges": list(price_edges),
            "indicator_plot_edges": list(indicator_edges),
            "title_bounds": self.bounds["title"],
            "legend_bounds": self.bounds["legend"],
            "y_axis_label_bounds": self.bounds["y_axis"],
            "annotation_bounds": self.bounds["annotation"],
            "caption_bounds": self.bounds["caption"],
            "collisions": collisions,
            "label_overlap": bool(collisions),
        }


def _text_box(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str,
              font: ImageFont.ImageFont) -> tuple[int, int, int, int]:
    box = draw.textbbox(xy, text, font=font)
    return tuple(int(round(value)) for value in box)


def _draw_tracked_text(
    draw: ImageDraw.ImageDraw,
    layout: _ChartLayout,
    kind: str,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    fill: str = INK,
) -> None:
    x, y = xy
    box = _text_box(draw, (x, y), text, font)
    if box[0] < layout.label_left:
        x += layout.label_left - box[0]
        box = _text_box(draw, (x, y), text, font)
    if box[2] > layout.label_right:
        x -= box[2] - layout.label_right
        box = _text_box(draw, (x, y), text, font)
    if box[0] < layout.label_left or box[2] > layout.label_right:
        raise ValueError("CHART_LABEL_LAYOUT_OVERFLOW")
    draw.text((x, y), text, fill=fill, font=font)
    layout.add_label(kind, text, box)


def _catmull_rom_points(points: list[tuple[float, float]], samples: int = 8) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    padded = [points[0], *points, points[-1]]
    result: list[tuple[float, float]] = []
    for index in range(1, len(padded) - 2):
        p0, p1, p2, p3 = padded[index - 1:index + 3]
        for sample in range(samples):
            t = sample / samples
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            result.append((x, y))
    result.append(points[-1])
    return result


def _draw_smooth_line(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    fill: str,
    width: int,
) -> None:
    if len(points) < 2:
        return
    scale = LINE_SUPERSAMPLE
    base_image = draw._image
    smooth = _catmull_rom_points(points)
    layer = Image.new("RGBA", (base_image.width * scale, base_image.height * scale), (0, 0, 0, 0))
    layer_draw = ImageDraw.Draw(layer)
    layer_draw.line(
        [(x * scale, y * scale) for x, y in smooth],
        fill=fill,
        width=max(1, width * scale),
        joint="curve",
    )
    layer = layer.resize(base_image.size, Image.Resampling.LANCZOS)
    base_image.paste(layer, (0, 0), layer)


def _draw_annotation(
    draw: ImageDraw.ImageDraw,
    layout: _ChartLayout,
    anchor_x: float,
    preferred_y: int,
    text: str,
    color: str,
    language: str,
) -> dict[str, Any]:
    occupied = layout.occupied()
    chosen: tuple[int, int, int, int] | None = None
    chosen_font: ImageFont.ImageFont | None = None
    chosen_font_size = 12
    chosen_text_bbox: tuple[int, int, int, int] | None = None
    chosen_box_size = (0, 0)
    candidate_y = [
        preferred_y, preferred_y + 28, preferred_y - 28,
        preferred_y + 56, preferred_y - 56,
        preferred_y + 84, preferred_y - 84,
    ]
    for font_size in (12, 10):
        font = _language_font(language, font_size, True)
        text_bbox = draw.textbbox((0, 0), text, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        box_width = text_width + 20
        box_height = text_height + 12
        horizontal_offsets = [
            0, -(box_width + 10), box_width + 10,
            -int(round(box_width * .55)), int(round(box_width * .55)),
        ]
        for top in candidate_y:
            for offset in horizontal_offsets:
                left = int(round(anchor_x + offset - box_width / 2))
                left = min(
                    layout.label_right - box_width,
                    max(layout.label_left, left),
                )
                box = (left, top, left + box_width, top + box_height)
                if top >= 0 and box[3] <= draw._image.height - 8 and not any(
                    _boxes_overlap(box, item) for item in occupied
                ):
                    chosen = box
                    chosen_font = font
                    chosen_font_size = font_size
                    chosen_text_bbox = text_bbox
                    chosen_box_size = (box_width, box_height)
                    break
            if chosen is not None:
                break
        if chosen is not None:
            break
    if chosen is None:
        raise ValueError("CHART_LABEL_LAYOUT_OVERLAP")
    assert chosen_font is not None and chosen_text_bbox is not None
    font = chosen_font
    text_bbox = chosen_text_bbox
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    box_width, box_height = chosen_box_size
    overlay = Image.new("RGBA", draw._image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    rgba = ImageColor.getrgb(color) + (ANNOTATION_ALPHA,)
    overlay_draw.rounded_rectangle(chosen, radius=8, fill=rgba)
    draw._image.paste(overlay, (0, 0), overlay)
    text_x = chosen[0] + (box_width - text_width) / 2 - text_bbox[0]
    text_y = chosen[1] + (box_height - text_height) / 2 - text_bbox[1]
    draw.text((text_x, text_y), text, fill=INK, font=font)
    actual_text_box = _text_box(draw, (text_x, text_y), text, font)
    box_center = ((chosen[0] + chosen[2]) / 2, (chosen[1] + chosen[3]) / 2)
    text_center = (
        (actual_text_box[0] + actual_text_box[2]) / 2,
        (actual_text_box[1] + actual_text_box[3]) / 2,
    )
    return layout.add_label(
        "annotation", text, chosen,
        text_bounds=list(actual_text_box),
        text_centered=(abs(box_center[0] - text_center[0]) <= 1 and abs(box_center[1] - text_center[1]) <= 1),
        background_alpha=ANNOTATION_ALPHA,
        font_size=chosen_font_size,
        horizontal_offset=round(box_center[0] - anchor_x, 2),
    )


def _rsi_points(left: int, top: int, right: int, bottom: int, mode: str) -> list[tuple[float, float]]:
    points = []
    for index in range(72):
        ratio = index / 71
        if mode == "overbought":
            value = 50 + 30 * math.sin(index / 11) + 10 * math.sin(index / 4.2)
        elif mode == "oversold":
            value = 48 - 30 * math.sin(index / 11) - 10 * math.sin(index / 4.2)
        else:
            value = 50 + 25 * math.sin(index / 7) + 7 * math.sin(index / 2.7)
        value = max(5, min(95, value))
        points.append((left + ratio * (right - left), bottom - value / 100 * (bottom - top)))
    return points


def _draw_rsi_panel(draw: ImageDraw.ImageDraw, width: int, height: int, mode: str) -> None:
    left, top, right, bottom = 92, 64, width - 42, height - 42
    if mode == "overbought":
        draw.rectangle((left, top, right, bottom - (bottom - top) * .70), fill=RED_FILL)
    elif mode == "oversold":
        draw.rectangle((left, bottom - (bottom - top) * .30, right, bottom), fill=BLUE_FILL)
    for value, color in ((100, GRID), (70, RED), (30, CYAN), (0, GRID)):
        y = bottom - (bottom - top) * value / 100
        draw.line((left, y, right, y), fill=color, width=3)
        draw.text((25, y - 14), str(value), fill=INK, font=_font(22, True))
    label = {"overbought": "超买区域", "oversold": "超卖区域"}.get(mode, "RSI区间（0—100）")
    draw.text((left, 15), label, fill=INK, font=_font(28, True))
    points = _rsi_points(left, top, right, bottom, mode)
    _draw_smooth_line(draw, points, fill=CYAN, width=6)
    if mode == "overbought":
        target = min(points, key=lambda item: item[1])
        draw.ellipse((target[0] - 8, target[1] - 8, target[0] + 8, target[1] + 8), fill=RED)
        draw.text((target[0] - 80, max(top, target[1] - 38)), "高于70", fill=INK, font=_font(20, True))
    elif mode == "oversold":
        target = max(points, key=lambda item: item[1])
        draw.ellipse((target[0] - 8, target[1] - 8, target[0] + 8, target[1] + 8), fill=CYAN)
        draw.text((target[0] - 75, min(bottom - 28, target[1] + 12)), "低于30", fill=INK, font=_font(20, True))


def _draw_candles(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = box
    values = [42, 48, 45, 55, 63, 58, 67, 61, 73, 78, 72, 84]
    step = (right - left) / len(values)
    for index, close in enumerate(values):
        open_value = values[index - 1] if index else 38
        high, low = max(open_value, close) + 7, min(open_value, close) - 7
        x = left + step * (index + 0.5)
        to_y = lambda value: bottom - value / 100 * (bottom - top)
        color = CYAN if close >= open_value else "#727B85"
        draw.line((x, to_y(high), x, to_y(low)), fill=INK, width=2)
        body_top, body_bottom = sorted((to_y(open_value), to_y(close)))
        draw.rectangle((x - 10, body_top, x + 10, max(body_top + 3, body_bottom)), fill=color, outline=INK)


def _chart_transform(candles: list[dict[str, float]], box: tuple[int, int, int, int]):
    left, top, right, bottom = box
    price_min = min(item["low"] for item in candles)
    price_max = max(item["high"] for item in candles)
    padding = max((price_max - price_min) * .08, .1)
    price_min -= padding
    price_max += padding

    def x_for(index: int) -> float:
        return left + (index + .5) * (right - left) / len(candles)

    def y_for(value: float) -> float:
        return bottom - (value - price_min) / (price_max - price_min) * (bottom - top)

    return x_for, y_for


def _candle_geometry(candle: dict[str, float], y_for) -> dict[str, float]:
    """Map the source OHLC values directly to their drawable geometry."""
    body_top, body_bottom = sorted((y_for(candle["open"]), y_for(candle["close"])))
    return {
        "body_top": body_top,
        "body_bottom": body_bottom,
        "wick_top": y_for(candle["high"]),
        "wick_bottom": y_for(candle["low"]),
    }


def _display_candles(candles: list[dict[str, float]]) -> list[dict[str, float]]:
    """Return continuous, internally valid OHLC candles without visual exaggeration."""
    display: list[dict[str, float]] = []
    for candle in candles:
        open_price = display[-1]["close"] if display else float(candle["open"])
        close_price = float(candle["close"])
        display.append({
            "open": open_price,
            "high": max(float(candle["high"]), open_price, close_price),
            "low": min(float(candle["low"]), open_price, close_price),
            "close": close_price,
        })
    return display


def _draw_realistic_candles(draw: ImageDraw.ImageDraw, candles: list[dict[str, float]],
                            box: tuple[int, int, int, int]) -> tuple[Any, Any]:
    display_candles = _display_candles(candles)
    x_for, y_for = _chart_transform(display_candles, box)
    step = (box[2] - box[0]) / len(candles)
    half_body = min(CANDLE_BODY_WIDTH / 2, step * .45)
    for index, candle in enumerate(display_candles):
        x = x_for(index)
        color = CYAN if candle["close"] >= candle["open"] else "#5E6873"
        geometry = _candle_geometry(candle, y_for)
        draw.line((x, geometry["wick_top"], x, geometry["wick_bottom"]), fill=INK, width=1)
        draw.rectangle(
            (x - half_body, geometry["body_top"], x + half_body, geometry["body_bottom"]),
            fill=color,
            outline=INK,
            width=1,
        )
    return x_for, y_for


def _draw_rsi_teaching_scene(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    scene: dict[str, Any],
    language: str,
    layout: _ChartLayout,
) -> tuple[tuple[int, int], tuple[int, int]]:
    candles = scene["ohlc"]
    signal = scene["signals"][0]
    if scene["scenario_id"] == "range_overview":
        panel_box = _plot_box(width, 58, height - 38)
        _draw_tracked_text(
            draw, layout, "title", (layout.label_left, 12),
            _label("rsi_range_overview", language),
            _language_font(language, 25, True),
        )
        for upper, lower, fill in (
            (100, 70, RED_FILL),
            (70, 30, "#F7FAFC"),
            (30, 0, BLUE_FILL),
        ):
            y_top = panel_box[3] - upper / 100 * (panel_box[3] - panel_box[1])
            y_bottom = panel_box[3] - lower / 100 * (panel_box[3] - panel_box[1])
            draw.rectangle((panel_box[0], y_top, panel_box[2], y_bottom), fill=fill)
        for value, color in ((100, GRID), (70, RED), (50, GRID), (30, CYAN), (0, GRID)):
            y = panel_box[3] - value / 100 * (panel_box[3] - panel_box[1])
            draw.line((panel_box[0], y, panel_box[2], y), fill=color, width=2)
            _draw_tracked_text(
                draw, layout, "y_axis", (8, y - 9), str(value),
                _language_font(language, 14, True),
            )
        points = []
        for index, value in enumerate(scene["indicator_values"]):
            if value is not None:
                x = panel_box[0] + (index + .5) * layout.candle_pitch
                y = panel_box[3] - value / 100 * (panel_box[3] - panel_box[1])
                points.append((x, y))
        _draw_smooth_line(draw, points, fill="#138EB9", width=5)
        zone_font = _language_font(language, 14, True)
        _draw_tracked_text(
            draw, layout, "legend", (width - 175, panel_box[1] + 8),
            _label("overbought_zone", language), zone_font,
        )
        _draw_tracked_text(
            draw, layout, "legend", (width - 175, panel_box[3] - 24),
            _label("oversold_zone", language), zone_font,
        )
        return (panel_box[0], panel_box[2]), (panel_box[0], panel_box[2])
    price_box = _plot_box(width, 42, 306)
    panel_box = _plot_box(width, 350, height - 32)
    heading_key = {
        "range_overview": "rsi_range_overview",
        "overbought_reversal": "rsi_overbought_reversal",
        "worked_example": "rsi_worked_example",
    }.get(scene["scenario_id"], "rsi_oversold_recovery")
    _draw_tracked_text(
        draw, layout, "title", (layout.label_left, 5),
        _label(heading_key, language), _language_font(language, 23, True),
    )
    x_for, price_y = _draw_realistic_candles(draw, candles, price_box)
    if signal["threshold"] == 30:
        y30 = panel_box[3] - .30 * (panel_box[3] - panel_box[1])
        draw.rectangle((panel_box[0], y30, panel_box[2], panel_box[3]), fill=BLUE_FILL)
    else:
        y70 = panel_box[3] - .70 * (panel_box[3] - panel_box[1])
        draw.rectangle((panel_box[0], panel_box[1], panel_box[2], y70), fill=RED_FILL)
    for value, color in ((70, RED), (50, GRID), (30, CYAN)):
        y = panel_box[3] - value / 100 * (panel_box[3] - panel_box[1])
        draw.line((panel_box[0], y, panel_box[2], y), fill=color, width=2)
        _draw_tracked_text(
            draw, layout, "y_axis", (9, y - 9), str(value),
            _language_font(language, 14, True),
        )
    points = []
    for index, value in enumerate(scene["indicator_values"]):
        if value is not None:
            y = panel_box[3] - value / 100 * (panel_box[3] - panel_box[1])
            points.append((x_for(index), y))
    _draw_smooth_line(draw, points, fill="#138EB9", width=4)
    labels = [] if scene["scenario_id"] == "range_overview" else [
        (signal["indicator_candle_index"], "indicator_condition", "#E99AA5"),
        (signal["cross_candle_index"], "rsi_trigger", CYAN),
        (signal["confirmation_candle_index"], "price_confirmation", "#D9A62E"),
    ]
    for index, label_key, color in labels:
        x = x_for(index)
        draw.line((x, price_box[1], x, panel_box[3]), fill=color, width=2)
        candle = candles[index]
        py = price_y(candle["high"])
        draw.ellipse((x - 5, py - 5, x + 5, py + 5), fill=color)
        _draw_annotation(
            draw, layout, x, 52, _label(label_key, language), color, language,
        )
    _draw_tracked_text(
        draw, layout, "caption", (layout.label_left, 320),
        _label("rsi_caption", language), _language_font(language, 19, True),
    )
    return (price_box[0], price_box[2]), (panel_box[0], panel_box[2])


def _numeric_lines(
    indicator_values: Any,
    language: str,
    indicator_id: str = "",
) -> list[tuple[str, list[float | None]]]:
    label_keys = {
        "value": "indicator_line", "main": "fast_line", "fast": "fast_line",
        "slow": "slow_line", "signal": "signal_line", "histogram": "histogram",
        "middle": "middle_band", "upper": "upper_band", "lower": "lower_band",
        "k": "k_stochastic", "d": "d_stochastic", "j": "j_stochastic",
        "atr": "atr", "obv": "obv",
    }
    if isinstance(indicator_values, list):
        list_label_key = indicator_id if indicator_id in {"atr", "obv"} else "indicator_line"
        return [(_label(list_label_key, language), indicator_values)]
    if isinstance(indicator_values, dict):
        lines = []
        for name, values in indicator_values.items():
            if not isinstance(values, list):
                continue
            normalized = str(name).lower()
            if normalized.startswith("line_") and normalized[5:].isdigit():
                period = normalized[5:]
                display = (
                    f"{period}-period average" if _is_english(language)
                    else f"{period}周期均线"
                )
            else:
                display = _label(label_keys.get(normalized, normalized), language)
            lines.append((display, values))
        return lines
    return []


def _draw_generic_indicator_scene(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    scene: dict[str, Any],
    language: str,
    layout: _ChartLayout,
) -> tuple[tuple[int, int], tuple[int, int]]:
    candles = scene["ohlc"]
    family = scene.get("indicator_family")
    scenario_key = "indicator_overview" if scene["scenario_id"] == "overview" else str(scene["scenario_id"])
    scenario_name = _label(scenario_key, language)
    separator = " — " if _is_english(language) else "｜"
    title = f"{_indicator_name(scene['indicator_id'], language)}{separator}{scenario_name}"
    _draw_tracked_text(
        draw, layout, "title", (layout.label_left, 10), title[:58],
        _language_font(language, 22, True),
    )
    colors = ["#138EB9", "#D96B78", "#D9A62E", "#7256B8"]
    if family == "overlay":
        price_box = _plot_box(width, 52, height - 34)
        x_for, y_for = _draw_realistic_candles(draw, candles, price_box)
        legend_x = layout.label_left
        for line_index, (name, values) in enumerate(_numeric_lines(
            scene.get("indicator_values"), language, str(scene["indicator_id"]),
        )):
            points = [
                (x_for(index), y_for(float(value)))
                for index, value in enumerate(values)
                if value is not None
            ]
            if len(points) > 1:
                _draw_smooth_line(draw, points, fill=colors[line_index % len(colors)], width=3)
            font = _language_font(language, 13, True)
            _draw_tracked_text(
                draw, layout, "legend", (legend_x, height - 27), name[:20], font,
                colors[line_index % len(colors)],
            )
            legend_x += max(115, _text_box(draw, (0, 0), name[:20], font)[2] + 24)
        price_values = [float(item[key]) for item in candles for key in ("high", "low")]
        for value, y in ((max(price_values), price_box[1]), (min(price_values), price_box[3] - 16)):
            _draw_tracked_text(
                draw, layout, "y_axis", (3, y), f"{value:.1f}",
                _language_font(language, 11, True),
            )
        return (price_box[0], price_box[2]), (price_box[0], price_box[2])
    price_box = _plot_box(width, 48, 275)
    panel_box = _plot_box(width, 320, height - 34)
    x_for, _ = _draw_realistic_candles(draw, candles, price_box)
    lines = _numeric_lines(
        scene.get("indicator_values"), language, str(scene["indicator_id"]),
    )
    numeric = [float(value) for _, values in lines for value in values if value is not None]
    low, high = (min(numeric), max(numeric)) if numeric else (0.0, 1.0)
    padding = max((high - low) * .08, .1)
    low, high = low - padding, high + padding
    legend_x = layout.label_left
    for line_index, (name, values) in enumerate(lines):
        points = [
            (
                x_for(index),
                panel_box[3] - (float(value) - low) / (high - low) * (panel_box[3] - panel_box[1]),
            )
            for index, value in enumerate(values)
            if value is not None
        ]
        if len(points) > 1:
            _draw_smooth_line(draw, points, fill=colors[line_index % len(colors)], width=3)
        font = _language_font(language, 13, True)
        _draw_tracked_text(
            draw, layout, "legend", (legend_x, 292), name[:20], font,
            colors[line_index % len(colors)],
        )
        legend_x += max(115, _text_box(draw, (0, 0), name[:20], font)[2] + 24)
    for value, y in ((high, panel_box[1]), (low, panel_box[3] - 14)):
        _draw_tracked_text(
            draw, layout, "y_axis", (2, y), f"{value:.1f}",
            _language_font(language, 10, True),
        )
    return (price_box[0], price_box[2]), (panel_box[0], panel_box[2])


def _draw_ict_teaching_scene(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    scene: dict[str, Any],
    language: str,
    layout: _ChartLayout,
) -> tuple[tuple[int, int], tuple[int, int]]:
    candles = scene["ohlc"]
    box = _plot_box(width, 52, height - 36)
    x_for, y_for = _draw_realistic_candles(draw, candles, box)
    signals = {item["signal_type"]: item for item in scene["signals"]}
    bearish = "bearish_order_block" in signals
    order_block = signals["bearish_order_block" if bearish else "bullish_order_block"]
    fvg = signals["fair_value_gap"]
    bos = signals["break_of_structure"]
    sweep = signals["liquidity_sweep"]

    def zone(item: dict[str, Any], fill: str, label_key: str) -> tuple[float, float]:
        left = x_for(item["zone_start_index"]) - 5
        right = x_for(item["zone_end_index"]) + 8
        top, bottom = sorted((y_for(item["price_high"]), y_for(item["price_low"])))
        draw.rectangle((left, top, right, bottom), fill=fill, outline=INK, width=1)
        _draw_annotation(
            draw, layout, (left + right) / 2, max(55, int(top) + 4),
            _label(label_key, language), fill, language,
        )
        return ((left + right) / 2, (top + bottom) / 2)

    order_center = zone(
        order_block, "#FBE3E6" if bearish else "#D9F3FA",
        "bearish_order_block" if bearish else "bullish_order_block",
    )
    zone(fvg, "#FCE6B8", "fair_value_gap")
    bos_y = y_for(bos["price"])
    draw.line((x_for(bos["reference_index"]), bos_y, x_for(bos["event_index"]) + 42, bos_y), fill="#D9A62E", width=3)
    _draw_annotation(
        draw, layout, x_for(bos["event_index"]), max(55, int(bos_y) - 30),
        _label("break_of_structure", language), "#D9A62E", language,
    )
    sweep_x = x_for(sweep["event_index"])
    sweep_y = y_for(candles[sweep["event_index"]]["high"] if bearish else candles[sweep["event_index"]]["low"])
    draw.ellipse((sweep_x - 7, sweep_y - 7, sweep_x + 7, sweep_y + 7), fill=RED)
    _draw_annotation(
        draw, layout, sweep_x, min(height - 60, int(sweep_y) + 12),
        _label("liquidity_sweep", language), RED, language,
    )
    retest_x = x_for(order_block["retest_index"])
    retest_y = y_for(candles[order_block["retest_index"]]["low"])
    if bearish:
        retest_y = y_for(candles[order_block["retest_index"]]["high"])
        draw.line((retest_x, retest_y + 5, retest_x, retest_y + 55), fill=RED, width=4)
        draw.polygon([(retest_x - 7, retest_y + 10), (retest_x + 7, retest_y + 10), (retest_x, retest_y)], fill=RED)
        label_y = min(height - 48, int(retest_y) + 60)
    else:
        draw.line((retest_x, retest_y - 55, retest_x, retest_y - 5), fill=CYAN, width=4)
        draw.polygon([(retest_x - 7, retest_y - 10), (retest_x + 7, retest_y - 10), (retest_x, retest_y)], fill=CYAN)
        label_y = max(55, int(retest_y) - 80)
    _draw_smooth_line(
        draw,
        [(sweep_x, sweep_y), order_center, (retest_x, retest_y)],
        fill="#8D98A4",
        width=2,
    )
    _draw_annotation(
        draw, layout, retest_x, label_y, _label("retest", language),
        RED if bearish else CYAN, language,
    )
    _draw_tracked_text(
        draw, layout, "title", (layout.label_left, 10),
        _label("ict_title", language), _language_font(language, 23, True),
    )
    return (box[0], box[2]), (box[0], box[2])


def _draw_price_rsi_example(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    draw.text((55, 10), "价格与RSI相互确认", fill=INK, font=_font(28, True))
    _draw_candles(draw, (55, 55, width - 45, 265))
    left, top, right, bottom = 75, 305, width - 45, height - 35
    y30 = bottom - (bottom - top) * .30
    draw.rectangle((left, y30, right, bottom), fill=BLUE_FILL)
    draw.line((left, y30, right, y30), fill=CYAN, width=3)
    points = _rsi_points(left, top, right, bottom, "oversold")
    _draw_smooth_line(draw, points, fill="#248DB2", width=5)
    low = max(points, key=lambda item: item[1])
    draw.ellipse((low[0] - 7, low[1] - 7, low[0] + 7, low[1] + 7), fill=CYAN)
    draw.text((20, y30 - 12), "30", fill=INK, font=_font(20, True))
    draw.text((left, top - 28), "RSI", fill=INK, font=_font(22, True))


def _draw_checklist(draw: ImageDraw.ImageDraw, width: int, height: int, elements: list[Any]) -> None:
    draw.text((55, 18), "指标使用检查清单", fill=INK, font=_font(30, True))
    defaults = ["确认更大级别趋势", "检查价格结构", "等待价格确认", "做好风险管理"]
    source_items = [str(item).strip() for item in elements if str(item).strip()]
    items = (source_items + defaults)[:4]
    y = 88
    for item in items[:4]:
        draw.rounded_rectangle((55, y, width - 55, y + 68), radius=16, fill="#F3F7FA", outline="#DCE5EB", width=2)
        draw.ellipse((78, y + 18, 110, y + 50), fill=CYAN)
        draw.line((87, y + 34, 95, y + 42, 104, y + 25), fill="white", width=4)
        draw.text((132, y + 16), item[:42], fill=INK, font=_font(25))
        y += 82


def _draw_steps(draw: ImageDraw.ImageDraw, width: int, height: int, elements: list[Any]) -> None:
    defaults = ["识别指标条件", "检查市场背景", "等待价格确认"]
    source_items = [str(item).strip() for item in elements if str(item).strip()]
    items = (source_items + defaults)[:3]
    for index in range(3):
        x = 55 + index * 280
        draw.ellipse((x, 120, x + 76, 196), fill=CYAN)
        draw.text((x + 27, 136), str(index + 1), fill="white", font=_font(28, True))
        text = items[index]
        draw.text((x - 5, 225), text[:20], fill=INK, font=_font(22, True))
        if index < 2:
            draw.line((x + 90, 158, x + 250, 158), fill="#AAB7C2", width=4)


def _template_key(page: dict[str, Any]) -> str:
    visual_type = str(page.get("visual_type") or "")
    combined = f"{page.get('visual_focus', '')} {' '.join(str(x) for x in page.get('required_elements') or [])}".lower()
    if visual_type == "indicator_panel":
        return "rsi_panel"
    if visual_type == "zone_diagram":
        return "rsi_oversold" if any(word in combined for word in ("oversold", "below 30", "low rsi")) else "rsi_overbought"
    if visual_type == "checklist":
        return "checklist"
    if visual_type in {"candlestick_demo", "market_chart"}:
        return "price_rsi_example"
    if visual_type in {"comparison", "step_diagram", "flow_diagram"}:
        return "steps"
    return "rsi_panel"


def render_chart(
    page: dict[str, Any],
    output_path: Path,
    route_payload: dict[str, Any] | None = None,
    language: str = "zh-CN",
) -> dict[str, Any]:
    width, height = 900, 560
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    template = _template_key(page)
    scene = resolve_teaching_scene(page, route_payload)
    layout = _ChartLayout(width, len(scene["ohlc"]))
    price_edges = (layout.plot_left, layout.plot_right)
    indicator_edges = price_edges
    use_teaching_scene = str(page.get("visual_type") or "") in {
        "indicator_panel", "zone_diagram", "candlestick_demo", "market_chart",
    }
    if use_teaching_scene and scene["indicator_id"] == "ict":
        template = "ict_structure"
        price_edges, indicator_edges = _draw_ict_teaching_scene(
            draw, width, height, scene, language, layout,
        )
    elif use_teaching_scene and scene["indicator_id"] in {"rsi", "generic"}:
        template = f"rsi_{scene['scenario_id']}"
        price_edges, indicator_edges = _draw_rsi_teaching_scene(
            draw, width, height, scene, language, layout,
        )
    elif use_teaching_scene:
        template = f"{scene['indicator_id']}_{scene['scenario_id']}"
        price_edges, indicator_edges = _draw_generic_indicator_scene(
            draw, width, height, scene, language, layout,
        )
    elif template == "checklist":
        _draw_checklist(draw, width, height, list(page.get("required_elements") or []))
    else:
        _draw_steps(draw, width, height, list(page.get("required_elements") or []))
    layout_metadata = layout.metadata(price_edges, indicator_edges)
    if layout_metadata["label_overlap"]:
        raise ValueError("CHART_LABEL_LAYOUT_OVERLAP")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")
    return {
        "page_no": int(page["page_no"]), "asset_key": f"chart_page_{int(page['page_no']):02d}",
        "asset_type": str(page.get("visual_type") or ""), "asset_path": str(output_path),
        "is_teaching_demo": True, "included_elements": list(page.get("required_elements") or []),
        "template_key": template, "render_language": language, "disclaimer_drawn": False,
        "teaching_engine_version": scene["engine_version"],
        "indicator_id": scene["indicator_id"], "engine_id": scene["engine_id"],
        "indicator_family": scene["indicator_family"],
        "scenario_id": scene["scenario_id"], "ohlc_count": len(scene["ohlc"]),
        "signal_anchors": scene["signals"], "visual_layers": scene["layers"],
        "signal_contract_valid": scene["signal_contract_valid"],
        "data_fingerprint": scene["data_fingerprint"],
        "line_renderer": LINE_RENDERER,
        "line_supersample": LINE_SUPERSAMPLE,
        "left_plot_border": False,
        "right_plot_border": False,
        "label_overlap": layout_metadata["label_overlap"],
        "annotation_bounds": layout_metadata["annotation_bounds"],
        "collision_metadata": {
            key: layout_metadata[key]
            for key in (
                "title_bounds", "legend_bounds", "y_axis_label_bounds",
                "annotation_bounds", "caption_bounds", "collisions",
            )
        },
        "rendered_labels": layout.rendered_labels,
        "chart_layout": layout_metadata,
    }
