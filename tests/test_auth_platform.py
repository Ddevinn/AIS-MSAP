from __future__ import annotations

import csv
import io
import json
import pickle
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import numpy as np

from backend.app import main, storage
from backend.app.services import dataset, exporter, raster_tracks


class AccountPlatformTests(unittest.TestCase):
    """Exercise real HTTP authorization, files and query threads in a temporary store."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for module, field, value in (
            (storage, "DB_PATH", self.root / "private" / "test.sqlite3"),
            (storage, "DATASET_REGISTRY_PATH", self.root / "registry.json"),
            (dataset, "DATASETS_DIR", self.root / "datasets"),
            (main, "DATA_DIR", self.root),
            (exporter, "EXPORT_DIR", self.root / "exports"),
            (raster_tracks, "CACHE_DIR_PATH", self.root / "cache"),
        ):
            patcher = patch.object(module, field, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)
        storage.initialize()
        self.admin = self.client()
        response = self.admin.post("/api/auth/setup", json={"username": "admin", "password": "AdminPassword123!", "display_name": "管理员"})
        self.assertEqual(response.status_code, 200, response.text)
        self.login(self.admin, "admin", "AdminPassword123!")

    def client(self):
        client = TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
        self.addCleanup(client.close)
        return client

    def login(self, client, username, password):
        response = client.post("/api/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return response.json()

    def user(self, username="alice"):
        response = self.admin.post("/api/admin/users", json={"username": username, "password": "TemporaryPass123!", "display_name": username})
        self.assertEqual(response.status_code, 200, response.text)
        client = self.client()
        self.login(client, username, "TemporaryPass123!")
        response = client.post("/api/auth/password", json={"current_password": "TemporaryPass123!", "new_password": "ChangedPassword123!"})
        self.assertEqual(response.status_code, 200, response.text)
        self.login(client, username, "ChangedPassword123!")
        return client, self.admin.get("/api/admin/users").json()["users"][-1]["id"]

    def upload(self, client, count=3, name="船舶数据"):
        contents = "mmsi,updatetime,lon,lat,speed,course,heading,ship_type,length,width,draught\n"
        contents += "".join(f"413523230,2024-01-01 12:0{i}:00,{110 + i * 0.001},20,5,90,91,70,100,20,4\n" for i in range(count))
        response = client.post("/api/datasets/upload", data={"name": name}, files={"file": ("points.csv", contents.encode(), "text/csv")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["dataset"]["id"]

    def build(self, client):
        response = client.post("/api/build")
        self.assertEqual(response.status_code, 200, response.text)
        state = self.wait_for_job(client, "build_job")
        self.assertEqual(state["build_job"]["status"], "done", state)
        return state

    def wait_for_job(self, client, job_name):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            state = client.get("/api/status").json()
            if state[job_name]["status"] != "running":
                return state
            time.sleep(0.03)
        self.fail(f"Temporary {job_name} did not finish")

    def test_field_quality_confirmation_cancel_retry_and_rejected_export(self):
        client = self.admin
        csv_text = (
            "mmsi,updatetime,lon,lat,speed,course,type,heading,length,width,draught\n"
            "413523230,2024-01-01 12:00:00,110,20,5,90,70,91,100,20,4\n"
            "413523230,2024-01-01 12:00:00,110,20,5,90,999,91,100,20,4\n"
            "413523230,2024-01-01 12:01:00,110.001,20,bad-speed,bad-course,70,91,100,20,4\n"
            "413523230,2024-01-01 12:02:00,115,20,5,90,70,91,100,20,4\n"
            "413523230,2024-01-01 19:03:00,110,20,5,90,70,91,100,20,4\n"
        )
        response = client.post("/api/datasets/upload", files={"file": ("audit.csv", csv_text.encode(), "text/csv")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(client.post("/api/build").status_code, 200)
        waiting = self.wait_for_job(client, "build_job")
        self.assertFalse(waiting["dataset_ready"])
        self.assertEqual(waiting["build_job"]["status"], "awaiting_confirmation")
        warnings = " ".join(waiting["build_job"]["warnings"])
        self.assertIn("type", warnings)
        self.assertIn("speed", warnings)
        first_attempt = waiting["build_job"]["attempt_id"]
        decision = "/api/build/decision"
        self.assertEqual(client.post(decision, json={"attempt_id": "wrong", "proceed": True}).status_code, 409)
        self.assertEqual(client.post(decision, json={"attempt_id": first_attempt, "proceed": False}).status_code, 200)
        self.assertFalse(client.get("/api/status").json()["dataset_ready"])

        self.assertEqual(client.post("/api/build").status_code, 200)
        waiting = self.wait_for_job(client, "build_job")
        attempt = waiting["build_job"]["attempt_id"]
        self.assertNotEqual(attempt, first_attempt)
        self.assertEqual(client.post(decision, json={"attempt_id": attempt, "proceed": True}).status_code, 200)
        loaded = client.get("/api/status").json()
        self.assertEqual(loaded["manifest"]["usable_rows"], 4)
        self.assertTrue(loaded["manifest"]["ship_type_available"])
        self.assertEqual(client.get("/api/summary").json()["point_count"], 4)
        self.assertEqual(client.get("/api/summary?ship_type=70").json()["point_count"], 3)

        self.assertEqual(client.post("/api/cleaning/run").status_code, 200)
        cleaned = self.wait_for_job(client, "clean_job")
        self.assertEqual(cleaned["clean_job"]["status"], "done", cleaned)
        counts = cleaned["cleaning_manifest"]["counts"]
        response = client.get("/api/export/rejected")
        self.assertEqual(response.status_code, 200, response.text)
        rejected = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(len(rejected), counts["removed_rows"])
        self.assertEqual(counts["raw_rows"], len(rejected) + counts["output_rows"])
        self.assertEqual({row["rejected_stage"] for row in rejected}, {"validation", "duplicate", "motion"})
        by_record = {row["source_record"]: row for row in rejected}
        self.assertEqual(by_record["2"]["duplicate_retained_record"], "1")
        self.assertEqual(json.loads(by_record["2"]["source_invalid_values"])["type"], "999")
        self.assertIn("航速", by_record["3"]["reason_labels"])
        self.assertEqual(by_record["3"]["reason_codes"], "invalid_speed|invalid_course")
        self.assertEqual(json.loads(by_record["3"]["source_invalid_values"])["speed"], "bad-speed")
        self.assertIn("jump_point", by_record["4"]["reason_codes"])
        self.assertEqual(by_record["4"]["prev_source_record"], "1")
        self.assertNotIn("5", by_record)
        self.assertEqual(client.post("/api/cleaning/reset").status_code, 200)
        self.assertEqual(client.get("/api/export/rejected").status_code, 409)

    def test_missing_ship_type_can_load_but_unusable_core_fields_cannot(self):
        client = self.admin
        headers = "mmsi,updatetime,lon,lat,speed,course\n"
        valid = "413523230,2024-01-01 12:00:00,110,20,5,90\n"
        self.assertEqual(client.post("/api/datasets/upload", files={"file": ("no-type.csv", (headers + valid).encode())}).status_code, 200)
        self.assertEqual(client.post("/api/build").status_code, 200)
        waiting = self.wait_for_job(client, "build_job")
        self.assertEqual(waiting["build_job"]["status"], "awaiting_confirmation")
        attempt = waiting["build_job"]["attempt_id"]
        self.assertEqual(client.post("/api/build/decision", json={"attempt_id": attempt, "proceed": True}).status_code, 200)
        self.assertFalse(client.get("/api/status").json()["manifest"]["ship_type_available"])
        self.assertEqual(client.get("/api/ship-types").json()[0]["ship_type"], -1)

        invalid = "413523230,2024-01-01 12:00:00,500,20,5,90\n413523230,2024-01-01 12:01:00,110,200,5,90\n"
        self.assertEqual(client.post("/api/datasets/upload", files={"file": ("bad.csv", (headers + invalid).encode())}).status_code, 200)
        self.assertEqual(client.post("/api/build").status_code, 200)
        failed = self.wait_for_job(client, "build_job")
        self.assertEqual(failed["build_job"]["status"], "error")
        self.assertIn("lon", failed["build_job"]["message"])
        self.assertFalse(failed["dataset_ready"])


    def test_behavior_export_downloads_matching_pairs_with_current_filters(self):
        from openpyxl import load_workbook

        client = self.admin
        path = "/api/export/behavior"
        self.assertEqual(client.get(path).status_code, 422)
        self.assertEqual(client.get(path, params={"behavior": "invalid"}).status_code, 422)
        contents = (
            "mmsi,updatetime,lon,lat,speed,course,heading,type,length,width,draught\n"
            "413523230,2024-01-01 12:00:00,110,20,5,350,350,70,100,20,4\n"
            "413523230,2024-01-01 12:01:00,110.001,20,5,10,10,70,100,20,4\n"
            "413523230,2024-01-01 12:02:00,110.002,20,5,10,10,70,100,20,4\n"
            "413523231,2024-01-01 12:00:00,110,20,5,90,90,70,100,20,4\n"
            "413523231,2024-01-01 12:01:00,110.001,20,5,120,120,70,100,20,4\n"
        )
        uploaded = client.post("/api/datasets/upload", files={"file": ("behaviors.csv", contents.encode(), "text/csv")})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.build(client)
        for cleaned in (False, True):
            with self.subTest(cleaned=cleaned):
                if cleaned:
                    self.assertEqual(client.post("/api/cleaning/run").status_code, 200)
                    state = self.wait_for_job(client, "clean_job")
                    self.assertEqual(state["clean_job"]["status"], "done", state)
                response = client.get(path, params={"behavior": "turning", "mmsi": 413523230})
                self.assertEqual(response.status_code, 200, response.text if not response.is_success else "")
                workbook = load_workbook(io.BytesIO(response.content), read_only=True)
                try:
                    self.assertEqual(workbook.sheetnames, ["船舶行为轨迹"])
                    headers, *rows = workbook.active.values
                    self.assertEqual([row[headers.index("mmsi")] for row in rows], [413523230, 413523230])
                    self.assertEqual([row[headers.index("course")] for row in rows], [350, 10])
                finally:
                    workbook.close()

    def test_map_track_inspection_and_export_use_the_same_contiguous_segment(self):
        from openpyxl import load_workbook

        client = self.admin
        columns = "mmsi,updatetime,lon,lat,speed,course,heading,type,length,width,draught"
        rows = [
            "413523230,2024-01-01 12:00:00,110,20,5,0,0,70,100,20,4",
            "413523230,2024-01-01 12:01:00,110.003,20,0,1,,70,100,20,4",
            "413523230,2024-01-01 12:02:00,110.006,20,6,1,25,70,100,20,4",
            "413523230,2024-01-01 15:00:00,110.020,20,5,90,91,70,100,20,4",
            "413523230,2024-01-01 15:01:00,110.021,20,5,90,91,70,100,20,4",
            "413523231,2024-01-01 12:00:00,110,20.010,8,90,91,70,80,15,4",
        ]
        contents = chr(10).join([columns, *rows]).encode()
        uploaded = client.post("/api/datasets/upload", files={"file": ("map.csv", contents, "text/csv")})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.assertEqual(client.post("/api/build").status_code, 200)
        job = self.wait_for_job(client, "build_job")["build_job"]
        self.assertEqual(job["status"], "awaiting_confirmation")
        self.assertEqual(client.post("/api/build/decision", json={"attempt_id": job["attempt_id"], "proceed": True}).status_code, 200)
        viewport = "/api/track-inspection/viewport"
        detail_url = "/api/track-inspection/segment"
        export_url = "/api/export/selected-track"
        dataset_id = uploaded.json()["dataset"]["id"]
        bounds = {"west": 109.99, "east": 110.04, "south": 19.99, "north": 20.02}
        previous_version = None

        for cleaned in (False, True):
            with self.subTest(cleaned=cleaned):
                if cleaned:
                    self.assertEqual(client.post("/api/cleaning/run").status_code, 200)
                    state = self.wait_for_job(client, "clean_job")
                    self.assertEqual(state["clean_job"]["status"], "done", state)
                manifest = client.get("/api/status").json()["active_manifest"]
                version = ":".join(map(str, (dataset_id, manifest["data_role"], manifest["created_at"], manifest["row_count"])))
                if previous_version:
                    self.assertEqual(client.get(viewport, params={**bounds, "data_version": previous_version}).status_code, 409)
                previous_version = version
                candidates = client.get(viewport, params={**bounds, "data_version": version})
                self.assertEqual(candidates.status_code, 200, candidates.text)
                points = candidates.json()["points"]
                self.assertEqual(len(points), 6)
                point = next(p for p in points if p["lon"] == 110.003)
                self.assertEqual(point["speed"], 0)
                self.assertIsNone(point["heading"])
                self.assertEqual(point["previous"], [110, 20])
                self.assertEqual(next(p["heading"] for p in points if p["lon"] == 110 and p["mmsi"] == 413523230), 0)
                params = {
                    "data_version": version, "vessel_id": point["mmsi"], "track_id": point["track_id"],
                    "anchor_time": point["timestamp"], "anchor_lon": point["lon"], "anchor_lat": point["lat"],
                }
                response = client.get(detail_url, params=params)
                self.assertEqual(response.status_code, 200, response.text)
                detail = response.json()
                self.assertEqual(detail["point_count"], 3)
                self.assertAlmostEqual(detail["speed_avg"], 11 / 3)
                self.assertEqual(detail["path"], [[110, 20], [110.003, 20], [110.006, 20]])
                response = client.get(export_url, params=params)
                self.assertEqual(response.status_code, 200, response.text if not response.is_success else "")
                with io.BytesIO(response.content) as content:
                    workbook = load_workbook(content, read_only=True)
                    try:
                        headers, *exported = workbook.active.values
                        self.assertEqual(len(exported), detail["point_count"])
                        self.assertEqual([row[headers.index("lon")] for row in exported], [110, 110.003, 110.006])
                    finally:
                        workbook.close()
                missing = {**params, "anchor_lon": 111}
                self.assertEqual(client.get(detail_url, params=missing).status_code, 404)
                self.assertEqual(client.get(export_url, params=missing).status_code, 404)
                if cleaned:
                    first = next(p for p in points if p["lon"] == 110 and p["mmsi"] == 413523230)
                    filtered = {**params, "anchor_time": first["timestamp"], "anchor_lon": 110, "speed_min": 1}
                    self.assertEqual(client.get(detail_url, params=filtered).json()["point_count"], 1)

    def test_selected_track_annotations_preserve_true_endpoints_and_pairwise_marks(self):
        from backend.app.services import track_inspection

        client = self.admin
        column_names = "mmsi,updatetime,lon,lat,speed,course,heading,type,length,width,draught"
        times = ["12:00:00", "12:01:00", "12:02:00", "12:03:00", "12:04:00", "12:14:01", "15:00:00"]
        speeds = [3, 5, 1.5, 0.4, 0.3, 8, 8]
        records = [f"413523230,2024-01-01 {time},{110 + index / 1000:.3f},20,{speed},90,90,70,100,20,4"
                   for index, (time, speed) in enumerate(zip(times, speeds))]
        contents = chr(10).join([column_names, *records]).encode()
        upload = client.post("/api/datasets/upload", files={"file": ("labels.csv", contents, "text/csv")})
        self.assertEqual(upload.status_code, 200, upload.text)
        dataset_id = upload.json()["dataset"]["id"]
        self.build(client)
        manifest = client.get("/api/status").json()["active_manifest"]
        version = ":".join(map(str, (dataset_id, manifest["data_role"], manifest["created_at"], manifest["row_count"])))
        params = {"vessel_id": 413523230, "track_id": 0, "anchor_time": manifest["time_range"]["timestamp_min"],
                  "anchor_lon": 110, "anchor_lat": 20, "data_version": version}
        url = "/api/track-inspection/annotations"
        response = client.get(url, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        points = response.json()["points"]
        self.assertEqual([point["endpoint"] for point in points], ["start", None, None, None, None, "end"])
        self.assertEqual([point["activity"] for point in points], [None, "accelerate", "decelerate", "decelerate", "stop", None])
        self.assertEqual(points[1]["previous"], [110, 20])
        self.assertEqual(points[1]["timestamp"] - points[0]["timestamp"], 60)
        bounds = {"west": 110.0025, "east": 110.0045, "south": 19.99, "north": 20.01}
        clipped = client.get(url, params={**params, **bounds})
        self.assertEqual(clipped.status_code, 200, clipped.text)
        visible = clipped.json()["points"]
        self.assertEqual([point["index"] for point in visible], [4, 5, 6])
        self.assertEqual(visible[0]["previous"], [110.002, 20])
        self.assertEqual(visible[0]["activity"], "decelerate")
        self.assertIsNone(visible[0]["endpoint"])
        self.assertEqual(visible[-1]["endpoint"], "end")
        self.assertEqual(client.get(url, params={**params, "west": 110}).status_code, 422)
        with patch.object(track_inspection, "MAX_ANNOTATION_POINTS", 2):
            self.assertTrue(client.get(url, params=params).json()["limited"])
            detail = client.get("/api/track-inspection/segment", params=params).json()
            self.assertTrue(detail["annotation_viewport"])

    def test_pages_and_every_data_entry_require_login(self):
        anonymous = self.client()
        for path in ("/api/status", "/api/datasets", "/api/summary", "/api/density-grid",
                     "/api/tiles/tracks/8/206/113.png", "/api/export/excel", "/api/export/behavior", "/api/admin/users", "/api/cleaning/status"):
            self.assertEqual(anonymous.get(path).status_code, 401, path)
        for path in ("/", "/admin", "/static/index.html", "/static/admin.html", "/static//admin.html", "/static/ADMIN.HTML"):
            response = anonymous.get(path, follow_redirects=False)
            self.assertEqual(response.status_code, 303, path)
        self.assertEqual(anonymous.get("/login").status_code, 200)
        self.assertEqual(anonymous.get("/login").headers["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertEqual(self.admin.get("/").headers["Referrer-Policy"], "strict-origin-when-cross-origin")

    def test_empty_login_and_history_survive_relogin_without_auto_selection(self):
        alice, _ = self.user()
        initial = alice.get("/api/status").json()
        self.assertIsNone(initial["active_dataset_id"])
        self.assertEqual(initial["datasets"], [])
        self.assertFalse(initial["dataset_ready"])
        self.assertEqual(alice.post("/api/build").status_code, 409)
        dataset_id = self.upload(alice)
        self.build(alice)
        self.assertEqual(alice.get("/api/summary").json()["point_count"], 3)
        # Refresh keeps the current login's selection.
        self.assertEqual(alice.get("/api/status").json()["active_dataset_id"], dataset_id)
        self.assertEqual(alice.post("/api/auth/logout").status_code, 200)
        self.login(alice, "alice", "ChangedPassword123!")
        state = alice.get("/api/status").json()
        self.assertIsNone(state["active_dataset_id"])
        self.assertEqual(len(state["datasets"]), 1)
        self.assertEqual(state["datasets"][0]["filename"], "points.csv")
        response = alice.post(f"/api/datasets/select/{dataset_id}")
        self.assertEqual(response.status_code, 200)
        parquet = Path(alice.get("/api/status").json()["active_parquet"])
        modified = parquet.stat().st_mtime_ns
        self.assertFalse(alice.post("/api/build").json()["accepted"])
        self.assertEqual(parquet.stat().st_mtime_ns, modified)
        self.assertEqual(alice.post("/api/datasets/clear").status_code, 200)
        self.assertEqual(alice.delete(f"/api/datasets/{dataset_id}").status_code, 200)
        self.assertEqual(alice.get("/api/datasets").json()["datasets"], [])
        self.assertTrue(parquet.exists())

    def test_owned_datasets_and_admin_are_inaccessible_to_other_accounts(self):
        alice, _ = self.user("alice")
        bob, _ = self.user("bob")
        alice_id = self.upload(alice)
        self.build(alice)
        self.assertEqual(bob.get("/api/datasets").json()["datasets"], [])
        for method, path in (("post", f"/api/datasets/select/{alice_id}"), ("delete", f"/api/datasets/{alice_id}"),
                             ("get", f"/api/summary?dataset_id={alice_id}"),
                             ("get", f"/api/tiles/tracks/8/206/113.png?dataset_id={alice_id}"),
                             ("get", f"/api/export/excel?dataset_id={alice_id}")):
            self.assertEqual(getattr(bob, method)(path).status_code, 404, path)
        for path in ("/admin", "/static//admin.html", "/static/ADMIN.HTML", "/api/admin/users", "/api/admin/sessions", "/api/admin/logs", "/openapi.json"):
            self.assertEqual(alice.get(path).status_code, 403, path)
        self.assertEqual(alice.post("/api/datasets/register", json={"source_path": str(self.root)}).status_code, 403)
        self.assertEqual(alice.post("/api/datasets/preview", json={"source_path": str(self.root)}).status_code, 403)

    def test_pkl_upload_and_build_work_for_ordinary_users_and_admins(self):
        alice, _ = self.user()
        trajectory = np.asarray([
            [413523230, 1643715164, 110.12933, 20.21789, 196.8, 9.7, 70, 123, 20],
            [413523230, 1643715224, 110.13033, 20.21889, 196.8, 9.7, 70, 123, 20],
        ])
        for client, filename, other in ((alice, "船舶轨迹.PKL", self.admin),
                                        (self.admin, "legacy.pickle", alice)):
            with self.subTest(filename=filename):
                response = client.post("/api/datasets/upload", files={
                    "file": (filename, pickle.dumps([trajectory]), "application/octet-stream"),
                })
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                dataset_id = result["dataset"]["id"]
                self.assertEqual(result["dataset"]["name"], Path(filename).stem)
                self.assertEqual(result["active_dataset_id"], dataset_id)
                self.assertEqual(result["preview"]["format"], "pkl")
                self.assertEqual(result["preview"]["trajectory_count"], 1)
                self.assertEqual(result["preview"]["row_count"], 2)
                self.assertEqual(other.post(f"/api/datasets/select/{dataset_id}").status_code, 404)
                state = self.build(client)
                self.assertEqual(state["manifest"]["row_count"], 2)
                self.assertEqual(client.get("/api/summary").json()["point_count"], 2)

    def test_invalid_pkl_upload_keeps_selection_and_removes_failed_upload(self):
        alice, _ = self.user()
        previous = self.upload(alice)
        payloads = (b"not a pickle", pickle.dumps({}), pickle.dumps([]),
                    pickle.dumps([np.ones((1, 8))]), pickle.dumps([np.empty((0, 9))]),
                    pickle.dumps([np.full((1, 9), "text")]))
        for payload in payloads:
            response = alice.post("/api/datasets/upload", files={
                "file": ("invalid.pkl", payload, "application/octet-stream"),
            })
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("导入失败", response.json()["detail"])
            state = alice.get("/api/status").json()
            self.assertEqual(state["active_dataset_id"], previous)
            self.assertEqual(len(state["datasets"]), 1)
        self.assertEqual(list((self.root / "uploads").rglob("*.pkl")), [])

    def test_parallel_queries_rendering_jobs_and_export_stay_isolated(self):
        alice, _ = self.user("alice")
        bob, _ = self.user("bob")
        alice_id, bob_id = self.upload(alice, 2, "同名数据"), self.upload(bob, 5, "同名数据")
        self.assertNotEqual(alice_id, bob_id)
        self.build(alice)
        self.build(bob)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(client.get, "/api/summary") for client in (alice, bob, alice, bob)]
            self.assertEqual([future.result().json()["point_count"] for future in futures], [2, 5, 2, 5])
        # Exercise real run_in_executor rendering: lost context would read the legacy global registry.
        for client, dataset_id in ((alice, alice_id), (bob, bob_id)):
            tile = client.get(f"/api/tiles/tracks/8/206/113.png?dataset_id={dataset_id}")
            self.assertEqual(tile.status_code, 200, tile.text[:100] if tile.status_code != 200 else "")
            self.assertTrue(tile.content.startswith(b"\x89PNG"))
            self.assertEqual(tile.headers["Cache-Control"], "no-store")
            response = client.get("/api/export/excel")
            self.assertEqual(response.status_code, 200, response.text[:100] if response.status_code != 200 else "")
            from openpyxl import load_workbook
            workbook = load_workbook(io.BytesIO(response.content), read_only=True)
            values = dict(workbook["总览"].iter_rows(min_row=3, max_col=2, values_only=True))
            self.assertEqual(values["数据点/截面穿越次数"], 2 if client is alice else 5)
            workbook.close()
        self.assertTrue((self.root / "exports" / "user_2").exists())
        self.assertTrue((self.root / "exports" / "user_3").exists())

    def test_two_sessions_of_same_user_have_independent_active_data(self):
        alice, _ = self.user()
        first = self.upload(alice, 2)
        self.build(alice)
        second = self.upload(alice, 4)
        self.build(alice)
        other = self.client()
        self.login(other, "alice", "ChangedPassword123!")
        self.assertIsNone(other.get("/api/status").json()["active_dataset_id"])
        other.post(f"/api/datasets/select/{first}")
        self.assertEqual(alice.get("/api/status").json()["active_dataset_id"], second)
        self.assertEqual(other.get("/api/summary").json()["point_count"], 2)
        self.assertEqual(alice.get("/api/summary").json()["point_count"], 4)

    def test_cleaning_uses_owner_job_and_never_changes_other_users_data(self):
        alice, _ = self.user("alice")
        bob, _ = self.user("bob")
        self.upload(alice); self.build(alice)
        self.upload(bob, 4); self.build(bob)
        response = alice.post("/api/cleaning/run", json={})
        self.assertEqual(response.status_code, 200, response.text)
        end = time.monotonic() + 10
        while time.monotonic() < end:
            state = alice.get("/api/status").json()
            if state["clean_job"]["status"] != "running":
                break
            time.sleep(0.03)
        self.assertEqual(state["clean_job"]["status"], "done", state)
        self.assertTrue(state["cleaned_dataset_ready"])
        other = bob.get("/api/status").json()
        self.assertFalse(other["cleaned_dataset_ready"])
        self.assertEqual(other["clean_job"]["status"], "idle")
        self.assertEqual(bob.get("/api/summary").json()["point_count"], 4)

    def test_temp_password_restricts_features_until_change_and_invalidates_sessions(self):
        response = self.admin.post("/api/admin/users", json={"username": "alice", "password": "TemporaryPass123!"})
        self.assertEqual(response.status_code, 200)
        alice = self.client()
        self.login(alice, "alice", "TemporaryPass123!")
        response = alice.get("/api/status")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "PASSWORD_CHANGE_REQUIRED")
        response = alice.get("/", follow_redirects=False)
        self.assertEqual(response.headers["location"], "/account/password")
        self.assertEqual(alice.post("/api/auth/password", json={"current_password": "wrong", "new_password": "NewPassword123!"}).status_code, 400)

    def test_password_reset_disable_and_revoke_immediately_end_sessions(self):
        alice, user_id = self.user()
        reset = self.admin.post(f"/api/admin/users/{user_id}/password", json={"password": "ResetPassword123!", "must_change_password": False})
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(alice.get("/api/auth/me").status_code, 401)
        self.assertEqual(alice.post("/api/auth/login", json={"username": "alice", "password": "ChangedPassword123!"}).status_code, 401)
        self.login(alice, "alice", "ResetPassword123!")
        sessions = self.admin.get(f"/api/admin/sessions?user_id={user_id}").json()["sessions"]
        self.assertEqual(self.admin.delete(f"/api/admin/sessions/{sessions[0]['id']}").status_code, 200)
        self.assertEqual(alice.get("/api/status").status_code, 401)
        self.login(alice, "alice", "ResetPassword123!")
        self.assertEqual(self.admin.patch(f"/api/admin/users/{user_id}", json={"display_name": "Alice", "role": "user", "enabled": False}).status_code, 200)
        self.assertEqual(alice.get("/api/status").status_code, 401)
        self.assertEqual(alice.post("/api/auth/login", json={"username": "alice", "password": "ResetPassword123!"}).status_code, 401)

    def test_csrf_origin_and_expiry_are_enforced(self):
        alice, _ = self.user()
        saved = alice.headers.pop("X-CSRF-Token")
        self.assertEqual(alice.post("/api/auth/logout").status_code, 403)
        alice.headers["X-CSRF-Token"] = saved
        self.assertEqual(alice.post("/api/auth/logout", headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(alice.post("/api/auth/logout", headers={"Origin": "http://127.0.0.1:invalid"}).status_code, 403)
        with storage.connection() as con:
            con.execute("UPDATE sessions SET expires_at=0 WHERE user_id=2")
        self.assertEqual(alice.get("/api/status").status_code, 401)

    def test_startup_recovers_jobs_and_password_reset_wins_login_race(self):
        alice, user_id = self.user()
        storage.save_job(user_id, "build", {"status": "running", "progress": {}})
        with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 123)) as started:
            self.assertEqual(started.get("/api/auth/setup-status").status_code, 200)
            self.assertEqual(storage.load_job(user_id, "build")["status"], "error")
        from backend.app.auth import password_hash
        real_verify = password_hash.verify
        replacement = password_hash.hash("ReplacementPass123!")
        def reset_during_verify(password, hashed):
            valid = real_verify(password, hashed)
            with storage.connection() as con:
                con.execute("UPDATE users SET password_hash=? WHERE id=?", (replacement, user_id))
                con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            return valid
        with patch.object(password_hash, "verify", reset_during_verify):
            response = alice.post("/api/auth/login", json={"username": "alice", "password": "ChangedPassword123!"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(alice.get("/api/status").status_code, 401)

    def test_admin_records_never_expose_passwords_or_session_secrets(self):
        alice, user_id = self.user()
        alice.get("/")
        response = self.admin.get("/api/admin/users")
        for user in response.json()["users"]:
            self.assertNotIn("password_hash", user)
            self.assertNotIn("password", user)
        with storage.connection() as con:
            hashed = con.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()[0]
        self.assertTrue(hashed.startswith("$argon2"))
        self.assertNotIn("ChangedPassword123!", hashed)
        for kind in ("login", "access", "audit"):
            result = self.admin.get(f"/api/admin/logs?kind={kind}").json()
            self.assertGreater(result["total"], 0)
            self.assertNotIn("Password123", json.dumps(result))
        sessions = self.admin.get("/api/admin/sessions").json()["sessions"]
        self.assertTrue(sessions)
        for session in sessions:
            self.assertNotIn("csrf_token", session)
            self.assertNotIn("token", session)
        self.assertEqual(self.admin.patch("/api/admin/users/1", json={"display_name": "管理员", "role": "user", "enabled": True}).status_code, 400)
        self.assertEqual(self.admin.patch("/api/admin/users/1", json={"display_name": "管理员", "role": "superadmin", "enabled": False}).status_code, 400)

    def test_initialization_is_one_time_and_local_and_legacy_migration_is_read_only(self):
        self.assertEqual(self.admin.post("/api/auth/setup", json={"username": "again", "password": "AnotherPassword123!"}).status_code, 409)
        second = self.root / "migration.sqlite3"
        original = {"active_dataset_id": "legacy", "datasets": {"legacy": {"id": "legacy", "name": "旧数据", "source_path": str(self.root / "old.csv"),
            "source_parquet": str(self.root / "old.parquet"), "source_manifest": str(self.root / "manifest.json"),
            "cleaned_parquet": str(self.root / "cleaned.parquet"), "cleaning_manifest": str(self.root / "cleaning.json")}}}
        storage.DATASET_REGISTRY_PATH.write_text(json.dumps(original), encoding="utf-8")
        before = storage.DATASET_REGISTRY_PATH.read_bytes()
        with patch.object(storage, "DB_PATH", second):
            storage.initialize()
            remote = TestClient(main.app, base_url="http://127.0.0.1", client=("192.168.1.10", 123))
            self.addCleanup(remote.close)
            self.assertEqual(remote.post("/api/auth/setup", json={"username": "root", "password": "AdminPassword123!"}).status_code, 403)
            local = self.client()
            self.assertEqual(local.post("/api/auth/setup", json={"username": "root", "password": "AdminPassword123!"}).json()["migrated_datasets"], 1)
            self.login(local, "root", "AdminPassword123!")
            result = local.get("/api/datasets").json()
            self.assertIsNone(result["active_dataset_id"])
            self.assertEqual(result["datasets"][0]["name"], "旧数据")
        self.assertEqual(storage.DATASET_REGISTRY_PATH.read_bytes(), before)

    def test_restart_marks_interrupted_jobs_and_admin_login_rejects_regular_user(self):
        alice, user_id = self.user()
        response = alice.post("/api/auth/login", headers={"X-Login-Target": "admin"}, json={"username": "alice", "password": "ChangedPassword123!"})
        self.assertEqual(response.status_code, 401)
        storage.save_job(user_id, "build", {"status": "running", "dataset_id": "example", "progress": {"percent": 12}})
        storage.recover_jobs()
        self.assertEqual(storage.load_job(user_id, "build")["status"], "error")
        self.assertEqual(self.admin.get("/api/status").json()["build_job"]["status"], "idle")


if __name__ == "__main__":
    unittest.main()
