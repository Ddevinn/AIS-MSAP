from __future__ import annotations

from datetime import datetime
from pathlib import Path
import uuid

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from backend.app.config import EXPORT_DIR
from backend.app.context import dataset_context
from backend.app.services.analysis import (
    length_distribution,
    nav_state_distribution,
    ship_type_distribution,
    speed_histogram,
    summary,
    time_series,
)
from backend.app.services.query import QueryFilters
from backend.app.services.directions import direction_histograms

_HEADER_FILL = PatternFill("solid", fgColor="1E3A5F")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_TITLE_FONT = Font(bold=True, size=13)

_SHIP_TYPE_NAMES: dict[int, str] = {
    20: "翼地效应船", 21: "翼地效应船", 22: "气垫船", 23: "气垫船",
    24: "气垫船", 25: "气垫船", 26: "气垫船", 27: "气垫船",
    28: "气垫船", 29: "气垫船", 30: "渔船", 31: "拖船",
    32: "拖船", 33: "疏浚船", 34: "潜水作业船", 35: "军用船",
    36: "帆船", 37: "游艇", 40: "高速船", 41: "高速船",
    42: "高速船", 43: "高速船", 44: "高速船", 45: "高速船",
    46: "高速船", 47: "高速船", 48: "高速船", 49: "高速船",
    50: "引航船", 51: "搜救船", 52: "拖轮", 53: "港口押运船",
    54: "防污染船", 55: "执法船", 56: "地方船", 57: "地方船",
    58: "医疗船", 59: "非战斗船", 60: "客船", 61: "客船",
    62: "客船", 63: "客船", 64: "客船", 65: "客船",
    66: "客船", 67: "客船", 68: "客船", 69: "客船",
    70: "货船", 71: "货船", 72: "货船", 73: "货船",
    74: "货船", 75: "货船", 76: "货船", 77: "货船",
    78: "货船", 79: "货船", 80: "油轮", 81: "油轮",
    82: "油轮", 83: "油轮", 84: "油轮", 85: "油轮",
    86: "油轮", 87: "油轮", 88: "油轮", 89: "油轮",
    90: "其他船舶", 91: "其他船舶", 92: "其他船舶",
    93: "其他船舶", 94: "其他船舶", 95: "其他船舶",
    96: "其他船舶", 97: "其他船舶", 98: "其他船舶", 99: "其他船舶",
}


def _ship_type_name(code: int) -> str:
    if code == -1:
        return "未知类型"
    return _SHIP_TYPE_NAMES.get(code, f"类型{code}")


def _write_header(ws, row: int, cols: list[str]) -> None:
    for c, title in enumerate(cols, 1):
        cell = ws.cell(row=row, column=c, value=title)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center")


def _autofit(ws) -> None:
    for col_cells in ws.columns:
        max_len = max(
            (len(str(cell.value)) if cell.value is not None else 0 for cell in col_cells),
            default=8,
        )
        ws.column_dimensions[get_column_letter(col_cells[0].column)].width = min(max_len + 2, 36)


def export_excel(filters: QueryFilters | None = None, direction_bin_width: int = 15) -> Path:
    context = dataset_context.get()
    directory = EXPORT_DIR / f"user_{context.owner_id}" if context else EXPORT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"ais_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.xlsx"

    wb = Workbook()

    # ── 1. 总览 ─────────────────────────────────────────────
    ws = wb.active
    ws.title = "总览"
    ws["A1"] = "AIS 综合统计报表"
    ws["A1"].font = _TITLE_FONT
    _write_header(ws, 2, ["统计项", "数值"])
    label_map = {
        "point_count": "数据点/截面穿越次数",
        "vessel_count": "独立船舶数",
        "trajectory_count": "独立轨迹数",
        "timestamp_min": "最早时间",
        "timestamp_max": "最晚时间",
        "speed_avg": "平均航速 (kn)",
        "speed_min": "最低航速 (kn)",
        "speed_max": "最高航速 (kn)",
        "lon_min": "最小经度",
        "lon_max": "最大经度",
        "lat_min": "最小纬度",
        "lat_max": "最大纬度",
        "spatial_mode": "空间筛选模式",
        "crossing_count": "截面穿越次数",
        "forward_crossings": "正向穿越次数",
        "reverse_crossings": "反向穿越次数",
    }
    r = 3
    for key, value in summary(filters).items():
        if key in ("manifest",):
            continue
        ws.cell(r, 1, label_map.get(key, key))
        ws.cell(r, 2, value)
        r += 1
    _autofit(ws)

    # ── 2. 航速分布 ──────────────────────────────────────────
    ws2 = wb.create_sheet("航速分布")
    ws2["A1"] = "航速分布直方图"
    ws2["A1"].font = _TITLE_FONT
    _write_header(ws2, 2, ["区间起点 (kn)", "区间终点 (kn)", "数据点数", "占比 (%)"])
    hist = speed_histogram(filters=filters)
    bins, counts = hist["bins"], hist["counts"]
    total_pts = sum(counts) or 1
    for i, (lo, hi, cnt) in enumerate(zip(bins[:-1], bins[1:], counts), 3):
        ws2.cell(i, 1, round(lo, 2))
        ws2.cell(i, 2, round(hi, 2))
        ws2.cell(i, 3, cnt)
        ws2.cell(i, 4, round(cnt / total_pts * 100, 2))
    _autofit(ws2)

    # ── 3. 船型结构 ──────────────────────────────────────────
    ws3 = wb.create_sheet("船型结构")
    ws3["A1"] = "船舶类型分布"
    ws3["A1"].font = _TITLE_FONT
    _write_header(ws3, 2, ["船型代码", "船型名称", "数据点数", "点数占比 (%)", "船舶数"])
    types = ship_type_distribution(filters=filters)
    total_type_pts = sum(t["point_count"] for t in types) or 1
    for i, row in enumerate(types, 3):
        code = row["ship_type"]
        ws3.cell(i, 1, code)
        ws3.cell(i, 2, _ship_type_name(code))
        ws3.cell(i, 3, row["point_count"])
        ws3.cell(i, 4, round(row["point_count"] / total_type_pts * 100, 2))
        ws3.cell(i, 5, row["vessel_count"])
    _autofit(ws3)

    # ── 4. 船长分布 ──────────────────────────────────────────
    ws4 = wb.create_sheet("船长分布")
    ws4["A1"] = "船长分布直方图（按独立船舶统计）"
    ws4["A1"].font = _TITLE_FONT
    _write_header(ws4, 2, ["区间起点 (m)", "区间终点 (m)", "船舶数", "占比 (%)"])
    ldist = length_distribution(filters=filters)
    lbins, lcounts = ldist["bins"], ldist["counts"]
    total_vessels = sum(lcounts) or 1
    for i, (lo, hi, cnt) in enumerate(zip(lbins[:-1], lbins[1:], lcounts), 3):
        ws4.cell(i, 1, round(lo, 1))
        ws4.cell(i, 2, round(hi, 1))
        ws4.cell(i, 3, cnt)
        ws4.cell(i, 4, round(cnt / total_vessels * 100, 2))
    _autofit(ws4)

    # ── 5. 交通流时序 ─────────────────────────────────────────
    ws5 = wb.create_sheet("交通流时序")
    ws5["A1"] = "交通流时间序列（按日）"
    ws5["A1"].font = _TITLE_FONT
    _write_header(ws5, 2, ["日期", "数据点数", "船舶数", "平均航速 (kn)"])
    for i, row in enumerate(time_series(grain="day", filters=filters), 3):
        ws5.cell(i, 1, row["bucket"])
        ws5.cell(i, 2, row["point_count"])
        ws5.cell(i, 3, row["vessel_count"])
        ws5.cell(i, 4, round(row["avg_speed"], 2) if row["avg_speed"] is not None else "")
    _autofit(ws5)

    # ── 6. 航行状态 ──────────────────────────────────────────
    ws6 = wb.create_sheet("航行状态")
    ws6["A1"] = "航行状态分布"
    ws6["A1"].font = _TITLE_FONT
    _write_header(ws6, 2, ["状态", "数据点数", "占比 (%)"])
    for i, row in enumerate(nav_state_distribution(filters=filters), 3):
        ws6.cell(i, 1, row["label"])
        ws6.cell(i, 2, row["point_count"])
        ws6.cell(i, 3, round(row["percentage"], 2))
    _autofit(ws6)

    directions = direction_histograms(bin_width=direction_bin_width, filters=filters)
    overview = wb.create_sheet("航向统计")
    overview["A1"] = "航向有效性与圆周统计"
    overview["A1"].font = _TITLE_FONT
    _write_header(overview, 2, ["指标", "对地航向 (COG)", "船首向 (heading)"])
    metrics = [
        ("字段是否提供", lambda item: "是" if item["available"] else "未提供"),
        ("筛选点数", lambda item: directions["point_count"]),
        ("有效点数", lambda item: item["valid_count"]),
        ("缺失/不可用点数", lambda item: item["missing_count"]),
        ("异常点数", lambda item: item["invalid_count"]),
        ("缺失/异常比例 (%)", lambda item: round(item["unusable_percentage"], 2)),
        ("圆周平均方向 (°)", lambda item: round(item["circular_mean"], 2) if item["circular_mean"] is not None else "无有效数据或方向分散"),
        ("主要方向区间", lambda item: "、".join(f"{interval['start']}–{interval['end']}°" for interval in item["dominant_bins"]) or "无有效数据"),
        ("分箱宽度 (°)", lambda item: directions["bin_width"]),
    ]
    for row, (label, value) in enumerate(metrics, 3):
        overview.cell(row, 1, label)
        for column, field in enumerate(("course", "heading"), 2):
            overview.cell(row, column, value(directions[field]))
    _autofit(overview)
    for field, title in (("course", "对地航向分布"), ("heading", "船首向分布")):
        sheet = wb.create_sheet(title)
        sheet["A1"] = title
        sheet["A1"].font = _TITLE_FONT
        _write_header(sheet, 2, ["区间起点 (°)", "区间终点 (°)", "有效点数", "有效点占比 (%)"])
        item = directions[field]
        for row, (start, end, count, percentage) in enumerate(zip(
            directions["bins"][:-1], directions["bins"][1:], item["counts"], item["percentages"]
        ), 3):
            for column, value in enumerate((start, end, count, round(percentage, 2)), 1):
                sheet.cell(row, column, value)
        _autofit(sheet)
    wb.save(path)
    return path
