# Steel Module Direct BC-S1 Execution and Analysis Record

Status: direct BC-S1 through BC-S4 execution, finalization, cumulative analysis,
and human review completed; final tail disposition `no-material-worsening`;
back-center progression decision `stop-success`; no further BC child

Study preset: `steel-module-scan-v1`

Record date: 2026-07-20 through 2026-07-21

## 1. Purpose of this record

This document is the human-readable audit trail for the first production-sized
back-center endpoint increment. It records the scientific setup, the pilot
reasoning, the change from the abandoned managed/preflight route to the ordinary
campaign route, the actual Slurm execution, finalization, preliminary statistics,
and the remaining review gate.

The machine-readable companion is produced by:

```bash
python3 hpc/osc/analyze_steel_module_direct_bc_s1.py \
  --campaign-dir "$DIRECT" \
  --pilot-campaign-dir "$CAMPAIGN"
```

It writes an immutable `finalized/direct-analysis` directory containing input
identities, endpoint estimates, 32 leave-one-block-out records, heavy-tail
diagnostics, pilot-to-production precision trajectory, a human-readable summary,
and `SHA256SUMS`. It neither submits Slurm work nor runs Geant4.

## 2. Fixed scientific model

The professor's updated request and the accepted
[`steel-module-scan-v1`](steel-module-scan-v1.md) decisions define this model:

| Component | Fixed value |
| --- | --- |
| Primary particle | neutron |
| Authoritative beam quantity | `1 GeV` kinetic energy |
| Beam | centered point gun, normal incidence, pencil direction |
| Tile | `100 x 100 mm`; endpoint thicknesses `4` and `24 mm` in BC-S1 |
| Steel | SAE 304 proxy, `500 x 500 x 40 mm` |
| Steel-to-tile spacing | no air gap |
| Tile boundary | painted/wrapped optical treatment retained at the steel-facing boundary |
| Optical baseline | polished-front-painted EJ-510 plus undimpled zero-gap EJ-550 coupling proxy |
| Readout layout | one SiPM at the center of the downstream `-Z` face (`back-center`) |
| Physics/reference scope | results apply to the fixed `500 mm` transverse steel-slab proxy |

The `500 mm` transverse size is a fixed v1 model reference. The pilot did not
prove it equivalent to an infinite absorber or to the complete official ePIC
calorimeter geometry.

## 3. Evidence path before production

The accepted sequence before BC-S1 was:

1. Geometry and source decisions were frozen in
   [`steel-module-scan-v1.md`](steel-module-scan-v1.md).
2. The three SiPM layouts and representative thicknesses passed manual geometry
   visualization.
3. The 18-task geometry smoke passed finalization and event audit.
4. The four-task benchmark established that a 250-event block fit comfortably
   inside the one-hour task target.
5. The 120-task convergence pilot sampled 30 configurations with four independent
   250-event blocks each: 30,000 audited events total.
6. Analysis-v2 identified `back-center 24/4` as the limiting heavy-tailed primary
   contrast: ratio `0.742`, 95% interval `[0.465, 1.307]`, relative half-width
   `56.71%`, and maximum pilot block-omission shift `19.22%`.
7. The accepted staged policy therefore assigned BC-S1 exactly 16 new blocks at
   each endpoint: 4,000 events at `4 mm`, 4,000 events at `24 mm`, 32 tasks and
   8,000 events total.

The sealed pilot events are a historical width/tail baseline only. They are not
pooled into the new production estimator.

## 4. Submission-route correction

An earlier Phase-2B/Phase-2C design introduced held preflights, readiness locks,
successor executions, intent journals, and a separate control plane. It produced
useful incident history but was disproportionate to this ordinary 32-task Geant4
array. Several pre-simulation jobs failed in that route without consuming a
production event or production seed.

On 2026-07-20 the current route was deliberately simplified:

- preserve the already frozen BC-S1 task/seed mapping and physics identities;
- convert it into a standard steel-module campaign;
- use the same ordinary submission/finalization path that had already completed
  the 120-task pilot;
- retain campaign, attempt, ROOT, event-audit, and checksum evidence;
- remove held preflight, readiness, intent, and successor requirements from the
  current BC-S1 path.

Historical managed/preflight files remain in the repository only for incident
provenance. They are not the current runbook and must not be interpreted as a
prerequisite for BC-S1 analysis or later ordinary campaign execution.

## 5. Actual BC-S1 execution

| Identity | Recorded value |
| --- | --- |
| Direct-route implementation commit | `ddea5462c3a01f39a83af3b9cfa964053cbde626` |
| Campaign | `sm-v1-production-bc-s1-direct-c89b659643dd` |
| OSC campaign directory | `$WORK/campaigns/steel-module-production-bc-s1-direct` |
| Slurm array job | `50615141` |
| Shape | `4/24 mm`, `back-center`, `500 mm` absorber, blocks `0-15` |
| Tasks | `32` |
| Events | `8,000` (`250` per task; `4,000` per endpoint) |
| Completion | `32/32 COMPLETED`, exit `0:0` |
| 4 mm task elapsed range | approximately `1:12-2:09` |
| 24 mm task elapsed range | approximately `5:39-10:49` |
| Finalization | passed |
| Integrated event audit | passed |
| Invalid attempt results ignored | `0` |

The successful execution demonstrated that no special compute-node preflight was
needed for this production increment. The ordinary workflow reached finalized
scientific data directly.

## 6. Exact command route

The campaign was generated and checked from the clean OSC checkout:

```bash
MANAGED="$WORK/campaigns/steel-module-production-bc-s1"
DIRECT="$WORK/campaigns/steel-module-production-bc-s1-direct"
FROZEN="$WORK/frozen-campaign-sources"

python3 hpc/osc/generate_steel_module_direct_bc_s1_campaign.py \
  --managed-child-dir "$MANAGED" \
  --out-dir "$DIRECT"

python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir "$DIRECT" \
  --project-root "$REPO" \
  --g4-data-root "$DATA_ROOT" \
  --check-only
```

The accepted check-only output reported exactly `32` tasks and `8,000` events.
The ordinary Slurm array was then submitted:

```bash
python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir "$DIRECT" \
  --project-root "$REPO" \
  --account PAS2524 \
  --g4-data-root "$DATA_ROOT" \
  --frozen-root "$FROZEN"
```

After all 32 tasks completed, the standard finalizer produced the sealed event
evidence:

```bash
python3 hpc/osc/finalize_steel_module_campaign.py \
  --campaign-dir "$DIRECT"
```

The preliminary generic analysis was run as:

```bash
python3 hpc/osc/analyze_steel_module_campaign.py \
  --campaign-dir "$DIRECT" \
  --production-block-events 250
```

The dedicated review adapter is the next and final analysis command for BC-S1:

```bash
python3 hpc/osc/analyze_steel_module_direct_bc_s1.py \
  --campaign-dir "$DIRECT" \
  --pilot-campaign-dir "$CAMPAIGN"
```

## 7. Preliminary finalized result

The generic 10,000-resample event bootstrap produced:

| 24/4 quantity | Point ratio | Bootstrap 95% interval |
| --- | ---: | ---: |
| Scintillation production | `6.117831` | `[5.270064, 7.116985]` |
| Optical collection | `0.268996` | `[0.221707, 0.329606]` |
| Observed net SiPM response | `1.645670` | `[1.286898, 2.106082]` |

The factorization is physically interpretable:

```text
production gain x collection ratio = observed net ratio
6.117831       x 0.268996         = 1.645670
```

Within this fixed model, increasing the tile from `4` to `24 mm` produces about
`6.12` times as much scintillation light while retaining about `26.9%` of the
relative collection efficiency. The production increase is stronger: the mean
observed SiPM response is about `64.6%` higher, and this preliminary interval
excludes unity.

The net interval's relative half-width is `24.89%`, so BC-S1 has not reached the
accepted `10%` precision target. Effect direction and precision are distinct:
the current sample resolves a positive net ratio while still being too broad to
finish the staged precision program automatically.

## 8. Dedicated LOO/tail evidence contract

`analyze_steel_module_direct_bc_s1.py` reads only existing checksum-valid inputs:

- the direct campaign and its frozen 32-task plan;
- finalized validation, configuration totals, event audit, and 32 audited ROOT
  files;
- the sealed pilot's checksum-valid `analysis-v2` primary identity and the eight
  back-center endpoint pilot ROOT blocks from which the already-tested
  production-analysis core recomputes the baseline distribution diagnostics.

It reuses the reviewed production-analysis core and emits:

- `endpoint_estimates.csv`;
- `distribution_diagnostics.csv`;
- `pilot_distribution_diagnostics.csv`;
- `block_loo.csv` with exactly 32 omissions;
- `precision_trajectory.csv`;
- `primary_contrast.json`;
- `numeric_eligibility.json`;
- `pathway_decomposition.json`;
- `analysis_config.json` and `summary.md`;
- `SHA256SUMS` covering every output.

The analysis records its Git commit/tree, analyzer/core hashes, Python/NumPy/
uproot versions, campaign/finalization/pilot identities, Slurm attempt/job IDs,
the finalized task-index and event-audit hashes, and one digest over the 32
audited ROOT hashes. It refuses to overwrite an existing output directory.

This is proportional evidence hardening, not a return to the abandoned control
plane: there is no preflight, checkpoint, readiness lock, intent, scheduler
contact, or new simulation.

## 9. Completed progression review

The dedicated direct analysis ran successfully against the checksum-valid
finalized campaign. Its primary result is:

| Review quantity | Result |
| --- | ---: |
| Observed net SiPM response `24/4` | `1.64567` |
| Bootstrap 95% interval | `[1.27719, 2.12547]` |
| Relative half-width | `25.77%` |
| Accepted precision target | `10%` |
| Maximum leave-one-block-out shift | `5.03%` |
| Maximum-shift source | `4 mm`, block `6` |
| Interval narrower than sealed pilot | `true` |
| Numeric choices | `continue, pause-review` |

Human review used three independent questions:

1. Is the production interval narrower than the sealed pilot interval?
2. Is the maximum leave-one-250-event-block-out ratio shift no greater than the
   accepted `20%` continuation ceiling?
3. Did the new 4 mm or 24 mm SiPM distribution show a material worsening in
   zero rate or top-1%/top-5% tail concentration relative to the pilot?

The observed tail comparison was:

| Sample | Tile | Zero fraction | Top 1% share | Top 5% share | Maximum |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sealed pilot | 4 mm | `79.60%` | `58.67%` | `84.59%` | `26,045` |
| BC-S1 production | 4 mm | `81.75%` | `45.71%` | `79.33%` | `20,704` |
| Sealed pilot | 24 mm | `76.50%` | `29.61%` | `65.06%` | `9,294` |
| BC-S1 production | 24 mm | `74.65%` | `31.86%` | `64.52%` | `44,395` |

For 4 mm, the zero fraction increased by `2.15` percentage points, while both
tail-concentration shares and the maximum decreased. For 24 mm, the zero
fraction improved, the top-5% share was essentially unchanged, and the top-1%
share increased by only `2.25` percentage points. The larger 24 mm maximum was
observed in a production sample four times the pilot size; it is therefore not
a sample-size-normalized worsening metric by itself. The maximum block-omission
effect remained only `5.03%`, so no single 250-event block controls the primary
ratio.

The accepted human and progression record on 2026-07-21 is therefore:

```text
tail_disposition    no-material-worsening
progression_decision continue
next_child           BC-S2
automatic_submission false
```

This decision makes only the separately generated direct BC-S2 increment
eligible. It does not submit Slurm work and does not authorize `FIXED`, `BC-S3`,
or `BC-S4`.

## 10. Direct BC-S2 campaign contract

BC-S2 adds blocks `16-39` independently for each endpoint:

- `4 mm back-center`: 24 new 250-event blocks;
- `24 mm back-center`: 24 new 250-event blocks;
- total: 48 tasks and 12,000 new events;
- cumulative after BC-S1 plus BC-S2: 40 blocks and 10,000 production events per
  endpoint, or 20,000 endpoint events total.

The direct BC-S2 generator must read the accepted frozen production program,
the completed direct BC-S1 campaign, and its checksum-valid `direct-analysis`.
It reuses the already allocated blocks `16-39` and their 96 production seeds,
records the complete predecessor evidence hashes and this human decision, and
writes a normal campaign with no preflight or scheduler action. Submission
remains a separate explicit step after campaign check-only reports exactly 48
tasks and 12,000 events.

## 11. Actual BC-S2 execution and cumulative-analysis contract

The accepted direct BC-S2 increment was generated, checked, submitted through
the ordinary array route, and finalized successfully:

| Identity | Recorded value |
| --- | --- |
| BC-S2 implementation commit | `8add11ffcb13edef63ca085ee91cfc1849b43820` |
| Campaign | `sm-v1-production-bc-s2-direct-636265fe741b` |
| OSC campaign directory | `$WORK/campaigns/steel-module-production-bc-s2-direct` |
| Slurm array job | `50617964` |
| Shape | `4/24 mm`, `back-center`, `500 mm` absorber, blocks `16-39` |
| Tasks | `48` |
| New events | `12,000` (`250` per task; `6,000` per endpoint) |
| Completion | `48/48 COMPLETED`, exit `0:0` |
| 4 mm task elapsed range | `1:10-2:18` |
| 24 mm task elapsed range | `4:53-10:53` |
| Finalization and integrated event audit | passed |
| Invalid attempt results ignored | `0` |
| Finalized checksum verification | exit `0` |

Together, BC-S1 and BC-S2 now provide blocks `0-39`, 40 blocks and 10,000
production events per endpoint, 80 tasks and 20,000 endpoint events total. The
sealed pilot remains a historical precision/tail baseline and is not pooled
into this production estimate.

The cumulative analysis command is:

```bash
BCS1="$WORK/campaigns/steel-module-production-bc-s1-direct"
BCS2="$WORK/campaigns/steel-module-production-bc-s2-direct"

python3 hpc/osc/analyze_steel_module_direct_bc_s2.py \
  --bc-s1-campaign-dir "$BCS1" \
  --bc-s2-campaign-dir "$BCS2"
```

It writes `BCS2/finalized/direct-cumulative-analysis` without overwrite and
records:

- cumulative endpoint estimates and generated/scintillation production,
  collection, and observed-net `24/4` decomposition;
- a pinned 10,000-resample event bootstrap and 95% percentile interval;
- exactly 80 leave-one-250-event-block-out records over both endpoints;
- zero fractions, quantiles, top-1%/top-5% concentration and maximum-event
  provenance separately for BC-S1, BC-S2 and the cumulative sample;
- independent-increment BC-S2-versus-BC-S1 consistency diagnostics, explicitly
  labeled as not being an equivalence test and not controlling progression;
- the precision trajectory, numeric review choices, input/analyzer provenance,
  a human-readable summary and complete checksums.

This adapter reads only the two finalized ordinary campaigns. It does not
contact Slurm, run Geant4, record the human tail decision, or authorize BC-S3.
After it runs, the cumulative tail and block-stability evidence still requires
human review before any next campaign is generated.

## 12. Completed BC-S2 cumulative review and BC-S3 authorization

The cumulative adapter ran from analysis commit
`8a297905922a4a9f7ce30ed1afc3cf04e6d6750d`. It reconciled all 80 finalized
tasks and 20,000 events, verified both campaign/finalization identities, and
produced checksum-valid `direct-cumulative-analysis` evidence.

The cumulative primary result is:

| Review quantity | Result |
| --- | ---: |
| Generated optical production `24/4` | `5.65886` |
| Scintillation production `24/4` | `5.65886` |
| Optical collection `24/4` | `0.26058` |
| Observed net SiPM response `24/4` | `1.4745857` |
| Bootstrap 95% interval | `[1.2463549, 1.7456003]` |
| Relative half-width | `16.93%` |
| Accepted precision target | `10%` |
| Maximum leave-one-block-out shift | `3.94%` |
| Maximum-shift source | `4 mm`, block `34` |
| Interval narrower than BC-S1 | `true` |
| Numeric choices | `continue, pause-review` |

The independent-increment BC-S2/BC-S1 ratio-of-ratios was `0.830678` with
95% interval `[0.593016, 1.16793]`. This did not detect an increment difference,
but it was not an equivalence test and did not control progression.

The human tail review compared the two independent increments and their
cumulative sample:

| Sample | Tile | Zero fraction | Top 1% share | Top 5% share | Maximum |
| --- | ---: | ---: | ---: | ---: | ---: |
| BC-S1 | 4 mm | `81.75%` | `45.71%` | `79.33%` | `20,704` |
| BC-S2 | 4 mm | `80.75%` | `46.88%` | `79.53%` | `34,679` |
| cumulative | 4 mm | `81.15%` | `46.46%` | `79.45%` | `34,679` |
| BC-S1 | 24 mm | `74.65%` | `31.86%` | `64.52%` | `44,395` |
| BC-S2 | 24 mm | `76.97%` | `31.42%` | `66.49%` | `27,920` |
| cumulative | 24 mm | `76.04%` | `31.63%` | `65.66%` | `44,395` |

For 4 mm, BC-S2 improved the zero fraction by `1.00` percentage point while
top-1% and top-5% concentration changed only `+1.17` and `+0.20` points. For
24 mm, the zero fraction and top-5% share increased by `2.32` and `1.97`
points, while top-1% decreased by `0.44` point and the raw maximum decreased.
The larger BC-S2 4 mm maximum occurred in block `34`, but omitting that block
changed the cumulative primary ratio by only `3.94%`. There is therefore no
consistent tail deterioration and no block that materially controls the result.

The accepted human and progression record on 2026-07-21 is:

```text
tail_disposition     no-material-worsening
progression_decision continue
next_child           BC-S3
automatic_submission false
```

BC-S3 uses the previously frozen production-program blocks `40-79` independently
for each endpoint:

- `4 mm back-center`: 40 new 250-event blocks;
- `24 mm back-center`: 40 new 250-event blocks;
- total: 80 tasks and 20,000 new events;
- cumulative after completion: 80 blocks and 20,000 production events per
  endpoint, or 40,000 endpoint events total.

Simple `1/sqrt(N)` scaling projects the current `16.93%` half-width to about
`11.97%` after BC-S3. This projection does not guarantee the target; BC-S3 must
be finalized and reviewed before deciding whether BC-S4 is needed.

The direct BC-S3 generator must read the accepted frozen production program,
the finalized direct BC-S2 campaign, and the checksum-valid cumulative analysis.
It binds the exact reviewed metrics and this decision, reuses the already
allocated 160 production seeds, and emits a normal campaign with no preflight
or scheduler action. Submission remains a separate explicit step after
check-only reports exactly 80 tasks and 20,000 events.

## 13. Actual BC-S3 execution and cumulative-analysis contract

The separately authorized BC-S3 increment completed through the ordinary array
route without a preflight:

| Identity | Recorded value |
| --- | --- |
| BC-S3 infrastructure commit | `d0da7b48ab64221d462587249542600249c37a00` |
| Campaign | `sm-v1-production-bc-s3-direct-addb00f16a9c` |
| OSC campaign directory | `$WORK/campaigns/steel-module-production-bc-s3-direct` |
| Slurm array job | `50618229` |
| Shape | `4/24 mm`, `back-center`, `500 mm` absorber, blocks `40-79` |
| Tasks | `80` |
| New events | `20,000` (`250` per task; `10,000` per endpoint) |
| Finalization and integrated event audit | passed for all 80 tasks |
| Invalid attempt results ignored | `0` |
| Finalized checksum verification | exit `0` |

BC-S1 through BC-S3 therefore contain the exact contiguous block range
`0-79`: 80 blocks and 20,000 production events per endpoint, 160 tasks and
40,000 endpoint events total. The three increments retain disjoint task IDs and
320 unique production seeds. The sealed pilot remains a historical baseline
and is not pooled into the production estimate.

The cumulative analysis command is:

```bash
BCS1="$WORK/campaigns/steel-module-production-bc-s1-direct"
BCS2="$WORK/campaigns/steel-module-production-bc-s2-direct"
BCS3="$WORK/campaigns/steel-module-production-bc-s3-direct"

python3 hpc/osc/analyze_steel_module_direct_bc_s3.py \
  --bc-s1-campaign-dir "$BCS1" \
  --bc-s2-campaign-dir "$BCS2" \
  --bc-s3-campaign-dir "$BCS3"
```

It atomically writes `BCS3/finalized/direct-cumulative-analysis` and refuses an
existing target. Before calculating statistics it validates all three campaign,
finalization, event-audit, ROOT checksum, runtime/source, block, task and seed
identities and binds the accepted BC-S2 cumulative analysis and progression
record.

The output includes cumulative production/collection/net decomposition, a
pinned 10,000-resample event-bootstrap interval, exactly 160
leave-one-250-event-block-out records, separate BC-S1/BC-S2/BC-S3/cumulative
tail diagnostics, and all three pairwise independent-increment comparisons.
Those increment comparisons are diagnostics rather than equivalence tests and
do not control progression. The analysis also reports a simple BC-S4 precision
projection, but it does not select a decision, submit Slurm work, or run Geant4.
A human must review the BC-S3 and cumulative tails before any BC-S4 decision.

## 14. Completed BC-S3 cumulative review and BC-S4 authorization

The cumulative BC-S1+BC-S2+BC-S3 adapter ran from analysis commit
`5bd9c258f1161db553c35816986f675c57512db9`. It reconciled all 160 finalized
tasks and 40,000 events, verified the three campaign/finalization identities,
and produced checksum-valid evidence (`CHECKSUM_EXIT=0`).

The cumulative primary result is:

| Review quantity | Result |
| --- | ---: |
| Generated optical production `24/4` | `5.75031` |
| Scintillation production `24/4` | `5.75031` |
| Optical collection `24/4` | `0.250457` |
| Observed net SiPM response `24/4` | `1.44020` |
| Bootstrap 95% interval | `[1.29124, 1.60717]` |
| Relative half-width | `10.97%` |
| Accepted precision target | `10%` |
| Maximum leave-one-block-out shift | `1.90%` |
| Maximum-shift source | `4 mm`, block `34` |
| Interval narrower than cumulative BC-S2 | `true` |
| Numeric choices | `continue, pause-review` |

The three independent-increment ratio-of-ratios intervals all included unity:
BC-S2/BC-S1 was `0.830678 [0.596464, 1.15608]`, BC-S3/BC-S2 was
`1.02851 [0.791309, 1.32873]`, and BC-S3/BC-S1 was
`0.854363 [0.638344, 1.14289]`. These comparisons did not detect a difference,
but they are not equivalence tests and do not control progression.

The BC-S3 tail was judged not to have materially worsened. At 4 mm, its zero
fraction (`81.67%`), top-1% share (`45.85%`), top-5% share (`80.66%`) and
maximum (`26,177`) remained comparable to the earlier increments. At 24 mm,
the BC-S3 zero fraction (`75.75%`), top-1% share (`28.12%`), top-5% share
(`63.93%`) and maximum (`19,979`) were stable or lower than BC-S2. The maximum
cumulative block sensitivity fell from `3.94%` to only `1.90%`; block 34 no
longer materially controls the result despite remaining the largest omission.

The accepted human and progression record on 2026-07-21 is:

```text
tail_disposition     no-material-worsening
progression_decision continue
next_child           BC-S4
automatic_submission false
```

`stop-success` is not numerically eligible because the precommitted relative
half-width target is `10%` and the observed value remains `10.97%`. BC-S4 is
the final frozen back-center increment: blocks `80-160` for each endpoint, 81
new 250-event blocks per endpoint, 162 tasks and 40,500 new events. Completion
would give 161 blocks and 40,250 production events per endpoint. Simple
`1/sqrt(N)` scaling projects a relative half-width of about `7.7%`; this is a
review aid, not a guaranteed result. At the BC-S4 ceiling, `continue` is never
allowed: the final review may select only `stop-success` or `pause-review`
according to the precommitted precision, LOO and tail rules.

This decision authorizes only generation of a separate ordinary direct BC-S4
campaign. It does not submit Slurm work, run Geant4, or authorize `FIXED`.

## 15. Completed BC-S4 execution and final-analysis contract

The separately authorized BC-S4 increment ran through the ordinary campaign
route without a preflight:

| Execution quantity | Recorded value |
| --- | --- |
| Campaign | `sm-v1-production-bc-s4-direct-46bf4fa3229b` |
| Infrastructure commit | `31d2e2ea95c8d7ae4aa6a5300e0c41a26bed723b` |
| Slurm array | `50618568` |
| Blocks per endpoint | `80-160` (81 blocks) |
| Tasks / events | `162 / 40,500` |
| Scheduler result | all array tasks `COMPLETED / 0:0` |
| Finalizer result | 162 selected and event-audited; 0 invalid results ignored |
| Finalized checksum | `FINALIZED_CHECKSUM_EXIT=0` |

Together, the four direct children now contain the exact contiguous production
block range `0-160`: 161 independent 250-event blocks and 40,250 events at
each of the 4 mm and 24 mm endpoints, or 322 tasks and 80,500 events total.

The final cumulative adapter is invoked with:

```bash
BCS1="$WORK/campaigns/steel-module-production-bc-s1-direct"
BCS2="$WORK/campaigns/steel-module-production-bc-s2-direct"
BCS3="$WORK/campaigns/steel-module-production-bc-s3-direct"
BCS4="$WORK/campaigns/steel-module-production-bc-s4-direct"

python3 hpc/osc/analyze_steel_module_direct_bc_s4.py \
  --bc-s1-campaign-dir "$BCS1" \
  --bc-s2-campaign-dir "$BCS2" \
  --bc-s3-campaign-dir "$BCS3" \
  --bc-s4-campaign-dir "$BCS4"
```

It must reconcile the checksum-valid S1-S3 predecessor result, all four
campaign/finalization identities, 644 unique production seeds, and the exact
block registry. The fixed statistics remain a 10,000-resample event bootstrap
and 322 leave-one-block-out evaluations. BC-S4 is the precommitted hard
ceiling, so the machine-readable numeric choices can never contain
`continue`: they are `stop-success, pause-review` only when both the 10%
relative-half-width and 10% maximum-LOO success rules pass, and otherwise only
`pause-review`. A human must still review the BC-S4 and cumulative tails before
recording the final interpretation.

### Final cumulative result and accepted human decision

The adapter ran from analysis commit
`9a74465aea6ee8bb482081694b28314918ee9cee`. Its output checksum verification
returned `CHECKSUM_EXIT=0` and reconciled all 322 tasks and 80,500 production
events. The final cumulative result is:

| Review quantity | Result |
| --- | ---: |
| Generated optical production `24/4` | `5.67082` |
| Scintillation production `24/4` | `5.67082` |
| Optical collection `24/4` | `0.245724` |
| Observed net SiPM response `24/4` | `1.39346` |
| Bootstrap 95% interval | `[1.29104, 1.50771]` |
| Relative half-width | `7.77%` |
| Accepted precision target | `10%` |
| Maximum leave-one-block-out shift | `0.89%` |
| Maximum-shift source | `4 mm`, block `34` |
| Interval narrower than cumulative BC-S3 | `true` |
| Numeric choices at hard ceiling | `stop-success, pause-review` |

The BC-S4 independent increment is consistent with the earlier increments.
In particular, BC-S4/BC-S3 for the observed net `24/4` ratio is `0.959854`
with interval `[0.800481, 1.15038]`; this diagnostic detects no difference but
is not an equivalence test.

The human tail review found no material worsening. For 4 mm, BC-S4 has
`82.01%` zero response, `48.90%` top-1% share, `81.83%` top-5% share, and a
maximum of `33,450`; the maximum remains below the earlier BC-S2 maximum of
`34,679`. For 24 mm, the corresponding values are `76.27%`, `30.36%`,
`65.17%`, and `46,704`. The new maximum is modestly above the earlier `44,395`
but the tail shares and zero fraction remain within the previous increment
range. The final cumulative top-tail and zero-response diagnostics move only
slightly, and the maximum block influence falls to `0.89%`.

The accepted final human record on 2026-07-21 is:

```text
tail_disposition     no-material-worsening
progression_decision stop-success
next_bc_child        none
automatic_submission false
```

This closes the adaptive back-center endpoint chain at its precommitted BC-S4
ceiling. It authorizes the separately frozen `FIXED` allocation but does not
submit it. `stop-success` means the primary back-center endpoint precision and
block-stability targets were met; it does not mean that the intermediate
thicknesses, `edge-center`, or `back-four` production curves are complete.

The accepted next execution is the entire FIXED allocation as one ordinary
`1-592` Slurm array: 64 intermediate back-center tasks, 240 edge-center tasks,
and 288 back-four tasks, totaling 148,000 events. The confirmed OSC array
concurrency limit is 1,000. There is no preflight and no planned subdivision;
only failed elements, if any, are retried. Campaign generation, check-only,
and explicit submission remain separate actions.

## 16. Claim boundary

The present evidence supports this scoped statement:

> For a centered 1 GeV kinetic-energy neutron pencil beam, the fixed
> 500 x 500 x 40 mm SAE-304 slab proxy, the retained painted/wrapped boundary,
> the EJ-550 coupling proxy, and a back-center SiPM, the cumulative 80,500-event
> BC-S1+BC-S2+BC-S3+BC-S4 production sample gives a 24/4 mean detected
> optical-response ratio of 1.393 with a 95% event-bootstrap interval of
> [1.291, 1.508]. In this fixed model, the neutron-induced production gain
> exceeds the optical-collection loss.

This result meets the precommitted 10% precision target for the back-center
primary contrast. It does not establish absorber-size independence,
equivalence to the complete ePIC calorimeter, the intermediate-thickness curve,
either other readout-layout curve, photoelectron/electronics response, or
detector-model systematics.

## 17. Completed FIXED execution and final-analysis handoff

The separately authorized FIXED allocation subsequently ran as one ordinary
array, without a preflight or planned subdivision:

| Execution quantity | Recorded value |
| --- | --- |
| Campaign | `sm-v1-production-fixed-direct-6d97109e0401` |
| Slurm array | `50619224` |
| Tasks / events | `592 / 148,000` |
| Scheduler result | `592 COMPLETED / 0:0` |
| Finalizer result | 592 selected and event-audited; 0 invalid results ignored |
| Finalized checksum | `FINALIZED_CHECKSUM_EXIT=0` |

This completes data acquisition for the six thicknesses and three layouts.
The five direct campaigns together contain exactly 914 independent 250-event
blocks and 228,500 production events. The final read-only analyzer combines
the FIXED configurations with the cumulative BC-S1 through BC-S4 endpoints,
while retaining their independent campaign, task, seed, ROOT, and finalization
identities.

The final production curve contract follows SMS-013 and SMS-016. It keeps
scintillation production, optical collection, and direct observed net response
separate; preserves per-sensor and heavy-tail diagnostics; reports the four
precommitted primary 24/4 contrasts; and gives each layout stratum exactly
one-third weight in pooled production. The unequal 40,250/10,000/12,000 event
endpoint samples must never be concatenated into an event-weighted pooled
production mean. Static figures are derived only after core checksums pass.
Neither the analyzer nor plotter submits work or creates Geant4 events.
