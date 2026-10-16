"""The plan file layout: a plan written as JSON and read back must give identical geometry and the same plain values. No real data needed."""
import json
import sys
from pathlib import Path

import pytest
from shapely.geometry import MultiPolygon, Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from plan_codec import plan_from_dict, plan_to_dict, plans_from_payload  # noqa: E402


def sample_plan():
    return {"id": 14433, "area": 103.123456789012345, "net_area": 0.0, "wall_depth": 0.2306594264587892, "neighbor": None,
            "inner": box(0, 0, 13.1, 12.1), "wall": MultiPolygon([Polygon([(-0.2, -0.2), (13.3, -0.2), (13.3, 12.3), (-0.2, 12.3)], [[(0, 0), (0, 12.1), (13.1, 12.1), (13.1, 0)]])]),
            "living": Polygon([(0.1, 0.1), (5.000000000000001, 0.1), (5.0, 3.3333333333333335)]), "window": None, "graph": object()}


def test_round_trip_is_exact():
    p = sample_plan()
    d = json.loads(json.dumps(plan_to_dict(p)))
    q = plan_from_dict(d)
    for k in ("inner", "wall", "living"):
        assert p[k].equals_exact(q[k], 0.0), k
    assert q["id"] == 14433 and q["area"] == p["area"] and q["wall_depth"] == p["wall_depth"]
    assert d["not_written"] == ["graph"]


def test_refuses_incomplete_file():
    with pytest.raises(ValueError):
        plan_from_dict({"layers": {"wall": "POLYGON ((0 0, 1 0, 1 1, 0 0))"}, "id": 1, "meta": {}})
    with pytest.raises(ValueError):
        plan_from_dict({"id": 1})


def test_payload_shapes():
    d = plan_to_dict(sample_plan())
    assert plans_from_payload(d) == [d]
    assert plans_from_payload([d, d]) == [d, d]
    assert plans_from_payload({"plans": [d]}) == [d]
