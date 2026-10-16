# Cell S20: batch runner. Solves many plans in parallel, writes one JSON file per plan, keeps a summary table, prints a report.
#   run_batch(splits=("val",), step=1, limit=None, workers=4, plan_s=20.0, budget_s=3000, folder="outputs_batch", redo=False)
#     splits   which parts of the data to run (needs the dict `split_of` from Cell 6; with no `split_of`, all plans are used)
#     step     take every step-th plan (1 = all);  limit  stop after that many plans (taken in a fixed random order, so a short run is a fair sample)
#     workers  parallel processes (Kaggle has 4 cores);  plan_s  soft time limit per plan in seconds;  budget_s  stop starting new plans after this many seconds
#     folder   JSON files go to <folder>/plan_<id>.json, the table to <folder>/summary.csv;  redo  False = skip plans whose JSON file already exists (so a run can be continued)
#   returns the list of rows (one dict per plan). Pictures are not drawn here (use export_plan or show_design for single plans).
# Needs: Cells V1, P1, S1, S11, S18, S19 and `data`. Linux only (uses fork, so the workers share the data without copying it).
import os, csv, json, time, math, random, traceback
import multiprocessing as s20_mp
from collections import Counter

S20_COLUMNS = ["id", "split", "status", "rooms", "hubs_lower_bound", "hubs_min", "hubs_out", "J", "L", "X", "X_touch", "O", "B", "area_gap", "seconds", "retry", "flags", "error", "file_check"]
S20_STATE = {}


def s20_work(i):
    # runs in a worker process: solve plan number i of `data`, write its JSON, return one small row
    t0 = time.time()
    plan = data[i]
    pid = plan.get("id", i)
    sp = S20_STATE["split_of"].get(pid, "unknown")
    row = {c: "" for c in S20_COLUMNS}
    row.update({"id": pid, "split": sp})
    folder, plan_s = S20_STATE["folder"], S20_STATE["plan_s"]
    try:
        pr, res = solve_plan(plan, "physical", plan_s)
        row.update({"rooms": pr["n"], "flags": ";".join(pr.get("flags", []))})
        if pr["n"]:
            area = sum(pr["areas"])
            row["hubs_lower_bound"] = max(math.ceil(pr["n"] / 5), math.ceil(area / 100))
            row["hubs_min"] = pr["hubs"]["physical"] if pr["hubs"] else ""
        if pr["n"] == 0:
            row["status"] = "no_rooms"
        elif "oversize room" in pr.get("flags", []) and not res.get("ok"):
            row["status"] = "oversize_not_solved"
        else:
            js = plan_to_json(plan, pr, res, "physical")
            chk = check_json(js, pr, "physical")
            with open(os.path.join(folder, f"plan_{js['plan_id']}.json"), "w") as f:
                json.dump(js, f, indent=1)
            row["status"] = js["status"] if chk["ok"] or js["status"] == "failed" else "file_check_failed"
            row["file_check"] = "ok" if chk["ok"] else "; ".join(chk["problems"])[:150]
            row["hubs_out"] = js["reported_metrics"]["hub_count"]
            row["retry"] = res.get("retry", "")
            if js["status"] != "failed":
                om, rep = js["our_metrics"], res["report"]
                row.update({"J": om["J"], "L": om["L"], "X": om["crossings"], "X_touch": om["crossings_with_touches"], "O": om["overlap_m"], "B": om["bends"],
                            "area_gap": round(rep["area_gap"], 3) if rep.get("area_gap") is not None else ""})
            else:
                row["error"] = js.get("error", "")[:150]
    except Exception as err:
        row["status"] = "crashed"
        row["error"] = f"{type(err).__name__}: {str(err)[:120]}"
    row["seconds"] = round(time.time() - t0, 2)
    return row


def run_batch(splits=("val",), step=1, limit=None, workers=4, plan_s=20.0, budget_s=3000, folder="outputs_batch", redo=False):
    os.makedirs(folder, exist_ok=True)
    split_of = globals().get("split_of", {})
    S20_STATE.update({"split_of": split_of, "folder": folder, "plan_s": plan_s})
    todo = []
    for i in range(0, len(data), step):
        pid = data[i].get("id", i)
        if split_of and splits and split_of.get(pid, "unknown") not in splits:
            continue
        if not redo and os.path.exists(os.path.join(folder, f"plan_{pid}.json")):
            continue
        todo.append(i)
    random.Random(7).shuffle(todo)
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} plans to run | {workers} workers | {plan_s:.0f} s soft limit per plan | folder {folder}")
    rows, start = [], time.time()
    ctx = s20_mp.get_context("fork")
    with ctx.Pool(workers, maxtasksperchild=40) as pool:
        it = pool.imap_unordered(s20_work, todo, chunksize=1)
        for k in range(len(todo)):
            if time.time() - start > budget_s:
                print(f"  time budget of {budget_s} s reached after {len(rows)} plans; stopping")
                pool.terminate()
                break
            try:
                rows.append(it.next(timeout=max(5.0, budget_s - (time.time() - start))))
            except s20_mp.TimeoutError:
                print(f"  no result for a long time, stopping after {len(rows)} plans")
                pool.terminate()
                break
            if len(rows) % 100 == 0:
                el = time.time() - start
                print(f"  ... {len(rows)} plans, {el:.0f} s, about {el / len(rows) * (len(todo) - len(rows)):.0f} s left")
    path = os.path.join(folder, "summary.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=S20_COLUMNS)
        if new:
            w.writeheader()
        w.writerows(rows)
    s20_report(rows, time.time() - start, workers)
    return rows


def s20_pct(vals, q):
    v = sorted(vals)
    return v[min(len(v) - 1, int(q * len(v)))] if v else float("nan")


def s20_report(rows, wall, workers):
    if not rows:
        print("no plans were run")
        return
    st = Counter(r["status"] for r in rows)
    print(f"\n==== batch report: {len(rows)} plans in {wall:.0f} s wall time ({wall / len(rows) * workers:.1f} s of work per plan on average) ====")
    for k, v in st.most_common():
        print(f"  {k:24s} {v:6d}  ({100 * v / len(rows):.1f}%)")
    ok = [r for r in rows if r["status"] == "ok"]
    if ok:
        over = [r for r in ok if r["hubs_out"] != r["hubs_min"]]
        lb = Counter(int(r["hubs_out"]) - int(r["hubs_lower_bound"]) for r in ok)
        print(f"  hub count different from the exact minimum: {len(over)} | hubs minus lower bound: " + ", ".join(f"+{k}: {v}" for k, v in sorted(lb.items())))
        js = [float(r["J"]) for r in ok]
        print(f"  J: median {s20_pct(js, 0.5):.2f} | mean {sum(js) / len(js):.2f} | 90th percentile {s20_pct(js, 0.9):.2f} | max {max(js):.2f}")
        print(f"  plans with crossings X > 0: {sum(1 for r in ok if int(r['X']) > 0)} | with touches counted: {sum(1 for r in ok if int(r['X_touch']) > 0)} | solved only after retry without margin: {sum(1 for r in ok if r['retry'])}")
        print(f"  flags: {dict(Counter(f for r in ok for f in r['flags'].split(';') if f))}")
    secs = [float(r["seconds"]) for r in rows]
    print(f"  seconds per plan: median {s20_pct(secs, 0.5):.1f} | 90th percentile {s20_pct(secs, 0.9):.1f} | 99th {s20_pct(secs, 0.99):.1f} | max {max(secs):.1f}")
    bysplit = {}
    for r in rows:
        d = bysplit.setdefault(r["split"], Counter())
        d["all"] += 1
        d["ok"] += r["status"] == "ok"
    print("  valid by split: " + " | ".join(f"{k}: {v['ok']}/{v['all']}" for k, v in sorted(bysplit.items())))
    bad = [r for r in rows if r["status"] not in ("ok",)]
    if bad:
        print(f"\n  first problem plans (of {len(bad)}):")
        for r in bad[:15]:
            print(f"    plan {r['id']} [{r['split']}] {r['status']}: rooms {r['rooms']}, hubs {r['hubs_min']} | {r['error'] or r['file_check']} | {r['flags']}")
    print(f"\n  table saved in summary.csv; plan ids of problems: {[r['id'] for r in bad[:40]]}")
