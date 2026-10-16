# Cell S19: JSON output in the format of slide 8 (one dict per plan), a round-trip check, and a save helper.
#   export_plan(plan_or_id, folder="outputs")  -> solves the plan, writes plan_<id>.json and plan_<id>.png into the folder, prints one summary line,
#                                                 returns (json_dict, check_report)
#   plan_to_json(plan, pr, res)                -> the dict only (no files)
#   check_json(js, pr, link_set)               -> reads the dict back (not our solver's objects), runs the validator on it, compares J and lengths
# Needs: Cells V1, P1, S1, S11 and S18 (S18 gives solve_plan, show_design, s18_legs).
# Format (slide 8 keys kept as they are): plan_id, units, hubs[ hub_id, room_id, location{x,y}, assigned_rooms, assigned_nodes, closed_sequence,
#   route_segments[ from, to, length_m, polyline ], ring_length_m ], reported_metrics{hub_count, total_length_m}.
# Our additions (extra keys, none of the slide's keys are changed): scale_units_per_m, nodes[ node_id, room_id, location ], our_metrics, flags, status.
# Node names N1.. run over the whole plan (the same numbers as in the picture), so a node name is unique inside the file.
# Coordinates: metres, in the same axes as the input (input drawing units divided by the plan's scale; no shift, no flip) - see organizer question 12.
import json, os, math
from shapely.geometry import Point


def s19_corners(pts):
    # keep only the turning points of a leg (a point where the cable continues in the same direction is dropped; a reversal is kept)
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, b, c = out[-1], pts[i], pts[i + 1]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        dot = (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1])
        if abs(cross) < 1e-12 and dot > 0:
            continue
        out.append(b)
    out.append(pts[-1])
    return out


def s19_len(pts):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts[:-1], pts[1:]))


def plan_to_json(plan, pr, res, link_set="physical", nd=6):
    pid = plan.get("id")
    pid = int(pid) if isinstance(pid, (int, float)) or hasattr(pid, "item") else pid
    rep, sol = res.get("report"), res.get("solution")
    base = {"plan_id": pid, "units": "m", "scale_units_per_m": round(float(pr["scale"]), 9)}
    if not sol or not rep:
        base.update({"status": "failed", "error": str(res.get("error", "no solution")), "hubs": [], "reported_metrics": {"hub_count": 0, "total_length_m": 0.0},
                     "flags": list(pr.get("flags", []))})
        return base
    tol = CFG["tol"]
    R = lambda v: round(float(v), nd)
    hubs_out, nodes_out, node_no, total = [], [], 0, 0.0
    for k, h in enumerate(sol["hubs"]):
        hid = f"H{k + 1}"
        names = []
        for d in h["devices"]:
            node_no += 1
            names.append(f"N{node_no}")
            nodes_out.append({"node_id": names[-1], "room_id": d["room"], "location": {"x": R(d["point"][0]), "y": R(d["point"][1])}})
        legs = s18_legs(h["path"], [tuple(d["point"]) for d in h["devices"]], tol)
        if len(legs) != len(names) + 1:
            raise ValueError(f"plan {pid} {hid}: the ring could not be cut into {len(names) + 1} legs (found {len(legs)})")
        seq = [hid] + names + [hid]
        hp = tuple(h["point"])
        stops = [hp] + [tuple(d["point"]) for d in h["devices"]] + [hp]
        segs, ring = [], 0.0
        for q, leg in enumerate(legs):
            pts = [stops[q]] + [tuple(p) for p in leg[1:-1]] + [stops[q + 1]]     # exact hub and device points at the ends of each leg
            pts = [tuple(R(c) for c in p) for p in s19_corners(pts)]
            ln = R(s19_len(pts))
            ring += ln
            segs.append({"from": seq[q], "to": seq[q + 1], "length_m": ln, "polyline": [[p[0], p[1]] for p in pts]})
        room = next((r for r in h["rooms"] if r in pr["ids"] and pr["geo"][pr["ids"].index(r)].buffer(tol).contains(Point(hp))), None)
        if room is None:
            room = min(h["rooms"], key=lambda r: pr["geo"][pr["ids"].index(r)].distance(Point(hp)))
        total += ring
        hubs_out.append({"hub_id": hid, "room_id": room, "location": {"x": R(hp[0]), "y": R(hp[1])}, "assigned_rooms": list(h["rooms"]),
                         "assigned_nodes": names, "closed_sequence": seq, "route_segments": segs, "ring_length_m": R(ring)})
    m = rep["metrics"]
    base.update({"hubs": hubs_out, "nodes": nodes_out,
                 "reported_metrics": {"hub_count": len(hubs_out), "total_length_m": R(total)},
                 "our_metrics": {"J": R(m["J"]), "L": R(m["L"]), "crossings": m["X"], "crossings_with_touches": m["X_conservative"], "overlap_m": R(m["O"]), "bends": m["B"],
                                 "valid_by_our_validator": bool(rep["valid"]), "link_rule": link_set},
                 "flags": list(pr.get("flags", [])), "status": "ok" if rep["valid"] else "invalid"})
    return base


def check_json(js, pr, link_set="physical"):
    # rebuild a solution from the JSON alone, run the validator on it, and compare with what the file reports
    if js.get("status") == "failed":
        return {"ok": False, "problems": ["no solution in the file"], "report": None}
    problems = []
    node = {n["node_id"]: n for n in js["nodes"]}
    hubs = []
    for h in js["hubs"]:
        path = []
        for s in h["route_segments"]:
            path += [tuple(p) for p in s["polyline"]] if not path else [tuple(p) for p in s["polyline"][1:]]
            if abs(s["length_m"] - s19_len(s["polyline"])) > 2e-6 * max(1, len(s["polyline"])):
                problems.append(f"{h['hub_id']}: segment {s['from']}->{s['to']} length does not match its polyline")
        if h["closed_sequence"][0] != h["hub_id"] or h["closed_sequence"][-1] != h["hub_id"] or h["closed_sequence"][1:-1] != h["assigned_nodes"]:
            problems.append(f"{h['hub_id']}: closed_sequence does not match assigned_nodes")
        if abs(h["ring_length_m"] - sum(s["length_m"] for s in h["route_segments"])) > 1e-5:
            problems.append(f"{h['hub_id']}: ring_length_m is not the sum of its segments")
        devs = [{"id": n, "room": node[n]["room_id"], "point": (node[n]["location"]["x"], node[n]["location"]["y"])} for n in h["assigned_nodes"]]
        hubs.append({"id": h["hub_id"], "point": (h["location"]["x"], h["location"]["y"]), "rooms": h["assigned_rooms"], "devices": devs, "path": path})
    rep = validate(to_context(pr, link_set), {"hubs": hubs}, expected_h=pr["hubs"][link_set])
    if not rep["valid"]:
        problems += [f"validator: {c}" for c, _ in rep["errors"]]
    om = js.get("our_metrics", {})
    if rep["valid"] and abs(rep["metrics"]["J"] - om.get("J", rep["metrics"]["J"])) > 1e-3:
        problems.append(f"J from the file {rep['metrics']['J']:.4f} differs from the solver's {om['J']:.4f}")
    if abs(js["reported_metrics"]["total_length_m"] - sum(h["ring_length_m"] for h in js["hubs"])) > 1e-5:
        problems.append("total_length_m is not the sum of the ring lengths")
    return {"ok": not problems, "problems": problems, "report": rep}


def export_plan(plan_or_id, folder="outputs", link_set="physical", seconds=60.0, picture=True):
    plan = plan_or_id
    if not isinstance(plan_or_id, dict):
        plan = next((p for p in data if p.get("id") == plan_or_id), None)
        if plan is None:
            print(f"plan {plan_or_id} is not in `data`")
            return None, None
    os.makedirs(folder, exist_ok=True)
    pr, res = solve_plan(plan, link_set, seconds)
    js = plan_to_json(plan, pr, res, link_set) if pr["n"] else {"plan_id": plan.get("id"), "units": "m", "status": "failed", "error": "no eligible rooms", "hubs": [],
                                                                  "reported_metrics": {"hub_count": 0, "total_length_m": 0.0}}
    chk = check_json(js, pr, link_set) if pr["n"] else {"ok": False, "problems": ["no eligible rooms"], "report": None}
    name = os.path.join(folder, f"plan_{js['plan_id']}")
    with open(name + ".json", "w") as f:
        json.dump(js, f, indent=1)
    if picture and pr["n"]:
        draw_design(plan, pr, res, link_set, save=name + ".png")
    om = js.get("our_metrics", {})
    print(f"plan {js['plan_id']}: status {js['status']} | hubs {js['reported_metrics']['hub_count']} | total length {js['reported_metrics']['total_length_m']:.2f} m | J {om.get('J', float('nan')):.2f} | "
          f"file check {'OK' if chk['ok'] else 'PROBLEMS: ' + '; '.join(chk['problems'])} | saved {name}.json")
    return js, chk
