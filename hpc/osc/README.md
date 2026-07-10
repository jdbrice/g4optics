# OSC Apptainer/Slurm Runs

`.sif` means Singularity Image Format. It is the single-file container image used by Apptainer/Singularity on clusters, roughly serving the role that a Docker image serves locally.

Build the Geant4 image on OSC:

```bash
apptainer build geant4.sif docker://carlomt/geant4:11.4.2-almalinux9
```

Run one smoke scan from the repository root:

```bash
hpc/osc/run_scan_apptainer.sh
```

Run an explicit scan:

```bash
hpc/osc/run_scan_apptainer.sh full custom --events 1 --source-mode gps \
  --tank-size "100 100 5 mm" \
  --x-min -5 --x-max 5 --y-min -5 --y-max 5 --step 5 --grid-unit mm
```

Submit the Week 5-8 smoke matrix as a Slurm array:

```bash
SCAN_ARGS_FILE=hpc/osc/scan_args_week5_8_smoke.txt \
  sbatch -A YOUR_ACCOUNT --array=1-7 hpc/osc/submit_scan.sbatch
```

Explicit scan arguments passed after `submit_scan.sbatch` take precedence over `SCAN_ARGS_FILE`.

## Point-Level Array Scans

For longer scans, split the grid so each Slurm array task runs one `(x, y)` point in serial Geant4. This keeps the stable `G4RUN_MANAGER_TYPE=Serial` path while letting Slurm run points concurrently.
`submit_scan.sbatch` automatically tags array outputs with `SCAN_RUN_ID_SUFFIX=job<jobid>_task<taskid>` so tasks that start in the same second do not overwrite each other's run directories.

Run the checked-in 3x3, 100-event pilot plan:

```bash
G4_DATA_ROOT=~/geant4-data/11.4.2 \
SCAN_ARGS_FILE=hpc/osc/scan_args_pilot_3x3_100events.txt \
  sbatch -A YOUR_ACCOUNT --array=1-9 hpc/osc/submit_scan.sbatch
```

After the array finishes, merge the per-point `efficiency_map.csv` files:

```bash
sacct -j JOBID --format=JobID,JobName,State,ExitCode,Elapsed
python3 hpc/osc/merge_array_efficiency_maps.py --job-id JOBID
```

By default the merge writes a ROOT-compatible run directory under `test/OpNovice2/scan_runs`.
For beam-size scans, the directory name is inferred from `run_config.json`, for example `test/OpNovice2/scan_runs/week9_2mm_thickness_1mm_beam_sigma/efficiency_map.csv`.
If auto-naming is not specific enough, pass `--label NAME`; if you need the old flat-file behavior, pass an explicit CSV path with `--out path/to/file.csv`.

If some tasks are still running or failed, the merge tool reports which Slurm output lacks `Scan complete`. To inspect only completed tasks, add `--allow-missing`.

Generate a new point-level plan:

```bash
python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/week5_grid_1000events.txt \
  --description "Week 5 19x19 position scan, 1000 events per point" \
  --events 1000 \
  --tank-size "100 100 5 mm" \
  --x-min -45 --x-max 45 \
  --y-min -45 --y-max 45 \
  --step 5 --grid-unit mm
```

The same generator can sweep Week 6 thicknesses or Week 7 surface presets by repeating options:

```bash
python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/week6_thickness_1000events.txt \
  --events 1000 \
  --tank-size "100 100 4 mm" \
  --tank-size "100 100 8 mm" \
  --x-min -45 --x-max 45 \
  --y-min -45 --y-max 45 \
  --step 5 --grid-unit mm

python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/week7_surface_1000events.txt \
  --events 1000 \
  --tank-size "100 100 5 mm" \
  --surface-preset polished \
  --surface-preset ground \
  --surface-preset wrapped \
  --x-min -45 --x-max 45 \
  --y-min -45 --y-max 45 \
  --step 5 --grid-unit mm
```

Additional painted presets are supported for lab matching:
`polishedfrontpainted`, `groundfrontpainted`, `polishedbackpainted`, and
`groundbackpainted`.

The lab v2 real-setup scaffold uses the repository's digitized EJ-510
reflectivity curve, the official EJ-550 constant refractive index 1.46, and the
confirmed circular Gaussian beam sigma of 5 mm. Grease thickness remains a
required experimental input:

```bash
GREASE_THICKNESS="... mm" \
hpc/osc/generate_lab_v2_realsetup_plans.sh
```

Divergence calibration starts from `polishedfrontpainted`, matching the
observed lab setup where EJ-510 is applied directly to the EJ-200 tile. The
later four-surface comparison retains both backpainted variants as sensitivity
models. Those variants explicitly use an air-gap surface `RINDEX=1.0003`; this
is recorded as a caveat in every plan and in `run_config.json`, not presented as
the refractive index of EJ-510. Override it only for a deliberate model study
with `BACKPAINTED_AIR_RINDEX=...` or the runner's `--surface-rindex`/
`--surface-rindex-csv` options.

The default grease absorption model is `transparent` (`ABSLENGTH=1000 mm`).
For an explicitly derived sensitivity model based on Eljen's 0.1 mm
transmission plot, use:

```bash
GREASE_THICKNESS="... mm" \
GREASE_ABSORPTION_MODEL=ej550-transmission-derived \
hpc/osc/generate_lab_v2_realsetup_plans.sh
```

`BEAM_SIGMA`, `GREASE_RINDEX`, `GREASE_RINDEX_CSV`, and
`GREASE_TRANSMISSION_CSV` remain optional overrides. The transmission-derived
model converts the digitized transmission values to an effective bulk
absorption length with `L=-0.1 mm/ln(T)`; it is recorded as a derived model in
`run_config.json`, not as a directly tabulated manufacturer absorption length.

For Week 8 dimple scans, add dimple options:

```bash
python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/week8_dimple_1000events.txt \
  --events 1000 \
  --tank-size "100 100 5 mm" \
  --dimple --dimple-radius 3 --dimple-sipm-mode surface \
  --x-min -45 --x-max 45 \
  --y-min -45 --y-max 45 \
  --step 5 --grid-unit mm
```

For side-mounted SiPM scans, add the existing scan-runner geometry overrides:

```bash
python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/side_x_center/week6_side_x_center_thickness_5mm_21x21_100events.txt \
  --description "Week 6 side-mounted +X center SiPM, 5 mm thickness, 21x21, 100 events per point" \
  --events 100 \
  --tank-size "100 100 5 mm" \
  --sipm-face +X \
  --sipm-local-position "0 0 0 cm" \
  --x-min -50 --x-max 50 \
  --y-min -50 --y-max 50 \
  --step 5 --grid-unit mm
```

For beam-size scans, keep the same point-level workflow and add `--beam-sigma`.
`--beam-sigma` requires `--source-mode gps`; omitted or `0` keeps the point-source GPS position, while a positive value uses a circular 2D Gaussian source spot around each scan center. The value is interpreted in `--grid-unit` units.

Generate bottom-center beam-size plans for the first production pass:

```bash
mkdir -p hpc/osc/generated/beam_sigma_bottom_center

for t in 2 4 5 8 10; do
  for s in 1 2 3; do
    python3 hpc/osc/generate_scan_plan.py \
      --out "hpc/osc/generated/beam_sigma_bottom_center/week9_beam_sigma_${s}mm_thickness_${t}mm_21x21_100events.txt" \
      --description "Beam sigma ${s} mm, bottom center, ${t} mm thickness, 21x21, 100 events per point" \
      --events 100 \
      --source-mode gps \
      --tank-size "100 100 ${t} mm" \
      --beam-sigma "${s}" \
      --x-min -50 --x-max 50 \
      --y-min -50 --y-max 50 \
      --step 5 --grid-unit mm
  done
done
```

Submit one or two arrays at a time, using `-A` on the command line rather than storing the account in tracked files:

```bash
G4_DATA_ROOT=~/geant4-data/11.4.2 \
SCAN_ARGS_FILE=hpc/osc/generated/beam_sigma_bottom_center/week9_beam_sigma_1mm_thickness_5mm_21x21_100events.txt \
  sbatch -A YOUR_ACCOUNT --time=01:00:00 --array=1-441 hpc/osc/submit_scan.sbatch
```

Merge after completion:

```bash
sacct -j JOBID --format=JobID,JobName,State,ExitCode,Elapsed
python3 hpc/osc/merge_array_efficiency_maps.py --job-id JOBID
```

The beam-size merge output defaults to a directory such as:

```text
test/OpNovice2/scan_runs/week9_2mm_thickness_1mm_beam_sigma/efficiency_map.csv
```

For a quick physics QA of a Gaussian beam run, inspect the ROOT ntuple columns `shoot_x_mm` and `shoot_y_mm`; their mean should be near the scan center, and their RMS should be close to the requested `beam_sigma`.

For Week 10 Sr-90 source-model scans, keep the GPS point-level workflow and add
`--source-model sr90-spectrum`. This is the Week 10.1 production default: GPS
samples the checked-in `sr90_allowed_beta_v1` Sr-90/Y-90 beta spectrum table.
The older `--electron-energy-mode sr90Beta` path remains available as
`--source-model sr90-empirical` for historical comparison only.

```bash
mkdir -p hpc/osc/generated/week10_sr90

python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/week10_sr90/week10_sr90_thickness_5mm_21x21_100events.txt \
  --description "Week 10 Sr-90 spectrum source, 5 mm thickness, 21x21, 100 events per point" \
  --events 100 \
  --source-mode gps \
  --source-model sr90-spectrum \
  --tank-size "100 100 5 mm" \
  --x-min -50 --x-max 50 \
  --y-min -50 --y-max 50 \
  --step 5 --grid-unit mm

G4_DATA_ROOT=~/geant4-data/11.4.2 \
SCAN_ARGS_FILE=hpc/osc/generated/week10_sr90/week10_sr90_thickness_5mm_21x21_100events.txt \
  sbatch -A YOUR_ACCOUNT --time=01:00:00 --array=1-441 hpc/osc/submit_scan.sbatch
```

The default merge label for this source mode is inferred from `run_config.json`,
for example `test/OpNovice2/scan_runs/week10_5mm_thickness_sr90_spectrum_source/efficiency_map.csv`.

For spectrum QA after merge, run:

```bash
cd test/OpNovice2
root -b -q 'plot_sr90_spectrum_qa.C("scan_runs/week10_5mm_thickness_sr90_spectrum_source")'
```

Week 10.2 adds angular divergence as a separate GPS beam property, not as part of the beam spot size. Internally, `--beam-divergence-mrad VALUE` is a GPS `beam2d` angular Gaussian with `/gps/ang/sigma_x VALUE mrad` and `/gps/ang/sigma_y VALUE mrad`; it is not a hard cone half-angle. The early `10 mrad` pass was only a small decision/smoke scan. After the lab estimate of roughly `5 deg` (about `100 mrad`), use sensitivity plans around `75, 100, 125 mrad`, while reusing or rerunning the corresponding `0 mrad` baselines.

```bash
mkdir -p hpc/osc/generated/week10_beam_divergence_sensitivity

python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/week10_beam_divergence_sensitivity/week10_1mev_100mrad_divergence_5mm_21x21_100events.txt \
  --description "Week 10.2 fixed 1 MeV, 100 mrad beam2d divergence, 5 mm thickness, 21x21, 100 events per point" \
  --events 100 \
  --source-mode gps \
  --primary-energy "1 MeV" \
  --beam-divergence-mrad 100 \
  --tank-size "100 100 5 mm" \
  --x-min -50 --x-max 50 \
  --y-min -50 --y-max 50 \
  --step 5 --grid-unit mm

python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/week10_beam_divergence_sensitivity/week10_sr90_spectrum_100mrad_divergence_5mm_21x21_100events.txt \
  --description "Week 10.2 Sr-90 spectrum, 100 mrad beam2d divergence, 5 mm thickness, 21x21, 100 events per point" \
  --events 100 \
  --source-mode gps \
  --source-model sr90-spectrum \
  --beam-divergence-mrad 100 \
  --tank-size "100 100 5 mm" \
  --x-min -50 --x-max 50 \
  --y-min -50 --y-max 50 \
  --step 5 --grid-unit mm
```

Repeat the same two commands with `75` and `125` when generating the full sensitivity set. Submit each divergence plan with its matching args file:

```bash
G4_DATA_ROOT=~/geant4-data/11.4.2 \
SCAN_ARGS_FILE=hpc/osc/generated/week10_beam_divergence_sensitivity/week10_1mev_100mrad_divergence_5mm_21x21_100events.txt \
  sbatch -A YOUR_ACCOUNT --time=01:00:00 --array=1-441 hpc/osc/submit_scan.sbatch

G4_DATA_ROOT=~/geant4-data/11.4.2 \
SCAN_ARGS_FILE=hpc/osc/generated/week10_beam_divergence_sensitivity/week10_sr90_spectrum_100mrad_divergence_5mm_21x21_100events.txt \
  sbatch -A YOUR_ACCOUNT --time=01:00:00 --array=1-441 hpc/osc/submit_scan.sbatch
```

The auto-merge labels include the divergence token, for example `week10_5mm_thickness_1MeV_energy_100mrad_beam_divergence` and `week10_5mm_thickness_100mrad_beam_divergence_sr90_spectrum_source`. For a quick QA, inspect `run_config.json` for `beam.angular_model = "beam2d"` plus `beam.divergence_parameter = "sigma_x=sigma_y"`, and compare `hit_x/y/z` or `scint_centroid_x/y/z` against the corresponding no-divergence baseline.

Week 10.1b also provides a validation-first decay source model:
`--source-model sr90-decay`. It uses a GPS Sr-90 ion primary at rest and
`G4RadioactiveDecayPhysics`; this is not the Week 10 default plan until the
decay beta spectrum has been checked. Use a new build directory or force a
rebuild for the first OSC smoke so the executable includes the decay physics
registration:

```bash
mkdir -p hpc/osc/generated/week10_sr90_decay

python3 hpc/osc/generate_scan_plan.py \
  --out hpc/osc/generated/week10_sr90_decay/week10_sr90_decay_5mm_center_100events.txt \
  --description "Week 10.1b Sr-90 decay source smoke, 5 mm thickness, center point" \
  --events 100 \
  --source-mode gps \
  --source-model sr90-decay \
  --tank-size "100 100 5 mm" \
  --x-min 0 --x-max 0 \
  --y-min 0 --y-max 0 \
  --step 5 --grid-unit mm

G4_FORCE_REBUILD=1 G4_BUILD_DIR=build-osc-sr90-decay \
G4_DATA_ROOT=~/geant4-data/11.4.2 \
SCAN_ARGS_FILE=hpc/osc/generated/week10_sr90_decay/week10_sr90_decay_5mm_center_100events.txt \
  sbatch -A YOUR_ACCOUNT --time=01:00:00 --array=1-1 hpc/osc/submit_scan.sbatch
```

After copying the ROOT outputs locally, validate with:

```bash
cd test/OpNovice2
root -b -q 'plot_sr90_decay_spectrum_qa.C("scan_runs/week10_5mm_thickness_sr90_decay_source","scan_runs/week10_5mm_thickness_sr90_spectrum_source")'
```

Acceptance is spectrum-first: the `decay_betas` ntuple should include both the
Sr-90 endpoint near `0.546 MeV` and the Y-90 high-energy tail near `2.28 MeV`.
If the decay run only reaches the Sr-90 endpoint, the chain is not yet suitable
for production scans; use the already validated `sr90-spectrum` plan instead.

Useful environment variables:

- `G4_APPTAINER_IMAGE=/path/to/geant4.sif`
- `G4_DATA_ROOT=/path/to/geant4-data`
- `G4_BUILD_DIR=build-osc`
- `G4_BUILD_JOBS=4`
- `G4_FORCE_REBUILD=1`
- `G4_PRINT_DATA_ENV=1`
- `G4RUN_MANAGER_TYPE=Serial`
- `PLOT_WITH_ROOT=0`

The wrapper binds this repository into the container at `/work/g4optics`, builds `test/OpNovice2` in `build-osc`, then calls the existing `run_sipm_cavity_scan.sh`. OSC runs default to `G4RUN_MANAGER_TYPE=Serial` because the scan macros use one Geant4 thread per point.

## Troubleshooting

If Geant4 aborts with messages such as `G4ENSDFSTATEDATA environment variable must be set`, `G4LEDATA data directory was not found`, or `G4LEVELGAMMADATA environment variable not set`, the Apptainer image does not expose the Geant4 runtime datasets. Keep the datasets in a persistent OSC directory and pass it to the wrapper:

```bash
hpc/osc/install_geant4_data.sh ~/geant4-data/11.4.2

G4_DATA_ROOT=~/geant4-data/11.4.2 G4_PRINT_DATA_ENV=1 hpc/osc/run_scan_apptainer.sh
```

The wrapper binds `G4_DATA_ROOT` into the container, auto-detects common Geant4 data paths, and exports Geant4 data environment variables. To inspect what the wrapper finds, run:

```bash
G4_DATA_ROOT=~/geant4-data/11.4.2 G4_PRINT_DATA_ENV=1 hpc/osc/run_scan_apptainer.sh
```

If `G4ENSDFSTATEDATA` is still missing, inspect the image contents directly:

```bash
apptainer exec geant4.sif find /opt/geant4-data /opt/geant4 /usr/local/share /usr/share -type d -name 'G4ENSDFSTATE*' -print
```
