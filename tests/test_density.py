from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from backend.app.services import analysis, density, query
from backend.app.services.query import QueryFilters, normalize_spatial_filter


class DensityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parquet = Path(temporary.name) / "points.parquet"
        self.manifest = {}
        for target, name, value in (
            (analysis, "dataset_ready", lambda: True),
            (query, "active_parquet_path", lambda: self.parquet),
            (query, "read_active_manifest", lambda: self.manifest),
            (density, "read_active_manifest", lambda: self.manifest),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def point(self, offset=0, **extra):
        row = {"mmsi": 413000001, "timestamp": 1700000000 + offset,
               "lon": 110.0, "lat": 20.0, "speed": 5.0, "course": 90.0,
               "ship_type": 70, "trajectory_id": 1}
        row.update(extra)
        return row

    def write(self, points):
        table = pa.Table.from_pylist(points)
        pq.write_table(table, self.parquet)
        self.manifest = {"columns": table.column_names,
                         "bounds": {"lat_min": 20, "lat_max": 21}}

    def test_counts_conserve_all_observations_and_distinct_vessels(self):
        self.write([self.point(), self.point(60), self.point(120, mmsi=413000002),
                    self.point(180, lon=111)])
        result = density.density_features()
        self.assertEqual(result["metadata"]["point_count"], 4)
        busiest = max(result["features"], key=lambda f: f["properties"]["point_count"])
        self.assertEqual(busiest["properties"]["point_count"], 3)
        self.assertEqual(busiest["properties"]["vessel_count"], 2)
        self.assertEqual(sum(f["properties"]["point_count"] for f in result["features"]), 4)
        ring = busiest["geometry"]["coordinates"][0]
        self.assertEqual(ring[0], ring[-1])
        self.assertLessEqual(ring[0][0], 110)
        self.assertGreaterEqual(ring[2][0], 110)
        self.assertLessEqual(ring[0][1], 20)
        self.assertGreaterEqual(ring[2][1], 20)
        json.dumps(result, allow_nan=False)

    def test_filtering_preserves_cell_geometry_and_applies_region_time_vessel_speed(self):
        self.write([self.point(), self.point(60), self.point(120, lon=111),
                    self.point(180, mmsi=413000002), self.point(240, speed=1)])
        all_rows = density.density_features()
        mode, geometry = normalize_spatial_filter("region", json.dumps(
            [[109.9, 19.9], [110.1, 19.9], [110.1, 20.1], [109.9, 20.1]]))
        subset = density.density_features(filters=QueryFilters(
            start_ts=1700000060, mmsi=413000001, speed_min=4,
            ship_type=70, spatial_mode=mode, spatial_geometry=geometry))
        self.assertEqual(subset["metadata"]["point_count"], 1)
        cell = subset["features"][0]
        original = next(f for f in all_rows["features"] if f["id"] == cell["id"])
        self.assertEqual(original["geometry"], cell["geometry"])

    def test_behavior_counts_match_recognized_points_without_extra_endpoint(self):
        self.write([self.point(), self.point(60, course=120), self.point(120, course=121)])
        result = density.density_features(filters=QueryFilters(behavior="turning"))
        self.assertEqual(result["metadata"]["point_count"], 1)
        self.assertEqual(result["metadata"]["behavior"], "turning")

    def test_section_density_uses_complete_selected_tracks_like_the_map(self):
        self.write([self.point(), self.point(60, lon=111), self.point(120, lon=112),
                    self.point(lon=109, trajectory_id=2, mmsi=413000002),
                    self.point(60, lon=109.1, trajectory_id=2, mmsi=413000002)])
        mode, geometry = normalize_spatial_filter("section", json.dumps([[110.5, 19.5], [110.5, 20.5]]))
        result = density.density_features(filters=QueryFilters(spatial_mode=mode, spatial_geometry=geometry))
        self.assertEqual(result["metadata"]["point_count"], 3)
        self.assertEqual(result["metadata"]["spatial_scope"], "selected_tracks")

    def test_empty_selection_and_degenerate_extent_are_valid_geojson(self):
        self.write([self.point()])
        result = density.density_features(filters=QueryFilters(mmsi=999999999))
        self.assertEqual(result["features"], [])
        self.assertEqual(result["metadata"]["max_count"], 0)
        self.assertEqual(density.density_features()["metadata"]["cell_count"], 1)
        json.dumps(result, allow_nan=False)

    def test_large_grid_coarsens_without_losing_points(self):
        self.write([self.point(lon=110 + i * .02) for i in range(12)])
        with patch.object(density, "MAX_CELLS", 2):
            result = density.density_features(cell_size_m=100)
        self.assertTrue(result["metadata"]["coarsened"])
        self.assertLessEqual(result["metadata"]["cell_count"], 2)
        self.assertEqual(result["metadata"]["point_count"], 12)

    def test_invalid_and_off_map_coordinates_do_not_reach_projection(self):
        self.write([self.point(), self.point(lon=float("nan")), self.point(lat=90),
                    self.point(lon=-110, lat=-20), self.point(lon=200)])
        result = density.density_features()
        self.assertEqual(result["metadata"]["point_count"], 2)
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
