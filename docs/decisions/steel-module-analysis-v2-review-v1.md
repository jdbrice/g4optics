# Steel Module Analysis-v2 Review, Absorber, and Production Policy

Status: analysis-v2 evidence reviewed; fixed-reference absorber, 10% staged
production, production-program topology, BC-S1-first gate, and human
progression-review contract accepted; execution infrastructure pending

Study preset: `steel-module-scan-v1`

Reviewed: 2026-07-17

## Decision summary

The checksum-sealed analysis-v2 output confirms that the competition between
greater neutron-induced light production and poorer optical collection is
layout-dependent. At the fixed `500 x 500 x 40 mm` steel-slab proxy, a `24 mm`
tile produces approximately `4.84` times the scintillation light of a `4 mm`
tile. The observed aggregate SiPM response shows no resolved gain for
`back-center`, but it increases for `edge-center` and aggregate `back-four`.

The absorber study does not establish transverse-size equivalence at either
the 5% or 10% review band. For this v1 study, `500 mm` is therefore accepted as
a **fixed model reference**, not as a data-proven converged or infinite
absorber. No additional absorber-convergence simulation is required before the
v1 production decision, provided every interpretation remains explicitly
conditional on this fixed slab proxy.

This decision closes the v1 absorber-policy and statistical-design gates. The
accepted target is a 10% relative half-width for the bootstrap 95% interval of
each of the four primary `24/4` contrasts. The heavy-tailed `back-center`
endpoint configurations use predeclared cumulative checkpoints rather than a
single unconditional submission. The policy authorizes preparation of the
production infrastructure; Slurm submission remains blocked until that
infrastructure preserves the fresh-seed, immutable-stage, cumulative-audit,
and non-overwrite requirements below and passes its dry-run validation.

## Evidence identity

| Item | Accepted value |
| --- | --- |
| Campaign | `sm-v1-convergence-pilot-4e9ef8618948` |
| Campaign directory | `steel-module-convergence-pilot-cfd7d974` |
| Simulation commit | `cfd7d974af2c5f40f7d76948172fc87ee70bfa3c` |
| Analysis commit | `ad680deecc7ae6f25ff68e7a0a3b052158a21eb3` |
| Analysis schema | `steel-module-analysis-v2-config-v1` |
| Sample | 30 configurations x 4 blocks x 250 events = 30,000 events |
| Bootstrap | 10,000 event resamples; seed `20260715` |
| Accepted evidence | `true` |
| v1 reconciliation | `passed`; 30 configurations and 3 endpoint ratios |
| Core outputs | `finalized/analysis-v2`; checksum verified |
| Figures | `finalized/analysis-v2-figures`; 5 PNG + 5 PDF; checksum verified |

The finalized campaign, v1 analysis, analysis-v2, figure manifests, and all 120
audited ROOT hashes were reverified after the campaign was copied locally. The
analysis-v2 run used a clean analysis checkout and recorded its analyzer hash,
Python, NumPy, and uproot versions. The v2 outputs are an analysis of the sealed
pilot events; they do not add Geant4 events or alter the v1 analysis.

## Primary endpoint evidence

The four accepted primary contrasts are:

| Primary contrast, 24/4 | Ratio | Bootstrap 95% interval | Max block-omission shift |
| --- | ---: | ---: | ---: |
| Pooled scintillation production | `4.836` | `[4.064, 5.759]` | `4.01%` |
| Observed net, `back-center` | `0.742` | `[0.465, 1.307]` | `19.22%` |
| Observed net, `edge-center` | `2.148` | `[1.626, 2.840]` | `7.74%` |
| Observed net, aggregate `back-four` | `3.424` | `[2.546, 4.649]` | `5.29%` |

The pooled production interval and the `edge-center` and `back-four` net
intervals exclude unity. The `back-center` interval does not; its point
estimate must not be described as a resolved decrease. The corresponding
standardized-response endpoint ratios are `0.773`, `2.056`, and `3.439`, so
pooling the layout-invariant production changes no endpoint interpretation.
Standardized response remains a model-based secondary diagnostic rather than
a replacement for direct observed net response.

The production increase is present across the full fixed-500-mm curve. Pooled
scintillation production relative to `4 mm` is `1.670`, `2.524`, `3.208`,
`3.881`, and `4.836` at `8`, `12`, `16`, `20`, and `24 mm`; every adjacent
thickness interval is above unity. Every thicker point also has an observed
net interval above unity for `edge-center` and `back-four`, while no
`back-center` thickness ratio excludes unity.

`back-four` uses four sensors and four times the nominal active area. Its
aggregate response may be compared across thickness within that layout, but it
must not be presented as a sensor-area-independent efficiency improvement over
a single-SiPM layout.

## Zero response, heavy tails, and block stability

For the 18 nominal `500 mm` configurations, the recorded primary-neutron
interaction fraction is `0.322-0.374`, while the SiPM-zero fraction is
`0.751-0.819`. The high zero fraction is primarily upstream of optical
collection:

- `P(generated > 0 | recorded interaction)` is `0.507-0.643`;
- `P(SiPM > 0 | generated > 0)` is `0.953-1.000`.

Once an event produces optical photons, at least one photon almost always
enters a SiPM proxy. The unstable quantity is the photon magnitude of a small
responding subset, not the zero-event occurrence rate.

The most extreme nominal distribution is `4 mm back-center`: `79.6%` of events
have zero SiPM response, the largest 1% of events carry `58.7%` of the total
response, and the largest 5% carry `84.6%`. This heavy tail explains the wide
`back-center` interval and its `19.22%` primary-ratio block sensitivity. The
pooled production, `edge-center`, and `back-four` primary ratios all remain
inside the 10% block-review line.

Block flags are diagnostics only. Stable zero fractions do not imply stable
mean photon response, and the `1/sqrt(N)` sizing extrapolation is not a
finite-sample guarantee for a rare-shower tail.

## Absorber evidence

Analysis-v2 pools production across the three readout layouts at fixed tile
thickness and absorber size. The endpoint comparisons against the `500 mm`
reference are:

| Thickness | Candidate/500 pooled production | Bootstrap 95% interval | 5% status | 10% status |
| ---: | ---: | ---: | --- | --- |
| `4 mm` | `200/500 = 0.816` | `[0.667, 0.993]` | difference detected, not equivalent | difference detected, not equivalent |
| `4 mm` | `300/500 = 0.849` | `[0.699, 1.036]` | inconclusive | inconclusive |
| `24 mm` | `200/500 = 0.998` | `[0.857, 1.160]` | inconclusive | inconclusive |
| `24 mm` | `300/500 = 0.910` | `[0.780, 1.063]` | inconclusive | inconclusive |

None of the 12 per-layout observed-net candidate/reference intervals
establishes equivalence at either band. The one nominally detected production
difference is an unadjusted pointwise comparison in a heavy-tailed study; it is
a review signal, not a stand-alone geometry claim. Generated-optical and
scintillation rows are identical in this sample and are not independent
confirmations.

Descriptive endpoint point ratios also show why the absorber cannot be treated
as a harmless common-mode factor:

| Absorber transverse size | Pooled production 24/4 | `back-center` net 24/4 | `edge-center` net 24/4 | `back-four` net 24/4 |
| ---: | ---: | ---: | ---: | ---: |
| `200 mm` | `5.91` | `1.61` | `2.60` | `3.79` |
| `300 mm` | `5.18` | `1.71` | `2.31` | `3.54` |
| `500 mm` | `4.84` | `0.742` | `2.15` | `3.42` |

The `200` and `300 mm` values above are descriptive ratios of endpoint point
estimates, not additional primary contrasts with accepted ratio intervals.
They are especially unstable for `back-center`. They nevertheless rule out an
unqualified claim that the fixed-500-mm result is absorber-size-independent.

## Accepted absorber policy

The v1 study adopts the following policy:

1. Every production configuration uses the existing centered
   `500 x 500 x 40 mm` steel slab, with the already accepted zero gap and
   painted/wrapped tile boundary model.
2. The `500 mm` transverse size is part of the frozen study definition. It is
   not labeled "converged," "infinite," or equivalent to the full ePIC
   absorber geometry.
3. The `200` and `300 mm` pilot configurations remain immutable engineering
   diagnostics. They are excluded from the primary six-thickness production
   curves and cannot replace the `500 mm` reference.
4. Every result intended for interpretation or external communication is
   scoped as applying **for the fixed 500 mm transverse steel-slab proxy**.
5. No additional absorber-convergence run is required before the v1 production
   decision. This is a scope decision, not an equivalence finding.
6. Reopen the absorber question only if the study must:
   - predict the official ePIC module rather than the fixed proxy;
   - use an absorber smaller than `500 mm`; or
   - claim absorber-size-independent thickness or layout effects.
7. A reopened convergence study must compare `500 mm` against a larger
   transverse extent or implement the relevant official continuous geometry.
   Merely accumulating more `200/300 mm` events cannot establish that
   `500 mm` has reached a large-size plateau.

Replacing the local slab with official detector geometry would define a new
model baseline and require proportionate geometry validation, regression,
benchmark, and statistical-pilot review. It is not silently folded into this
v1 production campaign.

## Accepted 10% precision target and sizing derivation

The production target applies to four and only four primary `24/4` contrasts:
pooled scintillation production and direct observed net response for each of
the three SiPM layouts. For each contrast, the target is

\[
h = \frac{U-L}{2\hat R} \leq 0.10,
\]

where `L` and `U` are the event-bootstrap 95% interval bounds and `R-hat` is
the point ratio. Reaching this precision target does not require the interval
to exclude unity and is not an effect-direction stopping rule. Secondary
curve ratios, standardized response, collection, and absorber comparisons do
not control production sizing.

Analysis-v2 projected the required sample from the sealed pilot using

\[
N_{raw}=\left\lceil N_{pilot}\left(\frac{h_{observed}}{0.10}\right)^2\right\rceil,
\qquad
N_{buffered}=\left\lceil1.25N_{raw}\right\rceil.
\]

The buffered count is then rounded upward to complete 250-event blocks. For a
single-layout net contrast this is
`ceil(N_buffered / 250)` blocks per endpoint configuration. For pooled
production, `N_pilot = 3,000` events per endpoint is the sum of three
1,000-event layout strata; its buffered count is divided equally across those
three strata before block rounding. The exact accepted 10% arithmetic is:

| Primary contrast | Observed `h` | Pilot events per endpoint | `N_raw` | `N_buffered` | Block calculation | Accepted events/configuration |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| Pooled scintillation production | `0.175253` | `3,000` total (`1,000 x 3`) | `9,215` total | `11,519` total | `ceil(11,519 / (3 x 250)) = 16` | `16 x 250 = 4,000` per layout stratum |
| Observed net, `back-center` | `0.567095` | `1,000` | `32,160` | `40,200` | `ceil(40,200 / 250) = 161` | `40,250` |
| Observed net, `edge-center` | `0.282524` | `1,000` | `7,983` | `9,979` | `ceil(9,979 / 250) = 40` | `10,000` |
| Observed net, `back-four` | `0.307109` | `1,000` | `9,432` | `11,790` | `ceil(11,790 / 250) = 48` | `12,000` |

Here, `events/configuration` means one thickness-layout configuration, not the
sum of both endpoints. The pooled-production sizing floor is therefore
`3 x 4,000 = 12,000` events per endpoint, or `24,000` across its two endpoints.
The actual production endpoint samples are larger because the three layouts
receive their direct-net allocations; the equal-stratum estimator below uses
all of those available events without changing the `1/3` weights. The
corresponding maximum two-endpoint totals for the three direct-net contrasts
are `80,500`, `20,000`, and `24,000` events.

The 5% projections remain preserved in the analysis-v2 evidence but are not
selected for v1. They would require approximately four times the 10% samples
under the same `1/sqrt(N)` assumption and would still omit detector-model,
photoelectron, and electronics systematics.

## Six-thickness allocation

The accepted production allocation uses only the fixed `500 mm` absorber and
the six thicknesses `4, 8, 12, 16, 20, 24 mm`:

| Layout and thickness group | Configurations | Events/configuration | Blocks/configuration | Group events | Group blocks |
| --- | ---: | ---: | ---: | ---: | ---: |
| `back-center`, intermediate `8/12/16/20 mm` | `4` | `4,000` | `16` | `16,000` | `64` |
| `back-center`, primary endpoints `4/24 mm`, maximum | `2` | `40,250` | `161` | `80,500` | `322` |
| `edge-center`, all six thicknesses | `6` | `10,000` | `40` | `60,000` | `240` |
| `back-four`, all six thicknesses | `6` | `12,000` | `48` | `72,000` | `288` |
| **Maximum new production sample** | **18** | -- | -- | **228,500** | **914** |

All six `back-center` thicknesses receive the `4,000`-event pooled-production
minimum. Only its `4` and `24 mm` primary endpoints are eligible for staged
extension toward `40,250`; the four intermediate net points are secondary and
do not inherit the endpoint heavy-tail projection. `edge-center` and
`back-four` use their layout-specific endpoint allocation at all six
thicknesses to keep each full curve on a uniform within-layout sample design.
These choices do not promise 10% precision for every secondary intermediate
ratio.

The pooled-production estimand retains equal `1/3` layout-stratum weights even
when the net-response allocations make the production sample sizes unequal.
At production analysis time, bootstrap each layout at its available event
count, calculate its production mean, and average the three stratum means with
equal weights. Do not concatenate all production events into an
event-count-weighted mean, because that would make the staged `back-center`
extension silently redefine the pooled estimand. This equal-weight rule and
the v2 event-count-weighted pilot rule are numerically identical for the
sealed pilot because all three pilot strata contain 1,000 events.

The 30,000-event pilot is sizing evidence and is not counted in any production
total. Production uses new seed blocks and new immutable manifests. This keeps
the exploratory pilot separate from the sample on which the final production
intervals will be reported. Therefore `228,500` means the maximum number of
**new** events; it must not be reduced to `198,500` by subtracting or pooling
the pilot.

## `back-center` cumulative checkpoints

The two `back-center` primary endpoint configurations use the same new
production sample cumulatively across four predeclared checkpoints:

| Checkpoint | Cumulative events per endpoint configuration | Cumulative blocks/configuration | Added blocks/configuration | Added events across both endpoints | Projected `h` from pilot scaling |
| --- | ---: | ---: | ---: | ---: | ---: |
| `BC-S1` | `4,000` | `16` | `16` | `8,000` | `28.4%` |
| `BC-S2` | `10,000` | `40` | `24` | `12,000` | `17.9%` |
| `BC-S3` | `20,000` | `80` | `40` | `20,000` | `12.7%` |
| `BC-S4` | `40,250` | `161` | `81` | `40,500` | `8.9%` |

The projected column is diagnostic only:
`0.567095 x sqrt(1,000 / N)`. It shows why early stages test heavy-tail scaling
rather than being expected to meet the target immediately.

If all non-staged allocations and `BC-S1` are complete, the new sample is
`156,000` events in `624` blocks. Continuing the two endpoints to `BC-S2`,
`BC-S3`, and `BC-S4` adds, respectively, `12,000/48`, `20,000/80`, and
`40,500/162` events/blocks. The cumulative campaign ceilings are therefore:

| Last completed checkpoint | New production events | 250-event blocks |
| --- | ---: | ---: |
| `BC-S1` | `156,000` | `624` |
| `BC-S2` | `168,000` | `672` |
| `BC-S3` | `188,000` | `752` |
| `BC-S4` | `228,500` | `914` |

At every checkpoint, finalize and checksum the new stage, audit event and seed
identity, form the cumulative production sample, and rerun the primary
contrast plus tail/block diagnostics. Review at least:

- the bootstrap 95% relative half-width of `back-center 24/4`;
- maximum leave-one-250-event-block-out ratio shift;
- zero fraction and positive-event count;
- top-1% and top-5% response shares and the maximum event; and
- observed interval-width change relative to the prior checkpoint and the
  `1/sqrt(N)` projection.

Here LOO means **leave one out**, and this workflow applies it at block level:
**leave one 250-event block out**. For every contributing endpoint block `b`,
recompute the `24/4` ratio as `R[-b]` and record
`abs(R[-b] / R[full] - 1)`. The maximum over all blocks is the maximum LOO
shift. `BC-S1...BC-S4` therefore have `32/80/160/322` endpoint-block
omissions, respectively. This is a sensitivity diagnostic for whether one
block dominates the estimate; it is not a confidence interval, a physical
effect size, a reason to remove that block from the reported sample, or a
replacement for the bootstrap interval.

The predeclared decision rules are:

1. **Precision success:** stop extending `back-center` if `h <= 10%` and the
   maximum leave-one-block-out shift is `<= 10%`.
2. **Continue to the next checkpoint:** the precision target is unmet, the
   block shift is `<= 20%`, and the interval width still decreases relative to
   the prior checkpoint. Progression always requires explicit review; no stage
   automatically submits the next one.
3. **Pause for method review:** the block shift exceeds `20%`, the interval
   fails to narrow, or a newly sampled extreme event materially worsens the
   top-tail diagnostics. Tail shares are review signals, not stand-alone
   pass/fail thresholds.
4. **Hard ceiling:** `BC-S4` is the maximum authorized sample. If the two
   success conditions are still unmet, do not add events automatically; reopen
   the statistical design.

For `BC-S1`, the sealed 1,000-event pilot interval is the comparison baseline
for the width-trend diagnostic only; its events are not merged into the
production estimate. Later stages compare against the immediately preceding
cumulative production checkpoint.

After the fixed allocations complete, the pooled-production, `edge-center`,
and `back-four` primary intervals must also be checked against `h <= 10%`.
Their sample sizes are not adaptive stages: if any one misses the target, do
not extrapolate and submit more events automatically; reopen the sizing
decision with its observed tail and block diagnostics.

Stopping is never based on whether the point ratio appears favorable, crosses
unity, or excludes unity. Because the cumulative percentile-bootstrap
interval is inspected repeatedly and is not adjusted as a formal sequential
confidence procedure, its nominal 95% coverage remains a pragmatic production
estimate rather than a strict confirmatory sequential guarantee. A request for
strict sequential coverage or an independent confirmatory sample would reopen
the statistical design.

## Accepted production-program topology

The maximum production design is frozen as one non-executable parent program
containing the complete 914-task registry, but execution is partitioned into
five immutable incremental child campaigns:

| Child campaign | Included configurations or block increments | Tasks | New events |
| --- | --- | ---: | ---: |
| `FIXED` | four intermediate `back-center` thicknesses at 16 blocks each; all six `edge-center` at 40 blocks each; all six `back-four` at 48 blocks each | `592` | `148,000` |
| `BC-S1` | two `back-center` endpoints, blocks `0-15` per configuration | `32` | `8,000` |
| `BC-S2` | two `back-center` endpoints, blocks `16-39` per configuration | `48` | `12,000` |
| `BC-S3` | two `back-center` endpoints, blocks `40-79` per configuration | `80` | `20,000` |
| `BC-S4` | two `back-center` endpoints, blocks `80-160` per configuration | `162` | `40,500` |
| **Maximum program** | all five children | **914** | **228,500** |

The ranges above are stage-continuous block indices **within each of the two
endpoint configurations**, not one shared block pool. For example,
`4 mm back-center, block 16` and `24 mm back-center, block 16` are distinct
series tasks with distinct seed pairs. A new child never resets an endpoint's
block index to zero. This makes missing, overlapping, or duplicated increments
auditable.

The parent program is an identity and authorization object, not a Slurm
campaign. It must be generated atomically from one clean checkout and bind at
least:

- the full 914-task registry and a full program-plan hash;
- the SMS-016 sizing/stopping policy and this topology decision;
- simulation commit, environment identity, executable, image, and Geant4 data
  identities;
- the sealed pilot identity and seed registry used only for exclusion;
- all five child plan hashes, their roles, the `BC-S1` through `BC-S4` prefix
  order, the allowed cumulative state graph, and exact task-set hashes; and
- one globally audited production seed allocation.

All five children are generated and frozen with the parent so future stages
cannot change code, choose new seeds after seeing data, or drift from the
accepted maximum plan. Each child contains only its new tasks and remains a
complete ordinary campaign for retry, finalization, event audit, and checksum
purposes. The 1,828 Geant4 seed integers are allocated and collision-checked
once across the full parent registry, and must also be disjoint from every
sealed pilot seed. A failed-task retry reuses its task's original seeds;
"fresh" means a newly authorized production block, not a retry with changed
random state.

Program-managed children must fail closed when passed directly to the generic
campaign submitter. A production-specific wrapper authorizes exactly one
child, while each child continues to use its existing child-local immutable
attempt journal and whole-child finalizer. `BC-S2`, `BC-S3`, and `BC-S4`
cannot be submitted without a non-overwriting progression decision bound to
the preceding cumulative analysis checksum.

An incremental child and a cumulative checkpoint are different objects.
`BC-S1` alone is an 8,000-event back-center diagnostic. A complete
four-primary-contrast checkpoint requires `FIXED` plus the contiguous
back-center prefix:

| Full evidence checkpoint | Required children | Cumulative tasks | Cumulative events |
| --- | --- | ---: | ---: |
| `BC-S1` | `FIXED + BC-S1` | `624` | `156,000` |
| `BC-S2` | `FIXED + BC-S1 + BC-S2` | `672` | `168,000` |
| `BC-S3` | `FIXED + BC-S1 + BC-S2 + BC-S3` | `752` | `188,000` |
| `BC-S4` | `FIXED + BC-S1 + BC-S2 + BC-S3 + BC-S4` | `914` | `228,500` |

The program-level cumulative finalizer/analyzer accepts either a checksum-valid
contiguous `BC-S1...BC-Sn` prefix for a back-center-only diagnostic, or that
same prefix plus `FIXED` for a complete four-contrast checkpoint. It refuses
gaps, duplicate series tasks, and overlapping block identities, and writes a
new non-overwriting checkpoint directory. Back-center-only diagnostics may
govern progression before `FIXED` completes, but they are not reported as
complete four-contrast production evidence. Once `FIXED` is included, pooled
production uses all available events with the fixed equal `1/3` layout-stratum
weights defined above.

## Accepted BC-S1-first submission gate

The first production submission contains only the `BC-S1` child: 16 new
250-event blocks for each of the two back-center endpoint configurations,
for `32` tasks and `8,000` events total. `FIXED`, `BC-S2`, `BC-S3`, and
`BC-S4` remain unauthorized while `BC-S1` is running and until its whole-child
finalization, event/seed audit, checksum verification, and BC-only analysis
have been reviewed.

This gate limits the initial exposure to approximately `3.5%` of the maximum
228,500-event program instead of submitting `FIXED + BC-S1`, which would expose
156,000 events (`68.3%`) before the heavy-tail scaling check. It is a method
and resource gate; it does not change the accepted target, design, or estimand,
and it is not an effect-direction test.
The expected `BC-S1` relative half-width is still about `28.4%`; failure to
reach the final 10% target in this first child review is not by itself a
failure.

The review outcome controls authorization as follows:

- `stop-success`: authorize `FIXED`; keep `BC-S2...BC-S4` locked because no
  additional back-center endpoint events are needed;
- `continue`: authorize `FIXED` and make `BC-S2` eligible for the next explicit
  scheduling decision; and
- `pause-review`: keep `FIXED` and every later BC child locked while the method
  is reviewed.

The BC-S1 review uses the already accepted width, leave-one-block-out, zero and
positive count, tail-share, maximum-event, and `1/sqrt(N)` trend diagnostics.
Neither the parent program nor the analyzer may convert a diagnostic outcome
into an automatic child submission. Whether an eligible `FIXED` and `BC-S2`
should run in parallel or sequentially after a `continue` outcome remains the
next scheduling decision.

## Accepted human-readable review and progression record

The cumulative analyzer remains the evidence-producing layer. It computes a
provisional `numeric_eligible_decisions` set, but it must not choose a decision,
mutate program authorization, or submit a child. Eligibility is evaluated in
this fail-closed precedence order:

1. Invalid identity, checksum, audit, or required metric evidence makes the
   analyzer fail before it emits a checksum-valid review report or recordable
   decision. With no valid decision, every child remains locked.
2. The human tail review must explicitly record whether a new extreme event or
   tail concentration materially worsens the diagnostic. If it does, the final
   allowed set is `pause-review` only, regardless of numerical eligibility.
3. At the `BC-S4` hard ceiling, `continue` is never allowed. If `h <= 10%` and
   maximum LOO shift `<= 10%`, the final choices are `stop-success` and
   `pause-review`; otherwise only `pause-review` is allowed.
4. At a valid non-ceiling checkpoint, `h <= 10%` plus maximum LOO shift
   `<= 10%` permits `stop-success` or `pause-review`.
5. Otherwise, at a valid non-ceiling checkpoint, `h > 10%`, maximum LOO shift
   `<= 20%`, and a narrowing interval permit `continue` or `pause-review`.
6. Every other valid state permits `pause-review` only.

`pause-review` is therefore always available for valid evidence. Ratio
direction, crossing unity, and excluding unity never alter eligibility. The
recorder derives the final `allowed_decisions` only after combining numerical
eligibility with the mandatory human tail disposition.

A separate, non-overwriting progression recorder captures the selected human
decision. It must support a check-only pass before writing and bind at least:

- parent program hash, checkpoint identity, exact task-set hash, task/event
  counts, and previous decision hash when one exists;
- finalized, audit, and cumulative-analysis checksum identities;
- simulation and analysis commits plus analyzer identity;
- observed `h`, width trend, maximum LOO shift, tail/zero diagnostics, the
  analyzer's numeric eligibility, and the human tail disposition; and
- selected decision, derived child authorization, reviewer, UTC time, and a
  human rationale.

The record is append-only. Once a downstream submission references its hash,
it cannot be amended retroactively. A managed submitter validates the exact
decision hash and authorized child; recording a decision still does not submit
anything. If `continue` makes both `FIXED` and `BC-S2` eligible, their eventual
invocation records the separate parallel-versus-sequential scheduling choice.

Human review must not require reading raw CSV or JSON. A presentation renderer
reads only checksum-valid cumulative analysis and creates a non-overwriting,
self-contained static review directory that can be downloaded from OSC and
opened locally without a server or network access. It contains at least:

- `index.html`, with all required styles and review data available offline;
- a print-equivalent `review_report.pdf`;
- the underlying review plots in both PNG and PDF form;
- a machine-readable `review_data.json`, provenance manifest, and independent
  `SHA256SUMS`.

The first screen shows checkpoint/program identity, task and event counts,
evidence/checksum status, the provisional numerically eligible choices, and
the required human tail disposition. The report then shows:

1. observed relative-half-width trajectory against event count, the `10%`
   target, and the declared `1/sqrt(N)` projection;
2. every block's leave-one-block-out shift with visible `10%` and `20%` review
   bands, identifying the maximum block and endpoint thickness;
3. zero/positive counts, top-1% and top-5% shares, and maximum-event heavy-tail
   diagnostics;
4. the observed endpoint `24/4` ratio with 95% interval and unity reference;
   and
5. a plain-language decision checklist and expandable provenance.

The interface must label the diagnostic as **Maximum leave-one-block-out shift
(LOO)** and include the definition above; acronym-only labels are not
sufficient. Color is supplemented by text and marker shape. The report may
offer a copyable recorder command, but it has no control that writes a decision,
unlocks a child, or invokes Slurm. These presentation artifacts are derived
review aids, not a replacement for the sealed core evidence or an independent
source of `accepted_statistical_evidence`.

## Claim boundary and next gate

The evidence supports the following statements:

- for the fixed `500 mm` slab proxy, thicker tiles create substantially more
  scintillation light;
- the production gain outweighs collection loss for `edge-center` and
  aggregate `back-four` over the `24/4` endpoint comparison;
- no direction of net change is resolved for `back-center`;
- `back-four` is an aggregate four-sensor result, not a normalized-efficiency
  ranking.

The evidence does not support:

- a claim that `500 mm` is a converged or official ePIC absorber;
- substitution of `200` or `300 mm` on equivalence grounds;
- absorber-size-independent thickness or layout conclusions;
- a universal production event count;
- a guarantee that every secondary ratio will reach 10% precision;
- a claim that checkpoint reuse constitutes a formal sequential 95%
  confidence procedure; or
- detector-model systematic, photoelectron, or electronics-response claims.

The statistical production design, parent/child topology, and initial
BC-S1-only gate are now frozen. If BC-S1 later returns `continue`, the remaining
scheduling decision is whether the newly eligible `FIXED` and `BC-S2` run in
parallel or sequentially. No production child may be submitted until the
parent generator, managed-child submission gate, whole-child finalization,
cumulative audit/analyzer, and non-overwrite contracts are implemented and
validated.
