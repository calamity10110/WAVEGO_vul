"""Dashboard tests — auth, estop semantics, status shape, rate limit.

Runs against a simulated validator via FastAPI TestClient. Proves:
  - /health is open, everything else rejects bad tokens (401)
  - /api/status shape is complete and truthful
  - /api/estop is the only write and actually halts (prev_code -> 000000)
  - /api/estop is exempt from the rate limit; /api/status is not
  - audit tail reflects validator history
"""

import os
import sys
import tempfile
import time
import unittest
import yaml

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.dashboard import create_app, DASHBOARD_HTML
from agent.validator import Validator

TOKEN = "test-token-123"
CFG = {
    "motion": {
        "codebook": {
            "000000": {"name": "HALT", "verified": True,
                       "esp32": '{"T":111,"FB":0,"LR":0}'},
            "050300": {"name": "WALK", "verified": True,
                       "esp32": '{"T":111,"FB":1,"LR":0}'},
        },
        "forbidden_transitions": [],
        "max_speed_byte": 10,
    },
    "esp32": {"allow_unverified": False},
    "safety": {"battery_floor_pct": 15, "max_bad_codes": 100},
}


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cfg_path = os.path.join(cls.tmp.name, "state_table.yaml")
        with open(cfg_path, "w") as fh:
            yaml.dump(CFG, fh)
        cls.validator = Validator(cfg_path, simulate=True)
        app = create_app(cls.validator, TOKEN, broadcast_interval=0.05)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.validator.close()
        cls.tmp.cleanup()

    def test_01_health_open_no_auth(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_02_index_serves_html(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("ESTOP", r.text)
        self.assertEqual(r.text, DASHBOARD_HTML)

    def test_03_status_rejects_missing_token(self):
        r = self.client.get("/api/status")
        self.assertEqual(r.status_code, 401)

    def test_04_status_rejects_bad_token(self):
        r = self.client.get("/api/status", headers={"X-Auth-Token": "wrong"})
        self.assertEqual(r.status_code, 401)

    def test_05_status_shape(self):
        r = self.client.get("/api/status", headers={"X-Auth-Token": TOKEN})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for key in ("code", "name", "consecutive_bad", "battery_pct",
                    "simulate", "estop_count", "uptime_s"):
            self.assertIn(key, body)
        self.assertEqual(body["code"], "000000")
        self.assertEqual(body["name"], "HALT")
        self.assertTrue(body["simulate"])
        self.assertGreaterEqual(body["uptime_s"], 0)

    def test_06_audit_rejects_bad_token(self):
        r = self.client.get("/api/audit", headers={"X-Auth-Token": "nope"})
        self.assertEqual(r.status_code, 401)

    def test_07_audit_lists_history(self):
        self.validator.execute("050300", source="test")
        self.validator.halt(source="test")
        r = self.client.get("/api/audit?limit=10",
                            headers={"X-Auth-Token": TOKEN})
        self.assertEqual(r.status_code, 200)
        entries = r.json()["entries"]
        self.assertGreaterEqual(len(entries), 2)
        self.assertEqual(entries[0]["source"], "test")
        self.assertIn(entries[0]["outcome"], ("EXECUTE", "REJECT", "FORCED_HALT"))

    def test_08_estop_halts_robot(self):
        self.validator.execute("050300", source="test")
        self.assertEqual(self.validator.prev_code, "050300")
        r = self.client.post("/api/estop", headers={"X-Auth-Token": TOKEN})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(self.validator.prev_code, "000000")

    def test_09_estop_rejects_bad_token(self):
        r = self.client.post("/api/estop", headers={"X-Auth-Token": "evil"})
        self.assertEqual(r.status_code, 401)

    def test_10_estop_count_visible_in_status(self):
        r = self.client.get("/api/status", headers={"X-Auth-Token": TOKEN})
        self.assertGreaterEqual(r.json()["estop_count"], 1)

    def test_11_status_rate_limited_but_estop_not(self):
        app = create_app(self.validator, TOKEN)
        flood = TestClient(app)
        codes = [flood.get("/api/status",
                           headers={"X-Auth-Token": TOKEN}).status_code
                 for _ in range(35)]
        self.assertIn(429, codes)
        for _ in range(35):
            r = flood.post("/api/estop", headers={"X-Auth-Token": TOKEN})
            self.assertEqual(r.status_code, 200)

    def test_12_audit_limit_clamped(self):
        r = self.client.get("/api/audit?limit=9999",
                            headers={"X-Auth-Token": TOKEN})
        self.assertEqual(r.status_code, 200)
        self.assertLessEqual(len(r.json()["entries"]), 200)

    def test_13_ws_rejects_bad_token(self):
        with self.assertRaises(Exception):
            with self.client.websocket_connect("/ws?token=wrong"):
                pass

    def test_14_ws_streams_status(self):
        with self.client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            msg = ws.receive_json()
        self.assertEqual(msg["code"], "000000")
        self.assertIn("uptime_s", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
