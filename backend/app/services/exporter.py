from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from backend.app.config import EXPORT_DIR
from backend.app.services.analysis import (
    ship_type_distribution,
    speed_histogram,
    summary,
    time_series,
)
from backend.app.services.query import QueryFilters


def export_excel(filters: QueryFilters | None = None) -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"ais_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Metric", "Value"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for key, value in summary(filters).items():
        if key == "manifest":
            continue
        ws.append([key, value])

    speed_ws = wb.create_sheet("Speed Histogram")
    speed_ws.append(["Speed From", "Speed To", "Point Count"])
    hist = speed_histogram(filters=filters)
    for left, right, count in zip(hist["bins"][:-1], hist["bins"][1:], hist["counts"]):
        speed_ws.append([left, right, count])

    type_ws = wb.create_sheet("Ship Types")
    type_ws.append(["Ship Type", "Point Count", "Vessel Count"])
    for row in ship_type_distribution(filters=filters):
        type_ws.append([row["ship_type"], row["point_count"], row["vessel_count"]])

    ts_ws = wb.create_sheet("Daily Traffic")
    ts_ws.append(["Date", "Point Count", "Vessel Count", "Average Speed"])
    for row in time_series(grain="day", filters=filters):
        ts_ws.append([row["bucket"], row["point_count"], row["vessel_count"], row["avg_speed"]])

    for sheet in wb.worksheets:
        for column_cells in sheet.columns:
            max_len = max(len(str(cell.value)) if cell.value is not None else 0 for cell in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = min(max_len + 2, 32)

    wb.save(path)
    return path
