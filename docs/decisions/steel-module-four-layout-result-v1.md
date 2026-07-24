# Steel Module Four-Layout Result Checkpoint v1

## Status and scope

This checkpoint freezes the completed **single-layer** steel-module comparison
on 2026-07-21. It closes the `edge-two` follow-up and its read-only four-layout
analysis. It does not describe, authorize, or contain results from the separate
ten-layer longitudinal-stack study in SMS-025.

No additional single-layer simulation is required by this checkpoint. The next
open scientific implementation is the six-thickness, ten-layer `edge-two`
stack.

## Frozen evidence identity

The added single-layer campaign is:

```text
campaign_id       sm-v1-production-edge-two-direct-ee66cf4fcace
simulation_commit 8c15d13be55f9a872248949919e28730f10ff487
Slurm job         50629433
tasks             240
events            60,000
configurations    6
layout            edge-two (2 SiPMs)
```

All 240 tasks completed and were selected by the finalizer. The finalized event
audit accepted the sample as statistical evidence. The local archive contains
all 240 task-index-referenced run configs, macros, simulation logs, ROOT files,
summaries, and efficiency maps; all 240 ROOT SHA-256 values reproduce the
frozen event audit.

The combined analysis identity is:

```text
schema             steel-module-four-layout-analysis-v1
analysis commit    3ddcd25f961925532b938f23573dd6eda3d8e0c6
input-set hash     c1ab952d5994e088b149461c92722d5ccafdad491bac0df5f20bf98471784e91
tasks              1,154
events             288,500
configurations     24
layouts            back-center=1, edge-center=1, edge-two=2, back-four=4
bootstrap          10,000 event resamples, seed 20260715, percentile 95% CI
```

The evidence combines the checksum-valid `FIXED`, `BC-S1`, `BC-S2`, `BC-S3`,
`BC-S4`, and `EDGE-TWO` campaigns. Local archive verification found all
`914/914` original-production tasks and all `240/240` edge-two tasks complete,
with no missing or mismatched audited ROOT files. All six finalized checksum
manifests pass.

The retained checksum-manifest identities are:

```text
four-layout core SHA256SUMS
  6e2c4da98dfee4c152221c0a2a44e586c988c53a4c53bf9708b01b9c72ae29a6

four-layout figures SHA256SUMS
  7c92e101fbd61d9d6c7628217ad0360d5aff76a5f5d1dcad93d178928d893c1a

edge-two finalized event_audit.json
  4900debf8d4fcc5f471055b1670c5baaa8018c4b1e71ff25dd231cad99ff04e3
```

The core output contains the ten declared science outputs, `analysis_config.json`,
and `SHA256SUMS`. The rendering output contains seven non-empty PNG/PDF pairs,
plot provenance, and an independent checksum manifest. Both manifests pass in
the downloaded local archive.

## Frozen scientific readout

Across the four layouts, increasing tile thickness from 4 mm to 24 mm raises
generated scintillation by approximately `5.2--5.7x`. Collection efficiency
decreases, but the production gain is larger, so the observed aggregate SiPM
response increases for every layout:

| Layout | 24/4 production | 24/4 collection | 24/4 observed net response |
| --- | ---: | ---: | ---: |
| back-center | 5.6708 | 0.2457 | 1.3935 |
| edge-center | 5.5179 | 0.4336 | 2.3927 |
| edge-two | 5.2924 | 0.4501 | 2.3822 |
| back-four | 5.2052 | 0.6838 | 3.5594 |

For `edge-two` relative to `edge-center` on the same `+X` face:

- aggregate observed response is `1.85--2.12x` across the six thicknesses;
- response per installed sensor and per active area is `0.927--1.059x`;
- every normalized 95% interval contains unity;
- the two installed copies differ by less than `0.9%` of their mean
  collection efficiency at every thickness.

The supported interpretation is that the second side SiPM provides an almost
additive increase in aggregate optical response, with no resolved per-sensor
penalty in this fixed proxy model. The four back-face copies are also balanced:
their maximum within-configuration spread is approximately `3.9%` of the mean.

`back-four` gives the largest aggregate response from 8 through 24 mm, while
its per-sensor and per-active-area response is lower. Thus aggregate response
and sensor-area-normalized response answer different design questions and must
remain separate in presentations and later decisions.

## Interpretation boundary

These results count optical photons entering fixed `2.4 x 2.4 x 0.5 mm` silicon
proxy volumes. They are not photoelectrons, PDE-folded signals, electronics
output, channel cost, or calorimeter energy resolution. The equal-stratum
pooled-production and production-standardized responses remain secondary
diagnostics. The analysis does not automatically select a detector layout.

The existing four-layout numerical outputs and figures are sufficient for
reporting. Raw `FIXED` and `BC-S1...BC-S4` events are needed only to rerun the
core event-level analysis or define new event-level metrics; they are not
inputs to the future ten-layer Geant4 simulation.

## Resume point

When work resumes, preserve this single-layer evidence unchanged and continue
from SMS-025:

```text
steel-module-stack-v1
6 uniform tile thicknesses: 4, 8, 12, 16, 20, 24 mm
10 repeated steel + tile sampling layers
edge-two on every tile
20 globally identified SiPM proxies
ordinary benchmark and campaign path; no managed preflight
```

Geometry implementation and visual acceptance precede any stack benchmark or
production submission.
