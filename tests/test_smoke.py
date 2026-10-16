"""Smoke test of the API. Runs only when a plan file is available (backend/data/ResPlan.pkl or RESPLAN_PKL) and resplan_utils can be imported.
   Run from the repository root:  python -m pytest -q
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
PKL = Path(os.environ.get("RESPLAN_PKL") or ROOT / "backend" / "data" / "ResPlan.pkl")
pytestmark = pytest.mark.skipif(not PKL.exists(), reason="no plan file")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from backend.app import app
    with TestClient(app) as c:
        yield c


def test_status_ready(client):
    st = client.get("/api/status").json()
    assert st["ready"], st


def test_solve_first_plan(client):
    pid = client.get("/api/plans?limit=1").json()["plans"][0]["id"]
    v = client.post(f"/api/plans/{pid}/solve", json={"seconds": 20, "force": True}).json()
    assert v["panel"]["solved"] and v["panel"]["valid"]
    assert v["file_check"]["ok"], v["file_check"]
    assert v["design"]["reported_metrics"]["hub_count"] == len(v["scene"]["hubs"])
