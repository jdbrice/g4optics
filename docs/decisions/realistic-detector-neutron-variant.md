# Realistic Detector Neutron Variant — Decision Record

- **Status:** Scientific inputs and engineering design confirmed; infrastructure baseline implemented, OSC production validation pending
- **Recorded:** 2026-07-13
- **Last updated:** 2026-07-15
- **Working branch:** `exp/realistic-detector`
- **Branch baseline:** `practice` at `50ec06d4`
- **Primary implementation area:** `test/OpNovice2`

The infrastructure baseline now includes the opt-in SAE 304 absorber, neutron
causal observables, mergeable event summaries, the locked
`realistic-neutron-v1` runner preset, deterministic campaign generation,
append-only submission/recovery attempts, frozen-source and executable gates,
per-task result identities, campaign finalization, complete ROOT event audits,
an absorber-disabled electron differential regression, benchmark resource
capture, absorber-convergence analysis, production-statistics recommendation,
and pinned event-level bootstrap analysis. An optional Geant4 visualization
tool provides a one-time human check of the steel/tile/SiPM layout. Local checks
include deterministic fixture and fake-Slurm coverage; formal acceptance and
statistical evidence still require executing the documented gates and campaigns
in the OSC Geant4 `11.4.2` environment.

## Purpose

This branch will add a simplified, more realistic detector variant to the existing EJ-200 tile and SiPM optical simulation:

1. place 4 cm of steel upstream of the scintillator tile;
2. replace the electron primary with a neutron;
3. compare tile thicknesses to determine whether increased neutron-induced light production outweighs the poorer optical collection observed for thicker tiles.

The first model is a local, single-absorber-plus-single-tile study. It is not yet a full multilayer sampling-calorimeter model.

## Authority and interpretation

The requirements below come from professor messages relayed on 2026-07-13. When a professor clarification differs from the current full-detector ePIC geometry, the explicit clarification governs this simplified study and the difference must be recorded.

The official geometry reference inspected for this record is the tagged ePIC release [`26.07.0`](https://github.com/eic/epic/tree/26.07.0). Future ePIC geometry changes are not automatically adopted.

## Confirmed decisions

### D-001 — Scientific question

**Decision:** Compare the competing thickness effects using quantities that keep light production and optical collection distinguishable.

The intended result should separate at least:

- scintillation photons produced per incident neutron;
- optical collection efficiency;
- photons entering the SiPM volume per incident neutron, which is the combined outcome.

In the current model, “SiPM detected photons” means optical photons entering the simplified silicon volume. It does not yet include a real SiPM photon-detection efficiency or electronics response.

### D-002 — Steel material and longitudinal thickness

**Decision:** Use the backward ePIC HCal absorber definition from release `26.07.0`:

- material: `StainlessSteelSAE304`;
- density: `7.9 g/cm3`;
- mass fractions: 74% Fe, 18% Cr, and 8% Ni;
- thickness along the beam direction: `4.0 cm`.

Sources:

- [`compact/hcal/backward_template.xml`](https://github.com/eic/epic/blob/26.07.0/compact/hcal/backward_template.xml#L10-L12)
- [`compact/materials.xml`](https://github.com/eic/epic/blob/26.07.0/compact/materials.xml#L423-L428)

The official absorber is a continuous 60-sided annular endcap layer, not one small steel plate per readout tile. Under the tagged geometry it spans approximately `r = 14–267 cm`. The `100 x 100 mm` readout segmentation must not be mistaken for absorber segmentation.

Sources:

- [`compact/definitions.xml`](https://github.com/eic/epic/blob/26.07.0/compact/definitions.xml#L665-L670)
- [`compact/definitions.xml`](https://github.com/eic/epic/blob/26.07.0/compact/definitions.xml#L693)
- [`compact/hcal/backward_template.xml`](https://github.com/eic/epic/blob/26.07.0/compact/hcal/backward_template.xml#L39-L61)

The transverse dimensions of the local steel proxy remain an engineering decision. They must be large enough that the result is insensitive to lateral edge leakage.

### D-003 — Steel-to-tile spacing

**Decision:** Use no geometric gap between the steel and the front face of the tile.

The current official ePIC `26.07.0` backward-HCal template contains a total `0.1 cm` layer gap, split into half-gaps around the active layer. The no-gap model is therefore a deliberate study-specific simplification based on the professor's direct clarification, not an exact copy of that full-detector geometry.

Source for the official gap: [`compact/hcal/backward_template.xml`](https://github.com/eic/epic/blob/26.07.0/compact/hcal/backward_template.xml#L10-L18)

Implementation consequence: the tile front face will border steel rather than the world volume. Its optical boundary must be defined for `tile -> steel`; the other tile faces still border their actual neighboring volumes. D-009 records the confirmed reflector treatment at this contact.

### D-004 — Neutron momentum and Geant4 energy input

**Decision:** Treat the professor's original neutron momentum as the source of truth:

```text
p_neutron = 1 GeV/c
```

Energy is a derived quantity, not a second free parameter. Using

```text
m_neutron c^2 = 0.939565 GeV
E_total = sqrt((pc)^2 + (m c^2)^2)
T_kinetic = E_total - m c^2
```

gives:

```text
E_total     = 1.372145 GeV
T_kinetic   = 0.432580 GeV
```

Geant4 GPS `/gps/energy` expects kinetic energy. The corresponding fixed-primary commands are therefore:

```text
/gps/particle neutron
/gps/energy 432.58 MeV
```

The professor's later phrase “if energy then about 2 GeV” is retained as an order-of-magnitude explanation, not interpreted as a separate instruction to use 2 GeV kinetic energy.

Run metadata must preserve both the requested momentum and the derived Geant4 input, for example:

```text
requested_momentum: 1 GeV/c
gps_kinetic_energy: 432.58 MeV
total_energy: 1.372145 GeV
energy_derivation: relativistic momentum-to-kinetic-energy conversion
```

### D-005 — Primary model scope

**Decision:** The first implementation will model one upstream steel absorber and one scintillator tile. The purpose is to isolate the competition between neutron-induced light production and tile light collection.

The following are not implied by this first scope:

- a full repeated steel/scintillator stack;
- complete ePIC backward-HCal geometry;
- calorimeter-level energy reconstruction;
- a realistic SiPM PDE or electronics chain.

Those can be considered only after the single-layer model is understood.

### D-006 — Local steel absorber proxy and convergence policy

**Decision:** Represent the local portion of the continuous ePIC absorber with a configurable rectangular steel slab. Keep its transverse dimensions fixed across all tile configurations so that absorber containment does not become an additional tile-dependent variable.

Use `500 x 500 x 40 mm` as the production-size candidate, where `40 mm` is the confirmed beam-axis thickness. Before production, run centered-beam transverse-size checks with:

```text
200 x 200 x 40 mm
300 x 300 x 40 mm
500 x 500 x 40 mm
```

Adopt the `500 x 500 mm` transverse size after the `300 x 300 mm` and `500 x 500 mm` results agree within statistical uncertainty for both scintillation photons per incident neutron and photons entering the SiPM per incident neutron.

All later position scans must also validate that every beam position, together with the required shower margin, remains inside the absorber footprint.

### D-007 — Controlled tile and optical baseline

**Decision:** The first neutron comparison will use only the two established `50 x 50 mm` EJ-200 tile configurations and vary thickness from `4 mm` to `16 mm`.

Keep the following optical configuration fixed across both thicknesses:

- surface preset: `polishedfrontpainted`;
- reflectivity model: repository empirical EJ-510 curve;
- SiPM: `2.4 x 2.4 x 0.5 mm`, centered on the `-Z` face;
- dimple: disabled;
- SiPM coupling: current undimpled zero-gap proxy (`optical_coupling=none`);
- steel absorber: the same D-006 slab configuration for both tiles;
- scintillator and optical-process properties: unchanged from the current practice baseline.

`polishedfrontpainted` is the best current working surface proxy under the primary 50 mm-tile shape metric. It is not claimed to uniquely identify the physical finish. The measured approximately `115 x 115 mm` tile groups and the prospective 24 mm-thick tile are excluded from the first neutron comparison.

### D-008 — Scope of the 55 mrad divergence choice

**Decision:** Use `55 mrad` as the nominal divergence for the first neutron study because D-007 intentionally includes only the two `50 x 50 mm` tile groups.

This choice is scoped and provisional:

- in the 5000-event, 50 mm-only refinement, `55 mrad` is the numerical minimum of the primary all-point scaled-RMSE metric;
- the same refinement supports a broad `45–75 mrad` plateau rather than a uniquely resolved divergence;
- when the two measured approximately `115 x 115 mm` groups are included, the four-sample calibration does not select 55 mrad: shape and inside-tile metrics favor `45 mrad`, while all-point chi-square favors `75 mrad`;
- therefore `55 mrad` must not be described as the global best value for all four tile groups.

If either `115 x 115 mm` group is added to this neutron study, the divergence decision must be reopened with `45 mrad` and `75 mrad` as the established candidates. The present use of 55 mrad records a deliberate dataset-scope choice, not a universal beam calibration.

### D-009 — Reflector retained at the no-air-gap steel contact

**Decision:** The tile remains painted or wrapped at the face next to the steel. The professor's “no air-gap” clarification means that the steel contacts the outside of the retained reflector; it does not mean that the reflector is removed to create a bare EJ-200-to-steel optical interface.

For the D-007 first comparison, retain the current EJ-510 painted baseline and represent the coating as an implicit optical boundary rather than an explicit volume:

- add an ordered `tile -> steel` border surface on the steel-facing tile face;
- give that boundary the same `polishedfrontpainted`, UNIFIED `dielectric_dielectric`, and empirical EJ-510 reflectivity model used by the controlled optical baseline;
- retain the existing painted `tile -> world` treatment on tile faces that still border the world;
- do not use a bare `dielectric_metal` steel interface in the primary comparison.

The simplified geometry may therefore place the tile and steel solids directly adjacent while the zero-thickness optical surface stands in for the retained coating. This preserves zero **air** gap without falsely removing the paint/wrap. A bare-steel boundary would be a separate sensitivity model and would require its own steel optical properties.

The professor's wording also covers wrapped tiles in general. It does not imply that every future wrapping system should use EJ-510 properties; EJ-510 is selected here specifically because D-007 fixes the current painted 50 mm-tile baseline.

### D-010 — Staged center, one-dimensional, and two-dimensional study

**Decision:** Build up the spatial study in three stages rather than starting with a full two-dimensional production scan:

1. run the controlled `4 mm` versus `16 mm` comparison at the tile center, `x = 0`, `y = 0`;
2. after the centered configuration passes validation, run a one-dimensional line scan through the tile center, using identical positions and source settings for both thicknesses;
3. add a two-dimensional scan only after the center and one-dimensional results are understood.

In the first two stages, “point” refers to the transverse source-position distribution. It does not mean a zero-angle beam: retain the D-008 nominal `55 mrad` angular divergence. Do not automatically copy the earlier electron-study `sigma = 5 mm` transverse profile into this neutron study.

Because the D-007 geometry is nominally symmetric in `x` and `y`, an `x` scan at `y = 0` is the default line-scan interpretation. D-011 fixes the first line-scan grid and symmetry check; a finite transverse profile remains a later-stage decision.

### D-011 — First one-dimensional scan grid

**Decision:** After the centered comparison is validated, use the following point-position line scan for both tile thicknesses:

```text
y = 0 mm
x = 0, 5, 10, 15, 20, 25, 30, 35, 40 mm
```

Reuse the validated centered result at `x = 0` rather than rerunning an identical configuration. The `x = 25 mm` point lies on the nominal edge of the `50 x 50 mm` tile; the `30–40 mm` points probe shower spill-in when the nominal beam position is outside the tile.

Also run `x = -20 mm`, `y = 0` as a symmetry and geometry-offset cross-check. Its result must agree with the corresponding `x = +20 mm` result within statistical uncertainty before the positive-half scan is interpreted as representative of the full line.

All positions retain the D-008 `55 mrad` angular divergence and the D-010 point-like transverse source-position distribution. A finite beam spot and the later two-dimensional grid are post-first-pass decisions, not blockers for implementing the center and one-dimensional stages.

### D-012 — Event-level causal observable model

**Decision:** Preserve a compact per-event record of the complete causal chain

```text
incident neutron
-> interaction in steel
-> charged-particle transport into the tile
-> energy deposited in the tile
-> optical photons produced
-> photons entering the SiPM
```

The production ntuple and mergeable run summary must contain the following quantities.

#### Steel interaction record

- total energy deposited in steel, `steel_edep_mev`;
- primary-neutron `elastic`, `inelastic`, and `capture` interaction counts in steel;
- derived boolean flags for each interaction category and an `any_interaction` flag.

These are diagnostic records, not switches that enable or disable physics. They are not mutually exclusive: for example, one primary neutron may scatter elastically before undergoing an inelastic interaction. `elastic` records direction/energy-changing nuclear scattering, `inelastic` identifies nuclear excitation or breakup that produces the main shower-like final state, and `capture` records neutron absorption after any slowing. Capture may be rare for the initial fast neutron but remains useful as an audit category.

Classify interactions from the Geant4 process category or subtype for the primary-neutron track while it is in steel, rather than relying only on fragile process-name string matching.

#### Transport into the tile

- number of unique charged tracks entering the tile for the first time;
- summed kinetic energy at first entry;
- entry counts and summed kinetic energy split into `electron/positron`, `proton`, and `other charged` categories;
- an explicit flag and position for whether the primary neutron itself enters the tile, retaining the meaning of the existing primary-only `hit_valid` field without mislabeling it as the whole shower.

A charged track must be counted only on its first entry into the tile so that boundary re-entry does not inflate the shower multiplicity.

#### Tile response and optical outcome

- total tile energy deposition, plus diagnostic `electron/positron`, `proton`, and `other charged` deposition components;
- existing total generated optical-photon and scintillation-photon counts;
- an explicit per-event Cerenkov-photon count;
- existing photons entering the SiPM proxy volume.

Do not treat steel energy deposition as the shower energy delivered to the tile: energy carried out of the steel by tracked secondaries is separate. Likewise, tile energy deposition and scintillation yield need not be proportional event by event because particle composition and the existing scintillator quenching model matter.

#### Zero-light handling and summary definitions

If an event creates no optical photons, its per-event collection efficiency is undefined. Store `NaN` plus a validity flag rather than assigning zero. Report zero-scintillation and zero-SiPM event fractions separately.

For every scan position and thickness, the primary run-level quantities are:

```text
production = sum(scintillation photons) / incident neutrons
collection = sum(SiPM photons) / sum(generated optical photons)
net response = sum(SiPM photons) / incident neutrons
```

The summary must also report the `16 mm / 4 mm` ratio for all three quantities, event-level mean/RMS/statistical error for tile energy deposition and photon yields, and the zero-light fractions. Do not use the mean of per-event collection ratios as the primary collection estimator.

Production runs store these fixed event aggregates, not complete step or trajectory dumps. Full process/secondary traces are limited to low-event diagnostic runs. If the Cerenkov contribution is not negligible, origin-tagged SiPM counts can be added as a later diagnostic rather than expanding the first production schema preemptively.

### D-013 — Geometry and configuration interface

**Decision:** Use a two-layer interface: keep the low-level absorber geometry minimally configurable, and lock the accepted scientific configuration behind a versioned study preset.

#### Low-level absorber geometry

Add the following `PreInit`-only, non-broadcast Geant4 messenger commands:

```text
/opnovice2/absorber/enabled true
/opnovice2/absorber/size 500 500 40 mm
```

`size` always means full `x y z` dimensions, not half-lengths. The detector defaults are `enabled=false` and `500 x 500 x 40 mm`, preserving all existing macros while making the production-size candidate the default whenever the absorber is enabled. Generated neutron-study macros must nevertheless write both commands explicitly rather than relying on those defaults.

The absorber material is the exact D-002 `StainlessSteelSAE304` definition and is not exposed as a messenger option in this first model. Likewise, do not expose independent absorber gap or position commands. The absorber is centered on the tile in `x` and `y`, and its downstream face is derived to coincide with the tile's `+Z` face. This prevents configurations that silently violate the confirmed material, thickness, or no-gap geometry while retaining the size freedom required for the D-006 convergence check.

Give the physical volume a stable `SteelAbsorber` identity and expose the constructed physical/logical volume through detector getters. D-012 interaction and energy-deposition accounting must use that volume identity rather than process or volume-name string matching.

When the absorber is enabled, retain the D-009 ordered `tile -> steel` optical border surface in addition to the `tile -> world` surface used on faces that still border the world.

#### Versioned study preset

Extend the existing scan runner with:

```text
--study-preset realistic-neutron-v1
```

The preset locks the D-002 through D-009 source, absorber, optical-surface, and SiPM baseline, including the `1 GeV/c` neutron momentum and its derived `432.58 MeV` GPS kinetic energy. Under this preset, only the intended study axes may vary:

- tile thickness: `4 mm` or `16 mm`;
- absorber transverse size for the accepted `200/300/500 mm` convergence study, while its longitudinal thickness remains `40 mm`;
- scan position/stage;
- event count and random seed.

Reject direct `--primary-energy`, material, gap, absorber-position, or manual `--beam-z` overrides under the preset. Momentum remains the authoritative source input. The generated macro must contain the resolved `/gps/particle neutron` and `/gps/energy 432.58 MeV` commands explicitly.

#### Absorber-aware source placement

Continue to center the tile at the origin and send the beam from `+Z` toward `-Z`. Derive the longitudinal positions from the resolved full tile thickness `t_tile`, absorber thickness `t_steel`, and a fixed source clearance `c = 1.5 mm`:

```text
z_tile_front     = t_tile / 2
z_steel_center   = z_tile_front + t_steel / 2
z_steel_upstream = z_tile_front + t_steel
z_source         = z_steel_upstream + c
```

The accepted resolved positions are:

| Tile thickness | Tile front | Steel center | Steel upstream face | Source Z |
| ---: | ---: | ---: | ---: | ---: |
| `4 mm` | `2 mm` | `22 mm` | `42 mm` | `43.5 mm` |
| `16 mm` | `8 mm` | `28 mm` | `48 mm` | `49.5 mm` |

Thus the source-to-tile-front distance is `41.5 mm` for both thicknesses. The `1.5 mm` clearance is a numerical separation between the virtual GPS vertex and the steel's upstream boundary; it is not a steel-to-tile air gap. No further professor clarification is needed unless a real gun-to-steel distance later becomes a scientific input. With the accepted per-axis `55 mrad` angular sigma, the nominal RMS transverse displacement over `41.5 mm` is approximately `2.28 mm` per axis.

When the absorber is enabled, the runner must use this absorber-aware calculation rather than the legacy `tile half-thickness + 1.5 mm` rule and must reject manual `--beam-z`. Scan-point `x/y` coordinates remain the GPS transverse center.

#### Validation and reproducibility metadata

Fail before event generation if dimensions are non-positive, the absorber does not cover the tile in `x/y`, any detector volume lies outside the world, the source is not strictly upstream of and outside the steel, the source lies outside the world or nominal absorber footprint, or the resolved geometry overlaps. The study preset additionally enforces the locked `40 mm` steel thickness and the D-007 `-Z` SiPM configuration. Do not invent a fixed shower-containment margin: record the nominal minimum transverse distance from all requested source positions to the absorber edge, and use the D-006 `200/300/500 mm` convergence result to establish edge insensitivity.

Extend `run_config.json` without removing existing fields. Record at least:

- a configuration-schema version and `study_preset=realistic-neutron-v1`;
- absorber enable state, box shape, stable volume identity, exact material name, density, mass fractions, and ePIC `26.07.0` reference;
- full size, center, upstream/downstream face positions, zero tile gap, and tile-contact optical-boundary model;
- requested neutron momentum and mass, derived total and kinetic energies, derivation label, GPS direction, source-clearance rule, and resolved source Z;
- point-like transverse profile, `55 mrad` angular model, and nominal minimum absorber-edge distance for the requested grid;
- runner validation results, exact generated macro paths, and the existing command and Git provenance.

The local runner owns this resolution and validation logic. D-015 propagates the same versioned configuration through the OSC plan, Slurm, merge, and archive workflows without redefining it.

### D-014 — Validation ladder and production-statistics policy

**Decision:** Do not copy a fixed event count from the electron studies. Use a staged validation ladder, estimate the neutron model's variance and cost with a fixed pilot, and freeze the final production event count from explicit precision targets before production begins.

#### Deterministic and geometry gates

Before statistical interpretation, the implementation must pass all of the following:

- a differential, fixed-seed, absorber-disabled electron regression: build the
  baseline executable from `practice` commit `50ec06d4` and the candidate from
  the study commit in the same pinned Geant4 `11.4.2` environment, then run the
  same `100-event`, centered, fixed-`1 MeV` electron macro with the `50 x 50 x 4
  mm`, polished-front-painted/EJ-510, `-Z` SiPM configuration and the same two
  seeds; selected integer summary fields must agree exactly and selected
  floating fields within the recorded tolerance, with both executable hashes
  retained;
- dry-run generation and schema checks for all `4/16 mm tile x 200/300/500 mm absorber` combinations;
- fail-fast tests for every D-013 forbidden preset override;
- geometry initialization and overlap checks for all six combinations;
- exact checks of the D-013 resolved source positions, including `43.5 mm` for the `4 mm` tile and `49.5 mm` for the `16 mm` tile;
- one full-optical event for each of the six geometry combinations to verify initialization, output creation, and the complete D-012 event schema.

The repository also provides an optional visualization for both tile thicknesses
with the production-size candidate, showing the steel/tile contact, `-Z` SiPM,
and `+Z -> -Z` beam direction. A one-time visual review is recommended because
the steel geometry is new, but by study-owner decision it is not an acceptance
gate. The generated macro and run configuration make the view reproducible;
machine-checked geometry initialization, overlaps, source resolution, and event
schema remain the formal geometry gates.

The one-event runs are execution and schema smoke tests only. They are not evidence that neutron interaction or light-production physics is correct. A full audit of every real ROOT `scan` tree must also enforce the D-012 causal invariants: category sums equal their totals, flags equal `count > 0` (including the derived any-interaction flag), a SiPM photon implies generated optical light, invalid collection ratios and optional coordinates use `NaN` plus their validity flags, event IDs are contiguous, and no unexpected `NaN` or negative count/energy appears.

Hadronic-only runs with optical production disabled may be used for bounded debugging of the shower and interaction classification. They cannot satisfy a full-model acceptance gate and cannot be merged with production results.

#### Full-optical benchmark and pilot

At the center with the `500 x 500 x 40 mm` absorber, run a `100-event` full-optical benchmark for each tile thickness. Immediately after finalization, capture the parent and batch-step `sacct` rows and record wall time per event, maximum resident memory, ROOT and total recorded bytes per event, photon multiplicities, interaction fraction, and zero-light fractions. The benchmark report identifies the measured limiting configuration and recommends a divisor of `1000` no larger than `250` events per task that is projected to remain within approximately one hour. Review and freeze that block size before generating the pilot; do not assume in advance that `16 mm` is slower.

The fixed statistical pilot is `1000 events` per configuration, split into four independent `250-event` seed blocks. If the benchmark requires smaller execution blocks to stay within the task-time target, retain the same `1000-event` aggregate pilot. All seeds must be explicit, distinct, and recorded. Do not reuse the same random stream for the `4 mm` and `16 mm` configurations or analyze their events as paired samples.

#### Absorber transverse-size convergence

Run the centered pilot for both tile thicknesses at `200`, `300`, and `500 mm` square transverse absorber sizes. The `200 mm` result diagnoses the edge-leakage trend but is not required to agree with the larger sizes.

For each thickness, compute independent-event bootstrap `95%` confidence intervals for the `300 mm / 500 mm` ratios of:

- scintillation photons per incident neutron;
- SiPM photons per incident neutron, the net response.

The production-size candidate passes only when both confidence intervals lie entirely within `[0.95, 1.05]` for both tile thicknesses. If an interval is too broad, add statistics before judging convergence. If it is sufficiently narrow but remains outside the equivalence band, do not declare `500 mm` converged; reopen the absorber-size decision with a larger candidate.

#### Production event-count derivation and inference

Use the full-optical pilot event records to estimate the event-level uncertainty of the `16 mm / 4 mm` ratios for production, collection, and net response. Determine the required event count so that the bootstrap `95%` confidence interval for every centered ratio has a relative half-width no larger than `5%`. The analysis tool projects each observed pilot half-width with the explicit `1/sqrt(N)` scaling approximation, takes the largest requirement, inflates it by `25%`, rounds upward to complete execution blocks with at least four blocks, and emits a recommendation requiring human review. Freeze the reviewed common event count for both thicknesses before generating the final centered production campaign; the analysis tool never launches production automatically.

Production data must contain at least four independent seed blocks per configuration. Use a fixed analysis seed and `10,000` event-level bootstrap resamples while recomputing each ratio-of-sums estimator. Report event-level mean, RMS, and standard error as required by D-012; use Wilson `95%` intervals for interaction and zero-light event fractions.

Interpret the centered net-response ratio as follows:

- if its `95%` confidence interval is wholly above `1`, the `16 mm` tile has the larger net response;
- if it is wholly below `1`, the `4 mm` tile has the larger net response;
- if it still crosses `1` after the `5%` precision target is met, report that no winner is resolved at the achieved precision rather than increasing the sample indefinitely.

The production and collection ratios explain the two competing effects; they must not be collapsed into the net ratio alone. If the final frozen sample unexpectedly misses the precision target, report the achieved interval and treat any top-up as a new, explicitly recorded extension rather than silently continuing the run.

#### One-dimensional scan precision

For the D-011 line scan, use the same event count for the two thicknesses at every compared position. At `x = 0–25 mm`, target a relative half-width no larger than `10%` for the thickness-ratio confidence intervals. At the low-signal spill-in points `x = 30–40 mm`, do not chase unstable relative precision when the response is near zero; report the mean response, zero-light fraction, and a `95%` upper interval instead.

The `x = -20 mm` symmetry check passes only when, for both production and net response, the `-20/+20` point estimate lies within `[0.95, 1.05]`, its `95%` confidence interval contains `1`, and its relative half-width is no larger than `10%`.

These `5%` centered/convergence and `10%` exploratory-line targets are analysis thresholds for this first study, not professor-specified detector constants. D-015 preserves configuration identity, independent seed blocks, and mergeable sufficient statistics so these rules can be applied without rerunning successful tasks.

### D-015 — Local and OSC campaign workflow

**Decision:** Keep the general scan runner as the single configuration resolver and add a dedicated, versioned campaign layer for the realistic-neutron study. Do not duplicate the D-013 scientific constants across local, plan-generation, Slurm, merge, or analysis scripts.

#### Campaign source of truth

The existing runner remains authoritative through:

```text
run_sipm_cavity_scan.sh --study-preset realistic-neutron-v1
```

Add a dedicated campaign generator, such as:

```text
hpc/osc/generate_realistic_neutron_campaign.py
```

Each generated campaign directory contains:

```text
campaign.json
tasks.tsv
scan_args.txt
README.md
```

`tasks.tsv` is the machine-readable logical-task manifest. `scan_args.txt` is a derived, shell-quoted execution file for the current Slurm harness. The campaign manifest records its schema version, study preset, stage, creation time, clean Git commit, plan hash, requested event/block structure, and expected task count. The general electron/Sr-90 plan generator remains available for its existing workflows and does not become the source of truth for this study.

#### Staged task decomposition

Generate and gate campaigns sequentially rather than submitting one combined plan:

| Campaign stage | Logical task matrix |
| --- | --- |
| full-optical geometry smoke | `2 thicknesses x 3 absorber sizes = 6` |
| OSC benchmark | `2 thicknesses = 2` |
| convergence pilot | `2 thicknesses x 3 sizes x 4 seed blocks = 24` |
| centered production | `2 thicknesses x B production blocks` |
| one-dimensional scan | `2 thicknesses x 9 new positions x B line blocks` |

One Slurm task represents exactly one `(stage, tile thickness, absorber size, x, y, seed block)` tuple. The D-011 line campaign contains only the nine new positions `x = 5–40 mm` plus `x = -20 mm`; it imports `x = 0` from the accepted centered production campaign. It must not rerun the center or substitute pilot data for it. The later two-dimensional stage requires a new campaign decision after the first stages are understood.

#### Stable identity and random seeds

Every logical task has a stable, Slurm-independent `logical_task_id` and a canonical `configuration_hash`. Slurm job and array-task IDs identify execution attempts only and are never used as the scientific configuration identity.

Add an explicit Geant4 seed-pair interface to the runner. The campaign generator deterministically derives two valid, globally unique seeds from a campaign seed and logical task ID, then writes them to the task manifest, generated macro, and `run_config.json`. Different thicknesses, positions, sizes, and blocks do not reuse a random stream. A retry of one logical task reuses that task's original seeds and configuration hash so that it is an exact replacement, not a new statistical block.

#### Pilot and confirmatory production separation

The convergence pilot may be extended with additional independent blocks solely to resolve the D-014 absorber-size gate. The centered `500 mm` pilot used to estimate production `N` is not included in the final thickness comparison. After `N` is frozen, centered production uses new independent seeds. This prevents the same pilot fluctuations from both choosing the sample size and entering the final confidence interval.

Pilot, hadronic-only debug, geometry smoke, benchmark, centered production, and line-scan data remain distinct stages in metadata and cannot be silently merged. The center reused by the line scan is the confirmatory centered-production dataset.

#### Pinned production environment and immutable source

The repository currently uses Geant4 `11.3.2` in the local Docker image and `11.4.2` in the OSC Apptainer workflow. Do not mix their event data. All accepted benchmark, pilot, convergence, and production evidence for this study uses the pinned OSC Geant4 `11.4.2` environment. Local `11.3.2` runs are development and early-smoke artifacts only; upgrading the local image for parity is desirable but is not a first-production blocker. Final smoke gates must be repeated in the pinned OSC environment.

Every OSC campaign records the Apptainer image SHA-256, reported Geant4 version, all Geant4 dataset identities, compiler/build identity, and executable checksum. Production generation and submission require a clean Git worktree at one fixed commit. Stage the campaign in an immutable commit-specific source/build directory, complete one prebuild smoke, and have all array tasks run that frozen executable. Tasks must fail rather than build from or continue against a shared checkout that changed after submission.

#### Submission, recovery, finalization, and analysis

Provide campaign-specific submission support for:

```text
--check-only
--resume
--retry-failed
```

The append-only submission manifest maps each execution attempt to the campaign/plan hash, Git commit, image hash, job ID, and submission time. Resume or recovery submits only missing or invalid logical tasks. A retry does not overwrite prior evidence; the finalizer selects the explicitly valid attempt and rejects ambiguous duplicate successes.

The dedicated finalizer audits every expected logical task for a completion marker, configuration hash, preset, commit, environment identity, event count, seed identity, and the presence of its ROOT file, summary, macro, log, and `run_config.json`. Existing one-row efficiency-map merging may be reused as a convenience view, but it is not sufficient for D-014.

The finalized campaign writes at least:

```text
task_index.tsv
configuration_summary.csv
thickness_ratios.csv              # finalized point estimates; no bootstrap CI
validation_report.json
analysis_config.json              # pinned bootstrap definition from finalizer
event-audit/event_audit.json
analysis/thickness_ratios.csv
analysis/configuration_intervals.csv
analysis/absorber_convergence.csv
analysis/production_statistics.json
analysis/analysis_config.json
benchmark/benchmark_report.csv     # benchmark stage only
benchmark/benchmark_report.json    # benchmark stage only
```

Event-level bootstrap analysis reads all audited per-block ROOT files. If a combined ROOT file is produced, retain the original block files and their seed provenance. The analysis configuration records the fixed bootstrap seed, resample count, estimators, thresholds, included campaign/task IDs, and any excluded invalid attempts.

#### Archive boundary

For every campaign that passes its gate, package the campaign/task/submission manifests, generated macros, run configurations, logs, original ROOT blocks, summaries, finalized analysis products, environment manifest, and `SHA256SUMS`. Keep debug and hadronic-only artifacts separate from full-optical evidence.

The repository stores source code and workflow infrastructure; large run products are working artifacts until a complete package is copied into the established OneDrive `Results` archive. The archive package, not repo-local `scan_latest` links or Slurm job IDs, is the durable result identity.

## Current repository facts relevant to implementation

- `OpNovice2` currently uses `FTFP_BERT` together with `G4OpticalPhysics`; the existing physics list already provides neutron elastic, inelastic, and capture processes in the energy range needed here.
- The detector now has an opt-in `StainlessSteelSAE304` absorber volume; it remains disabled outside the locked neutron preset so legacy studies retain their geometry.
- The primary source is backed by `G4GeneralParticleSource`; the general runner still supports electron/Sr-90 studies while the locked `realistic-neutron-v1` preset resolves and records the neutron configuration.
- The current beam convention is from `+Z` toward `-Z`; adding 4 cm of steel requires absorber-aware source placement.
- Existing output already separates generated/scintillation photon counts, collection efficiency, and photons entering the SiPM volume.

These statements describe the implemented baseline and its compatibility boundary; OSC acceptance remains pending.

## Engineering decision status

No engineering decisions remain open before implementation begins. Newly discovered choices that would alter a confirmed scientific input, comparison baseline, validation threshold, or campaign identity must be recorded as a new decision rather than silently folded into the implementation.

## Invariants for the next phase

- Preserve `1 GeV/c` as the authoritative neutron input and derive the GPS kinetic energy.
- Preserve 4 cm `StainlessSteelSAE304` as the absorber material/thickness reference.
- Preserve the professor-confirmed no-gap geometry and document its difference from ePIC `26.07.0`.
- Preserve the paint/wrap at the steel-facing tile surface; no air gap must not be implemented as a bare EJ-200-to-steel optical interface.
- Keep the same configurable steel transverse proxy across tile comparisons, and complete the accepted `200/300/500 mm` edge-leakage convergence check before production.
- Keep the D-007 `50 x 50 x 4/16 mm` optical baseline fixed in the first comparison.
- Label `55 mrad` as a provisional 50 mm-only choice; reopen `45/75 mrad` when the measured approximately `115 x 115 mm` groups enter scope.
- Follow the D-010 center -> one-dimensional -> two-dimensional staging, retaining 55 mrad angular divergence while the first-pass transverse source position remains point-like.
- Use the D-011 one-dimensional grid from `x = 0` through `40 mm` in `5 mm` steps at `y = 0`, plus the `x = -20 mm` symmetry check.
- Preserve the D-012 causal event record and keep neutron interaction probability, light production, optical collection, and net response as separate quantities.
- Preserve the D-013 two-layer configuration interface, absorber-aware source placement, and versioned `realistic-neutron-v1` preset; the `1.5 mm` source clearance is not a steel-to-tile gap.
- Follow the D-014 deterministic, geometry, benchmark, pilot, convergence, and production gates; derive and freeze production statistics from the accepted `5%/10%` precision policy rather than copying electron-study event counts.
- Follow the D-015 dedicated campaign workflow, keep pilot and confirmatory production separate, preserve stable logical-task and seed identities, and use only the pinned OSC Geant4 `11.4.2` environment for accepted statistical evidence.
- Change one scientific variable at a time when comparing tile thicknesses.
- Record enough metadata to reconstruct the geometry, source, physics environment, and derivation later.

## Source statements retained verbatim

Initial professor description:

> So the idea is to test in geant the more realistic detector. Which means 2 things. Adding 4cm of steel in front of the tile (between tile and particle gun) and then shooting neutrons instead of electrons.
>
> The reason. Our current studies show that thicker tiles collect less light but our old studies show that thicker tiles create more light from neutrons. So we now should see which effect is stronger.
>
> The neutron should have momentum of about 1 gev
>
> The calorimeter is made to measure neutral particles energy. But neutral particles don't create light in scintillation crystals, only charged particles do
>
> So you need to convert an uncharged particle like a neutron into a bunch of charged particles. You can do that by putting a very dense material in its way. It won't interact electromagnetically but if it hits a nucleus in the material it will interact and "shower" or break out a bunch of new particles. Some will be electrons and protons which will create light in the acintilators.
>
> So by combining layers of dense material with scintillating crystal you create a "sampling calorimeter" the dense material makes the neutral particle shower charged particles and the scintillator measures the amount of these. Together with many layers you can measure the total energy of the original neutral particle

Follow-up clarifications:

> We can find in the official epic geometry files.
>
> No gap
>
> If energy then about 2 GeV (1 GeV of mass and 1 GeV of momentum)

Optical-boundary clarification:

> the tiles will still be painted/wrapped, just mean there is no air-gap between the tiles and steel.
