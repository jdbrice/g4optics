#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

CONTAINER="${G4_DOCKER_CONTAINER:-g4dev}"
CONTAINER_WORKDIR="${G4_DOCKER_WORKDIR:-/work/g4optics/test/OpNovice2}"

N_EVENTS_VALUE="${N_EVENTS:-100}"
DRY_RUN_VALUE="${DRY_RUN:-0}"
SOURCE_MODE_VALUE="${SOURCE_MODE:-auto}"
PLOT_WITH_ROOT_VALUE="${PLOT_WITH_ROOT:-1}"
ROOT_COMMAND_VALUE="${ROOT_COMMAND:-root}"
ROOT_PLOT_FIDUCIAL_LIMIT_MM_VALUE="${ROOT_PLOT_FIDUCIAL_LIMIT_MM:-45}"

usage() {
  cat <<'USAGE'
Usage:
  ./run_sipm_cavity_scan_docker.sh [run_sipm_cavity_scan.sh args...]

This host-side wrapper runs the Geant4 scan inside the g4dev Docker container,
then generates ROOT macro plots on the host from scan_latest/efficiency_map.csv.

Environment:
  G4_DOCKER_CONTAINER=g4dev
  G4_DOCKER_WORKDIR=/work/g4optics/test/OpNovice2
  N_EVENTS=100
  DRY_RUN=0
  SOURCE_MODE=auto
  PLOT_WITH_ROOT=1
  ROOT_COMMAND=root
  ROOT_PLOT_FIDUCIAL_LIMIT_MM=45
USAGE
}

case "${1:-}" in
  -h|--help|help)
    usage
    exit 0
    ;;
esac

case "${PLOT_WITH_ROOT_VALUE}" in
  1|true|TRUE|yes|YES|on|ON)
    PLOT_WITH_ROOT_VALUE="1"
    ;;
  0|false|FALSE|no|NO|off|OFF)
    PLOT_WITH_ROOT_VALUE="0"
    ;;
  *)
    echo "Invalid PLOT_WITH_ROOT: ${PLOT_WITH_ROOT_VALUE}. Use 1 or 0." >&2
    exit 1
    ;;
esac

docker exec \
  -e "N_EVENTS=${N_EVENTS_VALUE}" \
  -e "DRY_RUN=${DRY_RUN_VALUE}" \
  -e "SOURCE_MODE=${SOURCE_MODE_VALUE}" \
  -e "PLOT_WITH_ROOT=${PLOT_WITH_ROOT_VALUE}" \
  -e "ROOT_PLOT_SKIP_LOCAL=1" \
  -e "ROOT_PLOT_FIDUCIAL_LIMIT_MM=${ROOT_PLOT_FIDUCIAL_LIMIT_MM_VALUE}" \
  "${CONTAINER}" \
  bash -lc 'cd "$1" && shift && ./run_sipm_cavity_scan.sh "$@"' \
  bash "${CONTAINER_WORKDIR}" "$@"

if [[ "${DRY_RUN_VALUE}" == "1" || "${PLOT_WITH_ROOT_VALUE}" != "1" ]]; then
  exit 0
fi

if ! command -v "${ROOT_COMMAND_VALUE}" >/dev/null 2>&1; then
  echo "Host ROOT plot generation skipped: ROOT command not found: ${ROOT_COMMAND_VALUE}" >&2
  echo "Set ROOT_COMMAND=/path/to/root or PLOT_WITH_ROOT=0." >&2
  exit 0
fi

if [[ ! -f "plot_efficiency_map.C" ]]; then
  echo "Host ROOT plot generation skipped: missing plot_efficiency_map.C" >&2
  exit 0
fi

echo "Generating host ROOT plots from scan_latest"
"${ROOT_COMMAND_VALUE}" -b -q "plot_efficiency_map.C(\"scan_latest\",${ROOT_PLOT_FIDUCIAL_LIMIT_MM_VALUE})"
echo "Host ROOT plots: scan_latest/root_*.png and scan_latest/root_*.pdf"
