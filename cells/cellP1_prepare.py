# Cell P1: prepare(plan) turns a ResPlan plan into everything the solver and the validator need (all geometry in metres).
# Needs: resplan_utils (Cell 25) and Cell V1 (make_context). Own names (prefix p_), does not touch other cells.
import math
import shapely
from shapely.affinity import scale as p_scale
from shapely.ops import unary_union
from shapely.validation import make_valid
import resplan_utils as ru

P_CFG = {"margin_m": 0.03,          # routing-space shrink away from walls
         "min_area_m2": 0.1,        # a room needs this much usable floor to hold a device
         "aligned_share": 0.95,     # inner counts as aligned if this share of the eligible-room area lies inside it
         "inner_tol_m": 0.001,      # 1 mm tolerance when cutting by inner
         "max_rooms_dp": 14}        # the hub-count search handles up to this many eligible rooms
P_ELIG = {"living", "bedroom", "kitchen", "storage"}
P_INTERIOR = P_ELIG | {"bathroom"}  # rooms a cable may pass through (stairs excluded on purpose)


def p_fix(g):
    if g is None or g.is_empty:
        return None
    return g if g.is_valid else make_valid(g)


def p_union(gs):
    gs = [p_fix(g) for g in gs]
    gs = [g for g in gs if g is not None]
    return unary_union(gs) if gs else None


def p_parts(g):
    if g is None or g.is_empty:
        return []
    return list(g.geoms) if hasattr(g, "geoms") else [g]


P_STATS = {"repaired_before_scale": 0, "repaired_after_scale": 0}   # how often a polygon needed repair (read after a run)


def p_polygonal(g):
    # keep only the area parts of a repaired geometry (make_valid can return polygons plus stray lines or points)
    if g is None or g.is_empty:
        return None
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    areas = [x for x in p_parts(g) if x.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(areas) if areas else None


def p_repair(m):
    # repair a polygon that scaling made invalid (a 1-ulp near-touch became a crossing: needle rooms, walls with a hole touching their own outline).
    # The structure method keeps shells as shells and holes as holes; the default linework method filled whole holes (16-21 m2 of wall, Kaggle S9/S10).
    try:
        return shapely.make_valid(m, method="structure")
    except (TypeError, ValueError, AttributeError):                # older shapely or GEOS without the structure method
        return make_valid(m)


def p_metres(g, s):
    # repair, scale to metres, repair again: scaling can make a valid polygon invalid (touching points collapse), seen on plan 7069
    was_area = g is not None and g.geom_type in ("Polygon", "MultiPolygon")
    if g is not None and not g.is_empty and not g.is_valid:
        P_STATS["repaired_before_scale"] += 1
    g = p_fix(g)
    if g is None:
        return None
    m = p_scale(g, 1 / s, 1 / s, origin=(0, 0))
    if not m.is_valid:
        P_STATS["repaired_after_scale"] += 1
        m = p_repair(m)
    if was_area:                                                    # a polygon stays a polygon (drop stray lines from the repair)
        m = p_polygonal(m)
    return m


def p_bits(x):
    while x:
        low = x & -x
        yield low.bit_length() - 1
        x ^= low


def p_connected(mask, nbr):
    seen = mask & -mask
    while True:
        grow = seen
        for i in p_bits(seen):
            grow |= nbr[i] & mask
        if grow == seen:
            return seen == mask
        seen = grow


def p_table(n, areas, nbr, comp, use_f2):
    # ok[mask]: at most 5 rooms, at most 100 m2, connected; with use_f2 all rooms also share one routing-space component
    ok = [False] * (1 << n)
    for mask in range(1, 1 << n):
        members = list(p_bits(mask))
        if len(members) == 1:
            ok[mask] = True             # a single room always gets its own hub (oversize rooms are flagged, best effort)
            continue
        if len(members) > 5 or sum(areas[i] for i in members) > 100:
            continue
        if use_f2:
            comps = {comp[i] for i in members if comp[i] is not None}
            if len(comps) > 1:
                continue
        ok[mask] = p_connected(mask, nbr)
    return ok


def p_min_hubs(n, ok):
    best = [0] + [99] * ((1 << n) - 1)
    for mask in range(1, 1 << n):
        low, sub = mask & -mask, mask
        while sub:
            if sub & low and ok[sub]:
                best[mask] = min(best[mask], best[mask ^ sub] + 1)
            sub = (sub - 1) & mask
    return best[-1]


def prepare(plan, cfg=P_CFG):
    s = math.sqrt(plan["inner"].area / plan["area"])                # drawing units per metre
    hg = ru.plan_to_graph(plan)
    wd = float(plan["wall_depth"]) / s                              # metres
    margin, min_area = cfg["margin_m"], cfg["min_area_m2"]
    flags = []

    nodes = [(nm, d["type"], p_metres(d["geometry"], s)) for nm, d in hg.nodes(data=True) if d["type"] in P_INTERIOR]
    elig = [(nm, t, g) for nm, t, g in nodes if t in P_ELIG and g is not None]
    if len(elig) < sum(1 for nm, t, g in nodes if t in P_ELIG):
        flags.append("eligible room without geometry")             # never drop a room silently (it would get no device)
    ids = [f"{t}_{nm}" for nm, t, g in elig]
    idx = {nm: i for i, (nm, t, g) in enumerate(elig)}
    n = len(elig)
    geo = [g for nm, t, g in elig]
    areas = [g.area for g in geo]
    doors = [p_metres(d, s) for d in p_parts(plan.get("door"))]
    doors = [d for d in doors if d is not None]
    door_u = p_union(doors)
    block = p_union([p_metres(plan.get("wall"), s), p_metres(plan.get("window"), s)])
    inner = p_metres(plan["inner"], s)
    if n == 0:
        return {"id": plan.get("id"), "scale": s, "n": 0, "flags": ["no eligible rooms"]}

    # inner is used as a cut only when it covers the eligible rooms (it is misplaced in most augmented plans)
    share = sum(g.intersection(inner).area for g in geo) / sum(areas)
    aligned = share >= cfg["aligned_share"]
    if not aligned:
        flags.append("inner not aligned")

    # links: helper rule (Cell 31) and helper rule plus a physical route check (Cell R)
    helper, physical = [], []
    for u, v, d in hg.edges(data=True):
        if d.get("type") not in ("via_door", "via_opening") or u not in idx or v not in idx:
            continue
        i, j = idx[u], idx[v]
        gu, gv = geo[i], geo[j]
        keep = d["type"] == "via_opening" or any(max(dr.distance(gu), dr.distance(gv)) <= 0.5 * wd for dr in doors) or (
            gu.distance(gv) <= 0.1 * wd and gu.boundary.intersection(gv.buffer(0.1 * wd)).length >= 2.0 * wd)
        if not keep:
            continue
        helper.append((i, j))
        region = p_union([gu, gv, door_u])
        if block is not None:
            region = region.difference(block)
        region = region.buffer(-margin)
        if any(part.intersection(gu).area >= min_area and part.intersection(gv).area >= min_area for part in p_parts(region)):
            physical.append((i, j))

    # routing space R: rooms and doors minus walls and windows, shrunk, cut by inner on aligned plans
    interior = p_union([g for nm, t, g in nodes if g is not None] + doors)
    if block is not None:
        interior = interior.difference(block)
    space = interior.buffer(-margin, join_style=2)
    if aligned:
        space = space.intersection(inner.buffer(cfg["inner_tol_m"]))
    space_parts = p_parts(space)
    if not space_parts:
        flags.append("routing space is empty")

    # usable region and placement tier per room, then the overlap rule (smaller room keeps the overlap)
    usable, tier, comp = [], [], []
    for i in range(n):
        reg = space.intersection(geo[i])
        t = 1
        if reg.area < min_area:
            base = p_union([geo[i], door_u])
            if block is not None:
                base = base.difference(block)
            reg, t = base.intersection(geo[i]), 2
            if reg.area < min_area:
                reg, t = geo[i], 3
        usable.append(reg)
        tier.append(t)
    for i in range(n):
        for j in range(n):
            if i != j and areas[j] < areas[i] and geo[i].intersection(geo[j]).area > 1e-6:
                cut = usable[i].difference(geo[j])
                if cut.area >= min_area:
                    usable[i] = cut
                if "nested rooms" not in flags:
                    flags.append("nested rooms")
    for i in range(n):
        best, best_area = None, 0.0
        for c, part in enumerate(space_parts):
            a = usable[i].intersection(part).area
            if a > best_area:
                best, best_area = c, a
        comp.append(best)                                           # None: the room's usable region is outside R (tier 2 or 3)

    # minimum hubs: helper, physical, with and without the routing-space component filter (F2)
    hubs = {}
    if n <= cfg["max_rooms_dp"]:
        for name, links in (("helper", helper), ("physical", physical)):
            nbr = [0] * n
            for i, j in links:
                nbr[i] |= 1 << j
                nbr[j] |= 1 << i
            hubs[name + "_noF2"] = p_min_hubs(n, p_table(n, areas, nbr, comp, False))
            hubs[name] = p_min_hubs(n, p_table(n, areas, nbr, comp, True))
    else:
        flags.append("too many rooms for the exact hub count")
    if max(areas) > 100:
        flags.append("oversize room")
    if any(t > 1 for t in tier):
        flags.append("device placed below tier 1")
    if any(g.geom_type == "MultiPolygon" and len(g.geoms) > 1 for g in geo):
        flags.append("room drawn in pieces")
    if hubs and hubs["physical"] > hubs["helper_noF2"]:
        flags.append("needs more hubs than the plain helper rule")
    return {"id": plan.get("id"), "scale": s, "n": n, "ids": ids, "type": [t for nm, t, g in elig], "geo": geo, "areas": areas,
            "links": {"helper": helper, "physical": physical}, "space": space, "inner": inner, "aligned": aligned,
            "usable": usable, "tier": tier, "comp": comp, "hubs": hubs, "flags": flags, "wall_depth_m": wd}


def to_context(prep, link_set="physical"):
    # the validator's context (Cell V1) for this plan
    rooms = {prep["ids"][i]: (prep["type"][i], prep["geo"][i]) for i in range(prep["n"])}
    links = [(prep["ids"][i], prep["ids"][j]) for i, j in prep["links"][link_set]]
    return make_context(rooms, links, space=prep["space"], inner=prep["inner"] if prep["aligned"] else None)
