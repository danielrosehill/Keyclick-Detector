#!/usr/bin/env python3
"""Run a trained ClickNet over every labelled original and record where it disagrees with the labels.

Usage: uv run scripts/predict_all.py [run]   (default run v1 -> runs/v1/clicknet.pt)
Writes labels/pred-<run>.jsonl: {id, pred: [[on, off], ...], disagree: bool}. A take disagrees when a predicted
click overlaps no label (+-10 ms) or a label overlaps no prediction. Labels: reviewed.jsonl over auto.jsonl.
The review page (scripts/review.py) shows these as a second layer and can list only disagreeing takes.
"""
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from keyclick.features import load  # noqa: E402
from keyclick.model import load_model, predict  # noqa: E402

TOL = 0.01


def overlaps(a, b):
    return a[0] <= b[1] + TOL and a[1] >= b[0] - TOL


def main():
    run = sys.argv[1] if len(sys.argv) > 1 else "v1"
    model = load_model(f"runs/{run}/clicknet.pt")
    rows = {json.loads(l)["id"]: json.loads(l) for l in Path("labels/auto.jsonl").read_text().splitlines()}
    rev = Path("labels/reviewed.jsonl")
    if rev.exists():
        for l in rev.read_text().splitlines():
            rows[json.loads(l)["id"]] = json.loads(l)
    out = []
    for r in rows.values():
        x, _ = load(Path("data/wake-words/data") / r["file"])
        pred, _ = predict(model, x)
        dis = any(not any(overlaps(p, c) for c in r["clicks"]) for p in pred) or \
            any(not any(overlaps(c, p) for p in pred) for c in r["clicks"])
        out.append(dict(id=r["id"], pred=pred, disagree=dis))
    Path(f"labels/pred-{run}.jsonl").write_text("".join(json.dumps(o) + "\n" for o in out))
    print(json.dumps(dict(run=run, takes=len(out), disagree=sum(o["disagree"] for o in out),
                          disagree_test=sum(o["disagree"] for o in out if rows[o["id"]]["split"] == "test"))))


if __name__ == "__main__":
    main()
