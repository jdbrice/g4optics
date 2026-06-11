#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

N_EVENTS="${N_EVENTS:-100}"
DRY_RUN="${DRY_RUN:-0}"

MODE="surface"
GRID="near5"
SIPM_FACE_OVERRIDE=""
SIPM_CAVITY_MODE_OVERRIDE=""
SIPM_LOCAL_POSITION_OVERRIDE=""

BEAM_DIRECTION="0 0 -1"
SCAN_RUNS_DIR="scan_runs"
LATEST_RUN_LINK="scan_latest"
LATEST_POINTS_CSV="points.csv"
LATEST_RUN_CONFIG="run_config.json"

usage() {
  cat <<'USAGE'
Usage:
  ./run_sipm_cavity_scan.sh full near5
  ./run_sipm_cavity_scan.sh full wide9
  ./run_sipm_cavity_scan.sh surface near5
  ./run_sipm_cavity_scan.sh opening near5
  ./run_sipm_cavity_scan.sh surface wide9
  ./run_sipm_cavity_scan.sh opening wide9

Placement options:
  --sipm-face FACE                    +X, -X, +Y, -Y, +Z, -Z, or bottomCavity
  --sipm-cavity-mode MODE             surface or opening
  --sipm-local-position "x y z unit"  override /opnovice2/sipm/localPosition

Environment:
  N_EVENTS=100   events per scan point
  DRY_RUN=1      generate macros/config only

Output:
  scan_runs/<UTC timestamp>_<mode>_<grid>/
  scan_latest -> most recent run directory
USAGE
}

POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help|help)
      usage
      exit 0
      ;;
    --sipm-face)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --sipm-face" >&2
        exit 1
      fi
      SIPM_FACE_OVERRIDE="$2"
      shift 2
      ;;
    --sipm-face=*)
      SIPM_FACE_OVERRIDE="${1#*=}"
      shift
      ;;
    --sipm-cavity-mode)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --sipm-cavity-mode" >&2
        exit 1
      fi
      SIPM_CAVITY_MODE_OVERRIDE="$2"
      shift 2
      ;;
    --sipm-cavity-mode=*)
      SIPM_CAVITY_MODE_OVERRIDE="${1#*=}"
      shift
      ;;
    --sipm-local-position)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --sipm-local-position" >&2
        exit 1
      fi
      SIPM_LOCAL_POSITION_OVERRIDE="$2"
      shift 2
      ;;
    --sipm-local-position=*)
      SIPM_LOCAL_POSITION_OVERRIDE="${1#*=}"
      shift
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        POSITIONAL+=("$1")
        shift
      done
      ;;
    --*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

if [[ "${#POSITIONAL[@]}" -gt 2 ]]; then
  echo "Too many positional arguments: ${POSITIONAL[*]}" >&2
  usage >&2
  exit 1
fi
if [[ "${#POSITIONAL[@]}" -ge 1 ]]; then
  MODE="${POSITIONAL[0]}"
fi
if [[ "${#POSITIONAL[@]}" -ge 2 ]]; then
  GRID="${POSITIONAL[1]}"
fi

case "${MODE}" in
  full)
    TEMPLATE_MACRO="ej200_sipm_test.mac"
    SCAN_LABEL="full tank"
    PREFIX_ROOT="full"
    ;;
  surface)
    TEMPLATE_MACRO="ej200_sipm_cavity_surface_test.mac"
    SCAN_LABEL="cavity surface"
    PREFIX_ROOT="cavity_surface"
    ;;
  opening)
    TEMPLATE_MACRO="ej200_sipm_cavity_opening_test.mac"
    SCAN_LABEL="cavity opening"
    PREFIX_ROOT="cavity_opening"
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

if [[ -n "${SIPM_FACE_OVERRIDE}" ]]; then
  case "${SIPM_FACE_OVERRIDE}" in
    +X|-X|+Y|-Y|+Z|-Z|bottomCavity)
      ;;
    *)
      echo "Invalid --sipm-face: ${SIPM_FACE_OVERRIDE}. Use +X, -X, +Y, -Y, +Z, -Z, or bottomCavity." >&2
      exit 1
      ;;
  esac
fi

if [[ -n "${SIPM_CAVITY_MODE_OVERRIDE}" ]]; then
  case "${SIPM_CAVITY_MODE_OVERRIDE}" in
    surface|opening)
      ;;
    *)
      echo "Invalid --sipm-cavity-mode: ${SIPM_CAVITY_MODE_OVERRIDE}. Use surface or opening." >&2
      exit 1
      ;;
  esac
fi

if [[ -n "${SIPM_LOCAL_POSITION_OVERRIDE}" ]]; then
  read -r sipm_local_x sipm_local_y sipm_local_z sipm_local_unit sipm_local_extra <<< "${SIPM_LOCAL_POSITION_OVERRIDE}"
  if [[ -z "${sipm_local_x:-}" || -z "${sipm_local_y:-}" || -z "${sipm_local_z:-}" || -z "${sipm_local_unit:-}" || -n "${sipm_local_extra:-}" ]]; then
    echo "Invalid --sipm-local-position: ${SIPM_LOCAL_POSITION_OVERRIDE}. Use quoted form like \"0 0 0 cm\"." >&2
    exit 1
  fi
fi

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
    SCAN_NAME="5x5 ${SCAN_LABEL} near-field scan"
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
    SCAN_NAME="9x9 ${SCAN_LABEL} wide scan"
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

PREFIX="${PREFIX_ROOT}_${GRID}"
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

template_output="$(macro_value_after "/analysis/setFileName")"
template_gun_position="$(macro_value_after "/gun/position")"
template_gun_direction="$(macro_value_after "/gun/direction")"
template_beam_on="$(macro_value_after "/run/beamOn")"
primary_particle="$(macro_value_after "/gun/particle")"
primary_energy="$(macro_value_after "/gun/energy")"
sipm_face="$(macro_value_after "/opnovice2/sipm/face")"
sipm_cavity_mode="$(macro_value_after "/opnovice2/sipm/cavityMode")"
sipm_local="$(macro_value_after "/opnovice2/sipm/localPosition")"
template_sipm_face="${sipm_face}"
template_sipm_cavity_mode="${sipm_cavity_mode}"
template_sipm_local="${sipm_local}"
if [[ -n "${SIPM_FACE_OVERRIDE}" ]]; then
  sipm_face="${SIPM_FACE_OVERRIDE}"
fi
if [[ -n "${SIPM_CAVITY_MODE_OVERRIDE}" ]]; then
  sipm_cavity_mode="${SIPM_CAVITY_MODE_OVERRIDE}"
fi
if [[ -n "${SIPM_LOCAL_POSITION_OVERRIDE}" ]]; then
  sipm_local="${SIPM_LOCAL_POSITION_OVERRIDE}"
fi
template_bottom_cavity="$(macro_value_after "/opnovice2/tank/bottomCavity")"
if [[ "${MODE}" == "full" ]]; then
  bottom_cavity=false
else
  bottom_cavity="${template_bottom_cavity}"
fi
if [[ -z "${bottom_cavity}" ]]; then
  bottom_cavity=false
fi

if [[ "${MODE}" == "full" && "${sipm_face}" == "bottomCavity" ]]; then
  echo "Invalid placement: --sipm-face bottomCavity requires a cavity mode, but full mode sets bottomCavity false." >&2
  exit 1
fi

scint_yield="$(macro_box_const "SCINTILLATIONYIELD")"
if [[ -z "${scint_yield}" ]]; then
  scint_yield=null
fi

if [[ -z "${template_output}" || -z "${template_gun_position}" || -z "${template_gun_direction}" || -z "${template_beam_on}" || -z "${template_sipm_face}" || -z "${template_sipm_local}" ]]; then
  echo "Could not parse output/gun/beamOn/SiPM commands from ${TEMPLATE_MACRO}" >&2
  exit 1
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

mkdir -p "${MACRO_DIR}" "${ROOT_DIR}" "${LOG_DIR}"

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
    printf '  "overrides": {\n'
    printf '    "sipm_face": %s,\n' "$(if [[ -n "${SIPM_FACE_OVERRIDE}" ]]; then echo true; else echo false; fi)"
    printf '    "sipm_cavity_mode": %s,\n' "$(if [[ -n "${SIPM_CAVITY_MODE_OVERRIDE}" ]]; then echo true; else echo false; fi)"
    printf '    "sipm_local_position": %s\n' "$(if [[ -n "${SIPM_LOCAL_POSITION_OVERRIDE}" ]]; then echo true; else echo false; fi)"
    printf '  },\n'
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
    printf '    "cavity_mode": '
    if [[ -n "${sipm_cavity_mode}" ]]; then
      printf '"%s"' "$(json_string "${sipm_cavity_mode}")"
    else
      printf 'null'
    fi
    printf ',\n'
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

  sed_args=(
    -e "s|/analysis/setFileName ${template_output}|/analysis/setFileName ${outfile}|" \
    -e "s|/gun/position ${template_gun_position}|/gun/position ${x} ${y} ${z} ${unit}|" \
    -e "s|/gun/direction ${template_gun_direction}|/gun/direction ${BEAM_DIRECTION}|" \
    -e "s|/run/beamOn ${template_beam_on}|/run/beamOn ${N_EVENTS}|" \
    -e "s|/opnovice2/sipm/face ${template_sipm_face}|/opnovice2/sipm/face ${sipm_face}|" \
    -e "s|/opnovice2/sipm/localPosition ${template_sipm_local}|/opnovice2/sipm/localPosition ${sipm_local}|"
  )

  if [[ -n "${template_sipm_cavity_mode}" && -n "${sipm_cavity_mode}" ]]; then
    sed_args+=(-e "s|/opnovice2/sipm/cavityMode ${template_sipm_cavity_mode}|/opnovice2/sipm/cavityMode ${sipm_cavity_mode}|")
  fi

  if [[ "${MODE}" == "full" && -n "${template_bottom_cavity}" ]]; then
    sed_args+=(-e "s|/opnovice2/tank/bottomCavity ${template_bottom_cavity}|/opnovice2/tank/bottomCavity false|")
  fi

  if [[ "${MODE}" == "full" && -z "${template_bottom_cavity}" ]]; then
    sed "${sed_args[@]}" "${TEMPLATE_MACRO}" | awk \
      -v inject="/opnovice2/tank/bottomCavity false" \
      '!inserted && $1 == "/opnovice2/sipm/face" { print inject; inserted = 1 } { print }' \
      > "${macro}"
  else
    sed "${sed_args[@]}" "${TEMPLATE_MACRO}" > "${macro}"
  fi

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
