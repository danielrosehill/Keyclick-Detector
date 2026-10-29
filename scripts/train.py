#!/usr/bin/env python3
"""Train ClickNet on the labelled Wake-Words originals and evaluate it on the dataset's test split.

Labels: labels/reviewed.jsonl where a take was reviewed, else labels/auto.jsonl. Split: the HF dataset's own
(test = sha1(id) % 100 < 15). Augmentation per batch item: random gain (+-12 dB), a little white noise, and
with probability TRANSPLANT_P one click cut from another training take pasted into this take's silence, half
the time after the speech (trailing clicks are rare in the data because captures stop as the key goes down).
Writes runs/<name>/clicknet.pt and runs/<name>/metrics.json. Usage: uv run scripts/train.py [name] [epochs]
"""
import json, random, sys, time
from pathlib import Path

import numpy as np
import torch
from torch import nn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from keyclick.features import load, merge_events  # noqa: E402
from keyclick.model import SR, ClickNet, frame_labels, n_frames, predict  # noqa: E402

DATA = Path("data/wake-words/data")
TRANSPLANT_P, BATCH, LR, SEED = 0.6, 16, 2e-3, 0


def labelled():
    rows = {json.loads(l)["id"]: json.loads(l) for l in Path("labels/auto.jsonl").read_text().splitlines()}
    rev = Path("labels/reviewed.jsonl")
    if rev.exists():
        for l in rev.read_text().splitlines():
            r = json.loads(l)
            rows[r["id"]] = r
    out = []
    for r in rows.values():
        x, sr = load(DATA / r["file"])
        assert sr == SR, r["file"]
        out.append(dict(r, x=x, clicks=merge_events(r["clicks"])))
    return out


def augment(t, pool, rng):
    x, clicks = t["x"].copy(), [list(c) for c in t["clicks"]]
    dur = len(x) / SR
    if pool and rng.random() < TRANSPLANT_P:
        src = rng.choice(pool)
        a, b = src["clicks"][rng.randrange(len(src["clicks"]))]
        s0, s1 = max(0, int((a - 0.01) * SR)), min(len(src["x"]), int((b + 0.03) * SR))
        snip = src["x"][s0:s1] * 10 ** (rng.uniform(-6, 6) / 20)
        sp = t["speech_s"] or [dur / 2, dur / 2]
        lo, hi = (sp[1] + 0.05, dur - len(snip) / SR) if rng.random() < 0.5 else (0.02, sp[0] - 0.05 - len(snip) / SR)
        if hi > lo:
            pos = rng.uniform(lo, hi)
            i0 = int(pos * SR)
            x[i0:i0 + len(snip)] += snip
            on = pos + (a - s0 / SR)
            clicks.append([on, on + (b - a)])
    x = x * 10 ** (rng.uniform(-12, 12) / 20) + np.random.randn(len(x)).astype(np.float32) * 10 ** (rng.uniform(-90, -70) / 20)
    return x.astype(np.float32), frame_labels(len(x), clicks)


def batches(items, rng, pool):
    items = items[:]
    rng.shuffle(items)
    for k in range(0, len(items), BATCH):
        xs, ys = zip(*(augment(t, pool, rng) for t in items[k:k + BATCH]))
        n = max(len(x) for x in xs)
        X = np.zeros((len(xs), n), np.float32)
        Y = np.zeros((len(xs), n_frames(n)), np.float32)
        M = np.zeros_like(Y)
        for j, (x, y) in enumerate(zip(xs, ys)):
            X[j, :len(x)] = x
            Y[j, :len(y)] = y
            M[j, :len(y)] = 1
        yield map(torch.from_numpy, (X, Y, M))


def evaluate(model, items, tol=0.01):
    """Event-level match: a labelled click counts as found if a predicted segment overlaps it +- tol.
    `edges` restricts both sides to outside the take's speech span, which is what trimming uses."""
    res = {}
    for scope in ("all", "edges"):
        tp = fn = fp = 0
        onset_err = []
        for t in items:
            pred, _ = predict(model, t["x"])
            lab = t["clicks"]
            if scope == "edges" and t["speech_s"]:
                out = lambda c: c[1] < t["speech_s"][0] or c[0] > t["speech_s"][1]
                pred, lab = [c for c in pred if out(c)], [c for c in lab if out(c)]
            hit = [any(p[0] <= c[1] + tol and p[1] >= c[0] - tol for p in pred) for c in lab]
            tp += sum(hit)
            fn += len(hit) - sum(hit)
            fp += sum(not any(p[0] <= c[1] + tol and p[1] >= c[0] - tol for c in lab) for p in pred)
            for c in lab:
                near = [p for p in pred if p[0] <= c[1] + tol and p[1] >= c[0] - tol]
                if near:
                    onset_err.append(1000 * (near[0][0] - c[0]))
        res[scope] = dict(clicks=tp + fn, found=tp, missed=fn, false_alarms=fp,
                          recall=round(tp / max(1, tp + fn), 3), precision=round(tp / max(1, tp + fp), 3),
                          onset_err_ms_median=round(float(np.median(np.abs(onset_err))), 1) if onset_err else None)
    return res


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "v1"
    epochs = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    rng = random.Random(SEED)
    torch.manual_seed(SEED)
    np.random.seed(SEED)
    data = labelled()
    train = [t for t in data if t["split"] == "train"]
    test = [t for t in data if t["split"] == "test"]
    pool = [t for t in train if t["clicks"]]
    print(f"train {len(train)} takes / {sum(len(t['clicks']) for t in train)} clicks, "
          f"test {len(test)} / {sum(len(t['clicks']) for t in test)}", flush=True)
    model = ClickNet()
    opt = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, LR, total_steps=epochs * -(-len(train) // BATCH))
    lossf = nn.BCEWithLogitsLoss(reduction="none", pos_weight=torch.tensor(8.0))
    t0 = time.time()
    for ep in range(epochs):
        model.train()
        tot = 0.0
        for X, Y, M in batches(train, rng, pool):
            loss = (lossf(model(X), Y) * M).sum() / M.sum()
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            tot += loss.item()
        if ep % 10 == 9 or ep == epochs - 1:
            model.eval()
            print(f"epoch {ep + 1} loss {tot:.3f} test {json.dumps(evaluate(model, test)['edges'])} {time.time() - t0:.0f}s", flush=True)
    model.eval()
    out = Path("runs") / name
    out.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), out / "clicknet.pt")
    metrics = dict(name=name, epochs=epochs, train_takes=len(train), test_takes=len(test),
                   params=sum(p.numel() for p in model.parameters()), test=evaluate(model, test),
                   train=evaluate(model, train), reviewed_takes=sum(1 for t in data if t.get("reviewed_at")))
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1))
    print(json.dumps(metrics))


if __name__ == "__main__":
    main()
