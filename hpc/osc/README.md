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
  sbatch --array=1-7 hpc/osc/submit_scan.sbatch
```

Useful environment variables:

- `G4_APPTAINER_IMAGE=/path/to/geant4.sif`
- `G4_BUILD_DIR=build-osc`
- `G4_BUILD_JOBS=4`
- `G4_FORCE_REBUILD=1`
- `PLOT_WITH_ROOT=0`

The wrapper binds this repository into the container at `/work/g4optics`, builds `test/OpNovice2` in `build-osc`, then calls the existing `run_sipm_cavity_scan.sh`.
