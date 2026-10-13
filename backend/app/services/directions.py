from __future__ import annotations

from math import atan2, ceil, degrees, hypot
from typing import Any

from backend.app.services.analysis import require_dataset
from backend.app.services.dataset import read_active_manifest
from backend.app.services.query import QueryFilters, analysis_source, connect, points_sql, trajectory_column


DIRECTION_BIN_WIDTHS = {5, 10, 15, 30, 45, 60, 90}


def validate_direction_bin_width(bin_width: int) -> int:
    if bin_width not in DIRECTION_BIN_WIDTHS:
        raise ValueError("航向分箱宽度应为 5、10、15、30、45、60 或 90 度。")
    return int(bin_width)


def _normalized_source(con, filters: QueryFilters | None) -> tuple[str, list[Any], bool]:
    # Inspect the real schema: legacy PKL datasets legitimately have no heading.
    columns = {row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {points_sql()}").fetchall()}
    heading_available = "heading" in columns
    heading = "heading::DOUBLE" if heading_available else "NULL::DOUBLE"
    source, params = analysis_source(filters)
    return f"""
        WITH raw AS (
            SELECT *, course::DOUBLE AS raw_course, {heading} AS raw_heading FROM {source}
        ), normalized AS (
            SELECT *,
                CASE WHEN isfinite(raw_course) AND raw_course >= 0 AND raw_course < 360
                     THEN raw_course END AS course_angle,
                CASE WHEN isfinite(raw_heading) AND raw_heading >= 0 AND raw_heading < 360
                     THEN raw_heading END AS heading_angle
            FROM raw
        )
    """, params, heading_available


def direction_histograms(bin_width: int = 15, filters: QueryFilters | None = None) -> dict[str, Any]:
    require_dataset()
    bin_width = validate_direction_bin_width(bin_width)
    bins = list(range(0, 361, bin_width))
    result: dict[str, Any] = {"bin_width": bin_width, "bins": bins}
    with connect() as con:
        source, params, heading_available = _normalized_source(con, filters)
        aggregates = []
        for field, unavailable in (("course", 360), ("heading", 511)):
            aggregates.extend([
                f"count({field}_angle)",
                f"count(*) FILTER (WHERE raw_{field} IS NULL OR raw_{field} = {unavailable})",
                f"avg(sin(radians({field}_angle)))",
                f"avg(cos(radians({field}_angle)))",
            ])
        row = con.execute(source + "SELECT count(*), " + ", ".join(aggregates) + " FROM normalized", params).fetchone()
        total = int(row[0])
        result["point_count"] = total
        histogram_rows = con.execute(source + """
            SELECT 'course' AS field, floor(course_angle / ?)::INTEGER AS bin, count(*)
            FROM normalized WHERE course_angle IS NOT NULL GROUP BY bin
            UNION ALL
            SELECT 'heading' AS field, floor(heading_angle / ?)::INTEGER AS bin, count(*)
            FROM normalized WHERE heading_angle IS NOT NULL GROUP BY bin
        """, [*params, bin_width, bin_width]).fetchall()
    for offset, field in enumerate(("course", "heading")):
        valid, missing, mean_sin, mean_cos = row[1 + offset * 4:5 + offset * 4]
        valid, missing = int(valid), int(missing)
        counts = [0] * (len(bins) - 1)
        for histogram_field, index, count in histogram_rows:
            if histogram_field == field:
                counts[int(index)] = int(count)
        resultant = hypot(mean_sin or 0, mean_cos or 0)
        mean = degrees(atan2(mean_sin, mean_cos)) % 360 if valid and resultant > 1e-8 else None
        if mean is not None and (mean < 1e-8 or 360 - mean < 1e-8):
            mean = 0.0
        maximum = max(counts, default=0)
        result[field] = {
            "available": field == "course" or heading_available,
            "valid_count": valid,
            "missing_count": missing,
            "invalid_count": total - valid - missing,
            "unusable_percentage": (total - valid) * 100.0 / total if total else 0.0,
            "circular_mean": mean,
            "counts": counts,
            "percentages": [count * 100.0 / valid if valid else 0.0 for count in counts],
            "dominant_bins": [
                {"start": bins[index], "end": bins[index + 1]}
                for index, count in enumerate(counts) if maximum and count == maximum
            ],
        }
    return result


def direction_time_series(filters: QueryFilters, max_points: int = 5000) -> dict[str, Any]:
    require_dataset()
    if filters.mmsi is None:
        raise ValueError("请先输入船舶编号（MMSI），再查看航向时间曲线。")
    max_points = max(2, min(int(max_points), 20_000))
    gap_minutes = (read_active_manifest() or {}).get("config", {}).get("max_time_gap_minutes", 60)
    gap_seconds = max(1, int(float(gap_minutes) * 60))
    segment = trajectory_column()
    with connect() as con:
        source, params, heading_available = _normalized_source(con, filters)
        total = int(con.execute(source + "SELECT count(*) FROM normalized WHERE timestamp > 0", params).fetchone()[0])
        if not total:
            return {"mmsi": filters.mmsi, "total_points": 0, "displayed_points": 0,
                    "sampled": False, "heading_available": heading_available, "points": []}
        step = max(1, ceil((total - 1) / (max_points - 1)))
        order = f"timestamp, {segment}, lon, lat"
        # Compute breaks BEFORE limiting chart points, so omitted wraps, gaps and
        # unavailable values cannot create a fictitious continuous line.
        rows = con.execute(source + f"""
            , ordered AS (
                SELECT *, row_number() OVER (ORDER BY {order}) AS rn,
                    lag(timestamp) OVER (ORDER BY {order}) AS prev_ts,
                    lag({segment}) OVER (ORDER BY {order}) AS prev_segment,
                    lag(course_angle) OVER (ORDER BY {order}) AS prev_course,
                    lag(heading_angle) OVER (ORDER BY {order}) AS prev_heading
                FROM normalized WHERE timestamp > 0
            ), runs AS (
                SELECT *,
                    sum(CASE WHEN prev_ts IS NULL OR {segment} <> prev_segment
                        OR timestamp - prev_ts > {gap_seconds}
                        OR course_angle IS NULL OR prev_course IS NULL
                        OR abs(course_angle - prev_course) > 180 THEN 1 ELSE 0 END)
                        OVER (ORDER BY rn) AS course_run,
                    sum(CASE WHEN prev_ts IS NULL OR {segment} <> prev_segment
                        OR timestamp - prev_ts > {gap_seconds}
                        OR heading_angle IS NULL OR prev_heading IS NULL
                        OR abs(heading_angle - prev_heading) > 180 THEN 1 ELSE 0 END)
                        OVER (ORDER BY rn) AS heading_run
                FROM ordered
            )
            SELECT timestamp, course_angle, heading_angle, course_run, heading_run
            FROM runs WHERE ((rn - 1) % ?) = 0 OR rn = ? ORDER BY rn
        """, [*params, step, total]).fetchall()
    points = []
    previous_runs = None
    for timestamp, course, heading, course_run, heading_run in rows:
        current_runs = (course_run, heading_run)
        points.append({
            "timestamp": int(timestamp), "course": course, "heading": heading,
            "course_break": previous_runs is None or course_run != previous_runs[0],
            "heading_break": previous_runs is None or heading_run != previous_runs[1],
        })
        previous_runs = current_runs
    return {"mmsi": filters.mmsi, "total_points": total, "displayed_points": len(points),
            "sampled": len(points) < total, "heading_available": heading_available, "points": points}
