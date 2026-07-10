#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PLAN_DIR="${PROJECT_ROOT}/hpc/osc/generated/lab_v2_realsetup/divergence_calibration"
MANIFEST="${PLAN_DIR}/submission-manifest.tsv"
EXPECTED_PLANS=24
EXPECTED_TASKS=516
MAX_ACTIVE_TASKS="${MAX_ACTIVE_TASKS:-1000}"

usage() {
  echo "Usage: $0 ACCOUNT G4_DATA_ROOT [--resume|--retry-all]" >&2
}

ACCOUNT="${1:-}"
G4_DATA_ROOT_VALUE="${2:-}"
SUBMIT_MODE="${3:-}"
if [[ -z "${ACCOUNT}" || -z "${G4_DATA_ROOT_VALUE}" || $# -gt 3 ]]; then
  usage
  exit 2
fi
case "${SUBMIT_MODE}" in
  ""|--resume|--retry-all)
    ;;
  *)
    usage
    exit 2
    ;;
esac
if [[ ! -d "${G4_DATA_ROOT_VALUE}" ]]; then
  echo "Missing Geant4 dataset root: ${G4_DATA_ROOT_VALUE}" >&2
  usage
  exit 1
fi

if [[ ! -d "${PLAN_DIR}" ]]; then
  echo "Missing plan directory: ${PLAN_DIR}" >&2
  exit 1
fi

mapfile -t plans < <(find "${PLAN_DIR}" -maxdepth 1 -type f -name '*.txt' | sort)
if [[ "${#plans[@]}" -ne "${EXPECTED_PLANS}" ]]; then
  echo "Expected ${EXPECTED_PLANS} plans, found ${#plans[@]} in ${PLAN_DIR}." >&2
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
  echo "Expected ${EXPECTED_TASKS} tasks, found ${total_tasks}." >&2
  exit 1
fi

if [[ -s "${MANIFEST}" && "${SUBMIT_MODE}" != "--resume" && "${SUBMIT_MODE}" != "--retry-all" ]]; then
  echo "Submission manifest already exists: ${MANIFEST}" >&2
  echo "Inspect it first, then pass --resume for missing plans or --retry-all after a failed attempt." >&2
  exit 1
fi
if [[ ! -e "${MANIFEST}" ]]; then
  printf 'job_id\ttask_count\tplan\tsubmitted_utc\tgit_commit\n' > "${MANIFEST}"
fi

is_submitted() {
  local relative_plan="$1"
  awk -F '\t' -v plan="${relative_plan}" '
    NR > 1 && $3 == plan { found = 1 }
    END { exit found ? 0 : 1 }
  ' "${MANIFEST}"
}

remaining_tasks=0
remaining_plans=0
for plan in "${plans[@]}"; do
  relative_plan="${plan#${PROJECT_ROOT}/}"
  if [[ "${SUBMIT_MODE}" != "--retry-all" ]] && is_submitted "${relative_plan}"; then
    continue
  fi
  remaining_tasks=$((remaining_tasks + $(count_plan_tasks "${plan}")))
  remaining_plans=$((remaining_plans + 1))
done

if [[ "${remaining_plans}" -eq 0 ]]; then
  echo "All ${EXPECTED_PLANS} plans are already recorded in ${MANIFEST}."
  exit 0
fi

active_tasks="$(squeue -h -r -u "${USER}" | wc -l | tr -d '[:space:]')"
if (( active_tasks + remaining_tasks > MAX_ACTIVE_TASKS )); then
  echo "Refusing submission: ${active_tasks} active tasks + ${remaining_tasks} new tasks exceeds MAX_ACTIVE_TASKS=${MAX_ACTIVE_TASKS}." >&2
  exit 1
fi

git_commit="$(git -C "${PROJECT_ROOT}" rev-parse HEAD)"
submitted_plans=0
submitted_tasks=0
for plan in "${plans[@]}"; do
  relative_plan="${plan#${PROJECT_ROOT}/}"
  if [[ "${SUBMIT_MODE}" != "--retry-all" ]] && is_submitted "${relative_plan}"; then
    echo "Skipping recorded plan: ${relative_plan}"
    continue
  fi

  task_count="$(count_plan_tasks "${plan}")"
  job_id="$(
    cd "${PROJECT_ROOT}"
    sbatch --parsable \
      -A "${ACCOUNT}" \
      --array="1-${task_count}" \
      --export="ALL,SCAN_ARGS_FILE=${relative_plan},G4_DATA_ROOT=${G4_DATA_ROOT_VALUE}" \
      hpc/osc/submit_scan.sbatch
  )"
  job_id="${job_id%%;*}"
  if [[ ! "${job_id}" =~ ^[0-9]+$ ]]; then
    echo "Unexpected sbatch job id for ${relative_plan}: ${job_id}" >&2
    exit 1
  fi

  printf '%s\t%s\t%s\t%s\t%s\n' \
    "${job_id}" \
    "${task_count}" \
    "${relative_plan}" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "${git_commit}" >> "${MANIFEST}"
  echo "Submitted ${job_id}: ${task_count} tasks, ${relative_plan}"
  submitted_plans=$((submitted_plans + 1))
  submitted_tasks=$((submitted_tasks + task_count))
done

echo "Submitted ${submitted_plans} arrays with ${submitted_tasks} tasks."
echo "Geant4 datasets: ${G4_DATA_ROOT_VALUE}"
echo "Manifest: ${MANIFEST}"
