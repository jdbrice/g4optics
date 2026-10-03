# Ten-layer steel / SiPM layout study

This guide builds and runs the four-layout study with **Geant4 11.4.2, Serial**.
Start with the 4 mm tile and ten neutrons per layout. This is a small engineering
check of geometry, output, and counting; it does not rank layouts or establish
scientific precision. No OSC account is needed for the Docker route.

The [completed first 24,000-event sample](results/first-scan-d0defcdc/README.md) includes figures, exact tables, provenance and commands to reproduce the figures without running Geant4.

The model has ten 500 × 500 × 40 mm steel plates, ten whole 100 × 100 mm tiles,
and nine 0.5 mm gaps between a tile and the next steel plate. Each SiPM proxy is
2.4 × 2.4 × 0.5 mm. The 1 GeV kinetic-energy neutron starts upstream and travels
along −Z. PDE and readout electronics are not simulated.

| Layout | SiPMs per tile | Face and local centers (mm) |
|---|---:|---|
| `back-four` | 4 | −Z: (−25,−25), (−25,+25), (+25,−25), (+25,+25) |
| `back-two` | 2 | −Z: (−25,−25), (+25,+25) |
| `back-center` | 1 | −Z: (0,0) |
| `edge-two` | 2 | Same +X face: (y,z)=(−25,0), (+25,0) |

The quadrants locate sensors; they do not split the tile. All four layouts have
the same gap. Global sensor IDs are `4*layer + local_sensor`; unused IDs are not
physical sensors. The final tile is followed by World, without a tenth gap.
The original `stack-v1` preset still has its original zero-gap, two-sensor behavior.

## Obtain the source

For the review branch:

```sh
git clone --branch codex/steel-layout-study https://github.com/Driver066/g4optics.git g4optics-layout
cd g4optics-layout
```

After this feature is merged upstream, a normal clone of
`https://github.com/jdbrice/g4optics.git` can be used instead. Record the exact
commit you use. The input templates are included here, so generation does not
require a particular Git history, an old checkout, or the author's output files.

## Mac Docker or Linux Docker

Install and start Docker Desktop on Mac, or Docker Engine on Linux. The image
index below selects `linux/arm64` on Apple Silicon and `linux/amd64` on Intel/AMD.
The run is headless; XQuartz and a display server are unnecessary.

Run all commands from the repository root. Use the study recipe below; the
repository's historical top-level Docker recipe is a different environment.

### 1. Build the runtime and install datasets

```sh
docker build -f tools/layout_study/docker/Dockerfile \
  -t g4-layout-study:11.4.2 tools/layout_study

docker volume create g4-layout-data-11.4.2

docker run --rm \
  --mount type=bind,source="$PWD",target=/work \
  --mount type=volume,source=g4-layout-data-11.4.2,target=/g4data \
  g4-layout-study:11.4.2 \
  bash tools/layout_study/docker/install_data.sh install
```

The first setup needs network access for public image layers, Python packages,
and Geant4 datasets. Dataset installation uses the pinned Geant4 tool's archive
checksums, then saves a file-level SHA-256 manifest. A later installation command
verifies that manifest. An incomplete directory without a receipt is rejected;
use a new empty volume to retry, preserving the failed attempt if needed.

### 2. Build, test, run, and audit the four layouts

This step works without network access and mounts the datasets read-only.
It builds only this example, then runs each layout in a separate process.

```sh
docker run --rm --network none \
  --user "$(id -u):$(id -g)" -e HOME=/tmp \
  --mount type=bind,source="$PWD",target=/work \
  --mount type=volume,source=g4-layout-data-11.4.2,target=/g4data,readonly \
  g4-layout-study:11.4.2 bash -c '
    set -e
    bash tools/layout_study/docker/install_data.sh verify
    cmake -S test/OpNovice2 -B outputs/layout-build \
      -DCMAKE_BUILD_TYPE=Release -DWITH_GEANT4_UIVIS=OFF
    cmake --build outputs/layout-build --parallel 2
    python3 -m unittest discover -s tools/layout_study/tests -v
    python3 tools/layout_study/run_local.py \
      --executable outputs/layout-build/OpNovice2 \
      --output-dir outputs/quickstart-t04 \
      --tile-thickness-mm 4 --events 10 --seed1 20261002 --seed2 10401
  '

docker image inspect --format '{{.Id}}' g4-layout-study:11.4.2 \
  > outputs/quickstart-t04/docker-image-id.txt
```

Success means all tests pass and the runner prints `passed` for every requested
layout, followed by `All requested layouts passed`. The root result is
`outputs/quickstart-t04/run-summary.json`, with `"status": "passed"`.
Use a new output name, such as `quickstart-t04-r2`, for a repeat; existing runs
are not overwritten. The installed Geant4 version must be exactly 11.4.2.

## What to inspect and reproduce

Each layout directory contains:

- `run.mac`, `config.json`: the exact inputs and geometry.
- `simulation.log`, `receipt.json`: actual process exit code, elapsed time, and input hashes.
- `result.root`, `result_summary.csv`: event records and aggregate response.
- `geometry-audit.json`, `event-audit.json`: paired checks using the same log hash.

The common directory records runtime versions, source and executable hashes,
Python package versions, seed choices, and checksums of the generated inputs.
A nonzero exit, timeout, or failed audit stops the runner and preserves the failure.
The program's return code is captured directly; the runner never substitutes zero
in order to pass an audit.

The ROOT tables `layout_layers`, `layout_sensors`, and `layout_transfers` retain
per-event, per-layer, per-sensor, and origin-to-destination counts. Active sensors
have rows even when they collect zero photons. The four legacy sensor columns
in `scan` mean global copies 0…3; do not sum them as the whole stack response.
The denominator G retains the historical scintillation-plus-Cerenkov definition,
not a complete photon birth/fate ledger. Zero-denominator events remain invalid/NaN.
The log's NoRINDEX check covers the existing boundary diagnostics, not every
boundary on every optical trajectory.

For another thickness, use `--tile-thickness-mm 8`, `12`, `16`, `20`, or `24` and
a fresh output directory. For one layout, append `--layout back-two`. Keep the
source, seeds, image/data identities and event count when repeating a check.
Cross-architecture runs must pass the same geometry/counting checks; bit-identical
ROOT files or identical event trajectories across architectures are not promised.

For geometry-only initialization, use `prepare.py --events 0`, execute each
`run.mac` in its own layout directory, and run `audit_geometry.py`. The normal
validation runner requires positive event counts so it also exercises optical output.

## OSC / Apptainer

The same source, inputs and audits work on OSC. Use an x86_64 compute allocation
for compiling and running. Replace the account and choose your own writable
runtime directory; no author-specific project or filesystem path is required.

On the login node, from this checkout:

```sh
export LAYOUT_REPO="$PWD"
export LAYOUT_WORK="$HOME/g4-layout-runtime"
export LAYOUT_DATA="$LAYOUT_WORK/data-11.4.2"
export LAYOUT_SIF="$LAYOUT_WORK/geant4-11.4.2-amd64.sif"
mkdir -p "$LAYOUT_WORK" "$LAYOUT_DATA"

apptainer build "$LAYOUT_SIF" \
  docker://carlomt/geant4@sha256:6d7e8dae53bcde0a09b9c47ce863798bafba58a86650de205c64a51abb18f24b

apptainer exec --cleanenv \
  --bind "$LAYOUT_DATA:/g4data" --bind "$LAYOUT_REPO:$LAYOUT_REPO:ro" \
  "$LAYOUT_SIF" bash "$LAYOUT_REPO/tools/layout_study/docker/install_data.sh" install

python3 -m venv "$LAYOUT_WORK/python"
source "$LAYOUT_WORK/python/bin/activate"
python -m pip install -r tools/layout_study/requirements.txt

export OSC_ACCOUNT=your-project-account
salloc --account="$OSC_ACCOUNT" --partition=cpu --nodes=1 --ntasks=1 \
  --cpus-per-task=2 --mem=4G --time=00:30:00
srun --pty bash
```

Use Python 3.9 or 3.10 for the pinned requirements (OSC validation used 3.9.21).
Load an appropriate site Python module before creating the virtual environment
if your default is different. Apptainer must be available in your shell.
The allocation's second CPU is useful for compiling; each simulation is Serial.

Inside the compute-node shell:

```sh
cd "$LAYOUT_REPO"
source "$LAYOUT_WORK/python/bin/activate"

apptainer exec --cleanenv \
  --bind "$LAYOUT_DATA:/g4data:ro" --bind "$LAYOUT_REPO:$LAYOUT_REPO" \
  --pwd "$LAYOUT_REPO" "$LAYOUT_SIF" bash -c '
    set -e
    export GEANT4_DATA_DIR=/g4data
    source /opt/geant4/bin/geant4.sh
    bash tools/layout_study/docker/install_data.sh verify
    cmake -S test/OpNovice2 -B outputs/layout-build-osc \
      -DCMAKE_BUILD_TYPE=Release -DWITH_GEANT4_UIVIS=OFF
    cmake --build outputs/layout-build-osc --parallel 2
  '

python -m unittest discover -s tools/layout_study/tests -v
python tools/layout_study/run_local.py \
  --apptainer-image "$LAYOUT_SIF" --data-dir "$LAYOUT_DATA" \
  --executable outputs/layout-build-osc/OpNovice2 \
  --output-dir outputs/quickstart-osc-t04 \
  --tile-thickness-mm 4 --events 10 --seed1 20261002 --seed2 10401
```

This uses host Python for input preparation and audits, and the pinned container
for the compiler and Geant4 executable. The runtime record includes the SIF hash.
Exit the compute shell and allocation when finished. These examples do not submit
an array or allocate a scientific scan automatically.

## Runtime identities and evidence boundaries

The Docker recipe pins the public `carlomt/geant4:11.4.2-gui` multi-platform index
`sha256:7e660c618ad80506c66e9f509a285e7d9e76dadd75b66f28cbe378ccf91ae3f5`.
Its architecture-specific manifests and the OSC AlmaLinux 9 image are listed in
[docker/images.json](docker/images.json), verified against the
[Docker Hub publisher metadata](https://hub.docker.com/r/carlomt/geant4/tags).
The GUI-capable base is used to match the previously tested Apple Silicon base;
this guide builds and runs the application headlessly.
The [publisher's image source](https://github.com/carlomt/docker-geant4) explains
its `/g4data` mount and external datasets. The
[Geant4 11.4.2 installer](https://github.com/Geant4/geant4/blob/v11.4.2/cmake/Templates/geant4-config.in)
checks archive checksums during download.

Direct Python dependencies are pinned to NumPy 2.0.2 and uproot 5.6.9, the tested
OSC/Python 3.9 pair. A separate local validation used NumPy 2.4.6/uproot 5.7.5
with Python 3.14; that is not the Python 3.9 requirements set. The derived Docker
image adds Python packages to the fixed Geant4 base; record its final image ID
and `python-freeze.txt` because OS package repositories and transitive Python
dependencies can change.

The simulation code has previously passed small engineering samples on Mac
arm64 Docker and Linux x86_64 OSC, including all six thicknesses and four layouts.
Those checks establish geometry and accounting behavior for the sampled inputs.
The documented Docker cold-start route has also passed; see [VALIDATION.md](VALIDATION.md)
and its small reference output. These checks do not prove full optical-history
closure, long-run stability or a preferred layout.


## Fixed first scientific scan

After the small qualification passes, follow [FORMAL_RUN.md](FORMAL_RUN.md).
It builds the exact published PR commit, freezes a 24,000-event manifest, submits
240 single-CPU tasks without a manual concurrency cap, and separates process
checks from final scheduler acceptance and analysis. The prespecified research
questions and uncertainty definitions are in [FIRST_SCAN.md](FIRST_SCAN.md).
