"""Plan files in the dataset's own layout, as JSON.

A ResPlan plan is a dict: a few plain values (id, area, net_area, wall_depth, neighbor, ...) and one Shapely geometry per layer (wall, door, window, living,
bedroom, kitchen, bathroom, storage, stair, balcony, inner, ...). The JSON file keeps exactly these keys; each geometry is written as WKT text
(17 significant digits, so reading it back gives exactly the same coordinates):

    {"format": "resplan-plan-json/1", "id": 14433, "meta": {"area": ..., "net_area": ..., "wall_depth": ...},
     "layers": {"wall": "MULTIPOLYGON (((...)))", "door": "...", "bedroom": "...", ...}}

The stored NetworkX graph (plan["graph"]) is not written: the solver builds its own graph from the layers with the dataset helper (resplan_utils.plan_to_graph).
"""
import json

import shapely
from shapely import wkt

FORMAT = "resplan-plan-json/1"


def _plain(v):
    # numpy numbers and similar become plain Python values; anything that JSON cannot hold is dropped (returns the marker)
    if hasattr(v, "item") and not isinstance(v, (list, tuple, dict)):
        try:
            v = v.item()
        except Exception:
            pass
    try:
        json.dumps(v)
        return v
    except (TypeError, ValueError):
        return _plain.DROP


_plain.DROP = object()


def plan_to_dict(plan):
    layers, meta, skipped = {}, {}, []
    for k, v in plan.items():
        if isinstance(v, shapely.geometry.base.BaseGeometry):
            layers[k] = shapely.to_wkt(v, rounding_precision=17, trim=True)
            continue
        pv = _plain(v)
        if pv is _plain.DROP:
            skipped.append(k)
        else:
            meta[k] = pv
    out = {"format": FORMAT, "id": meta.pop("id", None), "meta": meta, "layers": layers}
    if skipped:
        out["not_written"] = skipped
    return out


def plan_from_dict(d):
    if not isinstance(d, dict) or "layers" not in d:
        raise ValueError("not a plan file: the key 'layers' is missing")
    if d.get("format") not in (None, FORMAT):
        raise ValueError(f"unknown format {d.get('format')!r}")
    plan = dict(d.get("meta") or {})
    plan["id"] = d.get("id", plan.get("id"))
    for k, text in d["layers"].items():
        try:
            plan[k] = wkt.loads(text) if text else None
        except Exception as err:
            raise ValueError(f"layer '{k}' is not valid WKT: {err}")
    for need in ("id", "area", "wall_depth", "inner", "wall"):
        if plan.get(need) is None:
            raise ValueError(f"the plan has no '{need}'")
    return plan


def plans_from_payload(payload):
    # one plan file, a list of plan files, or {"plans": [...]}
    if isinstance(payload, dict) and "plans" in payload:
        payload = payload["plans"]
    items = payload if isinstance(payload, list) else [payload]
    return items
