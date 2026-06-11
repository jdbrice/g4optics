#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

N_EVENTS="${N_EVENTS:-100}"
DRY_RUN="${DRY_RUN:-0}"
TEMPLATE_MACRO="ej200_sipm_test.mac"
SCAN_NAME="5x5 near-field bottom-center scan"
BEAM_DIRECTION="0 0 -1"
SCAN_RUNS_DIR="scan_runs"
LATEST_RUN_LINK="scan_latest"
LATEST_RUN_CONFIG="run_config.json"

# Near-field scan around bottom-center SiPM.
# Grid spacing follows the larger active SiPM edge in /opnovice2/sipm/size.

if [[ "${DRY_RUN}" != "1" && ! -x "./build/OpNovice2" ]]; then
  echo "Missing executable: ./build/OpNovice2" >&2
  echo "Build first with: cmake -S . -B build && cmake --build build -j\$(nproc)" >&2
  exit 1
fi

format_num() {
  local v="$1"
  awk -v v="${v}" 'BEGIN {
    if (v == int(v)) {
      printf "%d", v
    }
    else {
      s = sprintf("%.6f", v)
      sub(/0+$/, "", s)
      sub(/\.$/, "", s)
      printf "%s", s
    }
  }'
}

label_num() {
  local formatted label
  formatted="$(format_num "$1")"
  if [[ "${formatted}" == -* ]]; then
    label="m${formatted#-}"
  else
    label="p${formatted}"
  fi
  echo "${label//./p}"
}

json_number_array() {
  local first=1
  local v
  printf "["
  for v in "$@"; do
    if [[ "${first}" -eq 0 ]]; then
      printf ", "
    fi
    printf "%s" "$(format_num "${v}")"
    first=0
  done
  printf "]"
}

macro_value_after() {
  local command="$1"
  awk -v cmd="${command}" '$1 == cmd { $1=""; sub(/^ /, ""); print; exit }' "${TEMPLATE_MACRO}"
}

macro_box_const() {
  local property="$1"
  awk -v prop="${property}" \
    '$1 == "/opnovice2/boxConstProperty" && $2 == prop { print $3; exit }' \
    "${TEMPLATE_MACRO}"
}

sipm_size_for_grid=$(macro_value_after "/opnovice2/sipm/size")
read -r sipm_active_u sipm_active_v _ sipm_unit _ <<< "${sipm_size_for_grid}"

if [[ -z "${sipm_active_u:-}" || -z "${sipm_active_v:-}" || -z "${sipm_unit:-}" ]]; then
  echo "Could not parse /opnovice2/sipm/size from ${TEMPLATE_MACRO}: ${sipm_size_for_grid}" >&2
  exit 1
fi

GRID_UNIT="${sipm_unit}"
GRID_STEP="$(awk -v u="${sipm_active_u}" -v v="${sipm_active_v}" 'BEGIN { print (u > v ? u : v) }')"
read -r -a XS <<< "$(awk -v s="${GRID_STEP}" 'BEGIN { printf "%.10g %.10g 0 %.10g %.10g", -2*s, -s, s, 2*s }')"
read -r -a YS <<< "$(awk -v s="${GRID_STEP}" 'BEGIN { printf "%.10g %.10g 0 %.10g %.10g", -2*s, -s, s, 2*s }')"
if [[ "${GRID_UNIT}" == "cm" ]]; then
  Z0="0.4"
else
  Z0="4"
fi

PREFIX="sipm_near5"
RUN_TIMESTAMP="$(date -u +"%Y%m%d_%H%M%SZ")"
RUN_ID="${RUN_TIMESTAMP}_${PREFIX}"
RUN_DIR="${SCAN_RUNS_DIR}/${RUN_ID}"
if [[ -e "${RUN_DIR}" ]]; then
  RUN_ID="${RUN_ID}_$$"
  RUN_DIR="${SCAN_RUNS_DIR}/${RUN_ID}"
fi
MACRO_DIR="${RUN_DIR}/macros"
ROOT_DIR="${RUN_DIR}/outputs"
LOG_DIR="${RUN_DIR}/logs"
RUN_CONFIG="${RUN_DIR}/run_config.json"

mkdir -p "${MACRO_DIR}" "${ROOT_DIR}" "${LOG_DIR}"

write_run_config() {
  local config="$1"
  local generated_at git_commit git_branch git_dirty
  local scint_yield primary_particle primary_energy sipm_face sipm_local sipm_size

  generated_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  git_commit=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
  git_branch=$(git branch --show-current 2>/dev/null || echo "unknown")
  if [[ -z "${git_branch}" ]]; then
    git_branch="detached"
  fi
  if [[ -n "$(git status --porcelain 2>/dev/null || true)" ]]; then
    git_dirty=true
  else
    git_dirty=false
  fi

  scint_yield=$(macro_box_const "SCINTILLATIONYIELD")
  if [[ -z "${scint_yield}" ]]; then
    scint_yield=null
  fi
  primary_particle=$(macro_value_after "/gun/particle")
  primary_energy=$(macro_value_after "/gun/energy")
  sipm_face=$(macro_value_after "/opnovice2/sipm/face")
  sipm_local=$(macro_value_after "/opnovice2/sipm/localPosition")
  sipm_size=$(macro_value_after "/opnovice2/sipm/size")

  {
    printf '{\n'
    printf '  "generated_at_utc": "%s",\n' "${generated_at}"
    printf '  "scan_name": "%s",\n' "${SCAN_NAME}"
    printf '  "script": "%s",\n' "$0"
    printf '  "template_macro": "%s",\n' "${TEMPLATE_MACRO}"
    printf '  "run_id": "%s",\n' "${RUN_ID}"
    printf '  "run_dir": "%s",\n' "${RUN_DIR}"
    printf '  "dry_run": %s,\n' "$(if [[ "${DRY_RUN}" == "1" ]]; then echo true; else echo false; fi)"
    printf '  "git": {\n'
    printf '    "commit": "%s",\n' "${git_commit}"
    printf '    "branch": "%s",\n' "${git_branch}"
    printf '    "dirty": %s\n' "${git_dirty}"
    printf '  },\n'
    printf '  "simulation": {\n'
    printf '    "events_per_point": %s,\n' "${N_EVENTS}"
    printf '    "scintillation_yield_per_mev": %s,\n' "${scint_yield}"
    printf '    "primary_particle": "%s",\n' "${primary_particle}"
    printf '    "primary_energy": "%s",\n' "${primary_energy}"
    printf '    "beam_direction": "%s",\n' "${BEAM_DIRECTION}"
    printf '    "beam_z": "%s %s"\n' "$(format_num "${Z0}")" "${GRID_UNIT}"
    printf '  },\n'
    printf '  "sipm": {\n'
    printf '    "face": "%s",\n' "${sipm_face}"
    printf '    "local_position": "%s",\n' "${sipm_local}"
    printf '    "size": "%s",\n' "${sipm_size}"
    printf '    "near_field_step": "%s %s"\n' "$(format_num "${GRID_STEP}")" "${GRID_UNIT}"
    printf '  },\n'
    printf '  "grid": {\n'
    printf '    "unit": "%s",\n' "${GRID_UNIT}"
    printf '    "x": '
    json_number_array "${XS[@]}"
    printf ',\n'
    printf '    "y": '
    json_number_array "${YS[@]}"
    printf '\n'
    printf '  },\n'
    printf '  "outputs": {\n'
    printf '    "macro_dir": "%s",\n' "${MACRO_DIR}"
    printf '    "root_dir": "%s",\n' "${ROOT_DIR}"
    printf '    "log_dir": "%s",\n' "${LOG_DIR}"
    printf '    "run_config": "%s",\n' "${RUN_CONFIG}"
    printf '    "latest_run_config": "%s",\n' "${LATEST_RUN_CONFIG}"
    printf '    "latest_run_link": "%s"\n' "${LATEST_RUN_LINK}"
    printf '  },\n'
    printf '  "points": [\n'

    local first_point=1
    local x y lx ly tag macro outfile log
    for x in "${XS[@]}"; do
      for y in "${YS[@]}"; do
        lx=$(label_num "${x}")
        ly=$(label_num "${y}")
        tag="x${lx}${GRID_UNIT}_y${ly}${GRID_UNIT}"
        macro="${MACRO_DIR}/sipm_scan_${tag}.mac"
        outfile="${ROOT_DIR}/sipm_scan_${tag}.root"
        log="${LOG_DIR}/sipm_scan_${tag}.log"

        if [[ "${first_point}" -eq 0 ]]; then
          printf ',\n'
        fi
        printf '    {"tag": "%s", "x": %s, "y": %s, "z": %s, "unit": "%s", "macro": "%s", "root": "%s", "log": "%s"}' \
          "${tag}" "$(format_num "${x}")" "$(format_num "${y}")" "$(format_num "${Z0}")" "${GRID_UNIT}" "${macro}" "${outfile}" "${log}"
        first_point=0
      done
    done

    printf '\n'
    printf '  ]\n'
    printf '}\n'
  } > "${config}"
}

write_run_config "${RUN_CONFIG}"
cp "${RUN_CONFIG}" "${LATEST_RUN_CONFIG}"
if [[ -L "${LATEST_RUN_LINK}" || ! -e "${LATEST_RUN_LINK}" ]]; then
  ln -sfn "${RUN_DIR}" "${LATEST_RUN_LINK}"
else
  echo "Not updating ${LATEST_RUN_LINK}: it exists and is not a symlink." >&2
fi
echo "Run directory: ${RUN_DIR}"
echo "Wrote run metadata to ${RUN_CONFIG} and ${LATEST_RUN_CONFIG}"

for x in "${XS[@]}"; do
  for y in "${YS[@]}"; do
    lx=$(label_num "${x}")
    ly=$(label_num "${y}")
    tag="x${lx}${GRID_UNIT}_y${ly}${GRID_UNIT}"

    macro="${MACRO_DIR}/sipm_scan_${tag}.mac"
    outfile="${ROOT_DIR}/sipm_scan_${tag}"
    log="${LOG_DIR}/sipm_scan_${tag}.log"

    sed \
      -e "s|/analysis/setFileName ej200_sipm_test|/analysis/setFileName ${outfile}|" \
      -e "s|/gun/position -6 4 0 cm|/gun/position $(format_num "${x}") $(format_num "${y}") $(format_num "${Z0}") ${GRID_UNIT}|" \
      -e "s|/gun/direction 1 0 0|/gun/direction ${BEAM_DIRECTION}|" \
      -e "s|/run/beamOn 100|/run/beamOn ${N_EVENTS}|" \
      "${TEMPLATE_MACRO}" > "${macro}"

    if [[ "${DRY_RUN}" == "1" ]]; then
      echo "Prepared ${tag}: x=$(format_num "${x}") ${GRID_UNIT}, y=$(format_num "${y}") ${GRID_UNIT}"
    else
      echo "Running ${tag}: x=$(format_num "${x}") ${GRID_UNIT}, y=$(format_num "${y}") ${GRID_UNIT}"
      ./build/OpNovice2 "${macro}" > "${log}" 2>&1
    fi
  done
done

if [[ "${DRY_RUN}" == "1" ]]; then
  echo "Dry run complete; no simulations were executed."
fi
