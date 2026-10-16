"""Turns a prepared plan (and, if there is one, a solution) into plain JSON that the web page draws.

Everything is in metres, in the same axes as the input (x to the right, y up). The page only draws; all geometry decisions (cable offsets, label corners,
marker size, one-device callouts) are made here, using the same helper functions as the notebook picture (cell S18), so both pictures agree.
"""
import math

from shapely.geometry import LineString, Point

from core import NS

HUB_COLORS = ["#2563c7", "#e8872a", "#2a9d3f", "#8e44ad", "#c0392b", "#16a2a2", "#8d6e63", "#d63384"]
SHIFT_M = 0.07            # display shift between cables that share a line (display only)


def _polys(g):
    return NS["s18_polys"](g)


def _rings(g, nd=3):
    # a geometry as a list of polygons, each a list of rings (outer ring first), each a list of [x, y]
    out = []
    for poly in _polys(g):
        out.append([[[round(x, nd), round(y, nd)] for x, y in ring.coords] for ring in [poly.exterior] + list(poly.interiors)])
    return out


def _layer(plan, key, s):
    g = []
    for part in NS["p_parts"](plan.get(key)):
        m = NS["p_metres"](part, s)
        if m is not None and not m.is_empty:
            g += _rings(m)
    return g


def _legs_display(h, tol):
    # cut a ring into legs and shift legs that run on top of each other (display only), like the notebook picture
    legs = NS["s18_legs"](h["path"], [tuple(d["point"]) for d in h["devices"]], tol)
    return legs


def build_scene(plan, pr, res=None):
    s = pr["scale"]
    sol = (res or {}).get("solution")
    rep = (res or {}).get("report")
    hubs = sol["hubs"] if sol else []
    tol = NS["CFG"]["tol"]
    wall = NS["p_metres"](plan.get("wall"), s)
    balc = [NS["p_metres"](g, s) for g in NS["p_parts"](plan.get("balcony"))]
    balc = [g for g in balc if g is not None and not g.is_empty]
    x0, y0, x1, y1 = (wall.bounds if wall is not None and not wall.is_empty else pr["space"].bounds)
    for g in list(pr["geo"]) + balc:
        b = g.bounds
        x0, y0, x1, y1 = min(x0, b[0]), min(y0, b[1]), max(x1, b[2]), max(y1, b[3])
    span = max(x1 - x0, y1 - y0) + 1.2

    marks = [tuple(h["point"]) for h in hubs] + [tuple(d["point"]) for h in hubs for d in h["devices"]]
    dist = [math.hypot(a[0] - c[0], a[1] - c[1]) for i, a in enumerate(marks) for c in marks[i + 1:]]
    dmin = min([d for d in dist if d > 0.02] or [9.9])
    r_node = max(0.10, min(min(0.30, max(0.13, 0.017 * span)), 0.44 * dmin))

    owner = {rid: k for k, h in enumerate(hubs) for rid in h["rooms"]}
    rooms = []
    for i in range(pr["n"]):
        pt, ha, va = NS["s18_label_spot"](pr["geo"][i], marks)
        rooms.append({"id": pr["ids"][i], "type": pr["type"][i], "name": NS["s18_name"](pr, i), "area": round(pr["areas"][i], 2),
                      "polys": _rings(pr["geo"][i]), "hub": owner.get(pr["ids"][i]),
                      "label": {"x": round(pt[0], 3), "y": round(pt[1], 3), "ha": ha, "va": va}})
    excluded = []
    for layer, label in (("bathroom", "Bathroom"), ("stair", "Stairs")):
        for part in NS["p_parts"](plan.get(layer)):
            m = NS["p_metres"](part, s)
            if m is None or m.is_empty:
                continue
            c = m.representative_point()
            excluded.append({"label": label, "polys": _rings(m), "x": round(c.x, 3), "y": round(c.y, 3)})
    balconies = []
    for m in balc:
        c = m.representative_point()
        balconies.append({"polys": _rings(m), "x": round(c.x, 3), "y": round(c.y, 3)})

    # cables: legs with display shift, number positions
    legs_all, hub_out, node_no = [], [], 0
    for k, h in enumerate(hubs):
        legs = _legs_display(h, tol)
        legs_all += [(k, q, leg) for q, leg in enumerate(legs)]
    lines = [LineString(leg) if len(leg) >= 2 else None for _, _, leg in legs_all]
    shown = []
    for j, (k, q, leg) in enumerate(legs_all):
        core, m = leg, 0
        if lines[j] is not None:
            m = sum(1 for z in range(j) if lines[z] is not None and lines[j].intersection(lines[z]).length > 1e-3)
            if m:
                try:
                    off = lines[j].offset_curve(SHIFT_M * (1 + min(m, 3)), join_style=2)
                    if off.geom_type == "LineString" and len(off.coords) >= 2:
                        core = list(off.coords)
                except Exception:
                    pass
        disp = ([leg[0]] + core + [leg[-1]]) if core is not leg else leg
        shown.append((disp, core))
    jj = 0
    names_all = []
    for k, h in enumerate(hubs):
        names = []
        for d in h["devices"]:
            node_no += 1
            names.append(f"N{node_no}")
        names_all.append(names)
    for k, h in enumerate(hubs):
        legs_out = []
        seq = [f"H{k + 1}"] + names_all[k] + [f"H{k + 1}"]
        n_legs = len(h["devices"]) + 1
        for q in range(n_legs):
            disp, core = shown[jj]
            ln = LineString(core).length if len(core) >= 2 else 0.0
            mid = NS["s18_mid"](core) if len(core) >= 2 and ln >= 3.4 * r_node else None
            legs_out.append({"from": seq[q], "to": seq[q + 1], "order": q + 1, "length": round(ln, 3),
                             "display": [[round(p[0], 3), round(p[1], 3)] for p in disp],
                             "mid": [round(mid[0], 3), round(mid[1], 3)] if mid else None,
                             "arrow": _arrow(core, r_node)})
            jj += 1
        dev_out = [{"name": names_all[k][i], "room": d["room"], "x": round(d["point"][0], 3), "y": round(d["point"][1], 3)} for i, d in enumerate(h["devices"])]
        callout = _callout(h, k, names_all[k], pr, marks)
        per = next((p for p in (rep or {}).get("hubs", []) if p["id"] == h["id"]), None)
        hub_out.append({"index": k, "name": f"H{k + 1}", "color": HUB_COLORS[k % len(HUB_COLORS)], "x": round(h["point"][0], 3), "y": round(h["point"][1], 3),
                        "rooms": h["rooms"], "room_names": [NS["s18_name"](pr, pr["ids"].index(r)) for r in h["rooms"] if r in pr["ids"]],
                        "devices": dev_out, "legs": legs_out, "callout": callout,
                        "area": round(per["area"], 2) if per else None, "length": round(per["L"], 2) if per else None, "bends": per["B"] if per else None})
    return {"plan_id": plan.get("id"), "bounds": [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)], "r_node": round(r_node, 3),
            "layers": {"wall": _rings(wall), "door": _layer(plan, "door", s), "front_door": _layer(plan, "front_door", s), "window": _layer(plan, "window", s)},
            "rooms": rooms, "excluded": excluded, "balconies": balconies, "hubs": hub_out}


def _arrow(core, r_node):
    # arrow head into a device: tip just before the device marker, direction of the last stretch
    if len(core) < 2:
        return None
    a, b = core[-2], core[-1]
    d = math.hypot(b[0] - a[0], b[1] - a[1])
    if d <= 1.6 * r_node:
        return None
    ux, uy = (b[0] - a[0]) / d, (b[1] - a[1]) / d
    tip = (b[0] - ux * r_node * 1.15, b[1] - uy * r_node * 1.15)
    return {"x": round(tip[0], 3), "y": round(tip[1], 3), "dx": round(ux, 4), "dy": round(uy, 4)}


def _callout(h, k, names, pr, marks):
    # a label for a one-device ring, whose hub and device markers touch
    if len(h["devices"]) != 1:
        return None
    dp, hp = h["devices"][0]["point"], h["point"]
    gap = math.hypot(dp[0] - hp[0], dp[1] - hp[1])
    if gap > 0.45:
        return None
    room = pr["geo"][pr["ids"].index(h["rooms"][0])] if h["rooms"][0] in pr["ids"] else None
    cx, cy = (hp[0] + dp[0]) / 2, (hp[1] + dp[1]) / 2
    best = None
    for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1), (0, 1), (0, -1), (1, 0), (-1, 0)):
        q = (cx + dx * 1.1, cy + dy * 0.7)
        if room is not None and not room.buffer(-0.25).contains(Point(q)):
            continue
        clear = min([math.hypot(q[0] - m[0], q[1] - m[1]) for m in marks if m != tuple(hp) and m != tuple(dp)] or [9.9])
        if best is None or clear > best[0]:
            best = (clear, q)
    q = best[1] if best else (cx + 1.1, cy + 0.7)
    return {"x": round(q[0], 3), "y": round(q[1], 3), "tx": round(cx, 3), "ty": round(cy, 3), "text": f"H{k + 1} + {names[0]}", "sub": f"one-device ring, {gap * 2:.1f} m of cable"}


def build_panel(pr, res, link_set):
    # numbers and checks for the side panel
    rep = (res or {}).get("report")
    sol = (res or {}).get("solution")
    n_el, area = pr["n"], sum(pr["areas"])
    lb = max(math.ceil(n_el / 5), math.ceil(area / 100))
    panel = {"rooms": n_el, "area": round(area, 1), "lower_bound": lb, "exact_min": pr["hubs"].get(link_set) if pr["hubs"] else None, "flags": list(pr["flags"]),
             "link_set": link_set}
    if not rep:
        panel.update({"solved": False, "error": str((res or {}).get("error", "")) or None})
        return panel
    codes = {c for c, _ in rep["errors"]}
    checks = [{"text": text, "ok": not (codes & set(bad))} for text, bad in NS["S18_CHECKS"]]
    m = rep["metrics"]
    panel.update({"solved": True, "valid": bool(rep["valid"]), "hubs": len(sol["hubs"]), "J": round(m["J"], 3), "L": round(m["L"], 3), "X": m["X"], "X_touch": m["X_conservative"],
                  "O": round(m["O"], 3), "B": m["B"], "area_gap": round(rep["area_gap"], 2) if rep.get("area_gap") is not None else None,
                  "checks": checks, "errors": sorted(codes), "warnings": sorted({c for c, _ in rep["warnings"]}), "retry": (res or {}).get("retry"),
                  "seconds": round((res or {}).get("seconds", 0.0), 2)})
    return panel
