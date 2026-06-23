#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

IMAGE="${G4_APPTAINER_IMAGE:-${PROJECT_ROOT}/geant4.sif}"
CONTAINER_PROJECT_ROOT="${G4_CONTAINER_PROJECT_ROOT:-/work/g4optics}"
CONTAINER_OPNOVICE2="${CONTAINER_PROJECT_ROOT}/test/OpNovice2"
BUILD_DIR="${G4_BUILD_DIR:-build-osc}"
BUILD_JOBS="${G4_BUILD_JOBS:-${SLURM_CPUS_PER_TASK:-1}}"
PLOT_WITH_ROOT_VALUE="${PLOT_WITH_ROOT:-0}"

usage() {
  cat <<'USAGE'
Usage:
  hpc/osc/run_scan_apptainer.sh [run_sipm_cavity_scan.sh args...]

Environment:
  G4_APPTAINER_IMAGE=/path/to/geant4.sif
  G4_BUILD_DIR=build-osc
  G4_BUILD_JOBS=4
  G4_FORCE_REBUILD=0
  PLOT_WITH_ROOT=0

If no scan args are supplied, a 1-event 1-point GPS smoke scan is run.
USAGE
}

case "${1:-}" in
  -h|--help|help)
    usage
    exit 0
    ;;
esac

if ! command -v apptainer >/dev/null 2>&1; then
  echo "Missing command: apptainer" >&2
  exit 1
fi

if [[ ! -f "${IMAGE}" ]]; then
  echo "Missing Apptainer image: ${IMAGE}" >&2
  echo "Build it on OSC with: apptainer build geant4.sif docker://carlomt/geant4:11.4.2-almalinux9" >&2
  exit 1
fi

if [[ $# -eq 0 ]]; then
  set -- full custom --events "${N_EVENTS:-1}" --source-mode gps \
    --tank-size "100 100 5 mm" \
    --x-min 0 --x-max 0 --y-min 0 --y-max 0 --step 5 --grid-unit mm
fi

apptainer exec \
  --bind "${PROJECT_ROOT}:${CONTAINER_PROJECT_ROOT}" \
  "${IMAGE}" \
  bash -lc '
    set -euo pipefail
    opnovice_dir="$1"
    build_dir="$2"
    build_jobs="$3"
    plot_with_root="$4"
    shift 4

    cd "${opnovice_dir}"
    if [[ -f /opt/geant4/bin/geant4.sh ]]; then
      # shellcheck source=/dev/null
      source /opt/geant4/bin/geant4.sh
    fi

    if [[ "${G4_FORCE_REBUILD:-0}" == "1" || ! -x "${build_dir}/OpNovice2" ]]; then
      cmake -S . -B "${build_dir}" -DWITH_GEANT4_UIVIS=OFF
      cmake --build "${build_dir}" -j"${build_jobs}"
    fi

    OPNOVICE2_EXECUTABLE="./${build_dir}/OpNovice2" \
    PLOT_WITH_ROOT="${plot_with_root}" \
    ./run_sipm_cavity_scan.sh "$@"
  ' bash "${CONTAINER_OPNOVICE2}" "${BUILD_DIR}" "${BUILD_JOBS}" "${PLOT_WITH_ROOT_VALUE}" "$@"
