"""Write plans from ResPlan.pkl as JSON files in the dataset's layout (see backend/plan_codec.py), for the web app's Upload button.

    python tools/export_plans.py --pkl backend/data/ResPlan.pkl --ids 14433 2373 --out samples
    python tools/export_plans.py --pkl backend/data/ResPlan.pkl --split val --limit 20 --out samples --bundle     (one file with all of them)
    add --verify to read every file back and compare the geometry with the original
"""
import argparse
import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from plan_codec import plan_from_dict, plan_to_dict  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkl", default=str(ROOT / "backend" / "data" / "ResPlan.pkl"))
    ap.add_argument("--ids", nargs="*", type=int, default=[])
    ap.add_argument("--split", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="samples")
    ap.add_argument("--bundle", action="store_true", help="write one file with all plans instead of one file per plan")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    plans = pickle.load(open(a.pkl, "rb"))
    pick = list(plans)
    if a.ids:
        want = set(a.ids)
        pick = [p for p in plans if p.get("id") in want]
    elif a.split:
        split = json.load(open(Path(a.pkl).with_name("split.json")))
        ids = set(split[a.split])
        pick = [p for p in plans if p.get("id") in ids]
    if a.limit:
        pick = pick[: a.limit]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    docs = [plan_to_dict(p) for p in pick]
    if a.verify:
        bad = 0
        for p, d in zip(pick, docs):
            q = plan_from_dict(json.loads(json.dumps(d)))
            for k, v in p.items():
                if hasattr(v, "equals_exact") and not (q.get(k) is not None and v.equals_exact(q[k], 0.0)):
                    bad += 1
                    print(f"plan {p.get('id')}: layer {k} differs after the round trip")
        print(f"verified {len(pick)} plans: {'all layers identical' if not bad else str(bad) + ' differences'}")
    if a.bundle:
        path = out / "plans_bundle.json"
        path.write_text(json.dumps({"plans": docs}))
        print(f"wrote {path} ({len(docs)} plans)")
    else:
        for d in docs:
            (out / f"plan_{d['id']}.json").write_text(json.dumps(d))
        print(f"wrote {len(docs)} files into {out}/")
    skipped = {k for d in docs for k in d.get("not_written", [])}
    if skipped:
        print("not written (cannot be stored as JSON):", sorted(skipped))


if __name__ == "__main__":
    main()
