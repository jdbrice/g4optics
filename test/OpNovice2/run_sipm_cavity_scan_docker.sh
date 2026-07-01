#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

CONTAINER="${G4_DOCKER_CONTAINER:-g4dev}"
CONTAINER_WORKDIR="${G4_DOCKER_WORKDIR:-/work/g4optics/test/OpNovice2}"
CONTAINER_BUILD_DIR="${G4_DOCKER_BUILD_DIR:-build}"
CONTAINER_BUILD_JOBS="${G4_DOCKER_BUILD_JOBS:-4}"
FORCE_REBUILD_VALUE="${G4_FORCE_REBUILD:-0}"

N_EVENTS_VALUE="${N_EVENTS:-100}"
DRY_RUN_VALUE="${DRY_RUN:-0}"
SOURCE_MODE_VALUE="${SOURCE_MODE:-auto}"
PLOT_WITH_ROOT_VALUE="${PLOT_WITH_ROOT:-1}"
ROOT_COMMAND_VALUE="${ROOT_COMMAND:-root}"
ROOT_PLOT_FIDUCIAL_LIMIT_MM_VALUE="${ROOT_PLOT_FIDUCIAL_LIMIT_MM:-45}"
USER_ARGS=("$@")

HOST_GIT_COMMIT="$(git rev-parse HEAD 2>/dev/null || echo "unknown")"
HOST_GIT_BRANCH="$(git branch --show-current 2>/dev/null || echo "unknown")"
if [[ -z "${HOST_GIT_BRANCH}" ]]; then
  HOST_GIT_BRANCH="detached"
fi
if [[ -n "$(git status --porcelain 2>/dev/null || true)" ]]; then
  HOST_GIT_DIRTY="true"
else
  HOST_GIT_DIRTY="false"
fi

usage() {
  cat <<'USAGE'
Usage:
  ./run_sipm_cavity_scan_docker.sh [run_sipm_cavity_scan.sh args...]

This host-side wrapper runs the Geant4 scan inside the g4dev Docker container,
then generates ROOT macro plots on the host from scan_latest/efficiency_map.csv.
All scan options are passed through, including --surface-preset and --dimple.

Environment:
  G4_DOCKER_CONTAINER=g4dev
  G4_DOCKER_WORKDIR=/work/g4optics/test/OpNovice2
  G4_DOCKER_BUILD_DIR=build
  G4_DOCKER_BUILD_JOBS=4
  G4_FORCE_REBUILD=0
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

i=0
while [[ "${i}" -lt "${#USER_ARGS[@]}" ]]; do
  arg="${USER_ARGS[${i}]}"
  case "${arg}" in
    --events)
      i=$((i + 1))
      if [[ "${i}" -ge "${#USER_ARGS[@]}" ]]; then
        echo "Missing value for --events" >&2
        exit 1
      fi
      N_EVENTS_VALUE="${USER_ARGS[${i}]}"
      ;;
    --events=*)
      N_EVENTS_VALUE="${arg#*=}"
      ;;
    --dry-run)
      DRY_RUN_VALUE="1"
      ;;
    --no-root-plots)
      PLOT_WITH_ROOT_VALUE="0"
      ;;
    --root-command)
      i=$((i + 1))
      if [[ "${i}" -ge "${#USER_ARGS[@]}" ]]; then
        echo "Missing value for --root-command" >&2
        exit 1
      fi
      ROOT_COMMAND_VALUE="${USER_ARGS[${i}]}"
      ;;
    --root-command=*)
      ROOT_COMMAND_VALUE="${arg#*=}"
      ;;
  esac
  i=$((i + 1))
done

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

case "${FORCE_REBUILD_VALUE}" in
  1|true|TRUE|yes|YES|on|ON)
    FORCE_REBUILD_VALUE="1"
    ;;
  0|false|FALSE|no|NO|off|OFF)
    FORCE_REBUILD_VALUE="0"
    ;;
  *)
    echo "Invalid G4_FORCE_REBUILD: ${FORCE_REBUILD_VALUE}. Use 1 or 0." >&2
    exit 1
    ;;
esac

docker exec \
  "${CONTAINER}" \
  bash -lc '
    set -euo pipefail
    cd "$1"
    build_dir="$2"
    build_jobs="$3"
    force_rebuild="$4"
    if [[ "${force_rebuild}" == "1" || ! -x "${build_dir}/OpNovice2" ]]; then
      cmake -S . -B "${build_dir}"
      cmake --build "${build_dir}" -j"${build_jobs}"
    fi
  ' \
  bash "${CONTAINER_WORKDIR}" "${CONTAINER_BUILD_DIR}" "${CONTAINER_BUILD_JOBS}" "${FORCE_REBUILD_VALUE}"

docker exec \
  -e "N_EVENTS=${N_EVENTS_VALUE}" \
  -e "DRY_RUN=${DRY_RUN_VALUE}" \
  -e "SOURCE_MODE=${SOURCE_MODE_VALUE}" \
  -e "OPNOVICE2_EXECUTABLE=./${CONTAINER_BUILD_DIR}/OpNovice2" \
  -e "PLOT_WITH_ROOT=${PLOT_WITH_ROOT_VALUE}" \
  -e "ROOT_COMMAND=${ROOT_COMMAND_VALUE}" \
  -e "ROOT_PLOT_SKIP_LOCAL=1" \
  -e "ROOT_PLOT_FIDUCIAL_LIMIT_MM=${ROOT_PLOT_FIDUCIAL_LIMIT_MM_VALUE}" \
  -e "SCAN_COMMAND_SCRIPT=./run_sipm_cavity_scan_docker.sh" \
  -e "SCAN_GIT_COMMIT=${HOST_GIT_COMMIT}" \
  -e "SCAN_GIT_BRANCH=${HOST_GIT_BRANCH}" \
  -e "SCAN_GIT_DIRTY=${HOST_GIT_DIRTY}" \
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
