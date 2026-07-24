# Steel Module Scan v1 — Decision Record and Implementation Contract

Status: completed and accepted with `no-material-worsening / stop-success`;
all five direct production campaigns and the final 914-task combined analysis
are checksum-valid; the later two-side-SiPM request is a separate extension
Study preset: `steel-module-scan-v1`
Last updated: 2026-07-21

## Purpose and relationship to the earlier neutron study

This record is derived from
`docs/decisions/realistic-detector-neutron-variant.md`, but it defines a new
experiment rather than revising the historical `realistic-neutron-v1` experiment in place.

The original preset, its `432.58 MeV` neutron input, completed benchmark, and
generated-but-unsubmitted convergence pilot remain immutable historical
artifacts. They may inform engineering, regression, and runtime expectations,
but they are not statistical evidence for this scan. All accepted data for the
new scan must identify `study_preset=steel-module-scan-v1`.

The immediate scientific question remains the same: determine whether the
greater neutron-induced light production of thicker tiles outweighs their
poorer optical collection. The new requirement expands the comparison to six
tile thicknesses and three SiPM layouts on a `100 x 100 mm` tile.

## Confirmed decisions

### SMS-001 — Primary scientific outputs

Keep the causal stages distinguishable. For every incident neutron, preserve
enough information to report at least:

- primary-neutron steel interaction and steel energy deposition;
- charged-particle entry and energy deposition in the scintillator;
- scintillation and total generated optical photons;
- optical collection efficiency;
- total photons entering any SiPM proxy volume;
- for multi-SiPM layouts, photons entering each sensor copy separately.

“SiPM detected photons” remains a geometry-level optical-entry proxy. It does
not include a measured photon-detection efficiency or electronics response.

### SMS-002 — Neutron source authority

The professor's latest instruction supersedes the momentum wording in
`SteelModuleScan.md` for this preset:

```text
neutron kinetic energy = 1 GeV
```

Kinetic energy is the authoritative source quantity. Using
`m_n c^2 = 0.9395654205 GeV`, the derived quantities are:

```text
E_total = 1.939565421 GeV
p       = 1.696800177 GeV/c
```

The generated Geant4 source commands must contain:

```text
/gps/particle neutron
/gps/energy 1000 MeV
```

The derived momentum and total energy are metadata only. No code path may
convert the old `1 GeV/c` requirement into `432.58 MeV` for this preset.

### SMS-003 — Centered pencil-gun interpretation

Interpret the professor's instruction, “shoot it as a gun, the location
shouldn't matter now - just shoot at the center for now,” as a monoenergetic,
point-like, zero-divergence beam at the tile center, traveling from `+Z` toward
`-Z` along the tile normal.

The implementation may continue to use `G4GeneralParticleSource`; “gun” fixes
the physical distribution, not the Geant4 source class. Therefore:

- transverse source center: `x = 0`, `y = 0`;
- transverse profile: point;
- angular model: pencil;
- direction: `(0, 0, -1)`;
- no `55 mrad` or other inherited electron-beam divergence.

Off-center, one-dimensional, and two-dimensional source scans are outside v1
unless a later requirement explicitly adds them.

### SMS-004 — Steel absorber and source clearance

Retain the accepted backward-ePIC local absorber proxy:

- material: `StainlessSteelSAE304`;
- density: `7.9 g/cm3`;
- mass fractions: 74% Fe, 18% Cr, 8% Ni;
- beam-axis thickness: `40 mm`;
- fixed v1 production reference: `500 x 500 x 40 mm`;
- pilot-only transverse diagnostics: `200` and `300 mm`.

The official ePIC absorber is continuous rather than one plate per readout
tile. The rectangular slab remains a local proxy, and every v1 interpretation
is conditional on its fixed `500 mm` transverse extent. SMS-015 records the
accepted policy and the conditions that would reopen a larger-size study or an
official-geometry study; v1 does not claim transverse convergence.

Keep the tile centered at the origin. With tile thickness `t`, derive:

```text
z_tile_front     = t / 2
z_steel_center   = t / 2 + 20 mm
z_steel_upstream = t / 2 + 40 mm
z_source         = t / 2 + 41.5 mm
```

The `1.5 mm` source clearance is a numerical separation between the virtual
source and the steel upstream face. It is not a steel-to-tile gap.

| Tile thickness | Tile front | Steel center | Steel upstream | Source Z |
| ---: | ---: | ---: | ---: | ---: |
| `4 mm` | `2 mm` | `22 mm` | `42 mm` | `43.5 mm` |
| `8 mm` | `4 mm` | `24 mm` | `44 mm` | `45.5 mm` |
| `12 mm` | `6 mm` | `26 mm` | `46 mm` | `47.5 mm` |
| `16 mm` | `8 mm` | `28 mm` | `48 mm` | `49.5 mm` |
| `20 mm` | `10 mm` | `30 mm` | `50 mm` | `51.5 mm` |
| `24 mm` | `12 mm` | `32 mm` | `52 mm` | `53.5 mm` |

### SMS-005 — No air gap and retained reflector

The steel downstream face and tile `+Z` face have zero geometric gap. The tile
nevertheless remains painted or wrapped. Represent the retained EJ-510 coating
as the same zero-thickness optical-surface proxy used by the accepted optical
baseline:

- ordered `tile -> steel` border surface at the contact;
- `polishedfrontpainted`, UNIFIED, `dielectric_dielectric`;
- repository empirical EJ-510 reflectivity curve;
- painted `tile -> world` treatment on exposed tile boundaries;
- no bare `dielectric_metal` EJ-200-to-steel boundary in the primary model.

### SMS-006 — Tile and optical baseline

Use an EJ-200 tile with fixed transverse size `100 x 100 mm`. The thickness
axis is exactly:

```text
4, 8, 12, 16, 20, 24 mm
```

Across all thicknesses and layouts, lock:

- surface preset: `polishedfrontpainted`;
- reflectivity model: empirical EJ-510 CSV;
- SiPM active volume: `2.4 x 2.4 x 0.5 mm` silicon proxy;
- dimple and bottom cavity: disabled;
- explicit grease solid: disabled;
- optical coupling: undimpled zero-gap EJ-550 proxy
  (`optical_coupling=none`);
- scintillator and optical-process properties: unchanged from the prior
  lab-optimization baseline.

The zero-gap coupling is intentionally described as an EJ-550 proxy: the
physical setup used EJ-550, while the previously optimized simulation did not
assign a confirmed grease-layer thickness. Enabling an explicit grease volume
would change the baseline and is not allowed under this preset.

### SMS-007 — Three SiPM layouts and stable sensor identity

The study-layout axis is exactly:

| Study layout | Detector layout | Face | Sensor centers in face-local coordinates | Copy numbers |
| --- | --- | --- | --- | --- |
| `back-center` | `single` | `-Z` | `(0, 0) mm` | `0` |
| `edge-center` | `single` | `+X` | `(0, 0) mm` | `0` |
| `back-four` | `back-four` | `-Z` | `(-25,-25)`, `(-25,+25)`, `(+25,-25)`, `(+25,+25) mm` | `0,1,2,3` in that order |

The `+X` choice for “on edge” is a coordinate convention. For a centered
square tile and centered source, the four lateral faces are symmetry-equivalent
in the nominal model. A later study of broken symmetry must introduce a new
layout or orientation axis rather than silently changing this convention.

All SiPM placements share one logical volume and use stable physical copy
numbers. Event output stores the aggregate count plus four fixed per-copy
fields. For single-SiPM layouts, sensor 0 carries the aggregate and sensors
1–3 remain zero. The required invariant is:

```text
sipm_detected_photons
  = sum(sipm_sensor_0_detected_photons ... sipm_sensor_3_detected_photons)
```

### SMS-008 — Scan matrix

The nominal centered scan contains:

```text
6 thicknesses x 3 SiPM layouts = 18 configurations
```

Tile thickness and SiPM layout are scientific axes. Absorber transverse size
is a validation axis, not part of the detector-optimization comparison. Event
count and seed block are execution/statistical axes.

### SMS-009 — Event-level causal model

Retain the earlier D-012 causal observables and invariants, including:

- elastic, inelastic, and capture interaction counts and derived flags;
- steel energy deposition;
- charged tile-entry counts and kinetic-energy sums split into
  electron/positron, proton, and other charged categories;
- tile energy deposition split into electron/positron, proton, other charged,
  and neutral categories;
- primary-neutron tile-entry coordinates and validity;
- generated optical, scintillation, and Cerenkov counts;
- aggregate and per-sensor SiPM counts;
- collection efficiency with `NaN` plus a validity flag when no optical photon
  was generated.

Counts, category sums, flags, optional coordinates, and per-SiPM sums must be
machine-audited before a task can be accepted.

### SMS-010 — Versioned preset and allowed variation

The general scan runner exposes:

```text
--study-preset steel-module-scan-v1
--tile-thickness-mm {4,8,12,16,20,24}
--sipm-layout {back-center,edge-center,edge-two,back-four}
```

Under the preset, lock the particle, kinetic energy, beam profile/direction,
tile transverse size, optical baseline, SiPM size, dimple state, coupling
model, absorber material/thickness/position, steel-to-tile gap, and source-Z
derivation. Reject manual overrides of those values.

Allow only:

- the six accepted tile thicknesses;
- the three original SiPM layouts plus the accepted `edge-two` extension;
- `200/300/500 mm` absorber transverse sizes for convergence work;
- one explicit centered scan point per invocation;
- event count, explicit seed pair, seed-block identity, and campaign metadata.

The new preset uses run-config schema `opnovice2-run-config-v4` and event schema
`opnovice2-scan-event-v3`. The old preset remains on its historical schema and
constants.

### SMS-011 — Evidence separation

Do not merge, pool, or substitute any of the following into the new scan:

- old `realistic-neutron-v1` events at `432.58 MeV`;
- the old 50 mm-tile benchmark;
- the generated but unsubmitted old convergence pilot;
- development runs lacking the new preset, layout, commit, environment, and
  seed identity.

The old benchmark may guide an initial task-size guess only. Because kinetic
energy, tile area, maximum thickness, and SiPM multiplicity changed, the new
scan requires its own full-optical benchmark before production statistics are
chosen.

### SMS-012 — Validation ladder before production

The minimum acceptance sequence is:

1. deterministic runner-contract checks for all 18 nominal configurations and
   fail-fast checks for locked overrides;
2. compile the candidate executable and retain the absorber-disabled fixed-seed
   electron regression against the accepted `practice@50ec06d4` baseline;
3. initialize and overlap-check all 18 geometries with the `500 mm` absorber;
4. run at least one full-optical event for every geometry and audit the complete
   event schema, including per-sensor sums;
5. visually inspect representative `back-center`, `edge-center`, and
   `back-four` geometries, including the steel placement and each SiPM face;
6. run a new full-optical resource benchmark that includes the likely cost
   envelope, at minimum the `4 mm back-center`, `24 mm back-center`,
   `24 mm edge-center`, and `24 mm back-four` configurations;
7. review the measured wall time, memory, output size, interaction fraction,
   photon multiplicity, and zero-light fraction before choosing pilot blocks;
8. run a new independent statistical/convergence pilot; freeze production N
   only after bootstrap precision and absorber-size convergence are reviewed.

The accepted benchmark used `100` events in each of the four envelope
configurations. Its slowest task was `24 mm back-four` at `279 s` for 100
events, so a linear estimate places a `250`-event block at `697.5 s`
(`11.625 min`), comfortably below the one-hour task target.

The convergence pilot is now frozen as follows:

- `500 mm` absorber for all six thicknesses and all three layouts: 18 nominal
  configurations;
- `200` and `300 mm` absorber checks only at the `4` and `24 mm` thickness
  endpoints for all three layouts: 12 additional configurations;
- 30 configurations total, each split into four independent seed blocks of
  250 events;
- 1,000 events per configuration, 120 logical tasks, and 30,000 events total.

The canonical ordered selection is
`hpc/osc/configurations/steel-module-convergence-pilot-v1.tsv`. This pilot is
an independent statistical and absorber-convergence study; it does not freeze
the later production event count.

### SMS-013 — Primary analysis

For each SiPM layout, report thickness-dependent curves for:

- interaction probability and zero-light fractions;
- scintillation photons per incident neutron;
- aggregate collection efficiency;
- aggregate SiPM photons per incident neutron;
- per-sensor SiPM photons where applicable.

Compare thicknesses within the same layout, then compare layouts at the same
thickness. Production, collection, and net-response effects must remain
separate. The pilot will determine which ratios or curve contrasts can meet the
candidate precision scenarios without pathological behavior from the large
zero-response component.

The scan-specific analyzer must preserve that separation and write:

- configuration-level thickness curves and Wilson intervals for interaction
  and all three zero-light definitions;
- per-sensor response intervals;
- thickness ratios relative to `4 mm` within one layout and absorber size;
- layout ratios relative to `back-center` at one thickness and absorber size;
- `200/500` and `300/500` absorber-convergence ratios at the two thickness
  endpoints;
- production-`N` projections for 5% and 10% relative 95% CI half-width
  scenarios.

The projection model applies a fixed `1.25` safety factor and rounds upward to
complete 250-event execution blocks. The 5% and 10% projections are review
aids only. Neither precision scenario is an accepted target, and the analyzer
must not auto-approve a production event count or absorber size.

### SMS-014 — Historical pilot review and analysis-v2 gate (closed)

This section preserves the gate as defined after the v1 review. Its required
analysis was subsequently completed; SMS-015 is authoritative for the current
absorber and production boundary.

The 30-configuration convergence pilot completed all 120 logical tasks and
passed finalization, the integrated event audit, and finalized/analysis
checksum verification. Its v1 interpretation is frozen in
`docs/decisions/steel-module-convergence-pilot-review-v1.md`.

The accepted preliminary result is that `24 mm` tiles produce approximately
five times the scintillation light of `4 mm` tiles, while the net SiPM response
depends on readout geometry. At `500 mm` absorber transverse size, the 24/4 net
ratios are `0.742 [0.461, 1.303]` for `back-center`,
`2.148 [1.621, 2.860]` for `edge-center`, and
`3.424 [2.553, 4.666]` for aggregate `back-four`. The latter uses four times
the active SiPM area of a single-SiPM layout.

The v1 absorber evidence does not establish 5% equivalence: production and net
intervals for every `200/500` and `300/500` check include unity, but none lies
wholly inside `[0.95, 1.05]`. Retain `500 mm` as the conservative analysis
reference without calling it converged.

The v1 universal production-size projections were not accepted because the
limiting `back-center 8/4` net ratio is a secondary, unresolved cancellation
and was not the agreed production estimand. The gate required analysis-v2 to
use the existing audited ROOT events to:

- diagnose zero inflation, heavy tails, and seed-block stability;
- pool layout-invariant production at fixed thickness and absorber size;
- report labeled standardized-response decompositions while preserving direct
  net response as the primary empirical quantity;
- make `24/4` endpoint production and per-layout net response the primary
  sizing contrasts;
- revisit absorber equivalence with pooled production and separate 5%/10%
  review windows;
- produce contrast-specific, non-automatic event-count projections in a new
  checksummed output directory without overwriting v1.

At that checkpoint, production remained blocked until the analysis-v2 outputs
were reviewed and an explicit precision target, primary contrast set, absorber
conclusion, and event count were accepted.

The implementation gate was complete at that checkpoint: the independent v2
analyzer, synthetic infrastructure checker, and headless plotter were present.
They did not modify the v1 analyzer or launch Geant4. The real pilot still had
to be analyzed from a clean OSC checkout, checksum-verified, plotted, and
reviewed. The v2 contract fixed exactly four sizing contrasts and eight
separate projections, with no universal recommendation or automatic production
acceptance.

### SMS-015 — Analysis-v2 review and fixed-reference absorber policy

The real analysis-v2 run completed from the sealed 30,000-event pilot and
passed its core and figure checksum verification. Its accepted review is
recorded in
`docs/decisions/steel-module-analysis-v2-review-v1.md`. The analysis used
simulation commit `cfd7d974af2c5f40f7d76948172fc87ee70bfa3c` and clean analysis
commit `ad680deecc7ae6f25ff68e7a0a3b052158a21eb3`, reconciled all 30 v1
configuration estimates and three v1 endpoint ratios, and retained exactly the
four accepted primary sizing contrasts.

At the `500 mm` transverse reference, the pooled scintillation-production
`24/4` ratio is `4.836 [4.064, 5.759]`. The direct observed-net ratios are
`0.742 [0.465, 1.307]` for `back-center`,
`2.148 [1.626, 2.840]` for `edge-center`, and
`3.424 [2.546, 4.649]` for aggregate `back-four`. The corresponding maximum
leave-one-block-out shifts are `19.22%`, `7.74%`, and `5.29%`; pooled
production shifts by at most `4.01%`. The `back-center` point estimate remains
an unresolved, heavy-tail-sensitive cancellation rather than a resolved
decrease.

Analysis-v2 does not establish absorber equivalence at either the 5% or 10%
review band. In particular, pooled production for `4 mm` gives
`200/500 = 0.816 [0.667, 0.993]`, while the other pooled endpoint comparisons
remain inconclusive. The candidate-size evidence therefore cannot justify
substituting `200` or `300 mm`, but comparisons only below the reference also
cannot prove that `500 mm` has reached a large-size plateau.

For this v1 scan, accept `500 x 500 x 40 mm` as a **fixed model reference**:

- every production configuration retains this exact centered slab;
- `200/300 mm` remain pilot-only engineering diagnostics and are excluded from
  the primary production curves;
- `500 mm` must not be described as converged, infinite, or equivalent to the
  full official ePIC absorber geometry;
- every interpretation is explicitly conditional on the fixed `500 mm`
  transverse steel-slab proxy; and
- no additional absorber-convergence run is required before the v1 production
  decision.

This is a scope decision, not a statistical equivalence finding. Reopen the
absorber question if the study must predict official ePIC geometry, use a
smaller slab, or claim absorber-size-independent thickness/layout effects. A
reopened study must compare `500 mm` with a larger extent or implement the
relevant official continuous geometry; adding only more `200/300 mm` events
cannot demonstrate convergence above the current reference.

At the SMS-015 checkpoint, the absorber-policy gate was closed for v1 and
production remained blocked on an explicit precision target, staged treatment
of the heavy-tailed `back-center` response, and exact per-layout event counts.
SMS-016 resolves those statistical-design choices.

### SMS-016 — Accepted 10% precision and staged production policy

Accept a 10% event-bootstrap 95% relative-half-width target for exactly four
primary `24/4` contrasts: pooled scintillation production and direct observed
net response for `back-center`, `edge-center`, and aggregate `back-four`.
Excluding unity is not a stopping requirement, and secondary curve,
standardized-response, collection, and absorber ratios do not control sample
size.

The accepted sizing calculation is

`N_raw = ceil(N_pilot x (h_observed / 0.10)^2)`, followed by
`N_buffered = ceil(1.25 x N_raw)` and upward rounding to complete 250-event
blocks. The sealed pilot gives:

| Primary contrast | `h_observed` | `N_pilot` per endpoint | Accepted events/configuration | Blocks/configuration |
| --- | ---: | ---: | ---: | ---: |
| Pooled production, per layout stratum | `0.175253` | `3,000` total | `4,000` | `16` |
| `back-center` observed net | `0.567095` | `1,000` | `40,250` | `161` |
| `edge-center` observed net | `0.282524` | `1,000` | `10,000` | `40` |
| `back-four` observed net | `0.307109` | `1,000` | `12,000` | `48` |

The exact raw, buffered, stratum-distribution, and block-rounding arithmetic is
preserved in `docs/decisions/steel-module-analysis-v2-review-v1.md` and the
checksum-sealed `production_sizing_v2.json`.

Apply those counts to the six-thickness curve as follows:

- all six `back-center` configurations start with `4,000` new events;
- only the `4` and `24 mm back-center` primary endpoints may extend
  cumulatively through `4,000`, `10,000`, `20,000`, and `40,250` events;
- all six `edge-center` configurations receive `10,000` events; and
- all six `back-four` configurations receive `12,000` events.

The existing 30,000-event pilot is not pooled into production. Every
production block uses a fresh seed and immutable stage manifest. At the
maximum checkpoint the new-event accounting is

`4 x 4,000 + 2 x 40,250 + 6 x 10,000 + 6 x 12,000 = 228,500 events`,

or

`4 x 16 + 2 x 161 + 6 x 40 + 6 x 48 = 914` complete 250-event blocks.

Because the final endpoint samples are unequal across layouts, pooled
production retains equal `1/3` layout-stratum weights: bootstrap within each
layout at its available sample size, then average the three layout means.
Do not concatenate all events into an event-count-weighted mean that would
silently overweight the staged `back-center` sample. This equals the v2 pilot
calculation when all three strata have the same event count.

For the two `back-center` endpoints, the cumulative stages add `8,000`,
`12,000`, `20,000`, and `40,500` events across the endpoint pair. Including
the fixed allocations, the corresponding whole-production ceilings are
`156,000/624`, `168,000/672`, `188,000/752`, and `228,500/914`
events/blocks.

At each checkpoint, require finalized checksums, event/seed audit, cumulative
analysis, and review of relative half-width, maximum leave-one-block-out
shift, zero/positive counts, top-tail shares, maximum event, and CI-width
scaling. Stop successfully only when the `back-center 24/4` relative
half-width and maximum block-omission shift are both `<= 10%`. Continue only
after explicit review when the target is unmet, the block shift is `<= 20%`,
and the interval is narrowing. Pause for method review if those diagnostics
deteriorate. `40,250` events/configuration is a hard ceiling, not permission
for automatic extension.

For `BC-S1`, compare interval-width trend against the sealed 1,000-event pilot
as a diagnostic only, without pooling pilot events. Later stages compare to
the immediately preceding cumulative production checkpoint.

The fixed pooled-production, `edge-center`, and `back-four` allocations must
also be checked against the accepted 10% target. If any misses it, do not add
events automatically; reopen that contrast-specific sizing decision.

This cumulative repeated-look policy is a pragmatic precision design, not a
formally adjusted sequential-confidence procedure. Stopping must never depend
on the effect direction, crossing unity, or excluding unity. The policy
authorizes production-infrastructure preparation; actual submission remains
blocked until immutable stage generation, fresh-seed identity, cumulative
audit/analysis, and non-overwrite behavior are implemented and validated.

### SMS-017 — Parent production program and five incremental children

Represent the accepted maximum design as one non-executable, checksum-bound
parent production program with a complete 914-task registry. Do not represent
it as one directly submittable 914-task campaign: the existing generic
submit/resume path operates on all tasks in a campaign, which would make an
accidental submission capable of bypassing the manual checkpoints.

Generate five immutable child campaigns atomically with the parent:

| Child | Exact incremental allocation | Tasks | Events |
| --- | --- | ---: | ---: |
| `FIXED` | `4 x 16` intermediate-back-center blocks + `6 x 40` edge-center blocks + `6 x 48` back-four blocks | `592` | `148,000` |
| `BC-S1` | two endpoint configurations x blocks `0-15` | `32` | `8,000` |
| `BC-S2` | two endpoint configurations x blocks `16-39` | `48` | `12,000` |
| `BC-S3` | two endpoint configurations x blocks `40-79` | `80` | `20,000` |
| `BC-S4` | two endpoint configurations x blocks `80-160` | `162` | `40,500` |

The block ranges are configuration-scoped and continuous across children.
They apply independently to `4 mm back-center` and `24 mm back-center`; the
same block number at different thicknesses identifies different series tasks.
Every task receives a program-level series identity, and the parent allocates
all production seeds once across all five children. Production seeds must be
globally unique and disjoint from the sealed pilot; retrying one task preserves
its original seed pair.

The parent program hash binds the full registry, policy identities, simulation
commit, environment and executable identities, pilot exclusion identity, all
five child plan/task-set hashes, their roles, the ordered `BC-S1...BC-S4`
prefix, and the allowed cumulative state graph. All children are frozen upfront
so later evidence cannot influence task or seed generation. Each child remains
an independently complete campaign with its own attempt journal, whole-child
finalization, event audit, and checksum manifest.

Generic submission must reject a program-managed child. A dedicated staged
wrapper authorizes one exact child; `BC-S2+` also requires an immutable
progression decision tied to the prior cumulative-analysis checksum. A
program-level cumulative finalizer/analyzer accepts either a contiguous,
checksum-valid `BC-S1...BC-Sn` prefix for back-center diagnosis or that prefix
plus `FIXED` for complete four-contrast evidence, and writes non-overwriting
evidence directories.

`BC-S1` child alone is a `32-task`, 8,000-event back-center diagnostic. The
complete four-primary-contrast checkpoints are:

| Checkpoint | Children included | Tasks | Events |
| --- | --- | ---: | ---: |
| `BC-S1` | `FIXED + BC-S1` | `624` | `156,000` |
| `BC-S2` | `FIXED + BC-S1 + BC-S2` | `672` | `168,000` |
| `BC-S3` | `FIXED + BC-S1 + BC-S2 + BC-S3` | `752` | `188,000` |
| `BC-S4` | all five children | `914` | `228,500` |

Back-center-only evidence may be used for the staged tail/precision review
before `FIXED` completes, but it is not a complete production checkpoint for
all four primary contrasts. SMS-018 resolves the initial scheduling gate.

### SMS-018 — Run BC-S1 alone before authorizing FIXED

The first production submission contains only the `BC-S1` child:

- `4 mm back-center`, blocks `0-15`;
- `24 mm back-center`, blocks `0-15`; and
- `32` logical tasks x `250` events = `8,000` new events total.

Do not submit `FIXED` in parallel with this first diagnostic. `FIXED` and all
later BC children remain locked until `BC-S1` has passed whole-child
finalization, event/seed audit, checksum verification, BC-only production
analysis, and explicit human review. This exposes only `3.5%` of the maximum
program before the heavy-tail scaling check, rather than the
`FIXED + BC-S1 = 156,000` events (`68.3%`) that form the first complete
four-contrast checkpoint.

The expected `BC-S1` relative half-width remains approximately `28.4%`, so the
first child is a scaling and stability diagnostic rather than an expectation
of immediately reaching the 10% target. Its outcome has these authorization
effects:

| Review decision | Authorization effect |
| --- | --- |
| `stop-success` | unlock `FIXED`; keep `BC-S2...BC-S4` locked |
| `continue` | unlock `FIXED`; make `BC-S2` eligible for a separate scheduling decision |
| `pause-review` | keep `FIXED` and every later BC child locked |

No analyzer may submit or unlock a child automatically. A human progression
record must bind the BC-S1 finalized and analysis checksums. If the outcome is
`continue`, whether eligible `FIXED` and `BC-S2` run in parallel or
sequentially remains the next scheduling decision.

### SMS-019 — Human-readable checkpoint review and immutable progression decision

LOO means **leave one out**; in this production workflow it specifically means
**leave one 250-event block out**. For each block contributing to the two
back-center endpoint samples, recompute the `24/4` ratio without that block and
measure `abs(R[-b] / R[full] - 1)`. Report the maximum across all omissions.
The `BC-S1...BC-S4` cumulative endpoint samples require `32/80/160/322`
omissions. This maximum LOO shift diagnoses sensitivity to a single block; it
is not the confidence interval, the physical effect size, permission to delete
the block, or a substitute for the event bootstrap.

The cumulative analyzer reports, but never selects, a provisional
`numeric_eligible_decisions` set. Apply this fail-closed precedence:

1. Invalid identity, checksum, audit, or required metric evidence fails before
   a checksum-valid report or recordable decision exists; all children stay
   locked.
2. The reviewer must record whether a new extreme event or tail concentration
   materially worsens the diagnostic. If yes, only `pause-review` is allowed.
3. At `BC-S4`, never allow `continue`; allow `stop-success` plus `pause-review`
   only when `h <= 10%` and maximum LOO shift `<= 10%`, otherwise pause only.
4. At a valid non-ceiling checkpoint, those same two success conditions allow
   `stop-success` or `pause-review`.
5. Otherwise, `h > 10%`, maximum LOO shift `<= 20%`, and interval narrowing
   allow `continue` or `pause-review` at a valid non-ceiling checkpoint.
6. Every other valid state allows only `pause-review`.

`pause-review` is always available for valid evidence. Effect direction, unity
crossing, and unity exclusion never change the allowed set. The recorder
combines numeric eligibility with the mandatory human tail disposition to
derive the final `allowed_decisions`.

A separate non-overwriting recorder performs a check-only validation before it
writes the human decision. Its append-only record binds the parent program,
checkpoint and exact task set; finalized/audit/analysis checksum identities;
simulation and analysis code identities; task/event counts and all gating
diagnostics; numeric eligibility, human tail disposition, final allowed and
selected decisions; derived child authorization; reviewer, UTC time, rationale,
and previous decision hash. Once a child
submission references that record hash, the decision cannot be amended
retroactively. The managed submitter validates the exact decision and child;
neither analyzer nor recorder submits work.

Each checkpoint also receives a non-overwriting, self-contained static review
directory generated only from checksum-valid cumulative analysis. Its required
human-facing artifacts are an offline `index.html`, print-equivalent
`review_report.pdf`, PNG/PDF figures, `review_data.json`, provenance, and an
independent `SHA256SUMS`. The report presents:

- checkpoint/program identity, task/event totals, and checksum/evidence state;
- provisional numerically eligible choices plus the mandatory human tail
  disposition, without selecting a decision;
- relative-half-width versus event count with the 10% target and
  `1/sqrt(N)` projection;
- all block-omission shifts with visible 10%/20% review bands and the maximum
  block identified;
- zero/positive counts and top-tail/maximum-event diagnostics;
- the observed endpoint `24/4` ratio with 95% interval and unity line; and
- a plain-language checklist, definitions, and expandable provenance.

The UI spells out **Maximum leave-one-block-out shift (LOO)**, supplements
color with text/marker shape, and may only offer a copyable recorder command.
It cannot write a decision, unlock a child, or invoke Slurm. The report is a
derived review aid; sealed finalized/audit/analysis artifacts remain the
accepted statistical evidence.

### SMS-020 — Final BC-S4 review closes the adaptive back-center chain

The checksum-valid direct production prefix now contains all four BC children:
322 tasks and 80,500 events, with 40,250 events in each of the 4 mm and 24 mm
back-center endpoint configurations. The final observed net response ratio is
`24/4 = 1.39346 [1.29104, 1.50771]`. Its relative half-width is `7.77%`, and
the maximum leave-one-250-event-block-out shift is `0.89%` from 4 mm block 34.
Both precommitted 10% success conditions pass.

The BC-S4 tail was reviewed against all three prior independent increments.
Zero fractions and top-1%/top-5% response shares remain in the established
heavy-tail regime; the new 24 mm maximum of `46,704` does not materially alter
the cumulative tail concentration or block sensitivity. The accepted human
record is therefore:

```text
tail_disposition     no-material-worsening
progression_decision stop-success
next_bc_child        none
```

No further back-center endpoint extension is allowed or needed. This decision
directly authorizes only the previously frozen `FIXED` child; submission stays
a separate explicit action. The back-center result is not a substitute for the
four intermediate back-center thicknesses or either complete alternative-layout
curve contained in `FIXED`.

### SMS-021 — Submit the complete FIXED allocation as one ordinary array

The remaining production work is exactly the frozen `FIXED` child: four
intermediate back-center configurations at 16 blocks each, all six edge-center
configurations at 40 blocks each, and all six back-four configurations at 48
blocks each. This is 16 configurations, 592 independent 250-event tasks, and
148,000 events. No result inside this child changes whether another FIXED task
is required, so there is no scientific adaptive boundary within it.

The accepted execution policy is one ordinary `1-592` Slurm array with no
preflight and no layout/thickness partition. The user's OSC array concurrency
limit is 1,000, so the complete shape is below the accepted limit. A failed
array element may be retried through the ordinary checksum-bound retry path;
the possibility of isolated task failure is not a reason to pre-split the
campaign.

### SMS-022 — Completed FIXED execution and final combined-analysis contract

The direct FIXED campaign completed through the accepted ordinary route:

| Execution quantity | Recorded value |
| --- | --- |
| Campaign | `sm-v1-production-fixed-direct-6d97109e0401` |
| Slurm array | `50619224` |
| Tasks / events | `592 / 148,000` |
| Configurations | `16` |
| Scheduler result | `592 COMPLETED / 0:0` |
| Finalizer result | 592 selected and event-audited; 0 invalid results ignored |
| Finalized checksum | `FINALIZED_CHECKSUM_EXIT=0` |

Together with the four finalized back-center endpoint children, the complete
production evidence is now exactly 914 tasks, 228,500 events, and all 18
nominal layout/thickness configurations. No production scan remains pending.

The final analyzer must read the five checksum-valid finalized campaigns
without copying pilot events or creating new Geant4 work. It reconstructs the
exact program-wide task, block, seed, ROOT, environment, and Slurm identities;
rejects gaps, overlap, or duplicate seeds; and writes a new non-overwriting
`direct-final-analysis` directory under the FIXED finalization.

Final outputs contain all configuration curves required by SMS-013, the four
precommitted SMS-016 primary `24/4` contrasts, causal-pathway and heavy-tail
diagnostics, per-sensor response, within-layout thickness ratios, same-thickness
layout ratios, and model-based standardized response clearly separated from
direct observed net response. Because endpoint statistics differ by layout,
pooled generated/scintillation production uses exactly equal `1/3` layout
weights at every thickness. Event-count weighting is prohibited.

An independent headless plotter reads only the checksum-valid core output and
exports PNG/PDF production, collection, net-response, normalized-response,
standardized-response, and primary-contrast figures. The analysis and plotter
are read-only with respect to campaigns and never contact Slurm or Geant4.
Actual numerical conclusions and the final claim boundary are recorded only
after the OSC analysis and figure checksums pass.

### SMS-023 — Final combined review records stop-success

The checksum-valid final analysis combined all five direct campaigns without
adding pilot events or generating new Geant4 work. The accepted sample is
exactly 914 tasks, 228,500 events, and all 18 originally specified
layout/thickness configurations. The analysis used 10,000 independent-event
bootstrap resamples with seed `20260715` and equal one-third layout-stratum
weights for pooled production.

The four precommitted `24 mm / 4 mm` primary contrasts are:

| Primary contrast | Ratio | Bootstrap 95% interval | Relative half-width |
| --- | ---: | ---: | ---: |
| Pooled scintillation production | `5.45937` | `[5.21503, 5.71827]` | `4.61%` |
| Back-center observed net | `1.39346` | `[1.28821, 1.50850]` | `7.90%` |
| Edge-center observed net | `2.39266` | `[2.17374, 2.63593]` | `9.66%` |
| Back-four aggregate observed net | `3.55943` | `[3.26221, 3.89536]` | `8.89%` |

All four intervals exclude unity and all four relative half-widths satisfy the
accepted 10% target. The production gain therefore outweighs the optical
collection loss for every originally specified layout over the precommitted
endpoint comparison. Independent archive review also found no checksum,
event-count, seed-identity, sensor-sum, or point-estimate discrepancy, and the
maximum leave-one-250-event-block-out shifts remained small.

The final human decision is:

```text
tail_disposition     no-material-worsening
progression_decision stop-success
additional_events    none for the original three-layout endpoint question
```

This closes `steel-module-scan-v1`; it does not claim transverse absorber
convergence, detector PDE/electronics response, full calorimeter resolution,
or universality beyond the fixed 1 GeV centered-neutron model. The professor
reviewed the result and requested a later fourth arrangement, described in
`SteelModuleScan.md` as “2 SiPM on the side.” That request is a new layout
extension rather than a reason to reopen this accepted stop decision.

### SMS-024 — Accepted two-SiPM side-layout extension

Interpret the later “2 SiPM on the side” request as two sensors on the same
existing `+X` side face. In face-local coordinates for `+X`, where `u = y` and
`v = z`, their centers are:

```text
sensor 0: (u, v) = (-25, 0) mm
sensor 1: (u, v) = (+25, 0) mm
```

The layout identity is `edge-two`. It reuses the accepted `2.4 x 2.4 x 0.5 mm`
SiPM proxy, undimpled zero-gap coupling model, and all source, tile, steel, and
optical-surface settings from the completed study. Copies 0 and 1 carry the
two sensor counts; copies 2 and 3 remain zero, and the aggregate count must
equal the sum of the two active copies. This convention fits all six accepted
tile thicknesses and is directly comparable with the original centered `+X`
single-SiPM layout.

`edge-two` remains a **single-layer** extension. Repeat the same six tile
thicknesses and plot its production, collection, and net-response curve beside
the original `back-center`, `edge-center`, and `back-four` curves. It must not
be conflated with the separate ten-layer longitudinal-stack study. The latter
uses one selected, identical SiPM layout on every layer rather than mixing the
four layouts or automatically selecting `edge-two`.

The accepted one-shot execution shape follows the completed `edge-center`
allocation: `40` independent `250`-event blocks for each of the six thicknesses.
This is six configurations, `240` tasks, and `60,000` new events. Run it as one
ordinary `1-240` Slurm array with no preflight and no adaptive boundary. Its
new deterministic seed registry must be disjoint from both the sealed pilot
seeds and all `914` tasks in the completed original production program.

Interactive review of the `4 mm` and `24 mm` geometries passed on 2026-07-21.
Both same-face side sensors were visible at the accepted positions, the two
thicknesses were visually distinct, and the exported PDFs were retained with
their generated macros and run-config provenance in the ignored local campaign
archive. This review authorizes campaign construction but does not itself
submit Slurm work.

### SMS-025 — Ten-layer edge-two longitudinal thickness study

The professor's ten-layer request is a second, independent follow-up study.
The earlier statement that a downstream-face SiPM necessarily prevents a
zero-gap stack was too strong: a longitudinal sequence may explicitly include
the sensor thickness, for example `[steel][tile][SiPM][steel]`. The accepted
ten-layer layout nevertheless uses `edge-two`, whose two sensors sit outside
the longitudinal material sequence on the `+X` side of every tile.

Create a separate `steel-module-stack-v1` preset. Its scientific matrix is:

```text
6 uniform tile thicknesses x 1 SiPM layout = 6 stack configurations
tile thickness = 4, 8, 12, 16, 20, or 24 mm
layout         = edge-two on every tile
per stack      = 10 steel slabs + 10 tiles + 20 SiPM proxies
```

Within one configuration, all ten tiles have the same selected thickness.
Number layers `0...9` from upstream to downstream, retain the centered 1 GeV
kinetic-energy neutron pencil beam, and repeat the `40 mm` SAE-304 steel plus
tile sampling unit along `-Z`. There is no longitudinal air gap at the
steel-to-tile or tile-to-next-steel boundaries. Both side sensors on every
tile retain the SMS-024 local centers `(-25, 0)` and `(+25, 0) mm`.

The primary output is the longitudinal, sensor-resolved optical response. For
each layer `i` and local sensor `j`, retain at least:

```text
generated optical photons in tile i
scintillation photons in tile i
photons entering SiPM (i, j)
collection(i, j) = sum[photons entering SiPM (i, j)]
                   / sum[generated optical photons in tile i]
net(i, j)        = sum[photons entering SiPM (i, j)] / incident neutrons
```

Also report the two-sensor aggregate for each layer and the full-stack total.
Use ratio-of-sums collection estimates; when the generated-light denominator
is zero, retain an explicit invalid flag rather than substituting zero. Stable
identity is `global_sensor_copy = 2 * layer + local_sensor`, giving copies
`0...19`. Preserve layer-resolved steel/tile energy deposition and charged
entry information so that shower development can be distinguished from
optical collection.

The ten-layer runtime and deep-layer zero fractions are not inferred from the
single-layer campaign. A small direct benchmark may determine task sizing,
after which the six configurations run through the ordinary campaign route;
no managed preflight or staged progression machinery is required.

The complete geometry, causal photon-attribution, ROOT evidence, bootstrap
sizing, workload-cap, and direct execution contract is frozen in
`docs/decisions/steel-module-stack-v1.md`. In particular, the primary
per-layer numerator is restricted to photons created in the same tile as the
destination sensor; all-origin counts and cross-layer transfer remain
secondary diagnostics. The controlling 10% precision target applies to the
two full-stack aggregate metrics, not independently to all twenty sensors.

### SMS-026 — Four-layout single-layer analysis semantics

Analyze the completed single-layer `edge-two` follow-up together with the
checksum-valid original direct-production campaigns through a new read-only
four-layout entry point. Do not overwrite or reinterpret the accepted
18-configuration final analysis. The combined descriptive sample contains
the same six thicknesses for `back-center`, `edge-center`, `edge-two`, and
`back-four`.

The directly observed layout curves use the aggregate number of photons
entering every installed SiPM proxy. Because the installed sensor counts are
`1`, `1`, `2`, and `4`, respectively, report three parallel views for every
layout and thickness:

```text
aggregate response
response per installed sensor
response per installed active area in mm2
```

Apply the normalized diagnostics to both multi-sensor layouts, not only to
`edge-two`. Since every active proxy is `2.4 x 2.4 mm`, per-sensor and
per-active-area comparisons differ only by the constant `5.76 mm2` per
sensor. They are secondary layout-cost diagnostics; they do not replace the
aggregate response or model PDE, electronics, packaging, or channel cost.

For `edge-two` and `back-four`, also retain each installed copy's individual
ratio-of-sums collection efficiency with a correlated event-bootstrap
interval. Generate the numerical evidence and figures through separate
entry points. Both must verify their checksum-bound inputs, refuse overwrite,
write provenance and checksums, and create no scheduler action or Geant4
event. A four-layout equal-stratum production reconciliation and
production-standardized response may be reported as explicitly secondary
diagnostics; they do not reopen the accepted original primary contrasts or
automatically choose a detector layout.

The completed single-layer campaign, combined analysis identities, local
archive audit, accepted interpretation, and ten-layer resume boundary are
frozen in `docs/decisions/steel-module-four-layout-result-v1.md`.

## Current engineering state

The runner contract, three detector-layout identities, and independent
`steel-module-campaign-v1` manifest/task/result chain are implemented. The
accepted frozen source is commit
`88f15eace17137475310913d585a96908a493c72` with Geant4 `11.4.2`. The
absorber-disabled fixed-seed electron differential regression against
`practice@50ec06d4` passed.

The user visually accepted all three layouts at `16 mm`. The formal 18-task
OSC geometry smoke, campaign `sm-v1-geometry-smoke-d77d801b947c`, completed and
passed the integrated event audit and finalized checksum verification.

The four-configuration, 100-event-per-task benchmark, campaign
`sm-v1-benchmark-378e088c618d`, also completed and passed finalization. Its
recorded envelope was:

| Configuration | Wall time | MaxRSS | Interaction fraction | Generated optical / event | SiPM / event | Collection | SiPM zero fraction |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `4 mm back-center` | `23 s` | `173416 K` | `0.340` | `2790.30` | `17.54` | `0.00629` | `0.890` |
| `24 mm back-center` | `217 s` | `227640 K` | `0.390` | `41415.77` | `186.20` | `0.00450` | `0.790` |
| `24 mm edge-center` | `222 s` | `223116 K` | `0.370` | `42595.82` | `184.17` | `0.00432` | `0.740` |
| `24 mm back-four` | `279 s` | `232324 K` | `0.320` | `46074.89` | `627.94` | `0.01363` | `0.770` |

These results support the accepted `250 events x 4 blocks` convergence pilot.
The resulting campaign used source commit
`cfd7d974af2c5f40f7d76948172fc87ee70bfa3c`, ran as Slurm array `50477293`,
and produced 30,000 accepted events. All 120 logical tasks were selected by the
finalizer; the integrated event audit, accepted-statistical-evidence flag, and
finalized/analysis checksum checks passed.

The historical v1 scientific review is recorded in
`docs/decisions/steel-module-convergence-pilot-review-v1.md`. The completed
analysis-v2 interpretation and accepted absorber and staged-production
policies are recorded in
`docs/decisions/steel-module-analysis-v2-review-v1.md`. Together they support
a layout-dependent thick-tile conclusion while preserving the fixed-proxy and
heavy-tail caveats.

No geometry, source-model, absorber, statistical-design, production-program
topology, initial-submission, or progression-review contract remains open. If
BC-S1 later returns `continue`, the remaining scheduling choice is parallel
versus sequential execution of eligible `FIXED` and `BC-S2`. Implementation
and validation of the accepted managed-child, cumulative-analysis, static
review-report, and decision-recorder workflow remain mandatory before the
first Slurm submission.

## Interactive geometry review

The scan-specific visualizer prepares all four accepted layouts by default:

```bash
cd test/OpNovice2
cmake -S . -B build-gui -DWITH_GEANT4_UIVIS=ON
cmake --build build-gui -j4
python3 visualize_steel_module_scan_geometry.py --tile-thickness-mm 16
```

Each layout receives its own exact macro, run config, visualization metadata,
and README. The GUI sessions open in `back-center`, `edge-center`, `edge-two`,
`back-four` order; close one session to open the next. The camera first
renders a close SiPM detail and finishes on a wider steel overview. This is a
human sanity check only and leaves `/run/beamOn 0`.

Use one or more repeated `--sipm-layout` options to prepare a subset, or
`--prepare-only` to retain the macros without opening Qt.

## Invariants

- `1 GeV` kinetic energy is authoritative for this preset.
- The source is centered, point-like, normal incidence, and pencil-like.
- The tile is `100 x 100 mm`; only the six accepted thicknesses are valid.
- The original layout axis contains `back-center`, `edge-center`, and
  `back-four`; the accepted single-layer extension adds `edge-two` with its
  recorded face/position/copy-number convention.
- The steel is 40 mm SAE 304 with zero tile air gap and retained paint/wrap.
- The optical baseline is polished-front-painted EJ-510 plus the undimpled
  zero-gap EJ-550 coupling proxy.
- Aggregate SiPM count equals the sum of the four fixed per-sensor fields.
- Old 432.58 MeV data never enter this scan's statistical evidence.
- Production submission starts only after the accepted pilot, analysis-v2,
  absorber, and SMS-016 statistical decisions are embodied in a validated
  immutable staged-production workflow.

## Requirement sources retained with scope

`SteelModuleScan.md` specifies the `100 x 100 mm` tile, six thicknesses, and
three SiPM arrangements. Its original momentum phrase is superseded for this
preset by the later professor instruction relayed on 2026-07-15 to set neutron
kinetic energy to `1 GeV`.

Professor clarification for source position:

> shoot it as a gun, the location shouldn't matter now - just shoot at the center for now

Previously confirmed optical-boundary clarification remains in force:

> the tiles will still be painted/wrapped, just mean there is no air-gap between the tiles and steel.
