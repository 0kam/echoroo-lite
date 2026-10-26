"""Run catalog checks, or --real-recording PATH for the real M0 smoke test."""
import argparse
import io
import json
import math
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from server import create_app
from storage import SCHEMA_VERSION, CatalogError, connect, initialize, preview_folder, recording_time
from predictions import perch_candidates

ROOT = Path(__file__).resolve().parents[2]


class SidecarCheck(unittest.TestCase):
    def test_fresh_database_gets_the_current_schema_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertTrue(initialize(root))
            with connect(root) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_incompatible_database_starts_but_only_health_is_available(self):
        for version in (0, SCHEMA_VERSION + 1):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                with closing(sqlite3.connect(root / "echoroo.sqlite")) as db:
                    with db:
                        db.execute("CREATE TABLE old_data (id INTEGER PRIMARY KEY)")
                        db.execute(f"PRAGMA user_version={version}")
                headers = {"Authorization": "Bearer test-token", "Origin": "http://localhost:1420"}
                with TestClient(create_app(root, ROOT / "poc/models", "test-token"), headers=headers) as client:
                    health = client.get("/health")
                    self.assertEqual(health.status_code, 200)
                    self.assertEqual(health.json()["status"], "data_incompatible")
                    self.assertEqual(health.json()["detail"], "data_incompatible")
                    self.assertEqual(health.json()["data_folder"], str(root))
                    response = client.get("/catalog")
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json(), {"detail": "data_incompatible"})

    def test_unexpected_endpoint_error_is_json_with_cors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            headers = {"Authorization": "Bearer test-token", "Origin": "http://localhost:1420"}
            app = create_app(root, ROOT / "poc/models", "test-token")
            errors = io.StringIO()
            with redirect_stderr(errors), patch("server.list_species", side_effect=RuntimeError("test failure")):
                with TestClient(app, headers=headers, raise_server_exceptions=False) as client:
                    response = client.get("/species", params={"q": "test"})
            self.assertEqual(response.status_code, 500)
            self.assertEqual(response.json(), {"detail": "unknown_error"})
            self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:1420")
            self.assertIn("RuntimeError: test failure", errors.getvalue())

    def test_cancelled_import_preview_stops_before_inspection(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);initialize(root);sf.write(root/'20240808_060000.wav',np.zeros(1000),1000)
            stopped=threading.Event();stopped.set()
            with self.assertRaisesRegex(CatalogError,'cancelled'):
                preview_folder(root,root,'Asia/Tokyo',stopped)
            with connect(root) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM recordings').fetchone()[0],0)

    def test_name_relevance_and_complete_perch_score_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with TestClient(create_app(root, ROOT / "poc/models", "test-token"),
                            headers={"Authorization": "Bearer test-token"}) as client:
                for query, name in [("ウグイス", "Horornis diphone"), ("キバシリ", "Certhia familiaris"),
                                    ("CERTHIA FAMILIARIS", "Certhia familiaris")]:
                    self.assertEqual(client.get("/species", params={"q": query}).json()[0]["scientific_name"], name)
                for i in range(60):
                    client.post("/species", json={"scientific_name": f"A test {i:02}", "en": f"Needle longer {i}"})
                client.post("/species", json={"scientific_name": "Z test", "en": "Needle"})
                client.post("/species", json={"scientific_name": "B test", "en": "A Needle"})
                found = client.get("/species", params={"q": "needle"}).json()
                self.assertEqual(len(found), 50)
                self.assertEqual(found[0]["scientific_name"], "Z test")
                self.assertTrue(found[1]["common_names"]["en"].startswith("Needle"))
            labels = np.array([f"Foreign {i}" for i in range(6)] + ["Horornis diphone", "Certhia familiaris"])
            logits = np.array([[1000, 999, 998, 997, 996, 995, 994, -1000], [-1000] * 6 + [0, 0]], dtype=np.float32)
            archive = root / "scores.npz"
            np.savez_compressed(archive, logits=logits, labels=labels)
            original = archive.read_bytes()
            candidates, meta = perch_candidates(archive, 7, 1, {"species": ["Horornis diphone", "Certhia familiaris"]})
            self.assertEqual(meta["display_species_count"], 2)
            self.assertEqual(len(candidates), 4)
            self.assertTrue(all(0 <= c["score"] <= 1 and math.isfinite(c["score"]) for c in candidates))
            self.assertEqual(candidates[0]["score"], 0.5)
            self.assertEqual((candidates[0]["start_s"], candidates[0]["end_s"]), (5, 7))
            # Recover an allowed Japanese species that was below the unfiltered top 5.
            recovered = next(c for c in candidates if c["label"] == "Horornis diphone" and c["start_s"] == 0)
            self.assertAlmostEqual(recovered["score"], math.exp(-6) / sum(math.exp(-i) for i in range(7)))
            self.assertEqual(perch_candidates(archive, 7, 1, {"species": []})[0], [])
            missing, status = perch_candidates(archive, 7, 1, None)
            self.assertEqual((missing, status["display_candidate_filter"]), ([], "unavailable"))
            self.assertEqual(archive.read_bytes(), original)

    def test_species_names_preserve_identity_and_original_predictions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            # Exercise addition to an existing catalog schema, without reanalysis.
            app = create_app(root, ROOT / "poc/models", "test-token")
            with TestClient(app, headers={"Authorization": "Bearer test-token"}) as client:
                found = client.get("/species", params={"q": "キバシリ"}).json()
                treecreeper = next(s for s in found if s["scientific_name"] == "Certhia familiaris")
                self.assertEqual(treecreeper["common_names"]["en"].casefold(), "eurasian treecreeper")
                original_id = treecreeper["id"]
                updated = client.post("/species", json={"scientific_name": "Certhia familiaris", "ja": "試験用の一般名"}).json()
                self.assertEqual(updated["id"], original_id)
                self.assertEqual(updated["common_names"]["ja"], "試験用の一般名")
                self.assertEqual(updated["name_sources"]["ja"], "user")
                archive = root / "perch.npz"
                np.savez_compressed(archive, logits=np.array([[0.5, 0.4, 0.4]]),
                                    labels=np.array(["Certhia familiaris", "Abavorana luctuosa", "Bowed_string_instrument"]))
                with connect(root) as db:
                    db.execute("INSERT INTO sites VALUES(1,'Test','Asia/Tokyo',35,138)")
                    db.execute("INSERT INTO datasets VALUES(1,1,'Test','/test')")
                    db.execute("INSERT INTO recordings VALUES(1,1,'/test.wav','2024-08-08T06:00:00','Asia/Tokyo','2024-08-07T21:00:00+00:00',3,24000,144044,0)")
                    db.execute("INSERT INTO jobs(id,dataset_id,status,total) VALUES(1,1,'complete',2)")
                    for aid, engine, label in [(1, "birdnet", "Certhia familiaris_Eurasian Treecreeper"), (2, "perch", "Certhia familiaris")]:
                        meta = {"species": [label]} if engine == "birdnet" else {"score_type": "logit", "candidate_filter": {"species": ["Certhia familiaris"]}}
                        db.execute("INSERT INTO analyses VALUES(?,1,1,?,?,?)", (aid, engine, json.dumps(meta), str(archive)))
                        db.execute("INSERT INTO candidates(analysis_id,start_s,end_s,label,score) VALUES(?,0,3,?,0.5)", (aid, label))
                    for label in ["Abavorana luctuosa", "Bowed_string_instrument"]:
                        db.execute("INSERT INTO candidates(analysis_id,start_s,end_s,label,score) VALUES(2,0,3,?,0.4)", (label,))
                client.put("/models", json={"main":"perch","extra":["birdnet"]})
                results = client.get("/recordings/1/results").json()
                for result in results:
                    candidate = result["candidates"][0]
                    self.assertEqual(candidate["species_id"], original_id)
                    self.assertEqual(candidate["common_names"]["ja"], "試験用の一般名")
                filtered = next(r for r in results if r["engine"] == "perch")
                self.assertEqual(filtered["metadata"]["display_candidate_filter"], "applied")
                self.assertEqual(len(filtered["candidates"]), 1)
                perch = filtered
                self.assertEqual(perch["candidates"][0]["raw_score"], 0.5)
                self.assertAlmostEqual(perch["candidates"][0]["score"], math.exp(0.5) / (math.exp(0.5) + 2 * math.exp(0.4)))
                reset = client.post("/species", json={"scientific_name": "Certhia familiaris"}).json()
                self.assertEqual(reset["id"], original_id)
                self.assertEqual(reset["common_names"]["ja"], "キバシリ")
                self.assertEqual(reset["name_sources"]["ja"], "bundled")
                custom = client.post("/species", json={"scientific_name": "Example species", "en": "Test species"}).json()
                self.assertEqual(custom["name_sources"]["en"], "user")
                self.assertEqual(client.post("/species", json={"scientific_name": "   "}).status_code, 422)
            with TestClient(create_app(root, ROOT / "poc/models", "test-token"), headers={"Authorization": "Bearer test-token"}) as client:
                self.assertEqual(client.get("/species", params={"q": "Example species"}).json()[0]["id"], custom["id"])
                candidate = client.get("/recordings/1/results").json()[0]["candidates"][0]
                self.assertEqual(candidate["label"], "Certhia familiaris_Eurasian Treecreeper")
                self.assertEqual(candidate["score"], 0.5)
                self.assertEqual(candidate["species_id"], original_id)

    def test_perch_only_analysis_uses_japan_candidates(self):
        class Session:
            def run(self, outputs, inputs):
                return np.ones((1, 1536), dtype=np.float32), np.array([[2, 1]], dtype=np.float32)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            models = root / "models"
            (models / "perch").mkdir(parents=True)
            (models / "perch/perch_v2_no_dft.onnx").write_bytes(b"test")
            (models / "perch-labels/assets").mkdir(parents=True)
            (models / "perch-labels/assets/labels.csv").write_text(
                "inat2024_fsd50k\nHorornis diphone\nAbavorana luctuosa\n", encoding="utf-8")
            audio = root / "SMOKE_20240808_060000.wav"
            sf.write(audio, np.zeros(160000, dtype=np.float32), 32000)
            data = root / "data"
            with TestClient(create_app(data, models, "test"), headers={"Authorization": "Bearer test"}) as client:
                imported = client.post("/imports", json={"folder": str(root), "site_name": "Test", "dataset_name": "Test",
                    "timezone": "Asia/Tokyo", "latitude": 35, "longitude": 138}).json()
                record = client.get(f"/datasets/{imported['dataset_id']}/recordings").json()[0]
                with connect(data) as db:
                    job = db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(?,'queued',1)",
                                     (imported["dataset_id"],)).lastrowid
                from analysis import run
                with patch("analysis.load_perch", return_value=(Session(), "perch-sha")):
                    run(data, models, job, [record], {"latitude": 35, "longitude": 138}, threading.Event())
                self.assertEqual(client.get("/catalog").json()["jobs"][0]["status"], "complete")
                results = client.get(f"/recordings/{record['id']}/results").json()
                self.assertEqual([result["engine"] for result in results], ["perch"])
                self.assertEqual({row["label"] for row in results[0]["candidates"]}, {"Horornis diphone"})

    def test_import_time_auth_duplicate_and_source_integrity(self):
        self.assertEqual(recording_time("rec_20240808_060000.wav", "Asia/Tokyo")[1], "2024-08-07T21:00:00+00:00")
        for stamp, code in [("20241103_013000", "ambiguous_time"), ("20240310_023000", "nonexistent_time")]:
            with self.assertRaisesRegex(CatalogError, code):
                recording_time(f"rec_{stamp}.wav", "America/New_York")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "audio"
            folder.mkdir()
            path = folder / "rec_20240808_060000.wav"
            sf.write(path, np.zeros(24000), 24000)
            sf.write(folder / "invalid.wav", np.zeros(24000), 24000)
            app = create_app(root / "data", ROOT / "poc/models", "test-token")
            with TestClient(app) as client:
                self.assertEqual(client.get("/catalog").status_code, 401)
                client.headers["Authorization"] = "Bearer test-token"
                body = dict(folder=str(folder), site_name="Test", dataset_name="August", timezone="Asia/Tokyo", latitude=35, longitude=138)
                preview = client.post("/imports/preview", json=body).json()
                self.assertEqual(preview["count"], 1)
                self.assertEqual(preview["first"], preview["last"])
                # The folder can be checked before the site is named.
                self.assertEqual(client.post("/imports/preview", json={"folder": str(folder), "timezone": "Asia/Tokyo"}).json()["count"], 1)
                self.assertEqual(preview["skipped"][0]["code"], "filename_time")
                imported = client.post("/imports", json=body)
                self.assertEqual(imported.status_code, 200, imported.text)
                recordings = client.get(f"/datasets/{imported.json()['dataset_id']}/recordings").json()
                self.assertEqual(recordings[0]["recorded_at"], "2024-08-07T21:00:00+00:00")
                self.assertEqual(client.post("/imports/preview", json=body).json()["count"], 0)
                other_folder=root/'other-site';other_folder.mkdir()
                copy=other_folder/path.name;copy.write_bytes(path.read_bytes())
                other=client.post('/imports',json={**body,'folder':str(other_folder),'site_name':'Other site'})
                self.assertEqual(other.status_code,200,other.text)
                other_records=client.get(f"/datasets/{other.json()['dataset_id']}/recordings").json()
                self.assertEqual(other_records[0]['path'],str(copy.resolve()))
                self.assertEqual(len(client.get('/catalog').json()['sites']),2)
                self.assertEqual(client.get(f"/recordings/{recordings[0]['id']}/audio", headers={"Range": "bytes=0-43"}).status_code, 206)
                sf.write(path, np.zeros(48000), 24000)
                self.assertEqual(client.get(f"/recordings/{recordings[0]['id']}/audio").json()["detail"], "source_changed")
                self.assertEqual(client.post("/imports/preview", json={**body, "timezone": "invalid"}).status_code, 400)


def real_smoke(path):
    data = ROOT / ".local-data/m0-smoke"
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        # Import the resolved original file; do not duplicate the field recording.
        (folder / path.name).symlink_to(path.resolve())
        app = create_app(data, ROOT / "poc/models", "smoke-token")
        started = time.perf_counter()
        with TestClient(app, headers={"Authorization": "Bearer smoke-token"}) as client:
            body = dict(folder=str(folder), site_name="試験地点", dataset_name="2024年8月・実音源試験", timezone="Asia/Tokyo",
                        latitude=35.49152777777778, longitude=138.20491666666666)
            imported = client.post("/imports", json=body)
            if imported.status_code == 200:
                dataset = imported.json()["dataset_id"]
            else:
                assert imported.json().get("detail") == "no_audio", imported.text
                dataset = client.get("/catalog").json()["datasets"][0]["id"]
            record = client.get(f"/datasets/{dataset}/recordings").json()[0]
            response = client.post("/jobs", json={"dataset_id": dataset, "recording_id": record["id"]})
            assert response.status_code == 200, response.text
            while True:
                job = client.get("/catalog").json()["jobs"][0]
                if job["status"] not in {"queued", "running"}:
                    break
                assert time.perf_counter() - started < 240, job
                time.sleep(1)
            assert job["status"] == "complete", job
            results = client.get(f"/recordings/{record['id']}/results").json()
            assert {r["engine"] for r in results} == {m['id'] for m in client.get('/models').json()['models'] if m['enabled'] and m['available']}, results
            for result in results:
                assert result["candidates"], result["engine"]
                with np.load(result["scores_path"], allow_pickle=False) as stored:
                    if result["engine"] == "perch":
                        assert stored["logits"].shape == (int(np.ceil(record["seconds"] / 5)), 14795)
                        assert np.isfinite(stored["logits"]).all()
                        allowed = set(result["metadata"]["candidate_filter"]["species"])
                        assert all(c["label"] in allowed and 0 <= c["score"] <= 1 for c in result["candidates"])
                        assert result["metadata"]["display_candidate_filter"] == "applied"
                    else:
                        assert np.isfinite(stored["scores"]).all()
                        assert len(set(stored["labels"])) == len(result["metadata"]["species"])
            with connect(data) as db:
                count = db.execute("SELECT count(*) FROM segments WHERE analysis_id=?", (next(r["id"] for r in results if r["engine"] == "perch"),)).fetchone()[0]
                assert count == int(np.ceil(record["seconds"] / 5)), count
                vector = db.execute("SELECT rowid,embedding FROM vectors LIMIT 1").fetchone()
                match = db.execute("SELECT rowid,distance FROM vectors WHERE embedding MATCH ? AND k=1 ORDER BY distance", (vector["embedding"],)).fetchone()
                assert abs(match["distance"]) < 1e-5, dict(match)
            analysis_seconds = time.perf_counter() - started
            searched = time.perf_counter()
            response = client.post('/search', json=dict(dataset_id=dataset, source_kind='recording',
                source_id=record['id'], start_s=678, end_s=681, limit=30))
            assert response.status_code == 200, response.text
            search = response.json()
            assert len(search['hits']) == 30 and search['searched_recordings'] == 1
            assert all(-1 <= h['similarity'] <= 1 and not (h['start_s'] < 681 and h['end_s'] > 678) for h in search['hits'])
            assert search['hits'] == sorted(search['hits'], key=lambda h: (-h['similarity'], h['segment_id']))
            assert search['query']['windows'][0]['padding_s'] == 2
            assert client.get(f"/sources/recording/{record['id']}/spectrogram?start_s=670&end_s=700").status_code == 200
            print(json.dumps({"job": job, "recording_seconds": record["seconds"], "wall_seconds": round(analysis_seconds, 2),
                              "search_seconds": round(time.perf_counter() - searched, 2),
                              "search_top": [{k: h[k] for k in ('start_s', 'end_s', 'similarity')} for h in search['hits'][:3]],
                              "vectors": count, "candidates_displayed": {r["engine"]: len(r["candidates"]) for r in results}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-recording", type=Path)
    args = parser.parse_args()
    if args.real_recording:
        real_smoke(args.real_recording)
    else:
        unittest.main(argv=[__file__])
