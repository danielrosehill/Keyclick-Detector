# Keyclick Detector

Finds keyboard and mouse clicks in the silence at the start and end of voice-training recordings (wake words,
TTS/ASR prompts) so they can be trimmed off. It does not touch audio inside speech.

The use case: a recorder started and stopped with Space or a mouse click leaves the press (and often the key
release ~55 ms later) in the take. A fixed trim either cuts too little (slow presses, long key holds) or too
much. A detector that returns click timestamps lets a trim cut exactly after the last leading click and
before the first trailing one.

Status (2026-10-29): v2 trained on auto-labels; reviewing model/label disagreements before v3.

A click is an **event**: transients less than 0.15 s apart are merged (`keyclick.features.merge_events`), since
one key press shows up as 2-4 transients over ~50-150 ms (finger on the cap, bottoming out, release) but is
heard, and trimmed, as one click. v1 counted transients; from v2 everything counts events.

v2 (`runs/v2/metrics.json`), test split (43 takes, 71 click events), outside speech, against auto-labels: recall
1.00, precision 0.68 (33 extra detections), median onset error 2.5 ms.


v1 (`runs/v1/metrics.json`, transients not events), test split (43 takes, 110 labelled clicks), against auto-labels: recall 0.96,
precision 0.66, median onset error 2.5 ms. Precision is understated: listening to the extra detections shows
several are real clicks the auto-labeller missed (it drops a second onset within 30 ms of the previous click,
and faint presses sit at its 5 dB threshold). Others are likely false: a detection at the very start of a
file (~0.003 s) and some at ~0.09 s. Reviewed labels will settle the numbers.

## Data

Only Daniel Rosehill's own recordings, no third-party audio. Source: the raw originals of the public Hugging
Face dataset [`danielrosehill/Wake-Words`](https://huggingface.co/datasets/danielrosehill/Wake-Words),
recorded with the Hey-Yossi recorder app (since its 1.8, every take keeps an untrimmed original under
`data/<split>/original/<YYYY-MM-DD>/<id>.flac` plus `key_events_s`, the logged times of the start and stop
key/click).

As of 2026-10-29: 247 originals (232 with logged events, 15 older ones without), one speaker, one laptop
(internal mic, laptop keyboard and mouse). What was measured on them:

- Start press: a click within ~±70 ms of the logged start event (~0.32 s in), then usually the key release
  ~55 ms later. Some presses are faint: ~+5 to +7 dB above the room noise above 2 kHz.
- Stop: the capture ends as the stop key goes down, so most originals hold at most the first few ms of the
  stop click. Trailing clicks are therefore rare in this data.
- The dataset's `speech_start_s` is not usable to bound the search: on takes that still contain clicks it
  fires on the click itself. `keyclick.features.speech_span` (100–2000 Hz energy runs ≥ 40 ms) is used instead.

## Pipeline

```bash
uv sync
# 1. Pull the originals and metadata (into data/, gitignored)
uv run python -c 'from huggingface_hub import snapshot_download as s; s("danielrosehill/Wake-Words", repo_type="dataset", local_dir="data/wake-words", allow_patterns=["data/*/original/**", "data/*/metadata.jsonl"])'
# 2. First-pass labels -> labels/auto.jsonl
uv run scripts/autolabel.py
# 3. Train -> runs/<name>/clicknet.pt + metrics.json (CPU, ~2 min for 80 epochs)
uv run scripts/train.py v1 80
# 4. Predict over all takes, flag model/label disagreements -> labels/pred-<run>.jsonl
uv run scripts/predict_all.py v1
# 5. Review the disagreements -> labels/reviewed.jsonl (open http://127.0.0.1:8765), then retrain
uv run scripts/review.py
```

Review page (controls listed at the top of `scripts/review.py`): green boxes are your clicks, dashed cyan
boxes are model detections you haven't marked. Drag to add a click, `+` on a cyan box to adopt it, `×` or
right-click to remove, Z to undo; clicking any box plays just that stretch. Edits save on Enter or when you leave
the take. The queue selector can show already-reviewed takes to re-check them.

Review page listening aids: playback normalised to the take's peak with +12 / +24 dB boosts behind a limiter,
a click filter (plays only >1.5 kHz), and a red trace of >1.5 kHz energy over the waveform. Only the page
amplifies or filters; the training audio is left raw so the model learns on real, noisy recordings.

`huggingface_hub` is not a project dependency; run step 1 with `uv run --with huggingface_hub`.

Model (`keyclick/model.py`): log-mel (48 bands, 10 ms window, 5 ms hop) into a small dilated 1-D CNN (~76k
parameters, ~165 ms receptive field), one click probability per 5 ms frame. Training augments with random gain,
light noise, and clicks cut from other takes pasted into silence (half after the speech, since trailing clicks
are rare in the data). Test set: the dataset's own test split. Still to do: a `trim` CLI, and publishing the
weights and click labels to Hugging Face.

## Label format

`labels/auto.jsonl` / `labels/reviewed.jsonl`, one JSON object per original:

| field | meaning |
|---|---|
| `id`, `split`, `file` | take id, dataset split, path under `data/wake-words/data/` |
| `duration_s` | length of the original |
| `key_events_s` | logged start / stop event times from the recorder (empty for the 15 pre-1.8 takes) |
| `speech_s` | `[start, end]` from `speech_span`, or null |
| `clicks` | `[[onset_s, offset_s], ...]` |
| `flags` | `no_events`, `event_unmatched` (no click within 250 ms of the logged start) |
| `reviewed_at` | reviewed.jsonl only; last line per id wins |
