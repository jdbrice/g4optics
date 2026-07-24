# Steel Module Ten-Layer Stack v1

## Status and scope

This document freezes the implementation and execution contract for the
ten-layer longitudinal steel-module follow-up. It is independent of the
completed single-layer four-layout study and does not reinterpret or replace
that evidence.

The implementation uses the ordinary direct campaign route. It must not add a
managed preflight, held-submission control plane, or staged production
authorization mechanism.

## Geometry and source

The preset identity is `steel-module-stack-v1`. It has six configurations with
uniform tile thicknesses of `4, 8, 12, 16, 20, or 24 mm`. Every configuration
contains ten repeated `[40 mm SAE-304 steel][EJ-200 tile]` modules and uses the
same `edge-two` layout on every tile.

For tile thickness `t`, the complete stack length is

```text
L = 10 * (40 mm + t)
```

The stack is centered at `z=0`. Layers are numbered `0...9` from upstream to
downstream, and their centers are

```text
z_steel(i) = L/2 - i*(40 mm + t) - 20 mm
z_tile(i)  = L/2 - i*(40 mm + t) - 40 mm - t/2
```

There is no longitudinal gap at a steel-to-tile or tile-to-next-steel
interface. The painted/wrapped optical boundary is explicitly installed for
every ordered tile-to-neighbour boundary. The stack uses the accepted
polished-front-painted EJ-510 surface and undimpled zero-gap EJ-550 coupling
proxy; it does not add an explicit grease volume.

Each tile has two `2.4 x 2.4 x 0.5 mm` SiPM proxies on its `+X` face at local
centres `(-25, 0)` and `(+25, 0) mm`. Stable identity is

```text
global_sensor_copy = 2 * layer + local_sensor
```

and therefore spans `0...19`.

The source is a centered, point-like pencil neutron gun with `1 GeV` kinetic
energy and direction `(0, 0, -1)`. Its z coordinate is `L/2 + 1.5 mm`.

## Causal optical response

Every optical photon carries its originating tile layer. Scintillation and
Cerenkov photons acquire the layer at creation, while WLS and WLS2 descendants
inherit the original layer. A detected photon with unknown origin invalidates
accepted stack evidence.

For layer `i` and local sensor `j`, the primary collection metric is

```text
local_collection(i,j)
  = photons created in tile i and detected by sensor (i,j)
    / generated optical photons in tile i
```

Generated optical photons are tile-created scintillation plus Cerenkov
photons. Estimates use ratios of sums. A zero generated-light denominator is
reported with an explicit invalid flag, never replaced by numeric zero.

The event record also preserves all-origin sensor counts, the sparse
origin-to-destination transfer matrix, cross-layer leakage, full-stack
any-destination response, layer-resolved steel/tile energy deposition,
charged entries, and primary-neutron interaction counts.

## Statistical and execution policy

The controlling metrics are each thickness's full-stack same-layer causal
collection and same-layer causal net response. Per-layer and per-sensor
results receive intervals and low-statistics flags but do not independently
increase the production sample.

Use 10,000 percentile event-bootstrap resamples with NumPy PCG64 seed
`20260715`. The precision target is 10% relative half-width. Task-block
leave-one-out shift must be at most 20% to authorize production and at most 10%
to call the final result stable.

Execution is fixed to:

1. six one-event geometry-smoke tasks;
2. two 25-event endpoint benchmark tasks (`4` and `24 mm`);
3. four pilot blocks for each thickness (`24` tasks total);
4. at most one ordinary direct production array.

Let `r` be the slower benchmark wall seconds per event. Choose the largest
block size `B` in `{250, 100, 50, 25, 10}` satisfying

```text
1.5 * r * B <= 2700 seconds.
```

The benchmark and all later tasks request one CPU and a one-hour wall limit.
The pilot is cryptographically bound to the benchmark report; that report also
sets later memory to `max(2 GiB, ceil(1.5 * benchmark MaxRSS / GiB))`.

For a pilot estimate with relative half-width `h` and pilot size `N_pilot`,

```text
N_raw    = N_pilot * (h / 0.10)^2
N_target = ceil_to_block(max(N_pilot, 1.25 * N_raw), B)
```

Each thickness uses the larger target from its two controlling metrics. Pilot
events are included in final cumulative evidence; geometry-smoke and benchmark
events are not. Pilot plus production is capped at `120,000` total events, and
the production array is capped at `1,000` tasks. Invalid metrics, unknown
origin detections, a leave-one-out shift above 20%, or either workload cap
causes a review stop instead of automatic expansion.

No second production increment is automatic. The final analysis reports the
achieved precision and leaves any further simulation to a new human decision.

## Acceptance boundary

Implementation acceptance requires all six static geometry checks, human
visual review of the `4` and `24 mm` endpoints, preservation of existing
single-layer behavior, complete stack ROOT causal invariants, checksum-bound
campaign finalization, deterministic analysis, and non-empty headless PNG/PDF
figures.

## Executed evidence and human reasoning record (2026-07-22)

The implementation and all statistical campaigns used simulation commit
`ca276b4d3f9c96d2a7a50e852216f805d66decf6` and frozen executable SHA-256
`a2870a157ec8eabba4a3b79611dd1451a09147fffd6fa19c9edf79770819b83f`.
The interactive `4` and `24 mm` vector-PDF visual records were archived
locally with their generated macros, run configurations, metadata, previews,
and checksums; these are visual sanity evidence, not statistical events.

The ordinary direct execution route was:

| stage | campaign | Slurm job | tasks | events | statistical use |
|---|---|---:|---:|---:|---|
| geometry smoke | `sms-v1-geometry-smoke-4d27dec57f7f` | `50644974` | 6 | 6 | excluded |
| endpoint benchmark | `sms-v1-benchmark-cebf84f8f6c4` | `50645003` | 2 | 50 | excluded |
| pilot | `sms-v1-pilot-efebd30f793b` | `50645158` | 24 | 2,400 | included |
| one-shot production | `sms-v1-production-2cfea7112de7` | `50646154` | 6 | 600 | included |

Every campaign reported zero invalid attempt results, passed its integrated
stack event/transfer audit, and passed finalized checksums. The benchmark
selected `B=100 events/task` and `2 GiB/task` under the frozen guarded
45-minute rule.

### Pilot sizing decision

The pilot supplied four 100-event blocks per thickness. All twelve controlling
estimates were positive, every bootstrap interval had 10,000 valid resamples,
unknown-origin detections were zero, and the maximum task-block leave-one-out
shift was `5.95%`. The collection relative half-widths were only
`0.45%...1.23%`; net response controlled the workload.

The net relative half-widths for `4, 8, 12, 16, 20, 24 mm` were respectively
`10.386%, 8.965%, 10.183%, 9.925%, 10.105%, 9.846%`. Applying the frozen
square-root projection, `1.25` safety factor, and 100-event block rounding
required two additional blocks for each of `4, 12, and 20 mm`, and no
additional block for `8, 16, or 24 mm`. Human review accepted that exact
6-task/600-event plan. Its projected cumulative size was 3,000 events, far
below the 120,000-event and 1,000-task caps.

### Final cumulative full-stack estimates

The checksum-bound cumulative analysis contains 30 tasks and 3,000 events:

| tile (mm) | same-layer collection (95% CI) | same-layer net photons/neutron (95% CI) | relative net half-width | max LOO shift |
|---:|---:|---:|---:|---:|
| 4 | 0.019166 [0.0190042, 0.0193295] | 934.467 [850.564, 1022.56] | 9.203% | 4.49% |
| 8 | 0.0147087 [0.0146430, 0.0147750] | 1384.83 [1264.00, 1512.30] | 8.965% | 5.06% |
| 12 | 0.0123264 [0.0122787, 0.0123759] | 1558.13 [1435.00, 1685.50] | 8.038% | 2.92% |
| 16 | 0.0107366 [0.0106588, 0.0108325] | 1956.01 [1769.18, 2157.44] | 9.925% | 5.75% |
| 20 | 0.00950267 [0.00944084, 0.00959684] | 1712.10 [1577.23, 1848.97] | 7.936% | 3.60% |
| 24 | 0.00850565 [0.00846762, 0.00855688] | 1858.88 [1678.22, 2044.26] | 9.846% | 3.03% |

All six net-response estimates reached the predeclared 10% precision target,
and every final LOO shift was below 10%. All-origin and same-origin net counts
were identical at the recorded precision: the observed cross-layer fraction
was zero for every thickness, and the causal audit found no unknown-origin
detections.

Collection decreases monotonically with thickness. From `4` to `24 mm`, the
collection ratio is approximately `0.444`, while the net-response ratio is
approximately `1.99`. Their decomposition implies an approximately `4.48`
increase in generated optical photons per incident neutron, so the production
gain is larger than the collection loss in this fixed ten-layer model. The
point estimate is highest at `16 mm`, but the `16, 20, and 24 mm` marginal
intervals overlap substantially; this evidence does not establish `16 mm` as
a unique optimum. The result supports a broad high-response region at larger
thickness, not an automatic detector selection.

The final analyzer also preserves every layer/sensor estimate, longitudinal
energy and light profile, sparse transfer matrix, tail diagnostic, and LOO
record in checksum-bound CSV/JSON outputs. Those files, rather than this
full-stack summary alone, are authoritative for later layer-by-layer or
individual-SiPM interpretation. Per the frozen policy, no second production
increment is authorized automatically.

### Layer, sensor, longitudinal, and tail review

The final sample contains 600 events for `4, 12, and 20 mm` and 400 events for
`8, 16, and 24 mm`. All 120 layer/sensor estimates have valid denominators and
none carries the analyzer's low-statistics flag. Summing across the ten layers,
sensor 0 and sensor 1 contribute between `49.31%` and `50.69%` of the two-SiPM
response for every thickness. The largest full-stack sensor imbalance is only
`1.38%`, at `16 mm`. Six of the 60 layer-level sensor pairs have disjoint
marginal intervals, but no paired sensor-difference or multiplicity-controlled
test was predeclared; those isolated differences are diagnostics, not evidence
of a systematic sensor asymmetry.

The combined two-SiPM collection is comparatively uniform along the stack at
a fixed thickness. Its population coefficient of variation across the ten
layer point estimates ranges from `0.84%` to `2.29%`. The generated-light
profile is instead strongly front-loaded: layers `0...2` contain approximately
`48.6%...56.3%` of generated light, and layers `0...4` contain
`70.4%...79.4%`. The generated-light centroid lies between layers `2.60` and
`3.18`; the detected-light centroid lies between layers `2.62` and `3.20`.
Primary-neutron tile-entry fractions fall from approximately `74.8%...79.7%`
at layer 0 to `3.5%...5.5%` at layer 9. These observations describe the
longitudinal sampling behavior; they do not imply that charged shower
particles remain in one layer.

The optical transfer matrix contains exactly zero off-diagonal detected
photons and zero unknown-origin detections. This means that, with the frozen
paint/wrap boundaries, every detected optical photon was assigned to the two
SiPMs on its origin tile. It is an optical-transport result, not a statement
that the hadronic shower lacks cross-layer particle or energy transport.

Detected-light event distributions remain non-Gaussian but are much less
zero-dominated than the earlier single-layer neutron evidence. Their zero
fractions are `3.67%...6.67%`; the largest 1% of events contribute
`4.77%...7.31%` and the largest 5% contribute `18.47%...22.49%` of detected
light. Together with final net-response LOO shifts of only `2.92%...5.75%`,
this supports the reported aggregate stability while retaining the heavy-tail
caveat for interpretation.
