# Cell S11: solver for plans that need two or more hubs: solve_multi(plan, prep) (solve_h1's big sibling).
# Needs Cells V1, P1 and S1 (the S1 that has `foreign` support: s_foreign, solve_territory(..., foreign)). Own names start with s11_ / S11_.
# Steps (algo-design.md sections 2 and 5):
#  A. list every way to split the eligible rooms into the minimum number of valid territories (at most 5 rooms, 100 m2, connected, one routing-space component);
#     rank the splits with a cheap ring solve per territory (best cyclic order only, no re-routing);
#  B. solve the rings of the best few splits properly, each alone, and score each split with the true total metric (crossings and overlaps BETWEEN rings count);
#  C. for the best splits: re-solve one ring at a time while the other rings stay fixed (the solver avoids their cables), keep a change only if the true total drops;
#  D. validate the finished candidates with the validator; return the best valid one (ties: smaller assigned-area gap).
import time
from shapely.geometry import Point

S11_CFG = dict(S_CFG, inset_m=0.1,
               top_k=6,               # splits that get a full ring solve
               joint_partitions=2,    # splits that get the joint re-solve rounds
               joint_rounds=2,
               max_partitions=30000,  # more splits than this are cut off (reported)
               budget_s=60.0)         # soft time limit per plan; the best validated plan so far is always returned


def s11_best(n, ok):
    best = [0] + [99] * ((1 << n) - 1)
    for mask in range(1, 1 << n):
        low, sub = mask & -mask, mask
        while sub:
            if sub & low and ok[sub]:
                best[mask] = min(best[mask], best[mask ^ sub] + 1)
            sub = (sub - 1) & mask
    return best


def s11_partitions(n, ok, best, cap):
    # every split of all rooms into best[full] valid territories (bitmasks); stops at cap
    out = []

    def rec(mask, k, acc):
        if len(out) >= cap:
            return
        if mask == 0:
            out.append(tuple(acc))
            return
        low = mask & -mask
        rest = mask ^ low
        sub = rest
        while True:
            t = sub | low
            if ok[t] and best[mask ^ t] == k - 1:
                rec(mask ^ t, k - 1, acc + [t])
            if sub == 0:
                break
            sub = (sub - 1) & rest
    rec((1 << n) - 1, best[(1 << n) - 1], [])
    return out


def s11_ring(hub):
    # the validator's ring record for a hub dict (None if the path is not usable)
    pts = v_clean(hub["path"], CFG["tol"])
    segs, bad = v_segments(pts, CFG["tol"])
    if bad or len(segs) < 2:
        return None
    return {"segs": segs, "nodes": [tuple(hub["point"])] + [tuple(d["point"]) for d in hub["devices"]]}


def s11_total(hubs, cfg=CFG):
    # true total metric of a set of rings; Jc counts touches between cables as crossings
    rings = [s11_ring(h) for h in hubs]
    if any(r is None for r in rings):
        return None, None
    m = v_metrics(rings, cfg)
    return m["J"] + cfg["w_cross"] * (m["X_conservative"] - m["X"]), m


def s11_alone(hub, cfg=CFG):
    r = s11_ring(hub)
    if r is None:
        return None
    m = v_metrics([r], cfg)
    return m["J"] + cfg["w_cross"] * (m["X_conservative"] - m["X"])


def s11_solve(plan, prep, cfg, link_set):
    t0 = time.time()
    deadline = t0 + cfg["budget_s"]
    s = prep["scale"]
    doors = [p_metres(dr, s) for dr in p_parts(plan.get("door"))]
    doors = [dr for dr in doors if dr is not None]
    grid = s_make_grid(prep, doors, cfg)
    t_grid = time.time() - t0
    out = {"grid_vertices": grid["V"], "seconds_grid": t_grid, "seconds": t_grid}
    if grid["V"] > cfg["max_vertices"]:
        out.update({"ok": False, "error": f"grid too large ({grid['V']} vertices)"})
        return out
    n = prep["n"]
    nbr = [0] * n
    for i, j in prep["links"][link_set]:
        nbr[i] |= 1 << j
        nbr[j] |= 1 << i
    ok_t = p_table(n, prep["areas"], nbr, prep["comp"], True)
    best_t = s11_best(n, ok_t)
    H = best_t[(1 << n) - 1]
    if H >= 99:
        out.update({"ok": False, "error": "no valid cover of the rooms by territories"})
        return out
    parts = s11_partitions(n, ok_t, best_t, cfg["max_partitions"])
    out.update({"H": H, "splits": len(parts), "splits_cut_off": len(parts) >= cfg["max_partitions"]})
    area_of = lambda mask: sum(prep["areas"][i] for i in p_bits(mask))
    gap_of = lambda part: (max(area_of(m) for m in part) - min(area_of(m) for m in part)) if len(part) > 1 else 0.0
    cents = [prep["geo"][i].representative_point() for i in range(n)]

    def spread(mask):
        xs = [cents[i].x for i in p_bits(mask)]
        ys = [cents[i].y for i in p_bits(mask)]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))
    parts.sort(key=lambda part: sum(spread(m) for m in part))                    # cheap geometric order: compact territories first

    cache_fast, cache_full = {}, {}
    fast_cfg = dict(cfg, top_orders=1, rounds=0)

    def terr(mask, table, c):
        if mask not in table:
            try:
                table[mask] = solve_territory(grid, prep, doors, list(p_bits(mask)), c)
            except Exception as err:
                table[mask] = {"error": f"{type(err).__name__}: {err}"}
        return table[mask]

    # A. cheap proxy for every split (until 40% of the budget is used)
    proxy = []
    for part in parts:
        if time.time() > t0 + 0.4 * cfg["budget_s"] and proxy:
            break
        tot = 0.0
        for m in part:
            h = terr(m, cache_fast, fast_cfg)
            if "error" in h:
                tot = float("inf")
                break
            tot += h["J_search"]
        proxy.append((tot, part))
    out["splits_scored"] = len(proxy)
    proxy.sort(key=lambda x: (x[0], gap_of(x[1])))
    chosen = [part for tot, part in proxy[: cfg["top_k"]] if tot < float("inf")]
    finite = [(gap_of(part), part) for tot, part in proxy if tot < float("inf")]
    if finite:
        balanced = min(finite, key=lambda x: x[0])[1]
        if balanced not in chosen:
            chosen.append(balanced)
    if not chosen:
        out.update({"ok": False, "error": "no split of the rooms could be routed", "seconds": time.time() - t0})
        return out

    # B. full independent solve of the chosen splits, scored by the true total
    cands = []
    for part in chosen:
        if time.time() > t0 + 0.7 * cfg["budget_s"] and cands:
            break
        hubs = [terr(m, cache_full, cfg) for m in part]
        if any("error" in h for h in hubs):
            continue
        jc, met = s11_total(hubs)
        if jc is None:
            continue
        alone = [s11_alone(h) for h in hubs]
        cands.append({"part": part, "hubs": hubs, "jc": jc, "independent_sum": sum(alone), "gap": gap_of(part), "stage": "alone"})
    if not cands:
        out.update({"ok": False, "error": "no split had routable rings", "seconds": time.time() - t0})
        return out
    cands.sort(key=lambda c: (round(c["jc"], 6), c["gap"]))
    out["cross_ring_extra_best_alone"] = cands[0]["jc"] - cands[0]["independent_sum"]
    out["J_alone_best"] = cands[0]["jc"]

    # C. joint re-solve: one ring at a time, the others fixed
    joint = []
    for c in cands[: cfg["joint_partitions"]]:
        cur, jc = list(c["hubs"]), c["jc"]
        for _ in range(cfg["joint_rounds"]):
            improved = False
            for t, m in enumerate(c["part"]):
                if time.time() > deadline:
                    break
                others = [h for u, h in enumerate(cur) if u != t]
                rings = [s11_ring(h) for h in others]
                if any(r is None for r in rings):
                    continue
                foreign = s_foreign(grid, [h["verts"] for h in others if h.get("verts")], rings)
                try:
                    new = solve_territory(grid, prep, doors, list(p_bits(m)), cfg, foreign)
                except Exception:
                    continue
                if "error" in new:
                    continue
                trial = cur[:t] + [new] + cur[t + 1:]
                j2, _ = s11_total(trial)
                if j2 is not None and j2 < jc - 1e-9:
                    cur, jc, improved = trial, j2, True
            if not improved:
                break
        joint.append({"part": c["part"], "hubs": cur, "jc": jc, "gap": c["gap"], "stage": "joint"})
    pool = sorted(cands + joint, key=lambda c: (round(c["jc"], 6), c["gap"]))
    out["J_joint_best"] = min(c["jc"] for c in pool)

    # D. validate, best first
    ctx = to_context(prep, link_set)
    first_fail = None
    for c in pool:
        sol = {"hubs": [dict(id=f"H{k + 1}", **{key: h[key] for key in ("point", "rooms", "devices", "path")}) for k, h in enumerate(c["hubs"])]}
        rep = validate(ctx, sol, expected_h=H)
        if rep["valid"]:
            out.update({"ok": True, "solution": sol, "report": rep, "J_search": c["jc"], "stage": c["stage"], "splits_validated_before": pool.index(c),
                        "seconds": time.time() - t0})
            return out
        if first_fail is None:
            first_fail = rep
    out.update({"ok": False, "report": first_fail, "error": ";".join(sorted({code for code, _ in first_fail["errors"]})), "seconds": time.time() - t0})
    return out


def solve_multi(plan, prep, cfg=S11_CFG, link_set="physical"):
    # the solver above; if it fails with a device margin, one more try without the margin (Kaggle S14: plan 362 failed at 0.1 m and passed at 0)
    res = s11_solve(plan, prep, cfg, link_set)
    if not res["ok"] and cfg["inset_m"] > 0:
        res2 = s11_solve(plan, prep, dict(cfg, inset_m=0.0), link_set)
        if res2["ok"]:
            res2["retry"] = "inset 0"
            res2["seconds"] += res["seconds"]
            return res2
    return res
