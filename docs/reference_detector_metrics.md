# Reference detector metrics (real models — not simulation)

Use this **only** for narrative context in writeups. Simulation precision/recall from
probabilistic workers are **not** comparable to deployed detectors.

## RGB / visible imagery

- Modern one-stage detectors (e.g. YOLO family) often report **~70–85%** mAP or class-specific
  precision/recall on standard benchmarks, depending on dataset and operating point.
- Your advisor mentioned **YOLOv11** as an example — **verify the exact paper/model card**
  for the metric definition (mAP@0.5, F1 at a chosen threshold, etc.) before citing a number.

## Thermal

- Reported precision/recall depends heavily on dataset (wildfire vs industrial) and resolution.
- Prefer citing a **specific** public thermal-detection or fire-segmentation model or survey paper
  rather than inventing percentages.

## How to use in a report

1. State that sim uses **synthetic** sensing for latency pipeline experiments.
2. Cite **one** RGB and **one** thermal reference with full citation — ballpark “70–80%” is
   qualitative only unless tied to a source.
3. Point to **Alice’s** measured numbers when available for project-specific alignment.

## NeRF / 3D representation (future)

Optional later comparison: bandwidth/latency tradeoffs of **2D image streams** vs a **compact 3D
scene representation** — discussed as a future direction, not required for current latency sweeps.
