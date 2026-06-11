#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

MODE="${1:-surface}"
GRID="${2:-near5}"
N_EVENTS="${N_EVENTS:-100}"
DRY_RUN="${DRY_RUN:-0}"

BEAM_DIRECTION="0 0 -1"
SCAN_RUNS_DIR="scan_runs"
LATEST_RUN_LINK="scan_latest"
LATEST_POINTS_CSV="points.csv"
LATEST_RUN_CONFIG="run_config.json"

usage() {
  cat <<'USAGE'
Usage:
  ./run_sipm_cavity_scan.sh surface near5
  ./run_sipm_cavity_scan.sh opening near5
  ./run_sipm_cavity_scan.sh surface wide9
  ./run_sipm_cavity_scan.sh opening wide9

Environment:
  N_EVENTS=100   events per scan point
  DRY_RUN=1      generate macros/config only

Output:
  scan_runs/<UTC timestamp>_cavity_<mode>_<grid>/
  scan_latest -> most recent run directory
USAGE
}

case "${MODE}" in
  surface)
    TEMPLATE_MACRO="ej200_sipm_cavity_surface_test.mac"
    ;;
  opening)
    TEMPLATE_MACRO="ej200_sipm_cavity_opening_test.mac"
    ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    echo "Unknown cavity mode: ${MODE}" >&2
    usage >&2
    exit 1
    ;;
esac

if [[ ! -f "${TEMPLATE_MACRO}" ]]; then
  echo "Template macro not found: ${TEMPLATE_MACRO}" >&2
  exit 1
fi

if [[ "${DRY_RUN}" != "1" && ! -x "./build/OpNovice2" ]]; then
  echo "Missing executable: ./build/OpNovice2" >&2
  echo "Build first with: cmake -S . -B build && cmake --build build -j\$(nproc)" >&2
  exit 1
fi

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

format_num() {
  awk -v v="$1" 'BEGIN {
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

json_string() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "${s}"
}

sipm_size="$(macro_value_after "/opnovice2/sipm/size")"
read -r sipm_active_u sipm_active_v sipm_thickness sipm_unit _ <<< "${sipm_size}"

if [[ -z "${sipm_active_u:-}" || -z "${sipm_active_v:-}" || -z "${sipm_unit:-}" ]]; then
  echo "Could not parse /opnovice2/sipm/size from ${TEMPLATE_MACRO}: ${sipm_size}" >&2
  exit 1
fi

case "${GRID}" in
  near5)
    GRID_UNIT="${sipm_unit}"
    SCAN_NAME="5x5 cavity ${MODE} near-field scan"
    GRID_STEP="$(awk -v u="${sipm_active_u}" -v v="${sipm_active_v}" 'BEGIN { print (u > v ? u : v) }')"
    read -r -a XS <<< "$(awk -v s="${GRID_STEP}" 'BEGIN { printf "%.10g %.10g 0 %.10g %.10g", -2*s, -s, s, 2*s }')"
    read -r -a YS <<< "$(awk -v s="${GRID_STEP}" 'BEGIN { printf "%.10g %.10g 0 %.10g %.10g", -2*s, -s, s, 2*s }')"
    if [[ "${GRID_UNIT}" == "cm" ]]; then
      Z0="0.4"
    else
      Z0="4"
    fi
    ;;
  wide9)
    GRID_UNIT="cm"
    SCAN_NAME="9x9 cavity ${MODE} wide scan"
    Z0="0.4"
    XS=(-4 -3 -2 -1 0 1 2 3 4)
    YS=(-4 -3 -2 -1 0 1 2 3 4)
    ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    echo "Unknown grid: ${GRID}" >&2
    usage >&2
    exit 1
    ;;
esac

PREFIX="cavity_${MODE}_${GRID}"
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
POINTS_CSV="${RUN_DIR}/points.csv"
RUN_CONFIG="${RUN_DIR}/run_config.json"

mkdir -p "${MACRO_DIR}" "${ROOT_DIR}" "${LOG_DIR}"

template_output="$(macro_value_after "/analysis/setFileName")"
primary_particle="$(macro_value_after "/gun/particle")"
primary_energy="$(macro_value_after "/gun/energy")"
sipm_face="$(macro_value_after "/opnovice2/sipm/face")"
sipm_cavity_mode="$(macro_value_after "/opnovice2/sipm/cavityMode")"
sipm_local="$(macro_value_after "/opnovice2/sipm/localPosition")"
bottom_cavity="$(macro_value_after "/opnovice2/tank/bottomCavity")"
scint_yield="$(macro_box_const "SCINTILLATIONYIELD")"
if [[ -z "${scint_yield}" ]]; then
  scint_yield=null
fi

generated_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
git_commit="$(git rev-parse HEAD 2>/dev/null || echo "unknown")"
git_branch="$(git branch --show-current 2>/dev/null || echo "unknown")"
if [[ -z "${git_branch}" ]]; then
  git_branch="detached"
fi
if [[ -n "$(git status --porcelain 2>/dev/null || true)" ]]; then
  git_dirty=true
else
  git_dirty=false
fi

{
  printf 'tag,x,y,z,unit,macro,root,log\n'
  for x in "${XS[@]}"; do
    for y in "${YS[@]}"; do
      lx="$(label_num "${x}")"
      ly="$(label_num "${y}")"
      tag="${PREFIX}_x${lx}_y${ly}"
      macro="${MACRO_DIR}/${tag}.mac"
      root="${ROOT_DIR}/${tag}.root"
      log="${LOG_DIR}/${tag}.log"
      printf '%s,%s,%s,%s,%s,%s,%s,%s\n' \
        "${tag}" "$(format_num "${x}")" "$(format_num "${y}")" "$(format_num "${Z0}")" \
        "${GRID_UNIT}" "${macro}" "${root}" "${log}"
    done
  done
} > "${POINTS_CSV}"

write_run_config() {
  {
    printf '{\n'
    printf '  "generated_at_utc": "%s",\n' "$(json_string "${generated_at}")"
    printf '  "scan_name": "%s",\n' "$(json_string "${SCAN_NAME}")"
    printf '  "script": "%s",\n' "$(json_string "$0")"
    printf '  "template_macro": "%s",\n' "$(json_string "${TEMPLATE_MACRO}")"
    printf '  "mode": "%s",\n' "$(json_string "${MODE}")"
    printf '  "grid_name": "%s",\n' "$(json_string "${GRID}")"
    printf '  "run_id": "%s",\n' "$(json_string "${RUN_ID}")"
    printf '  "run_dir": "%s",\n' "$(json_string "${RUN_DIR}")"
    printf '  "dry_run": %s,\n' "$(if [[ "${DRY_RUN}" == "1" ]]; then echo true; else echo false; fi)"
    printf '  "git": {\n'
    printf '    "commit": "%s",\n' "$(json_string "${git_commit}")"
    printf '    "branch": "%s",\n' "$(json_string "${git_branch}")"
    printf '    "dirty": %s\n' "${git_dirty}"
    printf '  },\n'
    printf '  "simulation": {\n'
    printf '    "events_per_point": %s,\n' "${N_EVENTS}"
    printf '    "scintillation_yield_per_mev": %s,\n' "${scint_yield}"
    printf '    "primary_particle": "%s",\n' "$(json_string "${primary_particle}")"
    printf '    "primary_energy": "%s",\n' "$(json_string "${primary_energy}")"
    printf '    "beam_direction": "%s",\n' "$(json_string "${BEAM_DIRECTION}")"
    printf '    "beam_z": "%s %s"\n' "$(format_num "${Z0}")" "$(json_string "${GRID_UNIT}")"
    printf '  },\n'
    printf '  "sipm": {\n'
    printf '    "face": "%s",\n' "$(json_string "${sipm_face}")"
    printf '    "cavity_mode": "%s",\n' "$(json_string "${sipm_cavity_mode}")"
    printf '    "local_position": "%s",\n' "$(json_string "${sipm_local}")"
    printf '    "size": "%s",\n' "$(json_string "${sipm_size}")"
    if [[ -n "${GRID_STEP:-}" ]]; then
      printf '    "near_field_step": "%s %s"\n' "$(format_num "${GRID_STEP}")" "$(json_string "${GRID_UNIT}")"
    else
      printf '    "near_field_step": null\n'
    fi
    printf '  },\n'
    printf '  "tank": {\n'
    printf '    "bottom_cavity": "%s"\n' "$(json_string "${bottom_cavity}")"
    printf '  },\n'
    printf '  "grid": {\n'
    printf '    "unit": "%s",\n' "$(json_string "${GRID_UNIT}")"
    printf '    "x": '
    json_number_array "${XS[@]}"
    printf ',\n'
    printf '    "y": '
    json_number_array "${YS[@]}"
    printf '\n'
    printf '  },\n'
    printf '  "outputs": {\n'
    printf '    "macro_dir": "%s",\n' "$(json_string "${MACRO_DIR}")"
    printf '    "root_dir": "%s",\n' "$(json_string "${ROOT_DIR}")"
    printf '    "log_dir": "%s",\n' "$(json_string "${LOG_DIR}")"
    printf '    "run_config": "%s",\n' "$(json_string "${RUN_CONFIG}")"
    printf '    "latest_run_config": "%s",\n' "$(json_string "${LATEST_RUN_CONFIG}")"
    printf '    "points_csv": "%s",\n' "$(json_string "${POINTS_CSV}")"
    printf '    "latest_points_csv": "%s",\n' "$(json_string "${LATEST_POINTS_CSV}")"
    printf '    "latest_run_link": "%s"\n' "$(json_string "${LATEST_RUN_LINK}")"
    printf '  }\n'
    printf '}\n'
  } > "${RUN_CONFIG}"
}

write_run_config
cp "${RUN_CONFIG}" "${LATEST_RUN_CONFIG}"
cp "${POINTS_CSV}" "${LATEST_POINTS_CSV}"
if [[ -L "${LATEST_RUN_LINK}" || ! -e "${LATEST_RUN_LINK}" ]]; then
  ln -sfn "${RUN_DIR}" "${LATEST_RUN_LINK}"
else
  echo "Not updating ${LATEST_RUN_LINK}: it exists and is not a symlink." >&2
fi

echo "Scan: ${SCAN_NAME}"
echo "Template: ${TEMPLATE_MACRO}"
echo "Run directory: ${RUN_DIR}"
echo "Grid unit: ${GRID_UNIT}; x=($(json_number_array "${XS[@]}")); y=($(json_number_array "${YS[@]}"))"
echo "Events per point: ${N_EVENTS}"
echo "Metadata: ${RUN_CONFIG}, ${POINTS_CSV}"
echo "Latest pointers: ${LATEST_RUN_LINK}, ${LATEST_RUN_CONFIG}, ${LATEST_POINTS_CSV}"

tail -n +2 "${POINTS_CSV}" | while IFS=, read -r tag x y z unit macro root log; do
  outfile="${root%.root}"

  sed \
    -e "s|/analysis/setFileName ${template_output}|/analysis/setFileName ${outfile}|" \
    -e "s|/gun/position 0 0 4 cm|/gun/position ${x} ${y} ${z} ${unit}|" \
    -e "s|/gun/direction 0 0 -1|/gun/direction ${BEAM_DIRECTION}|" \
    -e "s|/run/beamOn 100|/run/beamOn ${N_EVENTS}|" \
    "${TEMPLATE_MACRO}" > "${macro}"

  if [[ "${DRY_RUN}" == "1" ]]; then
    echo "Prepared ${tag}: x=${x} ${unit}, y=${y} ${unit}"
  else
    echo "Running ${tag}: x=${x} ${unit}, y=${y} ${unit}"
    ./build/OpNovice2 "${macro}" > "${log}" 2>&1
  fi
done

if [[ "${DRY_RUN}" == "1" ]]; then
  echo "Dry run complete; no simulations were executed."
else
  echo "Scan complete."
fi
