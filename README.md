# Keyclick Detector

Finds keyboard and mouse clicks in the silence at the start and end of voice-training recordings (wake words,
TTS/ASR prompts) so they can be trimmed off. It does not touch audio inside speech.

The use case: a recorder started and stopped with Space or a mouse click leaves the press (and often the key
release ~55 ms later) in the take. A fixed trim either cuts too little (slow presses, long key holds) or too
much. A detector that returns click timestamps lets a trim cut exactly after the last leading click and
before the first trailing one.

Status (2026-10-29): data and labelling. No model trained yet.

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
# 3. Human review -> labels/reviewed.jsonl (open http://127.0.0.1:8765)
uv run scripts/review.py
```

`huggingface_hub` is not a project dependency; run step 1 with `uv run --with huggingface_hub`.

Planned after review: a small frame-level classifier (5 ms frames, click / not click), trained on reviewed
takes with your own clicks re-mixed into the silences of clean takes for augmentation; a held-out set of
reviewed takes as the test set; a `trim` CLI; weights and the click labels published to Hugging Face.

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
