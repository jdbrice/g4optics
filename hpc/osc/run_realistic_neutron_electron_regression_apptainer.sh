#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
IMAGE="${G4_APPTAINER_IMAGE:-${PROJECT_ROOT}/geant4.sif}"
CONTAINER_PROJECT_ROOT="${G4_CONTAINER_PROJECT_ROOT:-/work/g4optics}"
CONTAINER_G4_DATA_ROOT="${G4_CONTAINER_DATA_ROOT:-/opt/geant4-data}"

usage() {
  cat <<'USAGE'
Usage:
  hpc/osc/run_realistic_neutron_electron_regression_apptainer.sh \
    --baseline-executable /host/path/to/practice/OpNovice2 \
    --candidate-executable /host/path/to/candidate/OpNovice2 \
    --output-dir /host/path/to/electron-regression [other regression options]

Environment:
  G4_APPTAINER_IMAGE=/path/to/geant4.sif
  G4_DATA_ROOT=/path/to/geant4-data
  G4_PRINT_DATA_ENV=0

Executable and output paths should be under a host directory visible inside
Apptainer, such as $HOME. The wrapper configures the same Geant4 dataset
environment used by run_scan_apptainer.sh.
USAGE
}

case "${1:-}" in
  -h|--help|help|"")
    usage
    [[ -n "${1:-}" ]] && exit 0
    exit 2
    ;;
esac

if ! command -v apptainer >/dev/null 2>&1; then
  echo "Missing command: apptainer" >&2
  exit 1
fi
if [[ ! -f "${IMAGE}" ]]; then
  echo "Missing Apptainer image: ${IMAGE}" >&2
  exit 1
fi

APPTAINER_BIND_ARGS=(--bind "${PROJECT_ROOT}:${CONTAINER_PROJECT_ROOT}:ro")
if [[ -n "${G4_DATA_ROOT:-}" ]]; then
  if [[ ! -d "${G4_DATA_ROOT}" ]]; then
    echo "Missing Geant4 data root: ${G4_DATA_ROOT}" >&2
    exit 1
  fi
  APPTAINER_BIND_ARGS+=(--bind "${G4_DATA_ROOT}:${CONTAINER_G4_DATA_ROOT}:ro")
fi

apptainer exec \
  "${APPTAINER_BIND_ARGS[@]}" \
  "${IMAGE}" \
  bash -lc '
    set -euo pipefail
    project_root="$1"
    shift
    if [[ -f /opt/geant4/bin/geant4.sh ]]; then
      # shellcheck source=/dev/null
      source /opt/geant4/bin/geant4.sh
    fi
    # shellcheck source=/dev/null
    source "${project_root}/hpc/osc/configure_geant4_data_env.sh"
    configure_geant4_data_env "${G4_PRINT_DATA_ENV:-0}"
    export G4RUN_MANAGER_TYPE=Serial
    cd "${project_root}"
    python3 hpc/osc/run_realistic_neutron_electron_regression.py "$@"
  ' bash "${CONTAINER_PROJECT_ROOT}" "$@"
