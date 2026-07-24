#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PLAN_DIR="${PROJECT_ROOT}/hpc/osc/generated/lab_v2_realsetup/surface_comparison_75mrad_5000events"
MANIFEST="${PLAN_DIR}/groundback-recovery-manifest.tsv"
EXPECTED_PLANS=4
EXPECTED_TASKS=86
MAX_ACTIVE_TASKS=1000

usage() {
  echo "Usage: $0 ACCOUNT G4_DATA_ROOT [--check-only]" >&2
}

ACCOUNT="${1:-}"
G4_DATA_ROOT_VALUE="${2:-}"
MODE="${3:-}"
if [[ -z "${ACCOUNT}" || -z "${G4_DATA_ROOT_VALUE}" || $# -gt 3 ]]; then
  usage
  exit 2
fi
if [[ -n "${MODE}" && "${MODE}" != "--check-only" ]]; then
  usage
  exit 2
fi
if [[ ! -d "${G4_DATA_ROOT_VALUE}" ]]; then
  echo "Missing Geant4 dataset root: ${G4_DATA_ROOT_VALUE}" >&2
  exit 1
fi

plans=()
while IFS= read -r plan; do
  plans+=("${plan}")
done < <(find "${PLAN_DIR}" -maxdepth 1 -type f -name '*_groundbackpainted_*.txt' | sort)
if [[ "${#plans[@]}" -ne "${EXPECTED_PLANS}" ]]; then
  echo "Expected ${EXPECTED_PLANS} groundback plans, found ${#plans[@]}." >&2
  exit 1
fi

count_plan_tasks() {
  awk '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    { count += 1 }
    END { print count + 0 }
  ' "$1"
}

total_tasks=0
for plan in "${plans[@]}"; do
  total_tasks=$((total_tasks + $(count_plan_tasks "${plan}")))
done
if [[ "${total_tasks}" -ne "${EXPECTED_TASKS}" ]]; then
  echo "Expected ${EXPECTED_TASKS} recovery tasks, found ${total_tasks}." >&2
  exit 1
fi

if [[ "${MODE}" == "--check-only" ]]; then
  echo "Validated ${EXPECTED_PLANS} groundback plans with ${EXPECTED_TASKS} tasks."
  echo "Recovery walltime: 02:00:00"
  exit 0
fi
if [[ -s "${MANIFEST}" ]]; then
  echo "Recovery manifest already exists: ${MANIFEST}" >&2
  exit 1
fi

active_tasks="$(squeue -h -r -u "${USER}" | wc -l | tr -d '[:space:]')"
if (( active_tasks + total_tasks > MAX_ACTIVE_TASKS )); then
  echo "Refusing submission: ${active_tasks} active tasks + ${total_tasks} recovery tasks exceeds ${MAX_ACTIVE_TASKS}." >&2
  exit 1
fi

printf 'job_id\ttask_count\tplan\tsubmitted_utc\tgit_commit\n' > "${MANIFEST}"
git_commit="$(git -C "${PROJECT_ROOT}" rev-parse HEAD)"
for plan in "${plans[@]}"; do
  relative_plan="${plan#${PROJECT_ROOT}/}"
  task_count="$(count_plan_tasks "${plan}")"
  job_id="$(
    cd "${PROJECT_ROOT}"
    sbatch --parsable \
      -A "${ACCOUNT}" \
      --time=02:00:00 \
      --array="1-${task_count}" \
      --export="ALL,SCAN_ARGS_FILE=${relative_plan},G4_DATA_ROOT=${G4_DATA_ROOT_VALUE}" \
      hpc/osc/submit_scan.sbatch
  )"
  job_id="${job_id%%;*}"
  if [[ ! "${job_id}" =~ ^[0-9]+$ ]]; then
    echo "Unexpected recovery job id for ${relative_plan}: ${job_id}" >&2
    exit 1
  fi
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "${job_id}" "${task_count}" "${relative_plan}" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${git_commit}" >> "${MANIFEST}"
  echo "Submitted recovery ${job_id}: ${task_count} tasks, ${relative_plan}"
done

echo "Submitted ${EXPECTED_PLANS} recovery arrays with ${EXPECTED_TASKS} tasks."
echo "Manifest: ${MANIFEST}"
