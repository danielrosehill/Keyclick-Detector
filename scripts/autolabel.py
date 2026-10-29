#!/usr/bin/env python3
"""First-pass click labels for the raw originals of the Wake-Words dataset, for human review.

Input: data/wake-words (snapshot of HF dataset danielrosehill/Wake-Words, data/*/original/** + metadata.jsonl).
Output: labels/auto.jsonl, one line per original:
  {id, split, file, duration_s, key_events_s, speech_s: [start, end] | null, clicks: [[on_s, off_s], ...], flags}
A click is a 5 ms frame whose >2 kHz energy is >= RISE_DB over the median of the previous 100 ms and >= JUMP_DB
over the frame before; frames within MERGE_S are one click, which ends when the energy falls back to within
3 dB of that median (at most MAX_LEN_S). Candidates inside the speech span (keyclick.features.speech_span, +-SPEECH_PAD_S; the dataset's
speech_start_s is unusable here, it fires on the clicks themselves) are dropped: the
detector is only for the silence before and after speech. Transients < EVENT_GAP_S apart are then merged into
one click event (keyclick.features.merge_events). Flags: `event_unmatched` (no click within 250 ms of the
logged start event; the stop event is not checked, as the capture ends as the key goes down so the stop click is
mostly not in the file) and `no_events` (takes whose events were never logged).
"""
import json, sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from keyclick.features import FRAME_S, hp_energy_db, load, merge_events, speech_span  # noqa: E402

RISE_DB, JUMP_DB, MERGE_S, MAX_LEN_S, SPEECH_PAD_S = 5.0, 4.0, 0.03, 0.08, 0.05
DATA = Path("data/wake-words/data")


def detect(e, speech=None):
    base = np.array([np.median(e[max(0, i - 20):i]) if i else e[0] for i in range(len(e))])
    clicks = []
    for i in range(1, len(e)):
        t = i * FRAME_S
        if e[i] - base[i] < RISE_DB or e[i] - e[i - 1] < JUMP_DB:
            continue
        if speech and speech[0] - SPEECH_PAD_S < t < speech[1] + SPEECH_PAD_S:
            continue
        if clicks and t - clicks[-1][1] < MERGE_S:
            continue
        j = i + 1
        while j < len(e) and (j - i) * FRAME_S < MAX_LEN_S and e[j] > base[i] + 3:
            j += 1
        clicks.append([round(t, 3), round(j * FRAME_S, 3)])
    return clicks


def main():
    out = []
    for split in ("train", "test"):
        for line in (DATA / split / "metadata.jsonl").read_text().splitlines():
            r = json.loads(line)
            if not r.get("original_file_name"):
                continue
            x, sr = load(DATA / split / r["original_file_name"])
            speech = speech_span(x, sr)
            clicks = merge_events(detect(hp_energy_db(x, sr), speech))
            events = r.get("key_events_s") or []
            flags = ["no_events"] if not events else []
            if events and all(abs(c[0] - events[0]) > 0.25 for c in clicks):
                flags.append("event_unmatched")
            out.append(dict(id=r["id"], split=split, file=f"{split}/{r['original_file_name']}",
                            duration_s=round(len(x) / sr, 3), key_events_s=events, speech_s=speech,
                            clicks=clicks, flags=flags))
    Path("labels").mkdir(exist_ok=True)
    Path("labels/auto.jsonl").write_text("".join(json.dumps(o) + "\n" for o in out))
    n = np.array([len(o["clicks"]) for o in out])
    print(json.dumps(dict(takes=len(out), clicks=int(n.sum()), per_take=np.bincount(n).tolist(),
                          event_unmatched=sum("event_unmatched" in o["flags"] for o in out),
                          no_events=sum("no_events" in o["flags"] for o in out))))


if __name__ == "__main__":
    main()
