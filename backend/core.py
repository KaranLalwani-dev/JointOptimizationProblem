"""Loads the solver cells (cells/*.py, the same code as in the notebook) into one namespace and gives the API a small interface.

The cells share global names (CFG, S_CFG, prepare, validate, ...), so they are executed one after the other into a single dict, in the same order as in the notebook.
After the organizers answer our questions, the changes go into the files in cells/ (see README, section "Where to change what"); nothing else needs to be touched.
"""
import json
import os
import pickle
import threading
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

ROOT = Path(__file__).resolve().parent.parent
CELL_DIR = ROOT / "cells"
CELLS = ["cellV1_validator.py", "cellP1_prepare.py", "cellS1_ring_solver.py", "cellS11_multi_hub.py", "cellS18_design_picture.py", "cellS19_json_export.py"]

NS = {"__name__": "ringcore"}          # the shared namespace of all cells
STATE = {"cells_ok": False, "cells_error": None, "plans": [], "index": {}, "split_of": {}, "data_error": None, "pkl": None}
SOLVE_LOCK = threading.Lock()          # the solver is CPU bound: one solve at a time


def load_cells():
    try:
        for name in CELLS:
            code = compile((CELL_DIR / name).read_text(), name, "exec")
            exec(code, NS)
        NS["data"] = STATE["plans"]      # cells S18/S19 look up plans by id in `data`
        STATE["cells_ok"] = True
    except ModuleNotFoundError as err:
        STATE["cells_error"] = (f"Missing Python module '{err.name}'. If it is 'resplan_utils', put the dataset's helper file resplan_utils.py "
                                f"(the one imported in Kaggle Cell 25) into backend/ or on PYTHONPATH.")
    except Exception as err:
        STATE["cells_error"] = f"{type(err).__name__}: {err}"


def load_plans(pkl_path=None):
    path = Path(pkl_path or os.environ.get("RESPLAN_PKL") or ROOT / "backend" / "data" / "ResPlan.pkl")
    STATE["pkl"] = str(path)
    if not path.exists():
        STATE["data_error"] = f"Plan file not found: {path}. Put ResPlan.pkl into backend/data/ or set the environment variable RESPLAN_PKL."
        return
    try:
        with open(path, "rb") as f:
            plans = pickle.load(f)
        STATE["plans"] = plans
        STATE["index"] = {str(p.get("id", i)): i for i, p in enumerate(plans)}
        sp = path.with_name("split.json")
        if sp.exists():
            raw = json.load(open(sp))
            if all(isinstance(v, list) for v in raw.values()):
                STATE["split_of"] = {str(pid): name for name, ids in raw.items() for pid in ids}
            else:
                STATE["split_of"] = {str(k): v for k, v in raw.items()}
        NS["data"] = plans
        NS["split_of"] = {p_id: v for p_id, v in STATE["split_of"].items()}
    except Exception as err:
        STATE["data_error"] = f"Could not read {path}: {type(err).__name__}: {err}"


def startup():
    load_plans()
    load_cells()


def add_plan(plan, split="uploaded"):
    """Register a plan that came from an uploaded file (replaces a plan with the same id)."""
    key = str(plan.get("id"))
    if key in STATE["index"]:
        STATE["plans"][STATE["index"][key]] = plan
    else:
        STATE["plans"].append(plan)
        STATE["index"][key] = len(STATE["plans"]) - 1
    STATE["split_of"][key] = split
    NS["data"] = STATE["plans"]


def get_plan(pid):
    i = STATE["index"].get(str(pid))
    return None if i is None else STATE["plans"][i]


def split_of(pid):
    return STATE["split_of"].get(str(pid), "unknown")
