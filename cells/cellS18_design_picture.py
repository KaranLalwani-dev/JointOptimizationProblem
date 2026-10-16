# Cell S18: solve one plan and draw it in the format of the reference schematic (slide 5).
#   show_design(plan_or_id)  -> solves the plan, draws it, returns (prep, result)
#   solve_plan(plan)         -> only the solving part (picks the one-hub or the multi-hub solver, retries without the device margin)
# Needs: Cells V1, P1, S1 (with the S1b patch if your S1 is the old one) and S11; `data` only when you pass a plan id instead of a plan.
# Picture: walls, doors (pale gaps in the walls), windows (blue), room labels with area; eligible rooms coloured by hub, excluded rooms grey ("no node");
# hubs = coloured squares H1.., devices = circles N1.. (numbered over the whole plan), cables = axis-aligned lines in the hub colour with an arrow into each
# device and a numbered circle on each leg (the last number is the return to the hub). Where cables run on top of each other (retraces, shared stretches)
# the runs are drawn 7 cm apart so that both are visible; this is display only, the checked geometry is not changed.
# Right side: per-hub summary and the validation summary (one line per item of statement 3.3, ticked or crossed from the validator's result).
import math, textwrap
import matplotlib.pyplot as plt
from matplotlib.path import Path
from matplotlib.patches import PathPatch, Circle, Rectangle, FancyArrowPatch
from matplotlib.colors import to_rgb
import matplotlib.patheffects as s18_pe
from shapely.geometry import LineString, Point

S18_HUB_COLORS = ["#2563c7", "#e8872a", "#2a9d3f", "#8e44ad", "#c0392b", "#16a2a2", "#8d6e63", "#d63384"]
S18_SHIFT_M = 0.07            # display shift between cables that share a line
S18_CHECKS = [                # (text of the statement-3.3 item, validator error codes that break it)
    ("Every eligible room has exactly one device", ("device_count", "device_not_in_room", "room_uncovered", "unknown_room", "empty")),
    ("Every device belongs to exactly one hub", ("room_double", "hub_no_devices")),
    ("Each zone connected through doors only", ("territory_disconnected",)),
    ("<= 100 m2 and <= 5 nodes per hub", ("capacity_area", "capacity_devices")),
    ("Each loop starts and ends at its own hub", ("ring_open", "hub_not_in_room")),
    ("Each device connected once, in visiting order", ("device_order",)),
    ("Axis-aligned cable segments only", ("not_axis_aligned",)),
    ("No wall, window or external area crossed", ("geometry_outside_space", "geometry_outside_inner")),
    ("Hub count is the minimum (our link rule)", ("hub_count_below_minimum",)),
]


# ---------------------------------------------------------------- solving
def solve_plan(plan, link_set="physical", seconds=60.0):
    # returns (prep, result); result["ok"] is True only for a plan that passed the validator
    pr = prepare(plan)
    if pr["n"] == 0:
        return pr, {"ok": False, "error": "no eligible rooms"}
    if not pr["hubs"]:
        return pr, {"ok": False, "error": "too many rooms for the exact hub count"}
    if pr["hubs"][link_set] == 1:
        res = solve_h1(plan, pr, dict(S_CFG, inset_m=0.1), link_set)
        if not res["ok"]:
            res2 = solve_h1(plan, pr, dict(S_CFG, inset_m=0.0), link_set)
            if res2["ok"]:
                res2["retry"] = "inset 0"
                res = res2
        return pr, res
    return pr, solve_multi(plan, pr, dict(S11_CFG, budget_s=seconds), link_set)


# ---------------------------------------------------------------- small geometry helpers
def s18_polys(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    if hasattr(g, "geoms"):
        return [q for x in g.geoms for q in s18_polys(x)]
    return []


def s18_fill(ax, g, **kw):
    for poly in s18_polys(g):
        verts, codes = [], []
        for ring in [poly.exterior] + list(poly.interiors):
            xy = list(ring.coords)
            verts += xy
            codes += [Path.MOVETO] + [Path.LINETO] * (len(xy) - 2) + [Path.CLOSEPOLY]
        ax.add_patch(PathPatch(Path(verts, codes), **kw))


def s18_light(color, k=0.80):
    return tuple(c + (1 - c) * k for c in to_rgb(color))


def s18_name(pr, i):
    nm = pr["ids"][i][len(pr["type"][i]) + 1:]
    return nm.replace("_", " ").title()


def s18_frac(a, b, q, tol):
    # where point q lies on segment a-b (0 to 1), or None if it is not on it
    dx, dy = b[0] - a[0], b[1] - a[1]
    d2 = dx * dx + dy * dy
    if d2 < 1e-18:
        return 0.0 if math.hypot(q[0] - a[0], q[1] - a[1]) <= tol else None
    f = ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / d2
    if f < -tol / math.sqrt(d2) or f > 1 + tol / math.sqrt(d2):
        return None
    f = min(max(f, 0.0), 1.0)
    return f if math.hypot(q[0] - (a[0] + f * dx), q[1] - (a[1] + f * dy)) <= tol else None


def s18_legs(path, stops, tol):
    # cut the closed path into legs: hub -> first device, ... , last device -> hub (devices matched in order, like the validator)
    pts = [tuple(map(float, p)) for p in v_clean(path, tol)]
    legs, cur, ti = [], [pts[0]], 0
    for a, b in zip(pts[:-1], pts[1:]):
        last_f = 0.0
        while ti < len(stops):
            f = s18_frac(a, b, stops[ti], tol)
            if f is None or f < last_f - 1e-9:
                break
            q = (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]))
            if math.hypot(q[0] - cur[-1][0], q[1] - cur[-1][1]) > 1e-9:
                cur.append(q)
            legs.append(cur)
            cur, last_f, ti = [q], f, ti + 1
        if math.hypot(b[0] - cur[-1][0], b[1] - cur[-1][1]) > 1e-9:
            cur.append(b)
    legs.append(cur)
    return legs


def s18_mid(pts):
    if len(pts) < 2:
        return pts[0]
    ln = LineString(pts)
    return ln.interpolate(ln.length / 2).coords[0]


# ---------------------------------------------------------------- drawing
def s18_side(ax, blocks, n_lines=62):
    # blocks: list of (title, [(text, colour, bold)]) drawn as boxes down the right-hand side (y in line units, 0 at the top)
    ax.set_xlim(0, 1)
    ax.set_ylim(n_lines, 0)
    ax.axis("off")
    y = 0.3
    for title, lines in blocks:
        wrapped = []
        for text, col, bold in lines:
            parts = textwrap.wrap(text, 46) or [""]
            wrapped += [(p, col, bold) for p in parts]
        h = 1.5 + 1.15 * len(wrapped) + 0.6
        if y + h > n_lines - 0.5:
            break
        ax.add_patch(Rectangle((0.0, y), 1.0, h, facecolor="#f4f6f9", edgecolor="#c7ccd4", linewidth=0.8))
        ax.text(0.04, y + 0.9, title, fontsize=9.5, fontweight="bold", color="#2b3440", va="center")
        ax.plot([0.04, 0.2], [y + 1.55, y + 1.55], color="#c8352f", linewidth=1.6)
        yy = y + 2.5
        for text, col, bold in wrapped:
            ax.text(0.04, yy, text, fontsize=7.6, color=col, fontweight="bold" if bold else "normal", va="center")
            yy += 1.15
        y += h + 0.7


def s18_label_spot(geo, marks):
    # where a room label goes: the corner of the room farthest from hubs and devices (the room centre if no corner is clear)
    bx = geo.bounds
    cands = [((bx[0] + 0.15, bx[3] - 0.15), "left", "top"), ((bx[2] - 0.15, bx[3] - 0.15), "right", "top"),
             ((bx[0] + 0.15, bx[1] + 0.15), "left", "bottom"), ((bx[2] - 0.15, bx[1] + 0.15), "right", "bottom")]
    c = geo.representative_point()
    best = None
    for pt, ha, va in cands:
        if not geo.buffer(0.01).contains(Point(pt)):
            continue
        clear = min([math.hypot(pt[0] - m[0], pt[1] - m[1]) for m in marks] or [9.9])
        if best is None or clear > best[0] + 1e-9:
            best = (clear, pt, ha, va)
    if best is None or best[0] < 0.9:
        cc = min([math.hypot(c.x - m[0], c.y - m[1]) for m in marks] or [9.9])
        if best is None or cc > best[0]:
            return (c.x, c.y), "center", "center"
    return best[1], best[2], best[3]


def draw_design(plan, pr, res, link_set="physical", save=None):
    s = pr["scale"]
    sol = res.get("solution")
    rep = res.get("report")
    hubs = sol["hubs"] if sol else []
    tol = CFG["tol"]
    wall = p_metres(plan.get("wall"), s)
    balconies = [p_metres(g, s) for g in p_parts(plan.get("balcony"))]
    balconies = [g for g in balconies if g is not None and not g.is_empty]
    xs0, ys0, xs1, ys1 = (wall.bounds if wall is not None and not wall.is_empty else pr["space"].bounds)
    for g in list(pr["geo"]) + balconies:
        b = g.bounds
        xs0, ys0, xs1, ys1 = min(xs0, b[0]), min(ys0, b[1]), max(xs1, b[2]), max(ys1, b[3])
    pad = 0.6
    xspan, yspan = xs1 - xs0 + 2 * pad, ys1 - ys0 + 2 * pad
    fig_h = min(12.0, max(6.5, 11.2 * yspan / xspan + 1.3))          # figure height follows the shape of the plan
    ax_h = fig_h - 1.1
    in_per_m = min(11.2 / xspan, ax_h / yspan)
    # marker size: normal, but smaller when a hub and a device (or two devices) are close together (a one-device ring is only 0.3 m long)
    marks = [tuple(h["point"]) for h in hubs] + [tuple(d["point"]) for h in hubs for d in h["devices"]]
    dist = [math.hypot(a[0] - c[0], a[1] - c[1]) for i, a in enumerate(marks) for c in marks[i + 1:]]
    dmin = min([d for d in dist if d > 0.02] or [9.9])
    r_node = max(0.10, min(min(0.30, max(0.13, 0.017 * max(xspan, yspan))), 0.44 * dmin))   # never below 0.10 m, so labels stay readable
    r_pts = r_node * in_per_m * 72
    f_node = min(8.0, max(4.5, 1.5 * r_pts))
    halo = [s18_pe.withStroke(linewidth=2.2, foreground="white")]

    fig = plt.figure(figsize=(17, fig_h))
    ax = fig.add_axes([0.01, 0.2 / fig_h, 0.66, ax_h / fig_h])
    side = fig.add_axes([0.685, 0.2 / fig_h, 0.305, ax_h / fig_h])

    # ---- floor plan: excluded rooms, eligible rooms (by hub colour), walls, doors, windows
    owner = {}
    for k, h in enumerate(hubs):
        for rid in h["rooms"]:
            owner[rid] = k
    for layer, label in (("bathroom", "Bathroom"), ("stair", "Stairs")):
        for part in [p_metres(g, s) for g in p_parts(plan.get(layer))]:
            if part is None or part.is_empty:
                continue
            s18_fill(ax, part, facecolor="#dcdfe3", edgecolor="#555555", linewidth=0.6, zorder=2)
            c = part.representative_point()
            ax.text(c.x, c.y, f"{label}\nno node", fontsize=7.5, color="#555555", ha="center", va="center", zorder=6, clip_on=True)
    for part in balconies:
        s18_fill(ax, part, facecolor="#f1f1f1", edgecolor="#777777", linewidth=0.7, linestyle="--", zorder=1)
        c = part.representative_point()
        ax.text(c.x, c.y, "Balcony\noutside", fontsize=7, color="#777777", ha="center", va="center", zorder=6, clip_on=True)
    for i in range(pr["n"]):
        k = owner.get(pr["ids"][i])
        face = s18_light(S18_HUB_COLORS[k % len(S18_HUB_COLORS)]) if k is not None else "#ffffff"
        s18_fill(ax, pr["geo"][i], facecolor=face, edgecolor="#555555", linewidth=0.6, zorder=2)
        pt, ha, va = s18_label_spot(pr["geo"][i], marks)
        ax.text(pt[0], pt[1], f"{s18_name(pr, i)}\n{pr['areas'][i]:.1f} m\u00b2", fontsize=7.8, color="#333333", ha=ha, va=va, zorder=6, clip_on=True)
    s18_fill(ax, wall, facecolor="#4a4f57", edgecolor="none", zorder=3)
    for g in [p_metres(d, s) for d in p_parts(plan.get("door"))]:
        s18_fill(ax, g, facecolor="#fbf3dc", edgecolor="#b08d57", linewidth=0.5, zorder=4)
    for g in [p_metres(d, s) for d in p_parts(plan.get("front_door"))]:
        s18_fill(ax, g, facecolor="#a86f1d", edgecolor="none", zorder=4)
    for g in [p_metres(d, s) for d in p_parts(plan.get("window"))]:
        s18_fill(ax, g, facecolor="#79b6f2", edgecolor="#2a6fb8", linewidth=0.5, zorder=4)

    # ---- cables, devices, hubs
    legs_all, hub_legs, node_no = [], [], 0
    for k, h in enumerate(hubs):
        legs = s18_legs(h["path"], [tuple(d["point"]) for d in h["devices"]], tol)
        hub_legs.append((k, len(legs_all), legs))
        legs_all += [(k, l) for l in legs]
    lines = [LineString(l) if len(l) >= 2 else None for k, l in legs_all]
    for j, (k, leg) in enumerate(legs_all):
        col = S18_HUB_COLORS[k % len(S18_HUB_COLORS)]
        core, m = leg, 0
        if lines[j] is not None:
            m = sum(1 for q in range(j) if lines[q] is not None and lines[j].intersection(lines[q]).length > 1e-3)
            if m:
                try:
                    off = lines[j].offset_curve(S18_SHIFT_M * (1 + min(m, 3)), join_style=2)
                    if off.geom_type == "LineString" and len(off.coords) >= 2:
                        core = list(off.coords)
                except Exception:
                    pass
        shown = ([leg[0]] + core + [leg[-1]]) if core is not leg else leg
        if len(shown) >= 2:
            ax.plot([p[0] for p in shown], [p[1] for p in shown], color=col, linewidth=2.2, solid_capstyle="round", zorder=8)
        legs_all[j] = (k, leg, core)
    for j, (k, leg, core) in enumerate(legs_all):
        col = S18_HUB_COLORS[k % len(S18_HUB_COLORS)]
        if len(core) >= 2:
            a, b = core[-2], core[-1]
            d = math.hypot(b[0] - a[0], b[1] - a[1])
            if d > 1.6 * r_node:
                ux, uy = (b[0] - a[0]) / d, (b[1] - a[1]) / d
                tip = (b[0] - ux * r_node * 1.15, b[1] - uy * r_node * 1.15)
                tail = (tip[0] - ux * min(0.55, 0.45 * d), tip[1] - uy * min(0.55, 0.45 * d))
                ax.add_patch(FancyArrowPatch(tail, tip, arrowstyle="-|>", mutation_scale=13, color=col, linewidth=0, zorder=9))
    for k, first, legs in hub_legs:
        col = S18_HUB_COLORS[k % len(S18_HUB_COLORS)]
        for q, leg in enumerate(legs):
            core = legs_all[first + q][2]
            if len(core) < 2 or LineString(core).length < 3.4 * r_node:
                continue                                                  # too short to carry a number (it would sit under the markers)
            c = s18_mid(core)
            ax.add_patch(Circle(c, r_node * 0.78, facecolor="white", edgecolor=col, linewidth=1.6, zorder=11))
            ax.text(c[0], c[1], str(q + 1), fontsize=max(4.5, f_node * 0.95), color="#222222", ha="center", va="center", zorder=12)
    for k, h in enumerate(hubs):
        col = S18_HUB_COLORS[k % len(S18_HUB_COLORS)]
        for d in h["devices"]:
            node_no += 1
            ax.add_patch(Circle(d["point"], r_node, facecolor=col, edgecolor="white", linewidth=1.2, zorder=13))
            ax.text(d["point"][0], d["point"][1], f"N{node_no}", fontsize=f_node, color="white", fontweight="bold", ha="center", va="center", zorder=14)
        hx, hy = h["point"]
        ax.add_patch(Rectangle((hx - r_node * 1.05, hy - r_node * 1.05), r_node * 2.1, r_node * 2.1, facecolor=col, edgecolor="#1b1b1b", linewidth=1.2, zorder=15))
        ax.text(hx, hy, f"H{k + 1}", fontsize=f_node, color="white", fontweight="bold", ha="center", va="center", zorder=16)
    # ---- callout for a one-device ring (hub and device only ~0.3 m apart, the markers touch): label with a leader line, placed in free floor space
    first_no = 0
    for k, h in enumerate(hubs):
        first_no += len(h["devices"])
        if len(h["devices"]) != 1:
            continue
        dp, hp = h["devices"][0]["point"], h["point"]
        if math.hypot(dp[0] - hp[0], dp[1] - hp[1]) > 0.45:
            continue
        col = S18_HUB_COLORS[k % len(S18_HUB_COLORS)]
        room = pr["geo"][pr["ids"].index(h["rooms"][0])] if h["rooms"][0] in pr["ids"] else None
        cx, cy = (hp[0] + dp[0]) / 2, (hp[1] + dp[1]) / 2
        best = None
        for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1), (0, 1), (0, -1), (1, 0), (-1, 0)):
            q = (cx + dx * 1.1, cy + dy * 0.7)
            if room is not None and not room.buffer(-0.25).contains(Point(q)):
                continue
            clear = min([math.hypot(q[0] - m[0], q[1] - m[1]) for m in marks if m is not hp and m is not dp] or [9.9])
            if best is None or clear > best[0]:
                best = (clear, q)
        q = best[1] if best else (cx + 1.1, cy + 0.7)
        ax.annotate(f"H{k + 1} + N{first_no}\none-device ring\n{math.hypot(dp[0] - hp[0], dp[1] - hp[1]) * 2:.1f} m of cable", xy=(cx, cy), xytext=q, fontsize=7, color="#222222",
                    ha="center", va="center", zorder=17, clip_on=True,
                    bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor=col, linewidth=1.2),
                    arrowprops=dict(arrowstyle="-", color=col, linewidth=1.2, shrinkA=0, shrinkB=6))
    ax.set_xlim(xs0 - pad, xs1 + pad)
    ax.set_ylim(ys0 - pad, ys1 + pad)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_anchor("NW")

    # ---- headline
    m = rep["metrics"] if rep else None
    status = "VALID" if rep and rep["valid"] else "INVALID"
    scol = "#1b7f2a" if status == "VALID" else "#c0392b"
    y1, y2 = 1 - 0.40 / fig_h, 1 - 0.78 / fig_h
    if m:
        extra = f" (+{m['X_conservative'] - m['X']} touches)" if m["X_conservative"] > m["X"] else ""
        fig.text(0.012, y1, f"Plan {pr['id']}: {len(hubs)} hub{'s' if len(hubs) != 1 else ''}, J = {m['J']:.2f}", fontsize=19, fontweight="bold", color="#2b3440", va="center")
        fig.text(0.012, y2, f"L {m['L']:.2f} m  +  5 x X ({m['X']}){extra}  +  O {m['O']:.2f} m  +  0.5 x B ({m['B']})    |    {status}", fontsize=10.5, color=scol, va="center")
    else:
        fig.text(0.012, y1, f"Plan {pr['id']}: no design produced", fontsize=19, fontweight="bold", color="#c0392b", va="center")
        fig.text(0.012, y2, str(res.get("error", ""))[:140], fontsize=10.5, color="#c0392b", va="center")

    # ---- side panels
    codes = {c for c, _ in rep["errors"]} if rep else set()
    n_el, area = pr["n"], sum(pr["areas"])
    lb = max(math.ceil(n_el / 5), math.ceil(area / 100))
    hub_lines = []
    per = {h["id"]: h for h in rep["hubs"]} if rep else {}
    for k, h in enumerate(hubs):
        names = ", ".join(s18_name(pr, pr["ids"].index(r)) for r in h["rooms"] if r in pr["ids"])
        ph = per.get(h["id"])
        hub_lines.append((f"H{k + 1}   {names}", S18_HUB_COLORS[k % len(S18_HUB_COLORS)], True))
        if ph:
            hub_lines.append((f"      {ph['area']:.1f} m\u00b2 | {ph['devices']} node{'s' if ph['devices'] != 1 else ''} | {ph['L']:.1f} m | {ph['B']} bends", "#333333", False))
    exact = pr["hubs"].get(link_set)
    summary = [("Hubs: %d  (lower bound %d, exact minimum %s; %d rooms, %.1f m\u00b2)" % (len(hubs), lb, exact, n_el, area), "#333333", False)]
    if rep and rep.get("area_gap") is not None and len(hubs) > 1:
        summary.append(("Assigned-area gap between hubs: %.1f m\u00b2" % rep["area_gap"], "#333333", False))
    if not rep:
        summary.append(("Validation: no design to check", "#c0392b", True))
    else:
        failed = [text for text, bad in S18_CHECKS if codes & set(bad)]
        if not failed and rep["valid"]:
            summary.append(("Validation: all hard constraints satisfied \u2713", "#1b7f2a", True))
        else:
            summary += [("\u2717 " + t, "#c0392b", True) for t in failed] or [("Validation failed", "#c0392b", True)]
    notes = [(f"flag: {f}", "#8a5a00", False) for f in pr["flags"]]
    if rep:
        notes += [(f"warning: {c}", "#8a5a00", False) for c in sorted({c for c, _ in rep["warnings"]})]
        notes += [(f"ERROR {c}", "#c0392b", True) for c in sorted(codes)]
    if res.get("retry"):
        notes.append((f"solved after retry: {res['retry']}", "#8a5a00", False))
    blocks = [("How to read", [("\u25a0 Hub (one colour per zone)", "#333333", False), ("\u25cf Node (fixed point in each eligible room)", "#333333", False),
                               ("\u2192 Cable; numbers give the visiting order", "#333333", False), ("\u25cb Last number is the return to the hub", "#333333", False),
                               ("\u25a2 Grey room: excluded, no node. Pale gaps in walls are doors", "#333333", False)]),
              ("Per-hub summary", hub_lines or [("no design", "#c0392b", True)]),
              ("Totals and validation", summary)]
    if notes:
        blocks.append(("Flags and warnings", notes))
    s18_side(side, blocks, n_lines=ax_h / 0.13)
    if save:
        fig.savefig(save, dpi=130, bbox_inches="tight")
    plt.show()


def show_design(plan_or_id, link_set="physical", seconds=60.0, save=None):
    plan = plan_or_id
    if not isinstance(plan_or_id, dict):
        plan = next((p for p in data if p.get("id") == plan_or_id), None)
        if plan is None:
            print(f"plan {plan_or_id} is not in `data`")
            return None, None
    pr, res = solve_plan(plan, link_set, seconds)
    if pr["n"] == 0:
        print(f"plan {plan.get('id')}: no eligible rooms, nothing to draw")
        return pr, res
    print(f"plan {pr['id']}: {'valid' if res.get('ok') else 'NOT valid'} | {res.get('seconds', 0):.1f} s | {res.get('error', '')}")
    draw_design(plan, pr, res, link_set, save)
    return pr, res
