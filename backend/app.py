"""Local web app: pick a plan, solve it, see the picture, download the JSON.

Run from the repository root:   python -m uvicorn backend.app:app --port 8000      (then open http://127.0.0.1:8000)
"""
import asyncio
import json
import random
import sys
from pathlib import Path

from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
from plan_codec import plan_from_dict, plans_from_payload  # noqa: E402
import scene  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "backend" / "cache"
app = FastAPI(title="Cable Ring Challenge")


@app.on_event("startup")
def _startup():
    CACHE.mkdir(parents=True, exist_ok=True)
    core.startup()


class SolveRequest(BaseModel):
    seconds: float = 30.0
    link_set: str = "physical"
    force: bool = False


def _need_ready():
    if core.STATE["cells_error"]:
        raise HTTPException(503, core.STATE["cells_error"])
    if not core.STATE["cells_ok"]:
        raise HTTPException(503, "the solver is not loaded")


def _plan_or_404(pid):
    p = core.get_plan(pid)
    if p is None:
        raise HTTPException(404, f"plan {pid} not found")
    return p


def _cache_file(pid, link_set):
    return CACHE / f"plan_{pid}_{link_set}.json"


def _make_view(plan, pr, res, link_set):
    # everything the page needs for one plan: geometry, solution drawing, panel numbers (and the JSON in slide-8 format when solved)
    view = {"scene": scene.build_scene(plan, pr, res), "panel": scene.build_panel(pr, res, link_set), "design": None, "file_check": None,
            "split": core.split_of(plan.get("id"))}
    if res and res.get("solution") and res.get("report"):
        js = core.NS["plan_to_json"](plan, pr, res, link_set)
        chk = core.NS["check_json"](js, pr, link_set)
        view["design"] = js
        view["file_check"] = {"ok": chk["ok"], "problems": chk["problems"]}
    return view


@app.get("/api/status")
def status():
    split_counts = {}
    for v in core.STATE["split_of"].values():
        split_counts[v] = split_counts.get(v, 0) + 1
    return {"plans": len(core.STATE["plans"]), "splits": split_counts, "plan_file": core.STATE["pkl"], "data_error": core.STATE["data_error"],
            "solver_error": core.STATE["cells_error"], "ready": core.STATE["cells_ok"]}


@app.get("/api/plans")
def list_plans(split: str = "", q: str = "", limit: int = Query(300, le=5000), offset: int = 0, link_set: str = "physical"):
    out = []
    for i, p in enumerate(core.STATE["plans"]):
        pid = p.get("id", i)
        sp = core.split_of(pid)
        if split and sp != split:
            continue
        if q and q not in str(pid):
            continue
        out.append({"id": pid, "split": sp})
    page = out[offset:offset + limit]
    for item in page:                                   # solved before? (read from the cache)
        f = _cache_file(item["id"], link_set)
        item["solved"] = f.exists()
        if item["solved"]:
            try:
                pn = json.loads(f.read_text())["panel"]
                item.update({"J": pn["J"], "hubs": pn["hubs"], "valid": pn["valid"]})
            except Exception:
                item["solved"] = False
    return {"total": len(out), "plans": page}


@app.get("/api/random")
def random_plan(split: str = ""):
    ids = [p.get("id", i) for i, p in enumerate(core.STATE["plans"]) if not split or core.split_of(p.get("id", i)) == split]
    if not ids:
        raise HTTPException(404, "no plans")
    return {"id": random.choice(ids)}


@app.get("/api/plans/{pid}")
def get_plan(pid: str, link_set: str = "physical"):
    """The plan's geometry; if it was solved before (cache), the solution too."""
    _need_ready()
    plan = _plan_or_404(pid)
    f = _cache_file(plan.get("id"), link_set)
    if f.exists():
        return json.loads(f.read_text())
    pr = core.NS["prepare"](plan)
    return _make_view(plan, pr, None, link_set)


def _solve_blocking(plan, link_set, seconds):
    with core.SOLVE_LOCK:
        pr, res = core.NS["solve_plan"](plan, link_set, seconds)
        return _make_view(plan, pr, res, link_set)


@app.post("/api/plans/{pid}/solve")
async def solve(pid: str, req: SolveRequest):
    _need_ready()
    plan = _plan_or_404(pid)
    if req.link_set not in ("physical", "helper"):
        raise HTTPException(400, "link_set must be 'physical' or 'helper'")
    f = _cache_file(plan.get("id"), req.link_set)
    if f.exists() and not req.force:
        return json.loads(f.read_text())
    loop = asyncio.get_running_loop()
    view = await loop.run_in_executor(None, _solve_blocking, plan, req.link_set, max(1.0, min(req.seconds, 300.0)))
    if view["design"] is not None:
        f.write_text(json.dumps(view))
    return view


def _upload_blocking(items):
    loaded, errors = [], []
    for item in items:
        pid = item.get("id") if isinstance(item, dict) else None
        try:
            plan = plan_from_dict(item)
            pid = plan["id"]
            core.NS["prepare"](plan)                       # runs the dataset helper on it: a plan it cannot read is refused here, with the reason
            core.add_plan(plan)
            for f in CACHE.glob(f"plan_{pid}_*.json"):     # an old solution of a plan with the same id no longer applies
                f.unlink()
            loaded.append(pid)
        except Exception as err:
            errors.append({"id": pid, "error": f"{type(err).__name__}: {err}"})
    return {"loaded": loaded, "errors": errors}


@app.post("/api/upload")
async def upload(payload: Any = Body(...)):
    """Plan files in the dataset's layout (see backend/plan_codec.py and tools/export_plans.py): one file, a list, or {"plans": [...]}."""
    _need_ready()
    items = plans_from_payload(payload)
    if not items:
        raise HTTPException(400, "no plans in the file")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _upload_blocking, items)


@app.get("/api/plans/{pid}/result.json")
def result_json(pid: str, link_set: str = "physical"):
    """The slide-8 JSON of a solved plan (solves with default settings if it was not solved yet)."""
    f = _cache_file(pid, link_set)
    if not f.exists():
        raise HTTPException(404, "this plan has not been solved yet; press Solve first")
    view = json.loads(f.read_text())
    return Response(json.dumps(view["design"], indent=1), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="plan_{pid}.json"'})


app.mount("/", StaticFiles(directory=str(ROOT / "frontend"), html=True), name="frontend")
