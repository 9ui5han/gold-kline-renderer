import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .indicator_engine import resolve_teaching_scene

CYAN, INK, MUTED = "#32C4EA", "#17212B", "#6B7785"
GRID, RED = "#D9E0E6", "#E99AA5"
BLUE_FILL, RED_FILL = "#D9F3FA", "#FBE3E6"


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
    draw.line(points, fill=CYAN, width=6, joint="curve")
    if mode == "overbought":
        target = min(points, key=lambda item: item[1])
        draw.ellipse((target[0] - 8, target[1] - 8, target[0] + 8, target[1] + 8), fill=RED)
        draw.text((target[0] - 80, max(top, target[1] - 38)), "高于70", fill=INK, font=_font(20, True))
    elif mode == "oversold":
        target = max(points, key=lambda item: item[1])
        draw.ellipse((target[0] - 8, target[1] - 8, target[0] + 8, target[1] + 8), fill=CYAN)
        draw.text((target[0] - 75, min(bottom - 28, target[1] + 12)), "低于30", fill=INK, font=_font(20, True))
    draw.rectangle((left, top, right, bottom), outline="#BFCAD4", width=2)


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


def _draw_realistic_candles(draw: ImageDraw.ImageDraw, candles: list[dict[str, float]],
                            box: tuple[int, int, int, int]) -> tuple[Any, Any]:
    x_for, y_for = _chart_transform(candles, box)
    step = (box[2] - box[0]) / len(candles)
    half_body = max(2.0, min(5.5, step * .34))
    for index, candle in enumerate(candles):
        x = x_for(index)
        color = CYAN if candle["close"] >= candle["open"] else "#5E6873"
        draw.line((x, y_for(candle["high"]), x, y_for(candle["low"])), fill=INK, width=1)
        body_top, body_bottom = sorted((y_for(candle["open"]), y_for(candle["close"])))
        draw.rectangle((x - half_body, body_top, x + half_body, max(body_top + 2, body_bottom)), fill=color, outline=INK, width=1)
    return x_for, y_for


def _draw_rsi_teaching_scene(draw: ImageDraw.ImageDraw, width: int, height: int,
                             scene: dict[str, Any]) -> None:
    candles = scene["ohlc"]
    signal = scene["signals"][0]
    if scene["scenario_id"] == "range_overview":
        panel_box = (72, 58, width - 34, height - 38)
        draw.text((72, 12), "RSI区间总览", fill=INK, font=_font(25, True))
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
            draw.text((22, y - 10), str(value), fill=INK, font=_font(15, True))
        points = []
        for index, value in enumerate(scene["indicator_values"]):
            if value is not None:
                x = panel_box[0] + (index + .5) * (panel_box[2] - panel_box[0]) / len(scene["indicator_values"])
                y = panel_box[3] - value / 100 * (panel_box[3] - panel_box[1])
                points.append((x, y))
        draw.line(points, fill="#138EB9", width=5, joint="curve")
        draw.text((width - 150, panel_box[1] + 8), "超买区", fill=INK, font=_font(14, True))
        draw.text((width - 150, panel_box[3] - 24), "超卖区", fill=INK, font=_font(14, True))
        draw.rectangle(panel_box, outline="#C8D2DB", width=1)
        return
    price_box = (58, 42, width - 28, 306)
    panel_box = (58, 350, width - 28, height - 32)
    heading = {
        "range_overview": "RSI与价格的关系",
        "overbought_reversal": "RSI超买转弱示例",
        "worked_example": "完整示例：指标信号到价格确认",
    }.get(scene["scenario_id"], "RSI超卖回升示例")
    draw.text((58, 5), heading, fill=INK, font=_font(23, True))
    x_for, price_y = _draw_realistic_candles(draw, candles, price_box)
    for value, color in ((70, RED), (50, GRID), (30, CYAN)):
        y = panel_box[3] - value / 100 * (panel_box[3] - panel_box[1])
        draw.line((panel_box[0], y, panel_box[2], y), fill=color, width=2)
        draw.text((18, y - 10), str(value), fill=INK, font=_font(15, True))
    if signal["threshold"] == 30:
        y30 = panel_box[3] - .30 * (panel_box[3] - panel_box[1])
        draw.rectangle((panel_box[0], y30, panel_box[2], panel_box[3]), fill=BLUE_FILL)
    else:
        y70 = panel_box[3] - .70 * (panel_box[3] - panel_box[1])
        draw.rectangle((panel_box[0], panel_box[1], panel_box[2], y70), fill=RED_FILL)
    points = []
    for index, value in enumerate(scene["indicator_values"]):
        if value is not None:
            y = panel_box[3] - value / 100 * (panel_box[3] - panel_box[1])
            points.append((x_for(index), y))
    draw.line(points, fill="#138EB9", width=4, joint="curve")
    labels = [] if scene["scenario_id"] == "range_overview" else [
        (signal["indicator_candle_index"], "指标条件出现", "#E99AA5"),
        (signal["cross_candle_index"], "RSI触发", CYAN),
        (signal["confirmation_candle_index"], "价格确认", "#D9A62E"),
    ]
    for position, (index, label, color) in enumerate(labels):
        x = x_for(index)
        draw.line((x, price_box[1], x, panel_box[3]), fill=color, width=2)
        candle = candles[index]
        py = price_y(candle["high"])
        draw.ellipse((x - 5, py - 5, x + 5, py + 5), fill=color)
        label_y = 52 + position * 25
        label_x = min(width - 220, max(65, x - 90))
        draw.rounded_rectangle((label_x, label_y, label_x + 185, label_y + 22), radius=8, fill=color)
        draw.text((label_x + 7, label_y + 3), label, fill=INK, font=_font(12, True))
    draw.text((58, 320), "RSI（14）— 与K线使用同一时间轴", fill=INK, font=_font(20, True))
    if scene["scenario_id"] == "range_overview":
        draw.text((width - 150, panel_box[1] + 8), "超买区", fill=INK, font=_font(14, True))
        draw.text((width - 150, panel_box[3] - 24), "超卖区", fill=INK, font=_font(14, True))
    draw.rectangle(price_box, outline="#C8D2DB", width=1)
    draw.rectangle(panel_box, outline="#C8D2DB", width=1)


def _numeric_lines(indicator_values: Any) -> list[tuple[str, list[float | None]]]:
    labels = {
        "value": "指标线", "fast": "快线", "slow": "慢线", "signal": "信号线",
        "histogram": "柱状差值", "middle": "中轨", "upper": "上轨", "lower": "下轨",
        "k": "K线", "d": "D线", "j": "J线", "atr": "ATR波动", "obv": "OBV能量潮",
    }
    if isinstance(indicator_values, list):
        return [("指标线", indicator_values)]
    if isinstance(indicator_values, dict):
        return [
            (labels.get(str(name).lower(), str(name).upper()), values)
            for name, values in indicator_values.items()
            if isinstance(values, list)
        ]
    return []


def _draw_generic_indicator_scene(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    scene: dict[str, Any],
) -> None:
    candles = scene["ohlc"]
    family = scene.get("indicator_family")
    scenario_names = {
        "overview": "指标总览", "state_a": "状态一", "state_b": "状态二",
        "components": "组成部分", "setup": "使用条件", "worked_example": "完整示例",
        "bullish_cross": "向上交叉", "bearish_cross": "向下交叉",
        "bullish_alignment": "多头排列", "bearish_alignment": "空头排列",
        "volatility_measure": "波动率总览", "volatility_expansion": "波动扩大",
        "volatility_contraction": "波动收缩", "three_bands": "三条轨道",
        "band_expansion": "轨道扩张", "band_contraction": "轨道收缩",
        "cumulative_volume": "累计成交量", "bullish_confirmation": "上涨确认",
        "bearish_confirmation": "下跌确认",
    }
    scenario_name = scenario_names.get(str(scene["scenario_id"]), str(scene["scenario_id"]).replace("_", " "))
    title = f"{str(scene['indicator_id']).replace('_', ' ').upper()}｜{scenario_name}"
    draw.text((52, 10), title[:58], fill=INK, font=_font(22, True))
    colors = ["#138EB9", "#D96B78", "#D9A62E", "#7256B8"]
    if family == "overlay":
        price_box = (52, 52, width - 28, height - 34)
        x_for, y_for = _draw_realistic_candles(draw, candles, price_box)
        for line_index, (name, values) in enumerate(_numeric_lines(scene.get("indicator_values"))):
            points = [
                (x_for(index), y_for(float(value)))
                for index, value in enumerate(values)
                if value is not None
            ]
            if len(points) > 1:
                draw.line(points, fill=colors[line_index % len(colors)], width=3, joint="curve")
            draw.text((58 + line_index * 150, height - 27), name[:16], fill=colors[line_index % len(colors)], font=_font(13, True))
        draw.rectangle(price_box, outline="#C8D2DB", width=1)
        return
    price_box = (52, 48, width - 28, 275)
    panel_box = (52, 320, width - 28, height - 34)
    x_for, _ = _draw_realistic_candles(draw, candles, price_box)
    lines = _numeric_lines(scene.get("indicator_values"))
    numeric = [float(value) for _, values in lines for value in values if value is not None]
    low, high = (min(numeric), max(numeric)) if numeric else (0.0, 1.0)
    padding = max((high - low) * .08, .1)
    low, high = low - padding, high + padding
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
            draw.line(points, fill=colors[line_index % len(colors)], width=3, joint="curve")
        draw.text((58 + line_index * 150, 292), name[:16], fill=colors[line_index % len(colors)], font=_font(13, True))
    draw.rectangle(price_box, outline="#C8D2DB", width=1)
    draw.rectangle(panel_box, outline="#C8D2DB", width=1)


def _draw_ict_teaching_scene(draw: ImageDraw.ImageDraw, width: int, height: int,
                             scene: dict[str, Any]) -> None:
    candles = scene["ohlc"]
    box = (50, 52, width - 30, height - 36)
    x_for, y_for = _draw_realistic_candles(draw, candles, box)
    signals = {item["signal_type"]: item for item in scene["signals"]}
    bearish = "bearish_order_block" in signals
    order_block = signals["bearish_order_block" if bearish else "bullish_order_block"]
    fvg = signals["fair_value_gap"]
    bos = signals["break_of_structure"]
    sweep = signals["liquidity_sweep"]

    def zone(item: dict[str, Any], fill: str, label: str) -> None:
        left = x_for(item["zone_start_index"]) - 5
        right = x_for(item["zone_end_index"]) + 8
        top, bottom = sorted((y_for(item["price_high"]), y_for(item["price_low"])))
        draw.rectangle((left, top, right, bottom), fill=fill, outline=INK, width=1)
        draw.text((left + 5, top + 4), label, fill=INK, font=_font(14, True))

    zone(order_block, "#FBE3E6" if bearish else "#D9F3FA", "看跌订单块" if bearish else "看涨订单块")
    zone(fvg, "#FCE6B8", "公允价值缺口")
    bos_y = y_for(bos["price"])
    draw.line((x_for(bos["reference_index"]), bos_y, x_for(bos["event_index"]) + 42, bos_y), fill="#D9A62E", width=3)
    draw.text((x_for(bos["event_index"]) + 10, bos_y - 24), "结构突破", fill=INK, font=_font(17, True))
    sweep_x = x_for(sweep["event_index"])
    sweep_y = y_for(candles[sweep["event_index"]]["high"] if bearish else candles[sweep["event_index"]]["low"])
    draw.ellipse((sweep_x - 7, sweep_y - 7, sweep_x + 7, sweep_y + 7), fill=RED)
    draw.text((max(55, sweep_x - 115), sweep_y + 12), "流动性扫损", fill=INK, font=_font(15, True))
    retest_x = x_for(order_block["retest_index"])
    retest_y = y_for(candles[order_block["retest_index"]]["low"])
    if bearish:
        retest_y = y_for(candles[order_block["retest_index"]]["high"])
        draw.line((retest_x, retest_y + 5, retest_x, retest_y + 55), fill=RED, width=4)
        draw.polygon([(retest_x - 7, retest_y + 10), (retest_x + 7, retest_y + 10), (retest_x, retest_y)], fill=RED)
        draw.text((retest_x - 45, retest_y + 60), "回测", fill=INK, font=_font(16, True))
    else:
        draw.line((retest_x, retest_y - 55, retest_x, retest_y - 5), fill=CYAN, width=4)
        draw.polygon([(retest_x - 7, retest_y - 10), (retest_x + 7, retest_y - 10), (retest_x, retest_y)], fill=CYAN)
        draw.text((retest_x - 45, retest_y - 80), "回测", fill=INK, font=_font(16, True))
    draw.text((50, 10), "ICT结构｜根据演示K线计算", fill=INK, font=_font(23, True))
    draw.rectangle(box, outline="#C8D2DB", width=1)


def _draw_price_rsi_example(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    draw.text((55, 10), "价格与RSI相互确认", fill=INK, font=_font(28, True))
    _draw_candles(draw, (55, 55, width - 45, 265))
    left, top, right, bottom = 75, 305, width - 45, height - 35
    y30 = bottom - (bottom - top) * .30
    draw.rectangle((left, y30, right, bottom), fill=BLUE_FILL)
    draw.line((left, y30, right, y30), fill=CYAN, width=3)
    points = _rsi_points(left, top, right, bottom, "oversold")
    draw.line(points, fill="#248DB2", width=5, joint="curve")
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


def render_chart(page: dict[str, Any], output_path: Path,
                 route_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    width, height = 900, 560
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    template = _template_key(page)
    scene = resolve_teaching_scene(page, route_payload)
    use_teaching_scene = str(page.get("visual_type") or "") in {
        "indicator_panel", "zone_diagram", "candlestick_demo", "market_chart",
    }
    if use_teaching_scene and scene["indicator_id"] == "ict":
        template = "ict_structure"
        _draw_ict_teaching_scene(draw, width, height, scene)
    elif use_teaching_scene and scene["indicator_id"] in {"rsi", "generic"}:
        template = f"rsi_{scene['scenario_id']}"
        _draw_rsi_teaching_scene(draw, width, height, scene)
    elif use_teaching_scene:
        template = f"{scene['indicator_id']}_{scene['scenario_id']}"
        _draw_generic_indicator_scene(draw, width, height, scene)
    elif template == "checklist":
        _draw_checklist(draw, width, height, list(page.get("required_elements") or []))
    else:
        _draw_steps(draw, width, height, list(page.get("required_elements") or []))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")
    return {
        "page_no": int(page["page_no"]), "asset_key": f"chart_page_{int(page['page_no']):02d}",
        "asset_type": str(page.get("visual_type") or ""), "asset_path": str(output_path),
        "is_teaching_demo": True, "included_elements": list(page.get("required_elements") or []),
        "template_key": template, "render_language": "zh-CN", "disclaimer_drawn": False,
        "teaching_engine_version": scene["engine_version"],
        "indicator_id": scene["indicator_id"], "engine_id": scene["engine_id"],
        "indicator_family": scene["indicator_family"],
        "scenario_id": scene["scenario_id"], "ohlc_count": len(scene["ohlc"]),
        "signal_anchors": scene["signals"], "visual_layers": scene["layers"],
        "signal_contract_valid": scene["signal_contract_valid"],
        "data_fingerprint": scene["data_fingerprint"],
    }
