# Keyclick-Detector: notes for agents

Public. Start at `README.md`. Release tier: **small** (`v1.N`).

- Training data is **only** Daniel's own recordings (raw originals in HF dataset `danielrosehill/Wake-Words`).
  No third-party audio (ESC-50, FSD50K, etc.): Daniel's decision, 2026-10-29.
- Recorder that produces the data: `~/repos/github/ai-models/Hey-Yossi-Recorder` (`original/`, `key_events_s`).
- Python: `uv`. `data/` is gitignored; `labels/` is committed.
- `labels/reviewed.jsonl` is Daniel's manual review: append-only, never rewrite or regenerate it.
