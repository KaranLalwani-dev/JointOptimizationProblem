# Cell V1: validator and metrics (everything in metres; needs only shapely, no dataset).
# One solution = {"hubs": [{"id": "H1", "point": (x, y), "rooms": [room ids of the territory],
#                           "devices": [{"id": "N1", "room": room id, "point": (x, y)}, ...]   # visiting order
#                           "path": [(x, y), ...]}]}                                          # closed polyline, starts and ends at the hub
# One context = {"rooms": {id: {"type", "geom", "area"}}, "links": {frozenset({a, b})}, "space": allowed region or None, "inner": region or None}
import math
from shapely.geometry import Point, LineString
from shapely.prepared import prep

CFG = {"tol": 0.001, "max_devices": 5, "max_area": 100.0,
       "w_len": 1.0, "w_cross": 5.0, "w_overlap": 1.0, "w_bend": 0.5,
       "hub_in_own_territory": False}


def make_context(rooms, links, space=None, inner=None):
    # rooms: {id: (type, polygon in metres)}; links: iterable of (a, b)
    ctx_rooms = {rid: {"type": t, "geom": g, "area": g.area} for rid, (t, g) in rooms.items()}
    return {"rooms": ctx_rooms, "links": {frozenset(l) for l in links}, "space": space, "inner": inner}


def v_dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def v_clean(path, tol):
    # drop repeated points (closer than the tolerance are the same point)
    out = [tuple(path[0])]
    for p in path[1:]:
        if v_dist(p, out[-1]) > tol:
            out.append(tuple(p))
    return out


def v_segments(points, tol):
    # axis-aligned segments as dicts; same-direction collinear pieces are merged; returns (segments, bad segment indices)
    segs, bad = [], []
    for k in range(len(points) - 1):
        (x0, y0), (x1, y1) = points[k], points[k + 1]
        dx, dy = x1 - x0, y1 - y0
        if abs(dy) <= tol:
            seg = {"o": "h", "c": (y0 + y1) / 2, "lo": min(x0, x1), "hi": max(x0, x1), "d": 1 if dx > 0 else -1}
        elif abs(dx) <= tol:
            seg = {"o": "v", "c": (x0 + x1) / 2, "lo": min(y0, y1), "hi": max(y0, y1), "d": 1 if dy > 0 else -1}
        else:
            bad.append(k)
            continue
        last = segs[-1] if segs else None
        if last and last["o"] == seg["o"] and last["d"] == seg["d"] and abs(last["c"] - seg["c"]) <= tol:
            last["lo"], last["hi"] = min(last["lo"], seg["lo"]), max(last["hi"], seg["hi"])
        else:
            segs.append(seg)
    return segs, bad


def v_bends(segs):
    # 90-degree turn or reversal = 1, straight = 0; the joint at the hub (last -> first) is included
    n = len(segs)
    if n < 2:
        return 0
    return sum(1 for i in range(n) if segs[i]["o"] != segs[(i + 1) % n]["o"] or segs[i]["d"] != segs[(i + 1) % n]["d"])


def v_overlap(all_segs, tol):
    # a stretch covered by k legs adds (k - 1) * length; legs on lines within the tolerance are on the same line
    total = 0.0
    for o in ("h", "v"):
        group = sorted((s for s in all_segs if s["o"] == o), key=lambda s: s["c"])
        cluster = []
        for s in group + [None]:
            if s is not None and (not cluster or s["c"] - cluster[-1]["c"] <= tol):
                cluster.append(s)
                continue
            if len(cluster) > 1:
                cuts = sorted({v for t in cluster for v in (t["lo"], t["hi"])})
                for a, b in zip(cuts, cuts[1:]):
                    if b - a <= tol:
                        continue
                    mid = (a + b) / 2
                    k = sum(1 for t in cluster if t["lo"] <= mid <= t["hi"])
                    if k > 1:
                        total += (k - 1) * (b - a)
            cluster = [s] if s is not None else []
    return total


def v_merge_points(points, tol):
    out = []
    for p in points:
        if not any(v_dist(p, q) <= tol for q in out):
            out.append(p)
    return out


def v_crossings(rings, tol):
    # strict: proper crossings away from every node and hub; conservative: also touches and crossings at a third ring's node
    strict, cons = [], []
    entries = [(ri, si, s) for ri, r in enumerate(rings) for si, s in enumerate(r["segs"])]
    for a in range(len(entries)):
        for b in range(a + 1, len(entries)):
            ra, ia, sa = entries[a]
            rb, ib, sb = entries[b]
            if sa["o"] == sb["o"]:
                continue
            if ra == rb:
                n = len(rings[ra]["segs"])
                if (ia - ib) % n in (1, n - 1):
                    continue            # consecutive legs meet at their own vertex: a bend, not a crossing
            h, v = (sa, sb) if sa["o"] == "h" else (sb, sa)
            px, py = v["c"], h["c"]
            if not (h["lo"] - tol <= px <= h["hi"] + tol and v["lo"] - tol <= py <= v["hi"] + tol):
                continue
            proper = (h["lo"] + tol < px < h["hi"] - tol) and (v["lo"] + tol < py < v["hi"] - tol)
            point = (px, py)
            own_nodes = rings[ra]["nodes"] + (rings[rb]["nodes"] if rb != ra else [])
            if any(v_dist(point, q) <= tol for q in own_nodes):
                continue
            cons.append(point)
            if proper and not any(v_dist(point, q) <= tol for r in rings for q in r["nodes"]):
                strict.append(point)
    return len(v_merge_points(strict, tol)), len(v_merge_points(cons, tol))


def v_metrics(rings, cfg=CFG):
    tol = cfg["tol"]
    length = sum(s["hi"] - s["lo"] for r in rings for s in r["segs"])
    bends = sum(v_bends(r["segs"]) for r in rings)
    overlap = v_overlap([s for r in rings for s in r["segs"]], tol)
    x_strict, x_cons = v_crossings(rings, tol)
    j = cfg["w_len"] * length + cfg["w_cross"] * x_strict + cfg["w_overlap"] * overlap + cfg["w_bend"] * bends
    return {"L": length, "X": x_strict, "X_conservative": x_cons, "O": overlap, "B": bends, "J": j}


def v_visits(points, target, tol):
    # path positions (metres along the polyline) where the polyline passes the target point
    pos, run = [], 0.0
    for k in range(len(points) - 1):
        a, b = points[k], points[k + 1]
        seg = v_dist(a, b)
        t = max(0.0, min(1.0, ((target[0] - a[0]) * (b[0] - a[0]) + (target[1] - a[1]) * (b[1] - a[1])) / (seg * seg)))
        q = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        if v_dist(q, target) <= tol:
            pos.append(run + t * seg)
        run += seg
    merged = []
    for p in pos:
        if not merged or p - merged[-1] > 2 * tol:
            merged.append(p)
    return merged


def v_connected(room_ids, links):
    room_ids = list(room_ids)
    if not room_ids:
        return False
    seen, stack = {room_ids[0]}, [room_ids[0]]
    while stack:
        cur = stack.pop()
        for other in room_ids:
            if other not in seen and frozenset({cur, other}) in links:
                seen.add(other)
                stack.append(other)
    return len(seen) == len(room_ids)


def validate(ctx, sol, cfg=CFG, expected_h=None):
    tol = cfg["tol"]
    errors, warnings = [], []
    rooms, links = ctx["rooms"], ctx["links"]
    hubs = sol.get("hubs", [])
    err = lambda code, msg: errors.append((code, msg))
    warn = lambda code, msg: warnings.append((code, msg))
    space = prep(ctx["space"].buffer(tol)) if ctx.get("space") is not None else None
    inner = prep(ctx["inner"].buffer(tol)) if ctx.get("inner") is not None else None
    if not hubs:
        err("empty", "no hubs")
    owner = {}
    for h in hubs:
        for r in h["rooms"]:
            if r not in rooms:
                err("unknown_room", f"{h['id']}: room {r} is not an eligible room")
            elif r in owner:
                err("room_double", f"room {r} assigned to {owner[r]} and {h['id']}")
            else:
                owner[r] = h["id"]
    for r in rooms:
        if r not in owner:
            err("room_uncovered", f"eligible room {r} is assigned to no hub")
    rings, per_hub, areas = [], [], []
    for h in hubs:
        hid, devs = h["id"], h.get("devices", [])
        area = sum(rooms[r]["area"] for r in h["rooms"] if r in rooms)
        areas.append(area)
        if not devs:
            err("hub_no_devices", f"{hid} serves no device")
        if len(devs) > cfg["max_devices"]:
            err("capacity_devices", f"{hid}: {len(devs)} devices (max {cfg['max_devices']})")
        if area > cfg["max_area"] + 1e-9:
            err("capacity_area", f"{hid}: {area:.2f} m2 (max {cfg['max_area']})")
        if not v_connected(h["rooms"], links):
            err("territory_disconnected", f"{hid}: rooms {sorted(h['rooms'])} are not connected through links")
        if sorted(d["room"] for d in devs) != sorted(h["rooms"]):
            err("device_count", f"{hid}: devices {[d['room'] for d in devs]} do not match territory {h['rooms']} (one device per room)")
        for d in devs:
            room = rooms.get(d["room"])
            if room is None or not room["geom"].buffer(tol).covers(Point(d["point"])):
                err("device_not_in_room", f"{hid}/{d['id']}: point {d['point']} is not inside room {d['room']}")
        hub_rooms = [r for r, v in rooms.items() if v["geom"].buffer(tol).covers(Point(h["point"]))]
        if not hub_rooms:
            err("hub_not_in_room", f"{hid}: hub point {h['point']} is not inside any eligible room")
        elif not set(hub_rooms) & set(h["rooms"]):
            (err if cfg["hub_in_own_territory"] else warn)("hub_outside_territory", f"{hid}: hub is in {hub_rooms}, outside its own territory")
        path = h.get("path", [])
        if len(path) < 3 or v_dist(path[0], h["point"]) > tol or v_dist(path[-1], h["point"]) > tol:
            err("ring_open", f"{hid}: the cable path must start and end at the hub point")
            continue
        pts = v_clean(path, tol)
        segs, bad = v_segments(pts, tol)
        if bad:
            err("not_axis_aligned", f"{hid}: segments {bad} are not horizontal or vertical")
            continue
        if len(segs) < 2:
            err("ring_open", f"{hid}: the cable path has no closed ring")
            continue
        prev = 0.0
        for d in devs:
            pos = v_visits(pts, d["point"], tol)
            nxt = [p for p in pos if p >= prev - tol]
            if not nxt:
                err("device_order", f"{hid}/{d['id']}: the path does not reach this device after the previous one (skipped or out of order)")
                break
            prev = nxt[0]
            if len(pos) > 1:
                warn("device_passed_twice", f"{hid}/{d['id']}: the path passes this device point {len(pos)} times")
        for k in range(len(pts) - 1):
            line = LineString([pts[k], pts[k + 1]])
            if space is not None and not space.covers(line):
                err("geometry_outside_space", f"{hid}: leg {k} {pts[k]} -> {pts[k + 1]} leaves the allowed routing space (wall, window or outside)")
            if inner is not None and not inner.covers(line):
                err("geometry_outside_inner", f"{hid}: leg {k} {pts[k]} -> {pts[k + 1]} leaves the building's inner polygon")
        rings.append({"id": hid, "segs": segs, "nodes": [tuple(h["point"])] + [tuple(d["point"]) for d in devs]})
        m = v_metrics([rings[-1]], cfg)
        per_hub.append({"id": hid, "rooms": list(h["rooms"]), "area": area, "devices": len(devs), "L": m["L"], "B": m["B"]})
    if expected_h is not None and hubs:
        if len(hubs) < expected_h:
            err("hub_count_below_minimum", f"{len(hubs)} hubs is below the minimum {expected_h}")
        elif len(hubs) > expected_h:
            warn("hub_count_not_minimal", f"{len(hubs)} hubs, minimum is {expected_h}")
    metrics = v_metrics(rings, cfg) if rings and len(rings) == len(hubs) else None
    return {"valid": not errors, "errors": errors, "warnings": warnings, "H": len(hubs), "metrics": metrics,
            "hubs": per_hub, "area_gap": (max(areas) - min(areas)) if len(areas) > 1 else 0.0}


def show(report):
    print(f"{'VALID' if report['valid'] else 'INVALID'} | H = {report['H']}")
    for code, msg in report["errors"]:
        print(f"  ERROR   {code}: {msg}")
    for code, msg in report["warnings"]:
        print(f"  warning {code}: {msg}")
    m = report["metrics"]
    if m:
        print(f"  L {m['L']:.3f} m | X {m['X']} (conservative {m['X_conservative']}) | O {m['O']:.3f} m | B {m['B']} | J {m['J']:.3f}")
    for h in report["hubs"]:
        print(f"  {h['id']}: rooms {h['rooms']} | {h['area']:.1f} m2 | {h['devices']} devices | L {h['L']:.2f} m | B {h['B']}")
