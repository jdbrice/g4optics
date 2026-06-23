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

Run the checked-in 3x3, 100-event pilot plan:

```bash
G4_DATA_ROOT=~/geant4-data/11.4.2 \
SCAN_ARGS_FILE=hpc/osc/scan_args_pilot_3x3_100events.txt \
  sbatch -A YOUR_ACCOUNT --array=1-9 hpc/osc/submit_scan.sbatch
```

After the array finishes, merge the per-point `efficiency_map.csv` files:

```bash
python3 hpc/osc/merge_array_efficiency_maps.py --job-id JOBID
```

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
