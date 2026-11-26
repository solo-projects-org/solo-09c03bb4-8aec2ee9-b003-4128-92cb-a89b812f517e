from __future__ import annotations

import unittest

try:
    from fastapi.testclient import TestClient
    from app.main import app

    _CLIENT = TestClient(app)
except Exception:  # pragma: no cover - dependencies are installed in Docker/CI.
    TestClient = None
    _CLIENT = None


@unittest.skipIf(_CLIENT is None, "FastAPI test dependencies are not installed")
class ApiTests(unittest.TestCase):
    def test_health_and_alias(self):
        response = _CLIENT.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

        payload = {
            "row_totals": [2, 2, 2],
            "column_totals": [3, 3],
            "fixed_cells": [{"row": 0, "column": 1, "shifts": 2}],
            "baseline": {
                "measured_shifts": [[2, 2], [2, 2], [2, 2]],
                "on_time_shifts": [[1, 1], [1, 1], [1, 1]],
            },
            "current": {
                "measured_shifts": [[2, 2], [2, 2], [2, 2]],
                "on_time_shifts": [[1, 1], [2, 1], [1, 2]],
            },
        }
        for path in ["/api/v1/assessments", "/evaluate", "/api/v1/evaluate"]:
            response = _CLIENT.post(path, json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["status"], "ok")
            self.assertEqual(body["minimum"]["fraction"], "1/12")
            self.assertEqual(body["maximum"]["fraction"], "1/4")
            self.assertEqual(body["min"]["fraction"], "1/12")
            self.assertEqual(body["max"]["fraction"], "1/4")
            self.assertEqual(body["minimum"]["schedule"], [[0, 2], [1, 1], [2, 0]])
            self.assertEqual(body["maximum"]["schedule"], [[0, 2], [2, 0], [1, 1]])

    def test_missing_measurements_returns_all_blocked_cells(self):
        payload = {
            "row_totals": [2, 2],
            "column_totals": [2, 2],
            "baseline": {
                "measured_shifts": [[4, 0], [2, 2]],
                "on_time_shifts": [[3, 0], [1, 2]],
            },
            "current": {
                "measured_shifts": [[0, 5], [2, 2]],
                "on_time_shifts": [[0, 4], [2, 1]],
            },
        }
        response = _CLIENT.post("/api/v1/assessments", json=payload)
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["status"], "missing_measurements")
        self.assertEqual({(x["row"], x["column"]) for x in body["blocked_cells"]}, {(0, 0), (0, 1)})
        self.assertNotIn("minimum", body)
        self.assertNotIn("maximum", body)

    def test_schedule_conflict_returns_409(self):
        payload = {
            "row_totals": [1, 1],
            "column_totals": [2, 0],
            "fixed_cells": [{"row": 0, "column": 0, "shifts": 0}],
            "baseline": {
                "measured_shifts": [[1, 0], [1, 0]],
                "on_time_shifts": [[1, 0], [1, 0]],
            },
            "current": {
                "measured_shifts": [[1, 0], [1, 0]],
                "on_time_shifts": [[1, 0], [1, 0]],
            },
        }
        response = _CLIENT.post("/api/v1/assessments", json=payload)
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["status"], "schedule_conflict")
        self.assertTrue(body["conflicts"])

    def test_invalid_on_time_count_returns_422(self):
        payload = {
            "row_totals": [1],
            "column_totals": [1],
            "baseline": {"measured_shifts": [[1]], "on_time_shifts": [[2]]},
            "current": {"measured_shifts": [[1]], "on_time_shifts": [[1]]},
        }
        response = _CLIENT.post("/api/v1/assessments", json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["status"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
