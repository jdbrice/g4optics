#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

N_EVENTS="${N_EVENTS:-100}"
DRY_RUN="${DRY_RUN:-0}"
PLOT_WITH_ROOT="${PLOT_WITH_ROOT:-1}"
ROOT_COMMAND="${ROOT_COMMAND:-root}"
ROOT_PLOT_MACRO="plot_efficiency_map.C"
ROOT_PLOT_FIDUCIAL_LIMIT_MM="${ROOT_PLOT_FIDUCIAL_LIMIT_MM:-45}"
ROOT_PLOT_SKIP_LOCAL="${ROOT_PLOT_SKIP_LOCAL:-0}"

MODE="surface"
GRID="near5"
SOURCE_MODE="${SOURCE_MODE:-auto}"
TEMPLATE_MACRO_OVERRIDE=""
SIPM_FACE_OVERRIDE=""
SIPM_CAVITY_MODE_OVERRIDE=""
SIPM_LOCAL_POSITION_OVERRIDE=""
TANK_SIZE_OVERRIDE=""
TANK_SIZE_PRESET_OVERRIDE=""
ELECTRON_ENERGY_MODE_OVERRIDE=""
CUSTOM_X_MIN=""
CUSTOM_X_MAX=""
CUSTOM_Y_MIN=""
CUSTOM_Y_MAX=""
CUSTOM_STEP=""
CUSTOM_GRID_UNIT=""
CUSTOM_BEAM_Z=""
BEAM_Z_INFERRED="0"

BEAM_DIRECTION="0 0 -1"
SCAN_RUNS_DIR="scan_runs"
LATEST_RUN_LINK="scan_latest"
LATEST_POINTS_CSV="points.csv"
LATEST_RUN_CONFIG="run_config.json"
LATEST_EFFICIENCY_MAP="efficiency_map.csv"

usage() {
  cat <<'USAGE'
Usage:
  ./run_sipm_cavity_scan.sh full near5
  ./run_sipm_cavity_scan.sh full wide9
  ./run_sipm_cavity_scan.sh surface near5
  ./run_sipm_cavity_scan.sh opening near5
  ./run_sipm_cavity_scan.sh surface wide9
  ./run_sipm_cavity_scan.sh opening wide9
  ./run_sipm_cavity_scan.sh full custom --x-min -50 --x-max 50 --y-min -50 --y-max 50 --step 5 --grid-unit mm --beam-z 4

Placement options:
  --source-mode MODE                  auto, gun, or gps
  --template-macro FILE               override the selected template macro
  --sipm-face FACE                    +X, -X, +Y, -Y, +Z, -Z, or bottomCavity
  --sipm-cavity-mode MODE             surface or opening
  --sipm-local-position "x y z unit"  override /opnovice2/sipm/localPosition
  --tank-size "x y z unit"            override full tank size, e.g. "10 10 0.8 cm"
  --tank-size-preset PRESET           5x5x0p4, 5x5x0p8, 5x5x1p6,
                                      10x10x0p4, 10x10x0p8, or 10x10x1p6
  --electron-energy-mode MODE         fixed or sr90Beta

Custom grid options:
  --x-min VALUE                       minimum x coordinate
  --x-max VALUE                       maximum x coordinate
  --y-min VALUE                       minimum y coordinate
  --y-max VALUE                       maximum y coordinate
  --step VALUE                        grid spacing; must evenly divide both ranges
  --grid-unit UNIT                    mm or cm
  --beam-z VALUE                      beam z coordinate in --grid-unit units;
                                      custom defaults to thickness/2 + 1.5 mm

Environment:
  N_EVENTS=100                        events per scan point
  DRY_RUN=1                           generate macros/config only
  SOURCE_MODE=auto                    source command mode: auto, gun, or gps
  PLOT_WITH_ROOT=1                    generate ROOT macro plots after scan if ROOT is available
  ROOT_COMMAND=root                   ROOT executable used for plot generation
  ROOT_PLOT_FIDUCIAL_LIMIT_MM=45      fiducial box half-width shown by ROOT plots

Output:
  scan_runs/<UTC timestamp>_<mode>_<grid>/
  scan_runs/<UTC timestamp>_<mode>_<grid>/efficiency_map.csv
  scan_runs/<UTC timestamp>_<mode>_<grid>/root_*.png and root_*.pdf when ROOT plotting runs
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
    --source-mode)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --source-mode" >&2
        exit 1
      fi
      SOURCE_MODE="$2"
      shift 2
      ;;
    --source-mode=*)
      SOURCE_MODE="${1#*=}"
      shift
      ;;
    --template-macro)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --template-macro" >&2
        exit 1
      fi
      TEMPLATE_MACRO_OVERRIDE="$2"
      shift 2
      ;;
    --template-macro=*)
      TEMPLATE_MACRO_OVERRIDE="${1#*=}"
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
    --tank-size)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --tank-size" >&2
        exit 1
      fi
      TANK_SIZE_OVERRIDE="$2"
      shift 2
      ;;
    --tank-size=*)
      TANK_SIZE_OVERRIDE="${1#*=}"
      shift
      ;;
    --tank-size-preset)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --tank-size-preset" >&2
        exit 1
      fi
      TANK_SIZE_PRESET_OVERRIDE="$2"
      shift 2
      ;;
    --tank-size-preset=*)
      TANK_SIZE_PRESET_OVERRIDE="${1#*=}"
      shift
      ;;
    --electron-energy-mode)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --electron-energy-mode" >&2
        exit 1
      fi
      ELECTRON_ENERGY_MODE_OVERRIDE="$2"
      shift 2
      ;;
    --electron-energy-mode=*)
      ELECTRON_ENERGY_MODE_OVERRIDE="${1#*=}"
      shift
      ;;
    --x-min)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --x-min" >&2
        exit 1
      fi
      CUSTOM_X_MIN="$2"
      shift 2
      ;;
    --x-min=*)
      CUSTOM_X_MIN="${1#*=}"
      shift
      ;;
    --x-max)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --x-max" >&2
        exit 1
      fi
      CUSTOM_X_MAX="$2"
      shift 2
      ;;
    --x-max=*)
      CUSTOM_X_MAX="${1#*=}"
      shift
      ;;
    --y-min)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --y-min" >&2
        exit 1
      fi
      CUSTOM_Y_MIN="$2"
      shift 2
      ;;
    --y-min=*)
      CUSTOM_Y_MIN="${1#*=}"
      shift
      ;;
    --y-max)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --y-max" >&2
        exit 1
      fi
      CUSTOM_Y_MAX="$2"
      shift 2
      ;;
    --y-max=*)
      CUSTOM_Y_MAX="${1#*=}"
      shift
      ;;
    --step)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --step" >&2
        exit 1
      fi
      CUSTOM_STEP="$2"
      shift 2
      ;;
    --step=*)
      CUSTOM_STEP="${1#*=}"
      shift
      ;;
    --grid-unit)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --grid-unit" >&2
        exit 1
      fi
      CUSTOM_GRID_UNIT="$2"
      shift 2
      ;;
    --grid-unit=*)
      CUSTOM_GRID_UNIT="${1#*=}"
      shift
      ;;
    --beam-z)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --beam-z" >&2
        exit 1
      fi
      CUSTOM_BEAM_Z="$2"
      shift 2
      ;;
    --beam-z=*)
      CUSTOM_BEAM_Z="${1#*=}"
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

case "${PLOT_WITH_ROOT}" in
  1|true|TRUE|yes|YES|on|ON)
    PLOT_WITH_ROOT="1"
    ;;
  0|false|FALSE|no|NO|off|OFF)
    PLOT_WITH_ROOT="0"
    ;;
  *)
    echo "Invalid PLOT_WITH_ROOT: ${PLOT_WITH_ROOT}. Use 1 or 0." >&2
    exit 1
    ;;
esac

if [[ "${GRID}" != "custom" ]]; then
  if [[ -n "${CUSTOM_X_MIN}" || -n "${CUSTOM_X_MAX}" || -n "${CUSTOM_Y_MIN}" ||
        -n "${CUSTOM_Y_MAX}" || -n "${CUSTOM_STEP}" || -n "${CUSTOM_GRID_UNIT}" ||
        -n "${CUSTOM_BEAM_Z}" ]]; then
    echo "Custom grid options require GRID=custom." >&2
    echo "Example: ./run_sipm_cavity_scan.sh full custom --x-min -50 --x-max 50 --y-min -50 --y-max 50 --step 5 --grid-unit mm --beam-z 4" >&2
    exit 1
  fi
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

case "${SOURCE_MODE}" in
  auto|gun|gps)
    ;;
  *)
    echo "Invalid --source-mode: ${SOURCE_MODE}. Use auto, gun, or gps." >&2
    exit 1
    ;;
esac

if [[ -n "${TEMPLATE_MACRO_OVERRIDE}" ]]; then
  TEMPLATE_MACRO="${TEMPLATE_MACRO_OVERRIDE}"
elif [[ "${SOURCE_MODE}" == "gps" ]]; then
  case "${MODE}" in
    full)
      TEMPLATE_MACRO="ej200_sipm_gps_test.mac"
      ;;
    *)
      echo "No default GPS template for mode ${MODE}." >&2
      echo "Use --template-macro with a GPS template, or use full mode." >&2
      exit 1
      ;;
  esac
fi

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

if [[ -n "${TANK_SIZE_OVERRIDE}" && -n "${TANK_SIZE_PRESET_OVERRIDE}" ]]; then
  echo "Use either --tank-size or --tank-size-preset, not both." >&2
  exit 1
fi

if [[ -n "${TANK_SIZE_OVERRIDE}" ]]; then
  read -r tank_size_x tank_size_y tank_size_z tank_size_unit tank_size_extra <<< "${TANK_SIZE_OVERRIDE}"
  if [[ -z "${tank_size_x:-}" || -z "${tank_size_y:-}" || -z "${tank_size_z:-}" || -z "${tank_size_unit:-}" || -n "${tank_size_extra:-}" ]]; then
    echo "Invalid --tank-size: ${TANK_SIZE_OVERRIDE}. Use quoted form like \"10 10 0.8 cm\"." >&2
    exit 1
  fi
fi

if [[ -n "${TANK_SIZE_PRESET_OVERRIDE}" ]]; then
  case "${TANK_SIZE_PRESET_OVERRIDE}" in
    5x5x0p4|5x5x0p8|5x5x1p6|10x10x0p4|10x10x0p8|10x10x1p6)
      ;;
    *)
      echo "Invalid --tank-size-preset: ${TANK_SIZE_PRESET_OVERRIDE}." >&2
      echo "Use 5x5x0p4, 5x5x0p8, 5x5x1p6, 10x10x0p4, 10x10x0p8, or 10x10x1p6." >&2
      exit 1
      ;;
  esac
fi

if [[ -n "${ELECTRON_ENERGY_MODE_OVERRIDE}" ]]; then
  case "${ELECTRON_ENERGY_MODE_OVERRIDE}" in
    fixed|sr90Beta|sr90)
      ;;
    *)
      echo "Invalid --electron-energy-mode: ${ELECTRON_ENERGY_MODE_OVERRIDE}. Use fixed or sr90Beta." >&2
      exit 1
      ;;
  esac
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

macro_has_command() {
  local command="$1"
  awk -v cmd="${command}" '$1 == cmd { found = 1; exit } END { exit(found ? 0 : 1) }' \
    "${TEMPLATE_MACRO}"
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

require_number() {
  local option="$1"
  local value="$2"
  if [[ -z "${value}" || ! "${value}" =~ ^-?([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]]; then
    echo "Invalid ${option}: ${value}. Expected a number." >&2
    exit 1
  fi
}

require_number "ROOT_PLOT_FIDUCIAL_LIMIT_MM" "${ROOT_PLOT_FIDUCIAL_LIMIT_MM}"

axis_values() {
  local axis="$1"
  local min_value="$2"
  local max_value="$3"
  local step_value="$4"
  awk -v axis="${axis}" -v min="${min_value}" -v max="${max_value}" -v step="${step_value}" '
    BEGIN {
      if (step <= 0) {
        printf "Invalid --step: %s. It must be greater than 0.\n", step > "/dev/stderr"
        exit 1
      }
      if (max < min) {
        printf "Invalid %s range: max %s is smaller than min %s.\n", axis, max, min > "/dev/stderr"
        exit 1
      }
      span = max - min
      n_float = span / step
      n = int(n_float + 0.5)
      diff = n_float - n
      if (diff < 0) {
        diff = -diff
      }
      if (diff > 1e-8) {
        printf "Invalid %s range: (%s - %s) is not divisible by step %s.\n", axis, max, min, step > "/dev/stderr"
        exit 1
      }
      for (i = 0; i <= n; ++i) {
        v = min + i * step
        if (v > -1e-12 && v < 1e-12) {
          v = 0
        }
        if (i > 0) {
          printf " "
        }
        printf "%.10g", v
      }
    }'
}

length_to_unit() {
  local value="$1"
  local from_unit="$2"
  local to_unit="$3"
  awk -v v="${value}" -v from="${from_unit}" -v to="${to_unit}" '
    BEGIN {
      if (from == "mm") {
        mm = v
      }
      else if (from == "cm") {
        mm = v * 10.
      }
      else {
        printf "Unsupported length unit: %s\n", from > "/dev/stderr"
        exit 1
      }

      if (to == "mm") {
        out = mm
      }
      else if (to == "cm") {
        out = mm / 10.
      }
      else {
        printf "Unsupported length unit: %s\n", to > "/dev/stderr"
        exit 1
      }
      printf "%.10g", out
    }'
}

infer_custom_beam_z() {
  local thickness_value thickness_unit thickness_grid offset_grid

  if [[ -n "${TANK_SIZE_OVERRIDE}" ]]; then
    thickness_value="${tank_size_z}"
    thickness_unit="${tank_size_unit}"
  elif [[ -n "${TANK_SIZE_PRESET_OVERRIDE}" ]]; then
    thickness_unit="cm"
    case "${TANK_SIZE_PRESET_OVERRIDE}" in
      5x5x0p4|10x10x0p4)
        thickness_value="0.4"
        ;;
      5x5x0p8|10x10x0p8)
        thickness_value="0.8"
        ;;
      5x5x1p6|10x10x1p6)
        thickness_value="1.6"
        ;;
      *)
        echo "Invalid --tank-size-preset: ${TANK_SIZE_PRESET_OVERRIDE}" >&2
        exit 1
        ;;
    esac
  else
    thickness_value="5"
    thickness_unit="mm"
  fi

  thickness_grid="$(length_to_unit "${thickness_value}" "${thickness_unit}" "${GRID_UNIT}")"
  offset_grid="$(length_to_unit "1.5" "mm" "${GRID_UNIT}")"
  awk -v t="${thickness_grid}" -v dz="${offset_grid}" 'BEGIN { printf "%.10g", 0.5 * t + dz }'
}

if [[ "${SOURCE_MODE}" == "auto" ]]; then
  if macro_has_command "/gps/pos/centre"; then
    SOURCE_MODE="gps"
  elif macro_has_command "/gun/position"; then
    SOURCE_MODE="gun"
  else
    echo "Could not auto-detect source mode from ${TEMPLATE_MACRO}." >&2
    echo "Expected /gps/pos/centre or /gun/position." >&2
    exit 1
  fi
fi

if [[ "${SOURCE_MODE}" == "gps" && -n "${ELECTRON_ENERGY_MODE_OVERRIDE}" &&
      "${ELECTRON_ENERGY_MODE_OVERRIDE}" != "fixed" ]]; then
  echo "GPS scan mode currently supports fixed energy only; Sr-90 GPS is a Week 10 task." >&2
  exit 1
fi

case "${SOURCE_MODE}" in
  gps)
    position_cmd="/gps/pos/centre"
    direction_cmd="/gps/direction"
    particle_cmd="/gps/particle"
    energy_cmd="/gps/energy"
    ;;
  gun)
    position_cmd="/gun/position"
    direction_cmd="/gun/direction"
    particle_cmd="/gun/particle"
    energy_cmd="/gun/energy"
    ;;
esac

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
    GRID_STEP="1"
    Z0="0.4"
    XS=(-4 -3 -2 -1 0 1 2 3 4)
    YS=(-4 -3 -2 -1 0 1 2 3 4)
    ;;
  custom)
    require_number "--x-min" "${CUSTOM_X_MIN}"
    require_number "--x-max" "${CUSTOM_X_MAX}"
    require_number "--y-min" "${CUSTOM_Y_MIN}"
    require_number "--y-max" "${CUSTOM_Y_MAX}"
    require_number "--step" "${CUSTOM_STEP}"
    case "${CUSTOM_GRID_UNIT}" in
      mm|cm)
        ;;
      "")
        echo "Missing value for --grid-unit. Use mm or cm." >&2
        exit 1
        ;;
      *)
        echo "Invalid --grid-unit: ${CUSTOM_GRID_UNIT}. Use mm or cm." >&2
        exit 1
        ;;
    esac
    GRID_UNIT="${CUSTOM_GRID_UNIT}"
    GRID_STEP="${CUSTOM_STEP}"
    if [[ -n "${CUSTOM_BEAM_Z}" ]]; then
      require_number "--beam-z" "${CUSTOM_BEAM_Z}"
      Z0="${CUSTOM_BEAM_Z}"
    else
      Z0="$(infer_custom_beam_z)"
      BEAM_Z_INFERRED="1"
    fi
    x_values="$(axis_values x "${CUSTOM_X_MIN}" "${CUSTOM_X_MAX}" "${CUSTOM_STEP}")"
    y_values="$(axis_values y "${CUSTOM_Y_MIN}" "${CUSTOM_Y_MAX}" "${CUSTOM_STEP}")"
    read -r -a XS <<< "${x_values}"
    read -r -a YS <<< "${y_values}"
    SCAN_NAME="${#XS[@]}x${#YS[@]} ${SCAN_LABEL} custom scan"
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

X_MIN_VALUE="${XS[0]}"
X_MAX_VALUE="${XS[$((${#XS[@]} - 1))]}"
Y_MIN_VALUE="${YS[0]}"
Y_MAX_VALUE="${YS[$((${#YS[@]} - 1))]}"
POINT_COUNT=$(( ${#XS[@]} * ${#YS[@]} ))

if [[ "${SOURCE_MODE}" == "gps" ]]; then
  SCAN_NAME="${SCAN_NAME} (GPS source)"
  PREFIX="${PREFIX_ROOT}_gps_${GRID}"
else
  PREFIX="${PREFIX_ROOT}_${GRID}"
fi
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
EFFICIENCY_MAP_CSV="${RUN_DIR}/efficiency_map.csv"

template_output="$(macro_value_after "/analysis/setFileName")"
template_position="$(macro_value_after "${position_cmd}")"
template_direction="$(macro_value_after "${direction_cmd}")"
template_beam_on="$(macro_value_after "/run/beamOn")"
primary_particle="$(macro_value_after "${particle_cmd}")"
primary_energy="$(macro_value_after "${energy_cmd}")"
electron_energy_mode="$(macro_value_after "/opnovice2/gun/electronEnergyMode")"
sipm_face="$(macro_value_after "/opnovice2/sipm/face")"
sipm_cavity_mode="$(macro_value_after "/opnovice2/sipm/cavityMode")"
sipm_local="$(macro_value_after "/opnovice2/sipm/localPosition")"
template_tank_size="$(macro_value_after "/opnovice2/tank/size")"
template_tank_size_preset="$(macro_value_after "/opnovice2/tank/sizePreset")"
template_electron_energy_mode="${electron_energy_mode}"
template_sipm_face="${sipm_face}"
template_sipm_cavity_mode="${sipm_cavity_mode}"
template_sipm_local="${sipm_local}"
if [[ "${SOURCE_MODE}" == "gps" ]]; then
  electron_energy_mode="fixed"
elif [[ -z "${electron_energy_mode}" ]]; then
  electron_energy_mode="fixed"
fi
if [[ -n "${ELECTRON_ENERGY_MODE_OVERRIDE}" ]]; then
  if [[ "${ELECTRON_ENERGY_MODE_OVERRIDE}" == "sr90" ]]; then
    electron_energy_mode="sr90Beta"
  else
    electron_energy_mode="${ELECTRON_ENERGY_MODE_OVERRIDE}"
  fi
fi
if [[ -n "${SIPM_FACE_OVERRIDE}" ]]; then
  sipm_face="${SIPM_FACE_OVERRIDE}"
fi
if [[ -n "${SIPM_CAVITY_MODE_OVERRIDE}" ]]; then
  sipm_cavity_mode="${SIPM_CAVITY_MODE_OVERRIDE}"
fi
if [[ -n "${SIPM_LOCAL_POSITION_OVERRIDE}" ]]; then
  sipm_local="${SIPM_LOCAL_POSITION_OVERRIDE}"
fi
tank_size="${template_tank_size}"
tank_size_preset="${template_tank_size_preset}"
if [[ -n "${TANK_SIZE_OVERRIDE}" ]]; then
  tank_size="${TANK_SIZE_OVERRIDE}"
  tank_size_preset=""
fi
if [[ -n "${TANK_SIZE_PRESET_OVERRIDE}" ]]; then
  tank_size=""
  tank_size_preset="${TANK_SIZE_PRESET_OVERRIDE}"
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

if [[ -z "${template_output}" || -z "${template_position}" || -z "${template_direction}" || -z "${template_beam_on}" || -z "${template_sipm_face}" || -z "${template_sipm_local}" ]]; then
  echo "Could not parse output/source/beamOn/SiPM commands from ${TEMPLATE_MACRO}" >&2
  echo "Source mode: ${SOURCE_MODE}; expected ${position_cmd}, ${direction_cmd}, ${particle_cmd}, ${energy_cmd}." >&2
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
    printf '  "source_mode": "%s",\n' "$(json_string "${SOURCE_MODE}")"
    printf '  "run_id": "%s",\n' "$(json_string "${RUN_ID}")"
    printf '  "run_dir": "%s",\n' "$(json_string "${RUN_DIR}")"
    printf '  "dry_run": %s,\n' "$(if [[ "${DRY_RUN}" == "1" ]]; then echo true; else echo false; fi)"
    printf '  "overrides": {\n'
    printf '    "sipm_face": %s,\n' "$(if [[ -n "${SIPM_FACE_OVERRIDE}" ]]; then echo true; else echo false; fi)"
    printf '    "sipm_cavity_mode": %s,\n' "$(if [[ -n "${SIPM_CAVITY_MODE_OVERRIDE}" ]]; then echo true; else echo false; fi)"
    printf '    "sipm_local_position": %s,\n' "$(if [[ -n "${SIPM_LOCAL_POSITION_OVERRIDE}" ]]; then echo true; else echo false; fi)"
    printf '    "tank_size": %s,\n' "$(if [[ -n "${TANK_SIZE_OVERRIDE}" ]]; then echo true; else echo false; fi)"
    printf '    "tank_size_preset": %s,\n' "$(if [[ -n "${TANK_SIZE_PRESET_OVERRIDE}" ]]; then echo true; else echo false; fi)"
    printf '    "electron_energy_mode": %s\n' "$(if [[ -n "${ELECTRON_ENERGY_MODE_OVERRIDE}" ]]; then echo true; else echo false; fi)"
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
    printf '    "electron_energy_mode": "%s",\n' "$(json_string "${electron_energy_mode}")"
    printf '    "beam_direction": "%s",\n' "$(json_string "${BEAM_DIRECTION}")"
    printf '    "beam_z": "%s %s",\n' "$(format_num "${Z0}")" "$(json_string "${GRID_UNIT}")"
    printf '    "beam_z_inferred": %s\n' "$(if [[ "${BEAM_Z_INFERRED}" == "1" ]]; then echo true; else echo false; fi)"
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
    if [[ "${GRID}" == "near5" && -n "${GRID_STEP:-}" ]]; then
      printf '    "near_field_step": "%s %s"\n' "$(format_num "${GRID_STEP}")" "$(json_string "${GRID_UNIT}")"
    else
      printf '    "near_field_step": null\n'
    fi
    printf '  },\n'
    printf '  "tank": {\n'
    printf '    "bottom_cavity": "%s",\n' "$(json_string "${bottom_cavity}")"
    printf '    "size": '
    if [[ -n "${tank_size}" ]]; then
      printf '"%s"' "$(json_string "${tank_size}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "size_preset": '
    if [[ -n "${tank_size_preset}" ]]; then
      printf '"%s"' "$(json_string "${tank_size_preset}")"
    else
      printf 'null'
    fi
    printf '\n'
    printf '  },\n'
    printf '  "grid": {\n'
    printf '    "unit": "%s",\n' "$(json_string "${GRID_UNIT}")"
    printf '    "step": %s,\n' "$(format_num "${GRID_STEP}")"
    printf '    "x_min": %s,\n' "$(format_num "${X_MIN_VALUE}")"
    printf '    "x_max": %s,\n' "$(format_num "${X_MAX_VALUE}")"
    printf '    "y_min": %s,\n' "$(format_num "${Y_MIN_VALUE}")"
    printf '    "y_max": %s,\n' "$(format_num "${Y_MAX_VALUE}")"
    printf '    "point_count": %s,\n' "${POINT_COUNT}"
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
    printf '    "efficiency_map_csv": "%s",\n' "$(json_string "${EFFICIENCY_MAP_CSV}")"
    printf '    "latest_efficiency_map_csv": "%s",\n' "$(json_string "${LATEST_EFFICIENCY_MAP}")"
    printf '    "root_plot_enabled": %s,\n' "$(if [[ "${PLOT_WITH_ROOT}" == "1" ]]; then echo true; else echo false; fi)"
    printf '    "root_plot_macro": "%s",\n' "$(json_string "${ROOT_PLOT_MACRO}")"
    printf '    "root_plot_fiducial_limit_mm": %s,\n' "$(format_num "${ROOT_PLOT_FIDUCIAL_LIMIT_MM}")"
    printf '    "latest_run_link": "%s"\n' "$(json_string "${LATEST_RUN_LINK}")"
    printf '  }\n'
    printf '}\n'
  } > "${RUN_CONFIG}"
}

write_efficiency_map() {
  {
    printf 'tag,x,y,z,unit,events,generated_optical_photons,scintillation_photons,sipm_detected_photons,collection_efficiency,shoot_position_events,shoot_x_mm,shoot_y_mm,shoot_z_mm,hit_position_events,hit_x_mm,hit_y_mm,hit_z_mm,scint_centroid_events,scint_centroid_x_mm,scint_centroid_y_mm,scint_centroid_z_mm,summary_csv,root,log\n'
    read -r _points_header
    while IFS=, read -r tag x y z unit macro root log; do
      summary="${root%.root}_summary.csv"
      if [[ ! -f "${summary}" ]]; then
        echo "Missing scan summary CSV: ${summary}" >&2
        echo "Point: ${tag}" >&2
        exit 1
      fi
      read -r _summary_header < "${summary}"
      read -r events generated scint detected efficiency shoot_events shoot_x shoot_y shoot_z hit_events hit_x hit_y hit_z scint_centroid_events scint_centroid_x scint_centroid_y scint_centroid_z < <(
        awk -F, 'NR == 2 {
          print $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17
        }' "${summary}"
      )
      if [[ -z "${events:-}" || -z "${generated:-}" || -z "${scint:-}" || -z "${detected:-}" || -z "${efficiency:-}" ]]; then
        echo "Could not parse scan summary CSV: ${summary}" >&2
        exit 1
      fi
      printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
        "${tag}" "${x}" "${y}" "${z}" "${unit}" \
        "${events}" "${generated}" "${scint}" "${detected}" "${efficiency}" \
        "${shoot_events}" "${shoot_x}" "${shoot_y}" "${shoot_z}" \
        "${hit_events}" "${hit_x}" "${hit_y}" "${hit_z}" \
        "${scint_centroid_events}" "${scint_centroid_x}" "${scint_centroid_y}" "${scint_centroid_z}" \
        "${summary}" "${root}" "${log}"
    done
  } < "${POINTS_CSV}" > "${EFFICIENCY_MAP_CSV}"
}

generate_root_plots() {
  if [[ "${PLOT_WITH_ROOT}" != "1" ]]; then
    echo "ROOT plot generation disabled: PLOT_WITH_ROOT=0"
    return 0
  fi
  if [[ "${ROOT_PLOT_SKIP_LOCAL}" == "1" ]]; then
    echo "ROOT plot generation deferred to caller."
    return 0
  fi
  if [[ ! -f "${ROOT_PLOT_MACRO}" ]]; then
    echo "ROOT plot generation skipped: macro not found: ${ROOT_PLOT_MACRO}" >&2
    return 0
  fi
  if ! command -v "${ROOT_COMMAND}" >/dev/null 2>&1; then
    echo "ROOT plot generation skipped: ROOT command not found: ${ROOT_COMMAND}" >&2
    echo "Set ROOT_COMMAND=/path/to/root or PLOT_WITH_ROOT=0." >&2
    return 0
  fi

  echo "Generating ROOT plots with ${ROOT_PLOT_MACRO}"
  "${ROOT_COMMAND}" -b -q "${ROOT_PLOT_MACRO}(\"${RUN_DIR}\",${ROOT_PLOT_FIDUCIAL_LIMIT_MM})"
  echo "ROOT plots: ${RUN_DIR}/root_*.png and ${RUN_DIR}/root_*.pdf"
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
echo "Source mode: ${SOURCE_MODE}"
echo "Run directory: ${RUN_DIR}"
echo "Grid unit: ${GRID_UNIT}; x=($(json_number_array "${XS[@]}")); y=($(json_number_array "${YS[@]}"))"
if [[ "${BEAM_Z_INFERRED}" == "1" ]]; then
  echo "Beam z: $(format_num "${Z0}") ${GRID_UNIT} (inferred)"
else
  echo "Beam z: $(format_num "${Z0}") ${GRID_UNIT}"
fi
echo "Events per point: ${N_EVENTS}"
echo "Electron energy mode: ${electron_energy_mode}"
if [[ -n "${tank_size}" ]]; then
  echo "Tank size: ${tank_size}"
fi
if [[ -n "${tank_size_preset}" ]]; then
  echo "Tank size preset: ${tank_size_preset}"
fi
echo "Metadata: ${RUN_CONFIG}, ${POINTS_CSV}"
echo "Latest pointers: ${LATEST_RUN_LINK}, ${LATEST_RUN_CONFIG}, ${LATEST_POINTS_CSV}"

tail -n +2 "${POINTS_CSV}" | while IFS=, read -r tag x y z unit macro root log; do
  outfile="${root%.root}"

  sed_args=(
    -e "s|/analysis/setFileName ${template_output}|/analysis/setFileName ${outfile}|" \
    -e "s|${position_cmd} ${template_position}|${position_cmd} ${x} ${y} ${z} ${unit}|" \
    -e "s|${direction_cmd} ${template_direction}|${direction_cmd} ${BEAM_DIRECTION}|" \
    -e "s|/run/beamOn ${template_beam_on}|/run/beamOn ${N_EVENTS}|" \
    -e "s|/opnovice2/sipm/face ${template_sipm_face}|/opnovice2/sipm/face ${sipm_face}|" \
    -e "s|/opnovice2/sipm/localPosition ${template_sipm_local}|/opnovice2/sipm/localPosition ${sipm_local}|"
  )

  if [[ -n "${template_sipm_cavity_mode}" && -n "${sipm_cavity_mode}" ]]; then
    sed_args+=(-e "s|/opnovice2/sipm/cavityMode ${template_sipm_cavity_mode}|/opnovice2/sipm/cavityMode ${sipm_cavity_mode}|")
  fi
  if [[ -n "${template_tank_size}" && -n "${tank_size}" ]]; then
    sed_args+=(-e "s|/opnovice2/tank/size ${template_tank_size}|/opnovice2/tank/size ${tank_size}|")
  fi
  if [[ -n "${template_tank_size_preset}" && -n "${tank_size_preset}" ]]; then
    sed_args+=(-e "s|/opnovice2/tank/sizePreset ${template_tank_size_preset}|/opnovice2/tank/sizePreset ${tank_size_preset}|")
  fi

  if [[ "${MODE}" == "full" && -n "${template_bottom_cavity}" ]]; then
    sed_args+=(-e "s|/opnovice2/tank/bottomCavity ${template_bottom_cavity}|/opnovice2/tank/bottomCavity false|")
  fi

  bottom_cavity_inject=""
  if [[ "${MODE}" == "full" && -z "${template_bottom_cavity}" ]]; then
    bottom_cavity_inject="/opnovice2/tank/bottomCavity false"
  fi
  tank_size_inject=""
  if [[ -n "${TANK_SIZE_OVERRIDE}" && -z "${template_tank_size}" ]]; then
    tank_size_inject="/opnovice2/tank/size ${tank_size}"
  elif [[ -n "${TANK_SIZE_PRESET_OVERRIDE}" && -z "${template_tank_size_preset}" ]]; then
    tank_size_inject="/opnovice2/tank/sizePreset ${tank_size_preset}"
  fi
  electron_energy_cmd=""
  if [[ "${SOURCE_MODE}" == "gun" ]]; then
    electron_energy_cmd="/opnovice2/gun/electronEnergyMode ${electron_energy_mode}"
  fi

  sed "${sed_args[@]}" "${TEMPLATE_MACRO}" | awk \
    -v bottom_cavity_inject="${bottom_cavity_inject}" \
    -v tank_size_inject="${tank_size_inject}" \
    -v electron_energy_cmd="${electron_energy_cmd}" '
      !tank_size_inserted && tank_size_inject != "" && $1 == "/run/initialize" {
        print tank_size_inject
        tank_size_inserted = 1
      }
      !bottom_cavity_inserted && bottom_cavity_inject != "" && $1 == "/opnovice2/sipm/face" {
        print bottom_cavity_inject
        bottom_cavity_inserted = 1
      }
      $1 == "/opnovice2/gun/electronEnergyMode" {
        if (!electron_energy_written && electron_energy_cmd != "") {
          print electron_energy_cmd
        }
        electron_energy_written = 1
        next
      }
      { print }
      !electron_energy_written && electron_energy_cmd != "" && $1 == "/gun/energy" {
        print electron_energy_cmd
        electron_energy_written = 1
      }
    ' > "${macro}"

  if [[ -n "${electron_energy_cmd}" ]] && ! grep -qxF "${electron_energy_cmd}" "${macro}"; then
    echo "Generated macro is missing expected electron energy mode: ${electron_energy_cmd}" >&2
    echo "Macro: ${macro}" >&2
    exit 1
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
  write_efficiency_map
  cp "${EFFICIENCY_MAP_CSV}" "${LATEST_EFFICIENCY_MAP}"
  echo "Efficiency map: ${EFFICIENCY_MAP_CSV}"
  echo "Latest efficiency map: ${LATEST_EFFICIENCY_MAP}"
  generate_root_plots
  echo "Scan complete."
fi
