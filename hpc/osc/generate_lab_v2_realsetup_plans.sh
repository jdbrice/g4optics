#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

OUT_ROOT="${OUT_ROOT:-hpc/osc/generated/lab_v2_realsetup}"
EVENTS="${EVENTS:-500}"
SIPM_SIZE="${SIPM_SIZE:-2.4 2.4 0.5 mm}"
BEAM_SIGMA="${BEAM_SIGMA:-5}"
SURFACE_REFLECTIVITY_CSV="${SURFACE_REFLECTIVITY_CSV:-optical_data/ej510_reflectivity_empirical.csv}"
CALIBRATION_SURFACE="${CALIBRATION_SURFACE:-polishedfrontpainted}"
BACKPAINTED_AIR_RINDEX="${BACKPAINTED_AIR_RINDEX:-1.0003}"
BEST_DIVERGENCE_MRAD="${BEST_DIVERGENCE_MRAD:-55}"
DIVERGENCES_MRAD="${DIVERGENCES_MRAD:-25 35 45 55 65 75}"
DIVERGENCE_STAGE="${DIVERGENCE_STAGE:-divergence_calibration}"
GENERATE_DIVERGENCE_CALIBRATION="${GENERATE_DIVERGENCE_CALIBRATION:-1}"
GENERATE_SURFACE_COMPARISON="${GENERATE_SURFACE_COMPARISON:-1}"
OPTICAL_COUPLING="${OPTICAL_COUPLING:-none}"
GREASE_ABSORPTION_MODEL="${GREASE_ABSORPTION_MODEL:-transparent}"
GREASE_TRANSMISSION_CSV="${GREASE_TRANSMISSION_CSV:-optical_data/ej550_transmission_empirical.csv}"

runner_data_path() {
  local path="$1"
  printf '%s' "${path#test/OpNovice2/}"
}

host_data_path() {
  local path="$1"
  if [[ -f "${path}" ]]; then
    printf '%s' "${path}"
  else
    printf 'test/OpNovice2/%s' "${path}"
  fi
}

SURFACE_REFLECTIVITY_CSV="$(runner_data_path "${SURFACE_REFLECTIVITY_CSV}")"
SURFACE_REFLECTIVITY_CSV_HOST="$(host_data_path "${SURFACE_REFLECTIVITY_CSV}")"
GREASE_TRANSMISSION_CSV="$(runner_data_path "${GREASE_TRANSMISSION_CSV}")"
GREASE_TRANSMISSION_CSV_HOST="$(host_data_path "${GREASE_TRANSMISSION_CSV}")"
if [[ -n "${GREASE_RINDEX_CSV:-}" ]]; then
  GREASE_RINDEX_CSV="$(runner_data_path "${GREASE_RINDEX_CSV}")"
  GREASE_RINDEX_CSV_HOST="$(host_data_path "${GREASE_RINDEX_CSV}")"
fi

csv_data_rows() {
  awk '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    header_seen == 0 { header_seen = 1; next }
    { count += 1 }
    END { print count + 0 }
  ' "$1"
}

if [[ ! -f "${SURFACE_REFLECTIVITY_CSV_HOST}" ]]; then
  echo "Missing EJ-510 reflectivity CSV: ${SURFACE_REFLECTIVITY_CSV_HOST}" >&2
  exit 1
fi
if [[ "$(csv_data_rows "${SURFACE_REFLECTIVITY_CSV_HOST}")" -lt 2 ]]; then
  echo "EJ-510 reflectivity CSV needs at least two official data rows before plan generation: ${SURFACE_REFLECTIVITY_CSV_HOST}" >&2
  exit 1
fi
case "${OPTICAL_COUPLING}" in
  none|ej550-grease)
    ;;
  *)
    echo "Invalid OPTICAL_COUPLING: ${OPTICAL_COUPLING}. Use none or ej550-grease." >&2
    exit 1
    ;;
esac
if [[ "${OPTICAL_COUPLING}" == "ej550-grease" ]]; then
  : "${GREASE_THICKNESS:?Set GREASE_THICKNESS for flat EJ-550 coupling.}"
fi
if [[ "${OPTICAL_COUPLING}" == "ej550-grease" && -n "${GREASE_RINDEX_CSV:-}" ]]; then
  if [[ ! -f "${GREASE_RINDEX_CSV_HOST}" ]]; then
    echo "Missing EJ-550 RINDEX CSV: ${GREASE_RINDEX_CSV_HOST}" >&2
    exit 1
  fi
  if [[ "$(csv_data_rows "${GREASE_RINDEX_CSV_HOST}")" -lt 2 ]]; then
    echo "EJ-550 RINDEX CSV needs at least two data rows before plan generation: ${GREASE_RINDEX_CSV_HOST}" >&2
    exit 1
  fi
fi
if [[ "${OPTICAL_COUPLING}" == "ej550-grease" ]]; then
  case "${GREASE_ABSORPTION_MODEL}" in
    transparent)
      if [[ -n "${GREASE_ABS_LENGTH:-}" ]]; then
        echo "GREASE_ABS_LENGTH requires GREASE_ABSORPTION_MODEL=constant." >&2
        exit 1
      fi
      ;;
    constant)
      : "${GREASE_ABS_LENGTH:?Set GREASE_ABS_LENGTH for the constant grease absorption model.}"
      ;;
    ej550-transmission-derived)
      if [[ -n "${GREASE_ABS_LENGTH:-}" ]]; then
        echo "GREASE_ABS_LENGTH cannot be used with GREASE_ABSORPTION_MODEL=ej550-transmission-derived." >&2
        exit 1
      fi
      if [[ ! -f "${GREASE_TRANSMISSION_CSV_HOST}" ]]; then
        echo "Missing EJ-550 transmission CSV: ${GREASE_TRANSMISSION_CSV_HOST}" >&2
        exit 1
      fi
      if [[ "$(csv_data_rows "${GREASE_TRANSMISSION_CSV_HOST}")" -lt 2 ]]; then
        echo "EJ-550 transmission CSV needs at least two data rows: ${GREASE_TRANSMISSION_CSV_HOST}" >&2
        exit 1
      fi
      ;;
    *)
      echo "Invalid GREASE_ABSORPTION_MODEL: ${GREASE_ABSORPTION_MODEL}" >&2
      exit 1
      ;;
  esac
fi

read -r -a DIVERGENCES <<< "${DIVERGENCES_MRAD}"
if [[ "${#DIVERGENCES[@]}" -eq 0 ]]; then
  echo "DIVERGENCES_MRAD must contain at least one divergence." >&2
  exit 1
fi
for divergence in "${DIVERGENCES[@]}"; do
  if [[ ! "${divergence}" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "Invalid divergence in DIVERGENCES_MRAD: ${divergence}" >&2
    exit 1
  fi
done
for toggle_name in GENERATE_DIVERGENCE_CALIBRATION GENERATE_SURFACE_COMPARISON; do
  toggle_value="${!toggle_name}"
  if [[ "${toggle_value}" != "0" && "${toggle_value}" != "1" ]]; then
    echo "${toggle_name} must be 0 or 1." >&2
    exit 1
  fi
done
SURFACES=(polishedfrontpainted groundfrontpainted polishedbackpainted groundbackpainted)

CSV_5X5X16="test/OpNovice2/lab_data/5x5x1.6_painted_undimpled_opticalGrease/responses_tile_50.000000x50.000000_pattern_eighth_grid_16mm_painted_optical_grease_tile_OM_1776182096.csv"
CSV_5X5X4="test/OpNovice2/lab_data/5x5x0.4_painted_undimpled_opticalGrease/responses_tile_50.000000x50.000000_pattern_eighth_grid_4mm_no_dimple_painted_optical_grease_JW_1776360791.csv"
CSV_10X10X16="test/OpNovice2/lab_data/10x10x1.6_painted_undimpled_opticalGrease/responses_tile_100.000000x100.000000_pattern_eighth_grid_16mm_painted_undimpled_optical_grease_RERUN_5_SC_1781802404.csv"
CSV_10X10X4="test/OpNovice2/lab_data/10x10x0.4_painted_undimpled_opticalGrease/responses_tile_100.000000x100.000000_pattern_eighth_grid_4mm_painted_undimpled_optical_grease_RERUN_2_JW_1781188758.csv"

generate_one() {
  local stage="$1"
  local label="$2"
  local lab_csv="$3"
  local tank_size="$4"
  local beam_z="$5"
  local surface="$6"
  local divergence="$7"
  local out_dir="${OUT_ROOT}/${stage}"
  local -a command=(
    python3 hpc/osc/generate_lab_scan_plan.py
    --lab-csv "${lab_csv}"
    --out-dir "${out_dir}"
    --label "${label}_${surface}_sigma${BEAM_SIGMA}mm"
    --events "${EVENTS}"
    --source-mode gps
    --source-model sr90-spectrum
    --surface-preset "${surface}"
    --surface-reflectivity-model ej510-empirical
    --surface-reflectivity-csv "${SURFACE_REFLECTIVITY_CSV}"
    --optical-coupling "${OPTICAL_COUPLING}"
    --tank-size "${tank_size}"
    --beam-z "${beam_z}"
    --beam-sigma "${BEAM_SIGMA}"
    --sipm-face=-Z
    --sipm-local-position "0 0 0 cm"
    --sipm-size "${SIPM_SIZE}"
    --divergence-mrad "${divergence}"
    --description "lab v2 real setup scaffold: EJ-200, EJ-510, SiPM ${SIPM_SIZE}; optical coupling=${OPTICAL_COUPLING}; none means zero-gap proxy for experimental EJ-550; stage=${stage}; backpainted uses air-gap RINDEX=${BACKPAINTED_AIR_RINDEX} caveat"
  )
  if [[ "${OPTICAL_COUPLING}" == "ej550-grease" ]]; then
    command+=(
      --grease-thickness "${GREASE_THICKNESS}"
      --grease-absorption-model "${GREASE_ABSORPTION_MODEL}"
    )
    if [[ -n "${GREASE_RINDEX:-}" ]]; then
      command+=(--grease-rindex "${GREASE_RINDEX}")
    elif [[ -n "${GREASE_RINDEX_CSV:-}" ]]; then
      command+=(--grease-rindex-csv "${GREASE_RINDEX_CSV}")
    fi
    if [[ "${GREASE_ABSORPTION_MODEL}" == "constant" ]]; then
      command+=(--grease-abs-length "${GREASE_ABS_LENGTH}")
    elif [[ "${GREASE_ABSORPTION_MODEL}" == "ej550-transmission-derived" ]]; then
      command+=(--grease-transmission-csv "${GREASE_TRANSMISSION_CSV}")
    fi
  fi
  case "${surface}" in
    polishedbackpainted|groundbackpainted)
      command+=(--surface-rindex "${BACKPAINTED_AIR_RINDEX}")
      ;;
  esac
  "${command[@]}"
}

generate_quadrant_set() {
  local stage="$1"
  local surface="$2"
  local divergence="$3"
  generate_one "${stage}" "lab_v2_5x5x16" "${CSV_5X5X16}" "50 50 16 mm" 30 "${surface}" "${divergence}"
  generate_one "${stage}" "lab_v2_5x5x4" "${CSV_5X5X4}" "50 50 4 mm" 36 "${surface}" "${divergence}"
  generate_one "${stage}" "lab_v2_11p5x11p5x16" "${CSV_10X10X16}" "115 115 16 mm" 30 "${surface}" "${divergence}"
  generate_one "${stage}" "lab_v2_11p5x11p5x4" "${CSV_10X10X4}" "115 115 4 mm" 36 "${surface}" "${divergence}"
}

generated_dirs=()
if [[ "${GENERATE_DIVERGENCE_CALIBRATION}" == "1" ]]; then
  generated_dirs+=("${OUT_ROOT}/${DIVERGENCE_STAGE}")
  for divergence in "${DIVERGENCES[@]}"; do
    generate_quadrant_set "${DIVERGENCE_STAGE}" "${CALIBRATION_SURFACE}" "${divergence}"
  done
fi

if [[ "${GENERATE_SURFACE_COMPARISON}" == "1" ]]; then
  generated_dirs+=("${OUT_ROOT}/surface_comparison")
  for surface in "${SURFACES[@]}"; do
    generate_quadrant_set "surface_comparison" "${surface}" "${BEST_DIVERGENCE_MRAD}"
  done
fi

if [[ "${#generated_dirs[@]}" -eq 0 ]]; then
  echo "Nothing requested: both generation toggles are 0." >&2
  exit 1
fi
find "${generated_dirs[@]}" -type f -name "*.txt" | sort
