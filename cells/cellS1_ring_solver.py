# Cell S1: ring solver for one territory (the rooms served by one hub), plus solve_h1(plan, prep) for plans that need one hub.
# Needs Cells V1 (validator) and P1 (prepare), numpy, scipy, shapely 2.x. Own names start with s_ / S_.
# Method (algo-design.md sections 3 and 4): Hanan-style grid over the routing space R with lane lines inside doors; shortest paths over
# states (vertex, heading) with cost = length + 0.5 per turn; for every cyclic order of the devices a min-plus chain over
# (candidate position, heading) finds the cheapest closed ring; the best orders are re-scored with the true metric and improved by
# re-routing one leg at a time with penalties for overlap and crossing; the hub is a point ON the finished ring.
import math, itertools, time
import numpy as np
import shapely
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import Point, LineString

S_CFG = {"gap_m": 0.3,          # hub-device distance (one-device ring costs 3 * gap + 1.0)
         "lanes": 4,            # parallel lines per doorway
         "lane_gap_m": 0.05,    # minimum spacing of lane lines
         "merge_m": 0.02,       # grid lines closer than this are merged
         "cands": 6,            # candidate device positions per room
         "turn": 0.5,           # cost of one 90-degree turn or reversal
         "w_cross": 5.0, "w_overlap": 1.0,
         "top_orders": 6,       # cyclic orders re-scored with the true metric
         "rounds": 3,           # re-routing rounds
         "near_door_m": 0.5,    # a door within this distance of a room counts as the room's door
         "conservative": True,  # the search counts touches between cables as crossings (the validator still reports both counts)
         "hub_min_m": 0.05,     # smallest hub-device distance accepted on the ring before a short detour is used instead
         "inset_m": 0.0,        # devices stay at least this far inside their room (0 = anywhere in the usable floor)
         "max_vertices": 20000}  # bigger grids are skipped for now (reported as an error, handled in a later step)
S_INF = float("inf")
cfg_validator = CFG


def s_values(vals, gap):
    # merge coordinates closer than `gap` to the first of their cluster (cluster mean is used)
    vals = np.sort(np.unique(np.round(np.asarray(vals, dtype=float), 6)))
    out, start, acc = [], vals[0], [vals[0]]
    for v in vals[1:]:
        if v - start <= gap:
            acc.append(v)
        else:
            out.append(float(np.mean(acc)))
            start, acc = v, [v]
    out.append(float(np.mean(acc)))
    return np.array(out)


def s_lane_values(lo, hi, cfg):
    if hi - lo < cfg["lane_gap_m"]:
        return [(lo + hi) / 2]
    n = max(2, min(cfg["lanes"], int((hi - lo) / cfg["lane_gap_m"]) + 1))
    return list(np.linspace(lo, hi, n))


def s_make_grid(prep, doors, cfg=S_CFG):
    # grid vertices inside R, grid edges between neighbouring vertices, and the state graph (vertex, heading)
    space = prep["space"]
    coords = shapely.get_coordinates(space)
    xs, ys = list(coords[:, 0]), list(coords[:, 1])
    for reg in prep["usable"]:                               # a line pair through each room so that every room gets a vertex
        if not reg.is_empty:
            rp = reg.representative_point()
            xs.append(rp.x)
            ys.append(rp.y)
    for d in doors:                                          # lane lines inside every doorway
        r = d.intersection(space)
        if r.is_empty:
            continue
        x0, y0, x1, y1 = r.bounds
        w, h = x1 - x0, y1 - y0
        if h >= 0.7 * w:
            ys += s_lane_values(y0, y1, cfg)                 # tall doorway: the cable passes horizontally
        if w >= 0.7 * h:
            xs += s_lane_values(x0, x1, cfg)
    xl, yl = s_values(xs, cfg["merge_m"]), s_values(ys, cfg["merge_m"])
    sb = space.buffer(1e-6)
    shapely.prepare(sb)
    sv = space.buffer(0.9e-3)                                # the validator accepts a leg lying inside the space grown by 1 mm; an edge must pass that test whole
    shapely.prepare(sv)
    X, Y = np.meshgrid(xl, yl, indexing="ij")
    inside = shapely.intersects_xy(sb, X.ravel(), Y.ravel()).reshape(X.shape)
    idx = -np.ones(X.shape, dtype=np.int64)
    V = int(inside.sum())
    idx[inside] = np.arange(V)
    vx, vy = X[inside], Y[inside]
    eu, ev, hor = [], [], []
    a, b = idx[:-1, :], idx[1:, :]                           # horizontal neighbours
    ii, jj = np.nonzero((a >= 0) & (b >= 0))
    good = shapely.intersects_xy(sb, (xl[ii] + xl[ii + 1]) / 2, yl[jj]) if len(ii) else np.zeros(0, bool)
    if good.any():
        gi, gj = ii[good], jj[good]
        whole = shapely.covers(sv, shapely.linestrings(np.stack([np.stack([xl[gi], yl[gj]], axis=1), np.stack([xl[gi + 1], yl[gj]], axis=1)], axis=1)))
        good[np.nonzero(good)[0][~whole]] = False
    eu.append(a[ii[good], jj[good]]); ev.append(b[ii[good], jj[good]]); hor.append(np.ones(int(good.sum()), bool))
    a, b = idx[:, :-1], idx[:, 1:]                           # vertical neighbours
    ii, jj = np.nonzero((a >= 0) & (b >= 0))
    good = shapely.intersects_xy(sb, xl[ii], (yl[jj] + yl[jj + 1]) / 2) if len(ii) else np.zeros(0, bool)
    if good.any():
        gi, gj = ii[good], jj[good]
        whole = shapely.covers(sv, shapely.linestrings(np.stack([np.stack([xl[gi], yl[gj]], axis=1), np.stack([xl[gi], yl[gj + 1]], axis=1)], axis=1)))
        good[np.nonzero(good)[0][~whole]] = False
    eu.append(a[ii[good], jj[good]]); ev.append(b[ii[good], jj[good]]); hor.append(np.zeros(int(good.sum()), bool))
    eu, ev, hor = np.concatenate(eu), np.concatenate(ev), np.concatenate(hor)
    E = len(eu)
    elen = np.hypot(vx[eu] - vx[ev], vy[eu] - vy[ev])
    # directed edges: forward = east (0) or north (1), backward = west (2) or south (3)
    dsrc = np.concatenate([eu, ev]); ddst = np.concatenate([ev, eu])
    ddir = np.concatenate([np.where(hor, 0, 1), np.where(hor, 2, 3)])
    dlen = np.concatenate([elen, elen]); dedge = np.concatenate([np.arange(E), np.arange(E)])
    nbr = -np.ones((V, 4), dtype=np.int64); nbr_len = np.zeros((V, 4)); nbr_e = -np.ones((V, 4), dtype=np.int64)
    nbr[dsrc, ddir] = ddst; nbr_len[dsrc, ddir] = dlen; nbr_e[dsrc, ddir] = dedge
    # transitions (vertex, arrival heading h) -> (next vertex, heading d); a reversal in the middle of a path is not allowed
    nd = len(dsrc)
    tf = np.repeat(dsrc, 4); th = np.tile(np.arange(4), nd); td = np.repeat(ddir, 4)
    tt = np.repeat(ddst, 4); tl = np.repeat(dlen, 4); te = np.repeat(dedge, 4)
    keep = th != (td + 2) % 4
    tf, th, td, tt, tl, te = tf[keep], th[keep], td[keep], tt[keep], tl[keep], te[keep]
    tw = tl + cfg["turn"] * (th != td)
    edge_of = {}
    for k in range(E):
        edge_of[(int(eu[k]), int(ev[k]))] = k
        edge_of[(int(ev[k]), int(eu[k]))] = k
    return {"xl": xl, "yl": yl, "vx": vx, "vy": vy, "V": V, "E": E, "eu": eu, "ev": ev, "hor": hor, "elen": elen,
            "nbr": nbr, "nbr_len": nbr_len, "nbr_e": nbr_e, "edge_of": edge_of,
            "t": {"f": tf, "h": th, "d": td, "to": tt, "len": tl, "e": te, "w": tw}}


def s_inset(region, inset):
    # the part of the usable floor at least `inset` from the room edge (falls back to the whole floor if nothing is left)
    if inset <= 0:
        return region
    cut = region.buffer(-inset)
    return cut if cut.area >= 0.05 else region


def s_pick(grid, region, centre, door_pts, count):
    # candidate vertices of one room: nearest to each door, nearest to the centre, then farthest-point fill
    vx, vy = grid["vx"], grid["vy"]
    minx, miny, maxx, maxy = region.bounds
    near = np.nonzero((vx >= minx - 1e-6) & (vx <= maxx + 1e-6) & (vy >= miny - 1e-6) & (vy <= maxy + 1e-6))[0]
    if len(near) == 0:
        return []
    ok = shapely.intersects_xy(region, vx[near], vy[near])
    ids = near[ok]
    if len(ids) <= count:
        return [int(i) for i in ids]
    pts = np.stack([vx[ids], vy[ids]], axis=1)
    chosen = []

    def add(p):
        k = int(np.argmin(((pts - np.asarray(p)) ** 2).sum(axis=1)))
        if k not in chosen:
            chosen.append(k)

    for p in door_pts[: max(1, count // 2)]:
        add(p)
    add(centre)
    while len(chosen) < count:
        dmin = np.min(((pts[:, None, :] - pts[chosen][None, :, :]) ** 2).sum(axis=2), axis=1)
        chosen.append(int(np.argmax(dmin)))
    return [int(ids[k]) for k in chosen]


def s_csr(src, dst, w, n):
    order = np.argsort(src, kind="stable")
    indptr = np.concatenate([[0], np.cumsum(np.bincount(src, minlength=n))])
    return order, indptr


def s_paths(grid, cand_vertices, cfg=S_CFG):
    # state graph with one virtual start node per (candidate, heading); returns the pieces needed for shortest paths and re-routing
    V, t = grid["V"], grid["t"]
    K = len(cand_vertices)
    ssrc, sdst, sw, se = [], [], [], []
    for k, c in enumerate(cand_vertices):
        for h in range(4):
            u = grid["nbr"][c, h]
            if u >= 0:
                ssrc.append(4 * V + 4 * k + h); sdst.append(4 * u + h)
                sw.append(grid["nbr_len"][c, h]); se.append(grid["nbr_e"][c, h])
    ssrc, sdst, sw, se = np.array(ssrc, dtype=np.int64), np.array(sdst, dtype=np.int64), np.array(sw), np.array(se, dtype=np.int64)
    src = np.concatenate([4 * t["f"] + t["h"], ssrc]); dst = np.concatenate([4 * t["to"] + t["d"], sdst])
    w = np.concatenate([t["w"], sw]); N = 4 * V + 4 * K
    order, indptr = s_csr(src, dst, w, N)
    # per-transition fields in sorted order; start edges have vertex -1 (no turn happens there)
    f = np.concatenate([t["f"], -np.ones(len(ssrc), dtype=np.int64)])[order]
    h = np.concatenate([t["h"], (ssrc - 4 * V) % 4])[order]
    d = np.concatenate([t["d"], (ssrc - 4 * V) % 4])[order]
    e = np.concatenate([t["e"], se])[order]
    ln = np.concatenate([t["len"], sw])[order]
    return {"N": N, "K": K, "V": V, "indptr": indptr, "indices": dst[order], "w": w[order], "f": f, "h": h, "d": d, "e": e, "len": ln,
            "cands": list(cand_vertices)}


def s_graph(P, extra=None):
    data = P["w"] if extra is None else P["w"] + extra
    return csr_matrix((data, P["indices"], P["indptr"]), shape=(P["N"], P["N"]))


def s_walk(pred_row, start_node, target_state):
    seq, cur = [], target_state
    while cur != start_node:
        seq.append(cur)
        cur = pred_row[cur]
        if cur < 0:
            return None
    return [s // 4 for s in reversed(seq)]


def s_turns():
    t4 = np.zeros((4, 4))
    for a in range(4):
        for b in range(4):
            t4[a, b] = S_CFG["turn"] * (a != b)
    return t4


def s_orders(d):
    first = list(range(1, d))
    out = []
    for perm in itertools.permutations(first):
        if d >= 3 and perm[0] > perm[-1]:
            continue                                         # a reversed ring costs the same
        out.append([0] + list(perm))
    return out


def s_dp(order, leg, ncand, t4):
    # cheapest closed ring for one cyclic order; returns (cost, {device: (candidate, heading in, heading out)})
    d = len(order)
    o0 = order[0]
    S0 = 4 * ncand[o0]
    F = np.full((S0, S0), S_INF)
    np.fill_diagonal(F, 0.0)
    Fs, Gs = [F], []
    for j in range(d):
        a, b = order[j], order[(j + 1) % d]
        G = (Fs[j][:, :, None] + leg(a, b)[None, :, :]).min(axis=1)
        Gs.append(G)
        if j == d - 1:
            break
        Gr = G.reshape(S0, ncand[b], 4)
        Fs.append((Gr[:, :, :, None] + t4[None, None, :, :]).min(axis=2).reshape(S0, 4 * ncand[b]))
    s_idx = np.arange(S0)
    Gc = Gs[-1].reshape(S0, ncand[o0], 4)
    close = Gc[s_idx, s_idx // 4, :] + t4[:, s_idx % 4].T           # arrival at the start candidate + turn into the start heading
    total = close.min(axis=1)
    s0 = int(np.argmin(total))
    if not np.isfinite(total[s0]):
        return S_INF, None
    hin0 = int(np.argmin(close[s0]))
    k0, hout0 = divmod(s0, 4)
    info = {o0: (k0, hin0, hout0)}
    zA = k0 * 4 + hin0
    for j in range(d - 1, 0, -1):
        a, b = order[j], order[(j + 1) % d]
        zD = int(np.argmin(Fs[j][s0, :] + leg(a, b)[:, zA]))
        k, hout = divmod(zD, 4)
        hin = int(np.argmin(Gs[j - 1][s0].reshape(ncand[a], 4)[k] + t4[:, hout]))
        info[a] = (k, hin, hout)
        zA = k * 4 + hin
    return float(total[s0]), info


def s_coords(grid, verts):
    return [(float(grid["vx"][v]), float(grid["vy"][v])) for v in verts]


def s_ring_metrics(points, nodes, cfg=CFG, others=None):
    # true metric of one closed ring (polyline closed on its first point); uses the validator's own functions.
    # others: rings already fixed ({"segs", "nodes"}); the metric is then the total of this ring and those (crossings and overlaps between rings count).
    pts = v_clean(points, cfg["tol"])
    segs, bad = v_segments(pts, cfg["tol"])
    if bad or len(segs) < 2:
        return None
    m = v_metrics([{"segs": segs, "nodes": nodes}] + list(others or []), cfg)
    m["Jc"] = m["J"] + cfg["w_cross"] * (m["X_conservative"] - m["X"])
    return m


def s_obj(m, cfg):
    # the number the search minimises
    return m["Jc"] if cfg["conservative"] else m["J"]


def s_leg_paths(grid, P, pred, order, info, cvert, rowoff):
    # vertex lists of the legs of a ring from the DP result
    d, V = len(order), P["V"]
    legs = []
    for j in range(d):
        a, b = order[j], order[(j + 1) % d]
        ka, _, hout = info[a]
        kb, hin, _ = info[b]
        row = rowoff[a] + 4 * ka + hout
        start = 4 * V + 4 * P["cand_index"][(a, ka)] + hout
        target = 4 * cvert[b][kb] + hin
        verts = s_walk(pred[row], start, target)
        if verts is None:
            return None
        legs.append({"a": a, "b": b, "ka": ka, "kb": kb, "hout": hout, "hin": hin, "start": start, "target": target,
                     "verts": [cvert[a][ka]] + verts})
    return legs


def s_ring_points(grid, legs):
    verts = list(legs[0]["verts"])
    for leg in legs[1:]:
        verts += leg["verts"][1:]
    return s_coords(grid, verts), verts


def s_occupancy(grid, legs, skip):
    V, E = grid["V"], grid["E"]
    used = np.zeros(E, bool)
    hn, vn = np.zeros(V, bool), np.zeros(V, bool)
    for j, leg in enumerate(legs):
        if j == skip:
            continue
        vs = leg["verts"]
        for a, b in zip(vs, vs[1:]):
            e = grid["edge_of"][(a, b)]
            used[e] = True
            tgt = hn if grid["hor"][e] else vn
            tgt[a] = True
            tgt[b] = True
    return used, hn, vn


def s_foreign(grid, vert_lists, rings):
    # what other, already fixed rings occupy: grid edges, vertices touched horizontally / vertically, and their metric rings
    V, E = grid["V"], grid["E"]
    used = np.zeros(E, bool)
    hn, vn = np.zeros(V, bool), np.zeros(V, bool)
    for vs in vert_lists:
        for a, b in zip(vs, vs[1:]):
            e = grid["edge_of"].get((a, b))
            if e is None:
                continue
            used[e] = True
            tgt = hn if grid["hor"][e] else vn
            tgt[a] = True
            tgt[b] = True
    return {"occ": (used, hn, vn), "rings": rings}


def s_ripup(grid, P, legs, nodes, cfg, best_j, foreign=None):
    # re-route one leg at a time with penalties for edges and vertices used by the other legs. The leg may leave its start device and
    # reach its end device in any direction (a parallel return needs different turns at the devices). A change is kept only if the
    # true J of the whole ring drops.
    V = P["V"]
    changed, rounds = True, 0
    while changed and rounds < cfg["rounds"]:
        changed, rounds = False, rounds + 1
        for j in range(len(legs)):
            used, hn, vn = s_occupancy(grid, legs, j)
            others = None
            if foreign is not None:
                used, hn, vn = used | foreign["occ"][0], hn | foreign["occ"][1], vn | foreign["occ"][2]
                others = foreign["rings"]
            f = P["f"]
            pen = cfg["w_overlap"] * P["len"] * used[P["e"]]
            safe = np.where(f >= 0, f, 0)
            leg_h = (P["h"] % 2 == 0) | (P["d"] % 2 == 0)
            leg_v = (P["h"] % 2 == 1) | (P["d"] % 2 == 1)
            oh, ov = hn[safe], vn[safe]
            conflict = ((leg_h | oh) & (leg_v | ov)) & ~((leg_h == oh) & (leg_v == ov)) & (oh | ov) & (f >= 0)
            pen = pen + cfg["w_cross"] * conflict
            leg = legs[j]
            base = 4 * V + 4 * P["cand_index"][(leg["a"], leg["ka"])]
            starts = [base + h for h in range(4)]
            dist, pred = dijkstra(s_graph(P, pen), directed=True, indices=starts, return_predecessors=True)
            tv, v0 = leg["verts"][-1], leg["verts"][0]
            old = leg["verts"]
            best_local = None
            for hs in range(4):
                for he in range(4):
                    tgt = 4 * tv + he
                    if not np.isfinite(dist[hs][tgt]):
                        continue
                    verts = s_walk(pred[hs], starts[hs], tgt)
                    if verts is None or [v0] + verts == old:
                        continue
                    leg["verts"] = [v0] + verts
                    pts, _ = s_ring_points(grid, legs)
                    m = s_ring_metrics(pts, nodes, cfg_validator, others)
                    if m is not None and s_obj(m, cfg) < best_j - 1e-9 and (best_local is None or s_obj(m, cfg) < best_local[0]):
                        best_local = (s_obj(m, cfg), [v0] + verts, hs, he)
            leg["verts"] = old
            if best_local is not None:
                best_j, leg["verts"] = best_local[0], best_local[1]
                leg["hout"], leg["hin"] = best_local[2], best_local[3]
                leg["start"], leg["target"] = starts[best_local[2]], 4 * tv + best_local[3]
                changed = True
    return best_j


def s_hub_point(points, dev_points, rooms_geo, gap):
    # a point on the ring (inside a room of the territory, at least `gap` from every device); prefers the one farthest from the devices
    best = None
    for k in range(len(points) - 1):
        (x0, y0), (x1, y1) = points[k], points[k + 1]
        for t in (0.5, 0.25, 0.75, 0.1, 0.9):
            p = (x0 + t * (x1 - x0), y0 + t * (y1 - y0))
            if math.hypot(x1 - x0, y1 - y0) < 1e-6:
                continue
            clear = min(v_dist(p, q) for q in dev_points)
            if clear < gap:
                continue
            if not any(g.buffer(CFG["tol"]).covers(Point(p)) for g in rooms_geo):
                continue
            if best is None or clear > best[0]:
                best = (clear, p, k)
    return best


def s_hub_stub(dev_pts, rooms_geo, space, gap):
    # fallback when no point of the ring is far enough from the devices: a short out-and-back detour of length `gap` off one device
    # (costs about 3 * gap + 1.0, like the one-device ring); returns (clearance, device, hub point) or None
    space_b = space.buffer(1e-6)
    best = None
    for a, p in dev_pts.items():
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            h = (p[0] + dx * gap, p[1] + dy * gap)
            if space_b.covers(LineString([p, h])) and any(g.buffer(CFG["tol"]).covers(Point(h)) for g in rooms_geo):
                clear = space.boundary.distance(Point(((p[0] + h[0]) / 2, (p[1] + h[1]) / 2)))
                if best is None or clear > best[0]:
                    best = (clear, a, h)
    return best


def s_room_region(prep, i, cfg):
    # where a device of room i may stand: its usable floor, kept strictly inside the room polygon (0.1 mm from the edge), away from every
    # smaller room that overlaps it (nested rooms), and at least `inset_m` from the room edge. Each step is skipped if it would leave nothing.
    reg = prep["usable"][i]
    geo = prep["geo"]
    tight = reg.intersection(geo[i].buffer(-1e-4))
    if tight.area >= 0.05:
        reg = tight
    other = [geo[j].buffer(1e-4) for j in range(prep["n"]) if j != i and prep["areas"][j] < prep["areas"][i] and geo[i].intersection(geo[j]).area > 1e-6]
    if other:
        cut = reg.difference(shapely.union_all(other))
        if cut.area >= 0.05:
            reg = cut
    return s_inset(reg, cfg["inset_m"])


def s_candidates(grid, prep, doors, rooms, cfg):
    # candidate device vertices for each room of the territory (empty list = the room cannot be served)
    door_c = [dr.centroid for dr in doors]
    out = []
    for i in rooms:
        near = [(prep["geo"][i].distance(dc), (dc.x, dc.y)) for dc in door_c if prep["geo"][i].distance(dc) <= cfg["near_door_m"]]
        near.sort()
        reg = s_room_region(prep, i, cfg)
        cp = reg.representative_point()
        out.append(s_pick(grid, reg, (cp.x, cp.y), [p for _, p in near], cfg["cands"]))
    return out


def solve_territory(grid, prep, doors, rooms, cfg=S_CFG, foreign=None):
    # rooms: indices (into prep) of the rooms of one territory. Returns a hub dict for the validator, or {"error": text}.
    d = len(rooms)
    geo, usable = prep["geo"], prep["usable"]
    if d == 1:
        return s_single(grid, prep, rooms[0], cfg, foreign)
    cvert = s_candidates(grid, prep, doors, rooms, cfg)
    for a, picks in enumerate(cvert):
        if not picks:
            i = rooms[a]
            return {"error": f"no grid vertex inside the usable floor of {prep['ids'][i]} (tier {prep['tier'][i]})"}
    flat = [(a, k, v) for a in range(d) for k, v in enumerate(cvert[a])]
    P = s_paths(grid, [v for _, _, v in flat], cfg)
    P["cand_index"] = {(a, k): n for n, (a, k, _) in enumerate(flat)}
    rowoff, run = [], 0
    for a in range(d):
        rowoff.append(run)
        run += 4 * len(cvert[a])
    srcs = [4 * P["V"] + 4 * n + h for n in range(len(flat)) for h in range(4)]
    dist, pred = dijkstra(s_graph(P), directed=True, indices=srcs, return_predecessors=True)
    ncand = [len(c) for c in cvert]
    cols = [np.array([4 * cvert[b][k] + h for k in range(ncand[b]) for h in range(4)]) for b in range(d)]
    cache = {}

    def leg(a, b):
        if (a, b) not in cache:
            cache[(a, b)] = dist[rowoff[a]:rowoff[a] + 4 * ncand[a]][:, cols[b]]
        return cache[(a, b)]

    t4 = s_turns()
    scored = []
    for order in s_orders(d):
        cost, info = s_dp(order, leg, ncand, t4)
        if info is not None:
            scored.append((cost, order, info))
    if not scored:
        return {"error": "devices cannot reach each other inside the routing space"}
    scored.sort(key=lambda x: x[0])
    best = None
    for cost, order, info in scored[: cfg["top_orders"]]:
        legs = s_leg_paths(grid, P, pred, order, info, cvert, rowoff)
        if legs is None:
            continue
        pts, _ = s_ring_points(grid, legs)
        nodes = [pts[0]] + [s_coords(grid, [cvert[a][info[a][0]]])[0] for a in order]
        others = foreign["rings"] if foreign is not None else None
        m = s_ring_metrics(pts, nodes, cfg_validator, others)
        if m is None:
            continue
        j = s_ripup(grid, P, legs, nodes, cfg, s_obj(m, cfg), foreign)
        if best is None or j < best[0] - 1e-9:
            best = (j, cost, legs, order, info)
    if best is None:
        return {"error": "no usable ring found"}
    j, dp_cost, legs, order, info = best
    pts, verts = s_ring_points(grid, legs)                          # closed polyline starting and ending at device order[0]
    dev_at = {}                                                      # device -> index in the vertex list
    pos = 0
    for leg_ in legs:
        dev_at[leg_["a"]] = pos
        pos += len(leg_["verts"]) - 1
    dev_pts = {a: s_coords(grid, [cvert[a][info[a][0]]])[0] for a in range(d)}
    hub = s_hub_point(pts, list(dev_pts.values()), [geo[i] for i in rooms], cfg["gap_m"])
    if hub is None:
        hub = s_hub_point(pts, list(dev_pts.values()), [geo[i] for i in rooms], cfg["hub_min_m"])
    m = len(pts) - 1
    if hub is not None:
        _, hp, seg = hub
        # the ring as seen from the hub: hub, points seg+1 ... seg (wrapping round), hub
        path = [hp] + [pts[(seg + 1 + q) % m] for q in range(m)] + [hp]
        start = (seg + 1) % m
    else:
        stub = s_hub_stub(dev_pts, [geo[i] for i in rooms], prep["space"], cfg["gap_m"])
        if stub is None:
            return {"error": "no hub position on the ring and no free stretch for a detour"}
        _, a_stub, hp = stub
        start = dev_at[a_stub]
        path = [hp] + [pts[(start + q) % m] for q in range(m)] + [pts[start], hp]
        j += 3 * cfg["gap_m"] + 1.0                                  # the detour: 2 * gap of cable, gap of overlap, two reversals
    seq = sorted(range(d), key=lambda a: (dev_at[a] - start) % m)
    devices = [{"id": f"N{n + 1}", "room": prep["ids"][rooms[a]], "point": dev_pts[a]} for n, a in enumerate(seq)]
    return {"point": hp, "rooms": [prep["ids"][i] for i in rooms], "devices": devices, "path": path, "J_search": j, "J_independent_legs": dp_cost,
            "dp_min": scored[0][0], "orders": len(scored), "verts": verts}


def s_single(grid, prep, i, cfg, foreign=None):
    # one device: out and back along a straight stretch of length gap (J = 3 * gap + 1.0); picks the roomiest spot.
    # With other rings fixed (foreign), the roomiest few spots are compared by the true total metric instead.
    # If no stretch of gap_m fits (a very small room), shorter stretches are tried: 2/3, 1/3 of the gap, then hub_min_m.
    reg = s_room_region(prep, i, cfg)
    ids = s_pick(grid, reg, reg.representative_point().coords[0], [], 200)
    if not ids:
        return {"error": f"no grid vertex inside the usable floor of {prep['ids'][i]} (tier {prep['tier'][i]})"}
    space_b = prep["space"].buffer(1e-6)
    room_b = prep["geo"][i].buffer(CFG["tol"])
    options, g = [], cfg["gap_m"]
    for g in dict.fromkeys([cfg["gap_m"], round(2 * cfg["gap_m"] / 3, 4), round(cfg["gap_m"] / 3, 4), cfg["hub_min_m"]]):
        for v in ids:
            p = (float(grid["vx"][v]), float(grid["vy"][v]))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                h = (p[0] + dx * g, p[1] + dy * g)
                if space_b.covers(LineString([p, h])) and room_b.covers(Point(h)):
                    clear = prep["space"].boundary.distance(Point(((p[0] + h[0]) / 2, (p[1] + h[1]) / 2)))
                    options.append((clear, p, h))
        if options:
            break
    if not options:
        return {"error": f"no straight stretch of {g} m inside {prep['ids'][i]}"}
    options.sort(key=lambda o: -o[0])
    best = options[0]
    if foreign is not None and foreign["rings"]:
        best_j = None
        for clear, p, h in options[:16]:
            m = s_ring_metrics([h, p, h], [h, p], cfg_validator, foreign["rings"])
            if m is not None and (best_j is None or s_obj(m, cfg) < best_j - 1e-9):
                best_j, best = s_obj(m, cfg), (clear, p, h)
    _, p, h = best
    return {"point": h, "rooms": [prep["ids"][i]], "devices": [{"id": "N1", "room": prep["ids"][i], "point": p}], "path": [h, p, h],
            "J_search": 3 * g + 1.0, "J_independent_legs": 3 * g + 1.0, "dp_min": 3 * g + 1.0, "orders": 1, "verts": None}


def solve_h1(plan, prep, cfg=S_CFG, link_set="physical"):
    # a plan that needs one hub: one territory holding every eligible room
    t0 = time.time()
    s = prep["scale"]
    doors = [p_metres(dr, s) for dr in p_parts(plan.get("door"))]
    doors = [dr for dr in doors if dr is not None]
    grid = s_make_grid(prep, doors, cfg)
    t_grid = time.time() - t0
    if grid["V"] > cfg["max_vertices"]:
        return {"ok": False, "error": f"grid too large ({grid['V']} vertices)", "grid_vertices": grid["V"], "seconds_grid": t_grid, "seconds": t_grid}
    hub = solve_territory(grid, prep, doors, list(range(prep["n"])), cfg)
    out = {"grid_vertices": grid["V"], "seconds_grid": t_grid, "seconds": time.time() - t0}
    if "error" in hub:
        out.update({"ok": False, "error": hub["error"]})
        return out
    sol = {"hubs": [dict(id="H1", **{k: hub[k] for k in ("point", "rooms", "devices", "path")})]}
    rep = validate(to_context(prep, link_set), sol, expected_h=1)
    out.update({"ok": rep["valid"], "solution": sol, "report": rep, "J_search": hub["J_search"], "J_independent_legs": hub["J_independent_legs"],
                "seconds": time.time() - t0})
    return out
