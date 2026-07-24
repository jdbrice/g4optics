#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

IMAGE="${G4_APPTAINER_IMAGE:-${PROJECT_ROOT}/geant4.sif}"
CONTAINER_PROJECT_ROOT="${G4_CONTAINER_PROJECT_ROOT:-/work/g4optics}"
CONTAINER_OPNOVICE2="${CONTAINER_PROJECT_ROOT}/test/OpNovice2"
CONTAINER_G4_DATA_ROOT="${G4_CONTAINER_DATA_ROOT:-/opt/geant4-data}"
CONTAINER_CAMPAIGN_ROOT="${G4_CONTAINER_CAMPAIGN_ROOT:-/work/g4optics-campaign}"
CONTAINER_PREBUILT_ROOT="${G4_CONTAINER_PREBUILT_ROOT:-/work/g4optics-prebuilt}"
BUILD_DIR="${G4_BUILD_DIR:-build-osc}"
BUILD_JOBS="${G4_BUILD_JOBS:-${SLURM_CPUS_PER_TASK:-1}}"
PLOT_WITH_ROOT_VALUE="${PLOT_WITH_ROOT:-0}"
RUN_MANAGER_TYPE="${G4RUN_MANAGER_TYPE:-Serial}"

usage() {
  cat <<'USAGE'
Usage:
  hpc/osc/run_scan_apptainer.sh [run_sipm_cavity_scan.sh args...]

Environment:
  G4_APPTAINER_IMAGE=/path/to/geant4.sif
  G4_DATA_ROOT=/path/to/geant4-data
  G4_PREBUILT_EXECUTABLE=/path/to/frozen/OpNovice2
  G4_BUILD_DIR=build-osc
  G4_BUILD_JOBS=4
  G4_FORCE_REBUILD=0
  G4_PRINT_DATA_ENV=0
  G4RUN_MANAGER_TYPE=Serial
  PLOT_WITH_ROOT=0

Formal neutron-study tasks additionally set RN_CAMPAIGN_HOST_DIR and the RN_*
identity variables through their campaign submission wrapper. They use the
frozen prebuilt executable and isolated per-attempt task output directories.

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

APPTAINER_BIND_ARGS=(--bind "${PROJECT_ROOT}:${CONTAINER_PROJECT_ROOT}")
if [[ -n "${G4_DATA_ROOT:-}" ]]; then
  if [[ ! -d "${G4_DATA_ROOT}" ]]; then
    echo "Missing Geant4 data root: ${G4_DATA_ROOT}" >&2
    exit 1
  fi
  APPTAINER_BIND_ARGS+=(--bind "${G4_DATA_ROOT}:${CONTAINER_G4_DATA_ROOT}:ro")
fi

CONTAINER_CAMPAIGN_DIR=""
if [[ -n "${RN_CAMPAIGN_HOST_DIR:-}" ]]; then
  if [[ ! -d "${RN_CAMPAIGN_HOST_DIR}" ]]; then
    echo "Missing realistic-neutron campaign directory: ${RN_CAMPAIGN_HOST_DIR}" >&2
    exit 1
  fi
  for required_name in \
    RN_ATTEMPT_ID RN_LOGICAL_TASK_ID RN_LOGICAL_TASK_INDEX RN_PLAN_HASH \
    RN_GIT_COMMIT RN_ENVIRONMENT_MODE RN_ENVIRONMENT_IDENTITY \
    RN_IMAGE_SHA256 RN_G4_DATA_MANIFEST_SHA256 RN_EXECUTABLE_SHA256 \
    RN_TASK_RESULT_RECORDER; do
    if [[ -z "${!required_name:-}" ]]; then
      echo "Missing formal campaign environment variable: ${required_name}" >&2
      exit 1
    fi
  done
  campaign_host_dir="$(cd "${RN_CAMPAIGN_HOST_DIR}" && pwd -P)"
  case "${RN_TASK_RESULT_RECORDER}" in
    record_realistic_neutron_task_result.py|record_steel_module_task_result.py|record_steel_module_stack_task_result.py)
      ;;
    *)
      echo "Unsupported formal task-result recorder: ${RN_TASK_RESULT_RECORDER}" >&2
      exit 1
      ;;
  esac
  APPTAINER_BIND_ARGS+=(--bind "${campaign_host_dir}:${CONTAINER_CAMPAIGN_ROOT}")
  CONTAINER_CAMPAIGN_DIR="${CONTAINER_CAMPAIGN_ROOT}"
fi

CONTAINER_PREBUILT_EXECUTABLE=""
if [[ -n "${G4_PREBUILT_EXECUTABLE:-}" ]]; then
  if [[ ! -f "${G4_PREBUILT_EXECUTABLE}" || ! -x "${G4_PREBUILT_EXECUTABLE}" ]]; then
    echo "Missing executable frozen OpNovice2 artifact: ${G4_PREBUILT_EXECUTABLE}" >&2
    exit 1
  fi
  prebuilt_host_dir="$(cd "$(dirname "${G4_PREBUILT_EXECUTABLE}")" && pwd -P)"
  prebuilt_name="$(basename "${G4_PREBUILT_EXECUTABLE}")"
  APPTAINER_BIND_ARGS+=(--bind "${prebuilt_host_dir}:${CONTAINER_PREBUILT_ROOT}:ro")
  CONTAINER_PREBUILT_EXECUTABLE="${CONTAINER_PREBUILT_ROOT}/${prebuilt_name}"
fi
if [[ -n "${CONTAINER_CAMPAIGN_DIR}" && -z "${CONTAINER_PREBUILT_EXECUTABLE}" ]]; then
  echo "Formal realistic-neutron tasks require G4_PREBUILT_EXECUTABLE." >&2
  exit 1
fi

if [[ $# -eq 0 ]]; then
  set -- full custom --events "${N_EVENTS:-1}" --source-mode gps \
    --tank-size "100 100 5 mm" \
    --x-min 0 --x-max 0 --y-min 0 --y-max 0 --step 5 --grid-unit mm
fi

apptainer exec \
  "${APPTAINER_BIND_ARGS[@]}" \
  "${IMAGE}" \
  bash -lc '
    set -euo pipefail
    opnovice_dir="$1"
    build_dir="$2"
    build_jobs="$3"
    plot_with_root="$4"
    run_manager_type="$5"
    campaign_dir="$6"
    prebuilt_executable="$7"
    shift 7

    cd "${opnovice_dir}"
    if [[ -f /opt/geant4/bin/geant4.sh ]]; then
      # shellcheck source=/dev/null
      source /opt/geant4/bin/geant4.sh
    fi

    # shellcheck source=/dev/null
    source "${opnovice_dir}/../../hpc/osc/configure_geant4_data_env.sh"
    configure_geant4_data_env "${G4_PRINT_DATA_ENV:-0}"

    if [[ -n "${prebuilt_executable}" ]]; then
      if [[ ! -x "${prebuilt_executable}" ]]; then
        echo "Frozen OpNovice2 executable is unavailable inside Apptainer: ${prebuilt_executable}" >&2
        exit 1
      fi
      executable="${prebuilt_executable}"
    else
      if [[ "${G4_FORCE_REBUILD:-0}" == "1" || ! -x "${build_dir}/OpNovice2" ]]; then
        cmake -S . -B "${build_dir}" -DWITH_GEANT4_UIVIS=OFF
        cmake --build "${build_dir}" -j"${build_jobs}"
      fi
      executable="./${build_dir}/OpNovice2"
    fi

    if [[ -n "${run_manager_type}" ]]; then
      export G4RUN_MANAGER_TYPE="${run_manager_type}"
    fi

    task_root=""
    if [[ -n "${campaign_dir}" ]]; then
      task_root="${campaign_dir}/attempts/${RN_ATTEMPT_ID}/tasks/${RN_LOGICAL_TASK_ID}"
      if [[ -e "${task_root}/task_result.json" ]]; then
        echo "Refusing to overwrite completed task result: ${task_root}/task_result.json" >&2
        exit 1
      fi
      if [[ -d "${task_root}/runs" ]] && find "${task_root}/runs" -mindepth 1 -print -quit | grep -q .; then
        echo "Refusing to reuse a non-empty formal task directory: ${task_root}/runs" >&2
        exit 1
      fi
      mkdir -p "${task_root}/runs"
      export SCAN_RUNS_DIR="${task_root}/runs"
      export LATEST_RUN_LINK="${task_root}/latest"
      export LATEST_POINTS_CSV="${task_root}/points.csv"
      export LATEST_RUN_CONFIG="${task_root}/run_config.json"
      export LATEST_EFFICIENCY_MAP="${task_root}/efficiency_map.csv"
      export SCAN_GIT_COMMIT="${RN_GIT_COMMIT}"
      export SCAN_GIT_BRANCH="detached-frozen-campaign"
      export SCAN_GIT_DIRTY="false"
    fi

    OPNOVICE2_EXECUTABLE="${executable}" \
      PLOT_WITH_ROOT="${plot_with_root}" \
      ./run_sipm_cavity_scan.sh "$@"

    if [[ -n "${task_root}" ]]; then
      python3 "${opnovice_dir}/../../hpc/osc/${RN_TASK_RESULT_RECORDER}" \
        --campaign-dir "${campaign_dir}" \
        --attempt-id "${RN_ATTEMPT_ID}" \
        --logical-task-id "${RN_LOGICAL_TASK_ID}" \
        --task-root "${task_root}"
    fi
  ' bash "${CONTAINER_OPNOVICE2}" "${BUILD_DIR}" "${BUILD_JOBS}" \
    "${PLOT_WITH_ROOT_VALUE}" "${RUN_MANAGER_TYPE}" \
    "${CONTAINER_CAMPAIGN_DIR}" "${CONTAINER_PREBUILT_EXECUTABLE}" "$@"
