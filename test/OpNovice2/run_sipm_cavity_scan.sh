#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

ORIGINAL_ARGS=("$@")

N_EVENTS="${N_EVENTS:-100}"
DRY_RUN="${DRY_RUN:-0}"
PLOT_WITH_ROOT="${PLOT_WITH_ROOT:-1}"
ROOT_COMMAND="${ROOT_COMMAND:-root}"
ROOT_PLOT_MACRO="plot_efficiency_map.C"
ROOT_PLOT_FIDUCIAL_LIMIT_MM="${ROOT_PLOT_FIDUCIAL_LIMIT_MM:-45}"
ROOT_PLOT_SKIP_LOCAL="${ROOT_PLOT_SKIP_LOCAL:-0}"
OPNOVICE2_EXECUTABLE="${OPNOVICE2_EXECUTABLE:-./build/OpNovice2}"
SCAN_LOG_TAIL_LINES="${SCAN_LOG_TAIL_LINES:-120}"
SCAN_RUN_ID_SUFFIX="${SCAN_RUN_ID_SUFFIX:-}"
MACRO_GENERATOR="generate_scan_macro.py"

MODE="surface"
GRID="near5"
SOURCE_MODE="${SOURCE_MODE:-auto}"
TEMPLATE_MACRO_OVERRIDE=""
PRIMARY_ENERGY_OVERRIDE=""
SOURCE_MODEL_OVERRIDE=""
SIPM_FACE_OVERRIDE=""
SIPM_CAVITY_MODE_OVERRIDE=""
SIPM_LOCAL_POSITION_OVERRIDE=""
TANK_SIZE_OVERRIDE=""
TANK_SIZE_PRESET_OVERRIDE=""
ELECTRON_ENERGY_MODE_OVERRIDE=""
SURFACE_PRESET_OVERRIDE=""
DIMPLE_ENABLED="0"
DIMPLE_RADIUS=""
DIMPLE_UNIT="mm"
DIMPLE_SIPM_MODE="surface"
DIMPLE_RADIUS_SET="0"
DIMPLE_UNIT_SET="0"
DIMPLE_SIPM_MODE_SET="0"
CUSTOM_X_MIN=""
CUSTOM_X_MAX=""
CUSTOM_Y_MIN=""
CUSTOM_Y_MAX=""
CUSTOM_STEP=""
CUSTOM_GRID_UNIT=""
CUSTOM_BEAM_Z=""
CUSTOM_BEAM_SIGMA=""
CUSTOM_BEAM_DIVERGENCE_MRAD=""
BEAM_Z_INFERRED="0"
BEAM_PROFILE="point"
BEAM_SIGMA=""
BEAM_ANGULAR_MODEL="pencil"
BEAM_DIVERGENCE_MRAD=""

BEAM_DIRECTION="0 0 -1"
SCAN_RUNS_DIR="scan_runs"
LATEST_RUN_LINK="scan_latest"
LATEST_POINTS_CSV="points.csv"
LATEST_RUN_CONFIG="run_config.json"
LATEST_EFFICIENCY_MAP="efficiency_map.csv"

SR90_SPECTRUM_MODEL="sr90_allowed_beta_v1"
SR90_SPECTRUM_TABLE="spectra/sr90_allowed_beta_v1.csv"
SR90_SPECTRUM_GPS_MACRO="spectra/sr90_allowed_beta_v1_gps.mac"
SR90_ENDPOINT_MEV="0.546"
Y90_ENDPOINT_MEV="2.28"
SR90_ACTIVITY_WEIGHT="1.0"
Y90_ACTIVITY_WEIGHT="1.0"
SR90_FERMI_CORRECTION="true"
SR90_INCIDENT_MODEL="bare_sr90_y90_beta_emission_no_encapsulation_air_or_collimator"
SR90_DECAY_MODEL="geant4_radioactive_decay_sr90_chain_v1"
SR90_DECAY_PRIMARY_ION="Sr-90"
SR90_DECAY_GPS_ION="38 90 0 0"
SR90_DECAY_RDM_TIME_THRESHOLD="1000 year"
SR90_DECAY_EXPECTED_CHAIN="Sr-90 -> Y-90 -> Zr-90"

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

Source options:
  --source-mode MODE                  auto, gun, or gps
  --source-model MODEL                fixed-electron, sr90-spectrum,
                                      sr90-empirical, or sr90-decay
  --template-macro FILE               override the selected template macro
  --primary-energy "VALUE UNIT"       override primary energy, e.g. "0.5 MeV"
  --electron-energy-mode MODE         fixed, sr90Spectrum, sr90Beta, sr90, or sr90Empirical

Surface options:
  --surface-preset PRESET             polished, ground, or wrapped

Geometry options:
  --sipm-face FACE                    +X, -X, +Y, -Y, +Z, -Z, or bottomCavity
  --sipm-cavity-mode MODE             surface or opening
  --sipm-local-position "x y z unit"  override /opnovice2/sipm/localPosition
  --tank-size "x y z unit"            override full tank size, e.g. "10 10 0.8 cm"
  --tank-size-preset PRESET           5x5x0p4, 5x5x0p8, 5x5x1p6,
                                      10x10x0p4, 10x10x0p8, or 10x10x1p6
  --dimple                            enable Week 8.1 strict hemispherical dimple
  --dimple-radius VALUE               dimple radius/depth; required with --dimple
  --dimple-unit UNIT                  mm or cm; default mm
  --dimple-sipm-mode MODE             surface or opening; default surface

Grid / beam options:
  --x-min VALUE                       minimum x coordinate
  --x-max VALUE                       maximum x coordinate
  --y-min VALUE                       minimum y coordinate
  --y-max VALUE                       maximum y coordinate
  --step VALUE                        grid spacing; must evenly divide both ranges
  --grid-unit UNIT                    mm or cm
  --beam-z VALUE                      beam z coordinate in --grid-unit units;
                                      custom defaults to thickness/2 + 1.5 mm
  --beam-sigma VALUE                  circular Gaussian GPS beam sigma in
                                      --grid-unit units; 0 or omitted keeps
                                      the point-source GPS position
  --beam-divergence-mrad VALUE        GPS beam angular sigma_r in mrad;
                                      0 or omitted keeps the pencil beam

Output / execution options:
  --events N                          events per scan point; overrides N_EVENTS
  --dry-run                           generate macros/config only
  --no-root-plots                     skip ROOT quick-look plot generation
  --root-command PATH                 ROOT executable used for plot generation

Environment:
  N_EVENTS=100                        events per scan point
  DRY_RUN=1                           generate macros/config only
  SOURCE_MODE=auto                    source command mode: auto, gun, or gps
  OPNOVICE2_EXECUTABLE=./build/OpNovice2
                                      executable used for each generated macro
  PLOT_WITH_ROOT=1                    generate ROOT macro plots after scan if ROOT is available
  ROOT_COMMAND=root                   ROOT executable used for plot generation
  ROOT_PLOT_FIDUCIAL_LIMIT_MM=45      fiducial box half-width shown by ROOT plots
  SCAN_LOG_TAIL_LINES=120             lines printed from a failed point log
  SCAN_RUN_ID_SUFFIX=value            optional suffix appended to the run directory

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
    --events)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --events" >&2
        exit 1
      fi
      N_EVENTS="$2"
      shift 2
      ;;
    --events=*)
      N_EVENTS="${1#*=}"
      shift
      ;;
    --dry-run)
      DRY_RUN="1"
      shift
      ;;
    --no-root-plots)
      PLOT_WITH_ROOT="0"
      shift
      ;;
    --root-command)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --root-command" >&2
        exit 1
      fi
      ROOT_COMMAND="$2"
      shift 2
      ;;
    --root-command=*)
      ROOT_COMMAND="${1#*=}"
      shift
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
    --source-model)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --source-model" >&2
        exit 1
      fi
      SOURCE_MODEL_OVERRIDE="$2"
      shift 2
      ;;
    --source-model=*)
      SOURCE_MODEL_OVERRIDE="${1#*=}"
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
    --primary-energy)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --primary-energy" >&2
        exit 1
      fi
      PRIMARY_ENERGY_OVERRIDE="$2"
      shift 2
      ;;
    --primary-energy=*)
      PRIMARY_ENERGY_OVERRIDE="${1#*=}"
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
    --surface-preset)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --surface-preset" >&2
        exit 1
      fi
      SURFACE_PRESET_OVERRIDE="$2"
      shift 2
      ;;
    --surface-preset=*)
      SURFACE_PRESET_OVERRIDE="${1#*=}"
      shift
      ;;
    --dimple)
      DIMPLE_ENABLED="1"
      shift
      ;;
    --dimple-radius)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --dimple-radius" >&2
        exit 1
      fi
      DIMPLE_RADIUS="$2"
      DIMPLE_RADIUS_SET="1"
      shift 2
      ;;
    --dimple-radius=*)
      DIMPLE_RADIUS="${1#*=}"
      DIMPLE_RADIUS_SET="1"
      shift
      ;;
    --dimple-unit)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --dimple-unit" >&2
        exit 1
      fi
      DIMPLE_UNIT="$2"
      DIMPLE_UNIT_SET="1"
      shift 2
      ;;
    --dimple-unit=*)
      DIMPLE_UNIT="${1#*=}"
      DIMPLE_UNIT_SET="1"
      shift
      ;;
    --dimple-sipm-mode)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --dimple-sipm-mode" >&2
        exit 1
      fi
      DIMPLE_SIPM_MODE="$2"
      DIMPLE_SIPM_MODE_SET="1"
      shift 2
      ;;
    --dimple-sipm-mode=*)
      DIMPLE_SIPM_MODE="${1#*=}"
      DIMPLE_SIPM_MODE_SET="1"
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
    --beam-sigma)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --beam-sigma" >&2
        exit 1
      fi
      CUSTOM_BEAM_SIGMA="$2"
      shift 2
      ;;
    --beam-sigma=*)
      CUSTOM_BEAM_SIGMA="${1#*=}"
      shift
      ;;
    --beam-divergence-mrad)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --beam-divergence-mrad" >&2
        exit 1
      fi
      CUSTOM_BEAM_DIVERGENCE_MRAD="$2"
      shift 2
      ;;
    --beam-divergence-mrad=*)
      CUSTOM_BEAM_DIVERGENCE_MRAD="${1#*=}"
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

case "${DRY_RUN}" in
  1|true|TRUE|yes|YES|on|ON)
    DRY_RUN="1"
    ;;
  0|false|FALSE|no|NO|off|OFF)
    DRY_RUN="0"
    ;;
  *)
    echo "Invalid DRY_RUN: ${DRY_RUN}. Use 1 or 0." >&2
    exit 1
    ;;
esac

if [[ ! "${N_EVENTS}" =~ ^[0-9]+$ || "${N_EVENTS}" -le 0 ]]; then
  echo "Invalid --events/N_EVENTS: ${N_EVENTS}. Expected a positive integer." >&2
  exit 1
fi

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

if [[ "${DIMPLE_ENABLED}" == "1" && "${MODE}" != "full" ]]; then
  echo "--dimple is supported only with MODE=full. Legacy surface/opening modes use /opnovice2/tank/bottomCavity." >&2
  exit 1
fi

case "${SOURCE_MODE}" in
  auto|gun|gps)
    ;;
  *)
    echo "Invalid --source-mode: ${SOURCE_MODE}. Use auto, gun, or gps." >&2
    exit 1
    ;;
esac

if [[ -n "${SOURCE_MODEL_OVERRIDE}" ]]; then
  case "${SOURCE_MODEL_OVERRIDE}" in
    fixed-electron|sr90-spectrum|sr90-empirical|sr90-decay)
      ;;
    *)
      echo "Invalid --source-model: ${SOURCE_MODEL_OVERRIDE}. Use fixed-electron, sr90-spectrum, sr90-empirical, or sr90-decay." >&2
      exit 1
      ;;
  esac
fi

if [[ -n "${CUSTOM_BEAM_SIGMA}" && "${SOURCE_MODE}" != "gps" ]]; then
  echo "--beam-sigma requires --source-mode gps." >&2
  exit 1
fi

if [[ -n "${CUSTOM_BEAM_DIVERGENCE_MRAD}" && "${SOURCE_MODE}" != "gps" ]]; then
  echo "--beam-divergence-mrad requires --source-mode gps." >&2
  exit 1
fi

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
    fixed|sr90Spectrum|sr90Beta|sr90|sr90Empirical)
      ;;
    *)
      echo "Invalid --electron-energy-mode: ${ELECTRON_ENERGY_MODE_OVERRIDE}. Use fixed, sr90Spectrum, sr90Beta, sr90, or sr90Empirical." >&2
      exit 1
      ;;
  esac
fi

if [[ -n "${PRIMARY_ENERGY_OVERRIDE}" ]]; then
  read -r primary_energy_value primary_energy_unit primary_energy_extra <<< "${PRIMARY_ENERGY_OVERRIDE}"
  if [[ -z "${primary_energy_value:-}" || -z "${primary_energy_unit:-}" || -n "${primary_energy_extra:-}" ]]; then
    echo "Invalid --primary-energy: ${PRIMARY_ENERGY_OVERRIDE}. Use quoted form like \"0.5 MeV\"." >&2
    exit 1
  fi
  if [[ ! "${primary_energy_value}" =~ ^([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]]; then
    echo "Invalid --primary-energy value: ${primary_energy_value}. Expected a positive number." >&2
    exit 1
  fi
  awk -v value="${primary_energy_value}" '
    BEGIN {
      if (value <= 0) {
        printf "Invalid --primary-energy value: %s. Expected a positive number.\n", value > "/dev/stderr"
        exit 1
      }
    }'
  case "${primary_energy_unit}" in
    eV|keV|MeV|GeV)
      ;;
    *)
      echo "Invalid --primary-energy unit: ${primary_energy_unit}. Use eV, keV, MeV, or GeV." >&2
      exit 1
      ;;
  esac
fi

if [[ -n "${SURFACE_PRESET_OVERRIDE}" ]]; then
  case "${SURFACE_PRESET_OVERRIDE}" in
    polished|ground|wrapped)
      ;;
    *)
      echo "Invalid --surface-preset: ${SURFACE_PRESET_OVERRIDE}. Use polished, ground, or wrapped." >&2
      exit 1
      ;;
  esac
fi

if [[ "${DIMPLE_ENABLED}" != "1" &&
      ( "${DIMPLE_RADIUS_SET}" == "1" || "${DIMPLE_UNIT_SET}" == "1" || "${DIMPLE_SIPM_MODE_SET}" == "1" ) ]]; then
  echo "Dimple options require --dimple." >&2
  exit 1
fi

if [[ "${DIMPLE_ENABLED}" == "1" ]]; then
  if [[ "${MODE}" != "full" ]]; then
    echo "--dimple is supported only with MODE=full. Legacy surface/opening modes use /opnovice2/tank/bottomCavity." >&2
    exit 1
  fi
  if [[ "${DIMPLE_RADIUS_SET}" != "1" ]]; then
    echo "--dimple requires --dimple-radius VALUE." >&2
    exit 1
  fi
  if [[ -z "${DIMPLE_RADIUS}" || ! "${DIMPLE_RADIUS}" =~ ^([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]]; then
    echo "Invalid --dimple-radius: ${DIMPLE_RADIUS}. Expected a positive number." >&2
    exit 1
  fi
  case "${DIMPLE_UNIT}" in
    mm|cm)
      ;;
    *)
      echo "Invalid --dimple-unit: ${DIMPLE_UNIT}. Use mm or cm." >&2
      exit 1
      ;;
  esac
  case "${DIMPLE_SIPM_MODE}" in
    surface|opening)
      ;;
    *)
      echo "Invalid --dimple-sipm-mode: ${DIMPLE_SIPM_MODE}. Use surface or opening." >&2
      exit 1
      ;;
  esac
  if [[ -n "${SIPM_FACE_OVERRIDE}" && "${SIPM_FACE_OVERRIDE}" != "-Z" ]]; then
    echo "--dimple supports only --sipm-face -Z in Week 8.1." >&2
    exit 1
  fi
  if [[ -n "${SIPM_CAVITY_MODE_OVERRIDE}" ]]; then
    if [[ "${DIMPLE_SIPM_MODE_SET}" == "1" && "${SIPM_CAVITY_MODE_OVERRIDE}" != "${DIMPLE_SIPM_MODE}" ]]; then
      echo "Conflicting dimple SiPM modes: --dimple-sipm-mode ${DIMPLE_SIPM_MODE} but legacy --sipm-cavity-mode ${SIPM_CAVITY_MODE_OVERRIDE}." >&2
      exit 1
    fi
    if [[ "${DIMPLE_SIPM_MODE_SET}" != "1" ]]; then
      DIMPLE_SIPM_MODE="${SIPM_CAVITY_MODE_OVERRIDE}"
    fi
  fi
fi

if [[ ! -f "${TEMPLATE_MACRO}" ]]; then
  echo "Template macro not found: ${TEMPLATE_MACRO}" >&2
  exit 1
fi

if [[ ! -f "${MACRO_GENERATOR}" ]]; then
  echo "Macro generator not found: ${MACRO_GENERATOR}" >&2
  exit 1
fi

if [[ "${DRY_RUN}" != "1" && ! -x "${OPNOVICE2_EXECUTABLE}" ]]; then
  echo "Missing executable: ${OPNOVICE2_EXECUTABLE}" >&2
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

json_string_array() {
  local first=1
  local v
  printf "["
  for v in "$@"; do
    if [[ "${first}" -eq 0 ]]; then
      printf ", "
    fi
    printf '"%s"' "$(json_string "${v}")"
    first=0
  done
  printf "]"
}

shell_join() {
  local first=1
  local v
  for v in "$@"; do
    if [[ "${first}" -eq 0 ]]; then
      printf " "
    fi
    printf "%q" "${v}"
    first=0
  done
}

sanitize_run_id_part() {
  local value="$1"
  value="${value//[^A-Za-z0-9_.-]/_}"
  echo "${value}"
}

require_number() {
  local option="$1"
  local value="$2"
  if [[ -z "${value}" || ! "${value}" =~ ^-?([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]]; then
    echo "Invalid ${option}: ${value}. Expected a number." >&2
    exit 1
  fi
}

require_nonnegative_number() {
  local option="$1"
  local value="$2"
  require_number "${option}" "${value}"
  awk -v option="${option}" -v value="${value}" '
    BEGIN {
      if (value < 0) {
        printf "Invalid %s: %s. Expected a non-negative number.\n", option, value > "/dev/stderr"
        exit 1
      }
    }'
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

if [[ -n "${CUSTOM_BEAM_SIGMA}" ]]; then
  require_nonnegative_number "--beam-sigma" "${CUSTOM_BEAM_SIGMA}"
  if awk -v sigma="${CUSTOM_BEAM_SIGMA}" 'BEGIN { exit(sigma > 0 ? 0 : 1) }'; then
    BEAM_PROFILE="gaussian"
    BEAM_SIGMA="${CUSTOM_BEAM_SIGMA}"
  else
    BEAM_PROFILE="point"
    BEAM_SIGMA=""
  fi
fi

if [[ -n "${CUSTOM_BEAM_DIVERGENCE_MRAD}" ]]; then
  require_nonnegative_number "--beam-divergence-mrad" "${CUSTOM_BEAM_DIVERGENCE_MRAD}"
  if awk -v sigma="${CUSTOM_BEAM_DIVERGENCE_MRAD}" 'BEGIN { exit(sigma > 0 ? 0 : 1) }'; then
    BEAM_ANGULAR_MODEL="beam2d"
    BEAM_DIVERGENCE_MRAD="${CUSTOM_BEAM_DIVERGENCE_MRAD}"
  else
    BEAM_ANGULAR_MODEL="pencil"
    BEAM_DIVERGENCE_MRAD=""
  fi
fi

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
RUN_ID_BASE="${RUN_TIMESTAMP}_${PREFIX}"
if [[ -n "${SCAN_RUN_ID_SUFFIX}" ]]; then
  RUN_ID_BASE="${RUN_ID_BASE}_$(sanitize_run_id_part "${SCAN_RUN_ID_SUFFIX}")"
fi
RUN_ID="${RUN_ID_BASE}"
RUN_DIR="${SCAN_RUNS_DIR}/${RUN_ID}"
mkdir -p "${SCAN_RUNS_DIR}"
run_id_attempt=0
while ! mkdir "${RUN_DIR}" 2>/dev/null; do
  run_id_attempt=$((run_id_attempt + 1))
  RUN_ID="${RUN_ID_BASE}_$$_${run_id_attempt}"
  RUN_DIR="${SCAN_RUNS_DIR}/${RUN_ID}"
done
MACRO_DIR="${RUN_DIR}/macros"
ROOT_DIR="${RUN_DIR}/outputs"
LOG_DIR="${RUN_DIR}/logs"
POINTS_CSV="${RUN_DIR}/points.csv"
RUN_CONFIG="${RUN_DIR}/run_config.json"
EFFICIENCY_MAP_CSV="${RUN_DIR}/efficiency_map.csv"

primary_particle="$(macro_value_after "${particle_cmd}")"
primary_energy="$(macro_value_after "${energy_cmd}")"
electron_energy_mode="$(macro_value_after "/opnovice2/gun/electronEnergyMode")"
sipm_face="$(macro_value_after "/opnovice2/sipm/face")"
sipm_cavity_mode="$(macro_value_after "/opnovice2/sipm/cavityMode")"
sipm_local="$(macro_value_after "/opnovice2/sipm/localPosition")"
template_tank_size="$(macro_value_after "/opnovice2/tank/size")"
template_tank_size_preset="$(macro_value_after "/opnovice2/tank/sizePreset")"
template_surface_model="$(macro_value_after "/opnovice2/surfaceModel")"
template_surface_type="$(macro_value_after "/opnovice2/surfaceType")"
template_surface_finish="$(macro_value_after "/opnovice2/surfaceFinish")"
template_surface_sigma_alpha="$(macro_value_after "/opnovice2/surfaceSigmaAlpha")"
if [[ "${SOURCE_MODE}" == "gps" ]]; then
  electron_energy_mode="fixed"
elif [[ -z "${electron_energy_mode}" ]]; then
  electron_energy_mode="fixed"
fi
source_model="fixed-electron"
if [[ "${electron_energy_mode}" == "sr90Beta" ]]; then
  source_model="sr90-empirical"
fi
if [[ -n "${SOURCE_MODEL_OVERRIDE}" ]]; then
  source_model="${SOURCE_MODEL_OVERRIDE}"
  case "${source_model}" in
    fixed-electron|sr90-spectrum|sr90-decay)
      electron_energy_mode="fixed"
      ;;
    sr90-empirical)
      electron_energy_mode="sr90Beta"
      ;;
  esac
fi
if [[ "${source_model}" == "sr90-decay" && -n "${ELECTRON_ENERGY_MODE_OVERRIDE}" ]]; then
  echo "--electron-energy-mode is incompatible with --source-model sr90-decay; decay mode uses a Sr-90 ion primary." >&2
  exit 1
fi
if [[ "${source_model}" == "sr90-decay" && "${BEAM_ANGULAR_MODEL}" == "beam2d" ]]; then
  echo "--beam-divergence-mrad is incompatible with --source-model sr90-decay; decay beta directions come from radioactive decay physics." >&2
  exit 1
fi
if [[ -n "${ELECTRON_ENERGY_MODE_OVERRIDE}" ]]; then
  case "${ELECTRON_ENERGY_MODE_OVERRIDE}" in
    fixed)
      if [[ -n "${SOURCE_MODEL_OVERRIDE}" && "${source_model}" == "sr90-empirical" ]]; then
        echo "Conflicting source options: --source-model ${source_model} with --electron-energy-mode fixed." >&2
        exit 1
      fi
      if [[ -z "${SOURCE_MODEL_OVERRIDE}" ]]; then
        source_model="fixed-electron"
      fi
      electron_energy_mode="fixed"
      ;;
    sr90Spectrum)
      if [[ -n "${SOURCE_MODEL_OVERRIDE}" && "${source_model}" != "sr90-spectrum" ]]; then
        echo "Conflicting source options: --source-model ${source_model} with --electron-energy-mode sr90Spectrum." >&2
        exit 1
      fi
      source_model="sr90-spectrum"
      electron_energy_mode="fixed"
      ;;
    sr90Beta|sr90|sr90Empirical)
      if [[ -n "${SOURCE_MODEL_OVERRIDE}" && "${source_model}" != "sr90-empirical" ]]; then
        echo "Conflicting source options: --source-model ${source_model} with --electron-energy-mode ${ELECTRON_ENERGY_MODE_OVERRIDE}." >&2
        exit 1
      fi
      source_model="sr90-empirical"
      electron_energy_mode="sr90Beta"
      ;;
  esac
fi
if [[ "${source_model}" == "sr90-spectrum" ]]; then
  if [[ "${SOURCE_MODE}" != "gps" ]]; then
    echo "--source-model sr90-spectrum requires --source-mode gps." >&2
    exit 1
  fi
  if [[ "${primary_particle}" != "e-" ]]; then
    echo "--source-model sr90-spectrum requires primary particle e-, but template resolves ${primary_particle}." >&2
    exit 1
  fi
  if [[ ! -f "${SR90_SPECTRUM_TABLE}" ]]; then
    echo "Missing Sr-90 spectrum table: ${SR90_SPECTRUM_TABLE}. Run python3 generate_sr90_spectrum.py first." >&2
    exit 1
  fi
  if [[ ! -f "${SR90_SPECTRUM_GPS_MACRO}" ]]; then
    echo "Missing Sr-90 GPS histogram fragment: ${SR90_SPECTRUM_GPS_MACRO}. Run python3 generate_sr90_spectrum.py first." >&2
    exit 1
  fi
  if [[ -n "${PRIMARY_ENERGY_OVERRIDE}" ]]; then
    echo "--primary-energy is incompatible with --source-model sr90-spectrum; the GPS histogram defines the energy spectrum." >&2
    exit 1
  fi
  primary_energy="${SR90_SPECTRUM_MODEL} spectrum"
fi
if [[ "${source_model}" == "sr90-decay" ]]; then
  if [[ "${SOURCE_MODE}" != "gps" ]]; then
    echo "--source-model sr90-decay requires --source-mode gps." >&2
    exit 1
  fi
  if [[ -n "${PRIMARY_ENERGY_OVERRIDE}" ]]; then
    echo "--primary-energy is incompatible with --source-model sr90-decay; the GPS ion is created at rest." >&2
    exit 1
  fi
  primary_particle="ion"
  primary_energy="${SR90_DECAY_PRIMARY_ION} ion at rest"
fi
if [[ -n "${PRIMARY_ENERGY_OVERRIDE}" ]]; then
  primary_energy="${PRIMARY_ENERGY_OVERRIDE}"
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
surface_preset="${SURFACE_PRESET_OVERRIDE}"
surface_model="${template_surface_model}"
surface_type="${template_surface_type}"
surface_finish="${template_surface_finish}"
surface_sigma_alpha="${template_surface_sigma_alpha}"
surface_reflectivity=""
surface_reflectivity_energy_min=""
surface_reflectivity_energy_max=""
if [[ -n "${surface_preset}" ]]; then
  surface_model="unified"
  surface_type="dielectric_dielectric"
  case "${surface_preset}" in
    polished)
      surface_finish="polished"
      surface_sigma_alpha="0.0"
      ;;
    ground)
      surface_finish="ground"
      surface_sigma_alpha="0.2"
      ;;
    wrapped)
      surface_finish="polishedfrontpainted"
      surface_sigma_alpha="0.0"
      surface_reflectivity="0.95"
      surface_reflectivity_energy_min="0.0000020 MeV"
      surface_reflectivity_energy_max="0.0000033 MeV"
      ;;
  esac
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

DIMPLE_RADIUS_MM=""
if [[ "${DIMPLE_ENABLED}" == "1" ]]; then
  if [[ "${sipm_face}" != "-Z" ]]; then
    echo "Week 8.1 dimple supports only bottom-center --sipm-face -Z; resolved face is ${sipm_face}." >&2
    exit 1
  fi

  read -r resolved_sipm_local_x resolved_sipm_local_y resolved_sipm_local_z resolved_sipm_local_unit resolved_sipm_local_extra <<< "${sipm_local}"
  if [[ -z "${resolved_sipm_local_x:-}" || -z "${resolved_sipm_local_y:-}" ||
        -z "${resolved_sipm_local_z:-}" || -z "${resolved_sipm_local_unit:-}" ||
        -n "${resolved_sipm_local_extra:-}" ]]; then
    echo "Could not parse resolved SiPM local position for dimple validation: ${sipm_local}" >&2
    exit 1
  fi
  require_number "resolved SiPM local x" "${resolved_sipm_local_x}"
  require_number "resolved SiPM local y" "${resolved_sipm_local_y}"
  require_number "resolved SiPM local z" "${resolved_sipm_local_z}"
  sipm_local_x_mm="$(length_to_unit "${resolved_sipm_local_x}" "${resolved_sipm_local_unit}" "mm")"
  sipm_local_y_mm="$(length_to_unit "${resolved_sipm_local_y}" "${resolved_sipm_local_unit}" "mm")"
  sipm_local_z_mm="$(length_to_unit "${resolved_sipm_local_z}" "${resolved_sipm_local_unit}" "mm")"
  awk -v x="${sipm_local_x_mm}" -v y="${sipm_local_y_mm}" -v z="${sipm_local_z_mm}" '
    BEGIN {
      if (x < 0) x = -x
      if (y < 0) y = -y
      if (z < 0) z = -z
      if (x > 1e-9 || y > 1e-9 || z > 1e-9) {
        printf "Week 8.1 dimple supports only --sipm-local-position \"0 0 0 unit\".\n" > "/dev/stderr"
        exit 1
      }
    }'

  DIMPLE_RADIUS_MM="$(length_to_unit "${DIMPLE_RADIUS}" "${DIMPLE_UNIT}" "mm")"
  require_number "resolved SiPM activeU" "${sipm_active_u}"
  require_number "resolved SiPM activeV" "${sipm_active_v}"
  require_number "resolved SiPM thickness" "${sipm_thickness}"
  sipm_active_u_mm="$(length_to_unit "${sipm_active_u}" "${sipm_unit}" "mm")"
  sipm_active_v_mm="$(length_to_unit "${sipm_active_v}" "${sipm_unit}" "mm")"
  sipm_thickness_mm="$(length_to_unit "${sipm_thickness}" "${sipm_unit}" "mm")"
  sipm_corner_radius_mm="$(awk -v u="${sipm_active_u_mm}" -v v="${sipm_active_v_mm}" 'BEGIN { printf "%.10g", sqrt((0.5*u)^2 + (0.5*v)^2) }')"

  if [[ -n "${tank_size}" ]]; then
    read -r resolved_tank_x resolved_tank_y resolved_tank_z resolved_tank_unit resolved_tank_extra <<< "${tank_size}"
    if [[ -z "${resolved_tank_z:-}" || -z "${resolved_tank_unit:-}" || -n "${resolved_tank_extra:-}" ]]; then
      echo "Could not parse resolved tank size for dimple validation: ${tank_size}" >&2
      exit 1
    fi
    require_number "resolved tank thickness" "${resolved_tank_z}"
    tank_thickness_mm="$(length_to_unit "${resolved_tank_z}" "${resolved_tank_unit}" "mm")"
  elif [[ -n "${tank_size_preset}" ]]; then
    case "${tank_size_preset}" in
      5x5x0p4|10x10x0p4)
        tank_thickness_mm="4"
        ;;
      5x5x0p8|10x10x0p8)
        tank_thickness_mm="8"
        ;;
      5x5x1p6|10x10x1p6)
        tank_thickness_mm="16"
        ;;
      *)
        echo "Invalid resolved tank size preset for dimple validation: ${tank_size_preset}" >&2
        exit 1
        ;;
    esac
  else
    tank_thickness_mm="5"
  fi

  awk -v radius="${DIMPLE_RADIUS_MM}" -v corner="${sipm_corner_radius_mm}" -v thickness="${tank_thickness_mm}" '
    BEGIN {
      safety = 1e-6
      if (radius <= corner + safety) {
        printf "Invalid dimple geometry: radius %.10g mm must exceed SiPM farthest active-face corner radius %.10g mm.\n", radius, corner > "/dev/stderr"
        exit 1
      }
      if (radius >= thickness - safety) {
        printf "Invalid dimple geometry: radius %.10g mm must be smaller than tile thickness %.10g mm.\n", radius, thickness > "/dev/stderr"
        exit 1
      }
    }'

  if [[ "${DIMPLE_SIPM_MODE}" == "opening" ]]; then
    awk -v radius="${DIMPLE_RADIUS_MM}" -v corner="${sipm_corner_radius_mm}" -v thickness="${tank_thickness_mm}" -v sipm_t="${sipm_thickness_mm}" '
      BEGIN {
        safety = 1e-6
        bottom = -0.5 * thickness
        clearance_z = bottom + sqrt(radius * radius - corner * corner)
        top_face_z = bottom + sipm_t
        if (top_face_z >= clearance_z - safety) {
          printf "Invalid dimple opening placement: SiPM top face z %.10g mm reaches dimple clearance z %.10g mm.\n", top_face_z, clearance_z > "/dev/stderr"
          exit 1
        }
      }'
  fi
fi

scint_yield="$(macro_box_const "SCINTILLATIONYIELD")"
if [[ -z "${scint_yield}" ]]; then
  scint_yield=null
fi

if [[ -z "${primary_particle}" || -z "${primary_energy}" || -z "${sipm_face}" || -z "${sipm_local}" ]]; then
  echo "Could not parse required source/SiPM defaults from ${TEMPLATE_MACRO}" >&2
  echo "Source mode: ${SOURCE_MODE}; expected ${position_cmd}, ${direction_cmd}, ${particle_cmd}, ${energy_cmd}." >&2
  echo "Use --template-macro with the expected defaults, or pass --sipm-face and --sipm-local-position explicitly." >&2
  exit 1
fi

generated_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
git_commit="${SCAN_GIT_COMMIT:-$(git rev-parse HEAD 2>/dev/null || echo "unknown")}"
git_branch="${SCAN_GIT_BRANCH:-$(git branch --show-current 2>/dev/null || echo "unknown")}"
if [[ -z "${git_branch}" ]]; then
  git_branch="detached"
fi
if [[ -n "${SCAN_GIT_DIRTY:-}" ]]; then
  case "${SCAN_GIT_DIRTY}" in
    true|false)
      git_dirty="${SCAN_GIT_DIRTY}"
      ;;
    *)
      echo "Invalid SCAN_GIT_DIRTY: ${SCAN_GIT_DIRTY}. Use true or false." >&2
      exit 1
      ;;
  esac
elif [[ -n "$(git status --porcelain 2>/dev/null || true)" ]]; then
  git_dirty=true
else
  git_dirty=false
fi

COMMAND_SCRIPT="${SCAN_COMMAND_SCRIPT:-$0}"
COMMAND_ARGV=("${COMMAND_SCRIPT}" "${ORIGINAL_ARGS[@]}")
COMMAND_ENV=(
  "N_EVENTS=${N_EVENTS}"
  "DRY_RUN=${DRY_RUN}"
  "SOURCE_MODE=${SOURCE_MODE}"
  "OPNOVICE2_EXECUTABLE=${OPNOVICE2_EXECUTABLE}"
  "PLOT_WITH_ROOT=${PLOT_WITH_ROOT}"
  "ROOT_COMMAND=${ROOT_COMMAND}"
  "ROOT_PLOT_FIDUCIAL_LIMIT_MM=${ROOT_PLOT_FIDUCIAL_LIMIT_MM}"
  "G4RUN_MANAGER_TYPE=${G4RUN_MANAGER_TYPE:-}"
  "SCAN_RUN_ID_SUFFIX=${SCAN_RUN_ID_SUFFIX}"
)
COMMAND_SHELL="$(shell_join "${COMMAND_ENV[@]}" "${COMMAND_ARGV[@]}")"

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
    printf '  "command": {\n'
    printf '    "argv": '
    json_string_array "${COMMAND_ARGV[@]}"
    printf ',\n'
    printf '    "shell": "%s",\n' "$(json_string "${COMMAND_SHELL}")"
    printf '    "environment": {\n'
    printf '      "N_EVENTS": "%s",\n' "$(json_string "${N_EVENTS}")"
    printf '      "DRY_RUN": "%s",\n' "$(json_string "${DRY_RUN}")"
    printf '      "SOURCE_MODE": "%s",\n' "$(json_string "${SOURCE_MODE}")"
    printf '      "PLOT_WITH_ROOT": "%s",\n' "$(json_string "${PLOT_WITH_ROOT}")"
    printf '      "ROOT_COMMAND": "%s",\n' "$(json_string "${ROOT_COMMAND}")"
    printf '      "ROOT_PLOT_FIDUCIAL_LIMIT_MM": "%s",\n' "$(json_string "${ROOT_PLOT_FIDUCIAL_LIMIT_MM}")"
    printf '      "G4RUN_MANAGER_TYPE": "%s",\n' "$(json_string "${G4RUN_MANAGER_TYPE:-}")"
    printf '      "SCAN_RUN_ID_SUFFIX": "%s"\n' "$(json_string "${SCAN_RUN_ID_SUFFIX}")"
    printf '    }\n'
    printf '  },\n'
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
    printf '    "primary_energy": %s,\n' "$(if [[ -n "${PRIMARY_ENERGY_OVERRIDE}" ]]; then echo true; else echo false; fi)"
    printf '    "source_model": %s,\n' "$(if [[ -n "${SOURCE_MODEL_OVERRIDE}" ]]; then echo true; else echo false; fi)"
    printf '    "electron_energy_mode": %s,\n' "$(if [[ -n "${ELECTRON_ENERGY_MODE_OVERRIDE}" ]]; then echo true; else echo false; fi)"
    printf '    "surface_preset": %s,\n' "$(if [[ -n "${SURFACE_PRESET_OVERRIDE}" ]]; then echo true; else echo false; fi)"
    printf '    "beam_sigma": %s,\n' "$(if [[ -n "${CUSTOM_BEAM_SIGMA}" ]]; then echo true; else echo false; fi)"
    printf '    "beam_divergence_mrad": %s,\n' "$(if [[ -n "${CUSTOM_BEAM_DIVERGENCE_MRAD}" ]]; then echo true; else echo false; fi)"
    printf '    "dimple": %s\n' "$(if [[ "${DIMPLE_ENABLED}" == "1" ]]; then echo true; else echo false; fi)"
    printf '  },\n'
    printf '  "git": {\n'
    printf '    "commit": "%s",\n' "$(json_string "${git_commit}")"
    printf '    "branch": "%s",\n' "$(json_string "${git_branch}")"
    printf '    "dirty": %s\n' "${git_dirty}"
    printf '  },\n'
    printf '  "simulation": {\n'
    printf '    "events_per_point": %s,\n' "${N_EVENTS}"
    printf '    "scintillation_yield_per_mev": %s,\n' "${scint_yield}"
    printf '    "source_model": "%s",\n' "$(json_string "${source_model}")"
    printf '    "spectrum_model": '
    if [[ "${source_model}" == "sr90-spectrum" ]]; then
      printf '"%s"' "$(json_string "${SR90_SPECTRUM_MODEL}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "spectrum_table_path": '
    if [[ "${source_model}" == "sr90-spectrum" ]]; then
      printf '"%s"' "$(json_string "${SR90_SPECTRUM_TABLE}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "spectrum_gps_fragment_path": '
    if [[ "${source_model}" == "sr90-spectrum" ]]; then
      printf '"%s"' "$(json_string "${SR90_SPECTRUM_GPS_MACRO}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "sr90_endpoint_mev": '
    if [[ "${source_model}" == "sr90-spectrum" ]]; then
      printf '%s' "${SR90_ENDPOINT_MEV}"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "y90_endpoint_mev": '
    if [[ "${source_model}" == "sr90-spectrum" ]]; then
      printf '%s' "${Y90_ENDPOINT_MEV}"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "sr90_activity_weight": '
    if [[ "${source_model}" == "sr90-spectrum" ]]; then
      printf '%s' "${SR90_ACTIVITY_WEIGHT}"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "y90_activity_weight": '
    if [[ "${source_model}" == "sr90-spectrum" ]]; then
      printf '%s' "${Y90_ACTIVITY_WEIGHT}"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "fermi_correction": '
    if [[ "${source_model}" == "sr90-spectrum" ]]; then
      printf '%s' "${SR90_FERMI_CORRECTION}"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "incident_transport_model": '
    if [[ "${source_model}" == "sr90-spectrum" ]]; then
      printf '"%s"' "$(json_string "${SR90_INCIDENT_MODEL}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "decay_model": '
    if [[ "${source_model}" == "sr90-decay" ]]; then
      printf '"%s"' "$(json_string "${SR90_DECAY_MODEL}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "decay_primary_ion": '
    if [[ "${source_model}" == "sr90-decay" ]]; then
      printf '"%s"' "$(json_string "${SR90_DECAY_PRIMARY_ION}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "decay_gps_ion": '
    if [[ "${source_model}" == "sr90-decay" ]]; then
      printf '"%s"' "$(json_string "${SR90_DECAY_GPS_ION}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "decay_rdm_threshold_for_very_long_decay_time": '
    if [[ "${source_model}" == "sr90-decay" ]]; then
      printf '"%s"' "$(json_string "${SR90_DECAY_RDM_TIME_THRESHOLD}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "decay_expected_chain": '
    if [[ "${source_model}" == "sr90-decay" ]]; then
      printf '"%s"' "$(json_string "${SR90_DECAY_EXPECTED_CHAIN}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "primary_particle": "%s",\n' "$(json_string "${primary_particle}")"
    printf '    "primary_energy": "%s",\n' "$(json_string "${primary_energy}")"
    printf '    "electron_energy_mode": "%s",\n' "$(json_string "${electron_energy_mode}")"
    printf '    "beam_direction": "%s",\n' "$(json_string "${BEAM_DIRECTION}")"
    printf '    "beam_z": "%s %s",\n' "$(format_num "${Z0}")" "$(json_string "${GRID_UNIT}")"
    printf '    "beam_z_inferred": %s\n' "$(if [[ "${BEAM_Z_INFERRED}" == "1" ]]; then echo true; else echo false; fi)"
    printf '  },\n'
    printf '  "beam": {\n'
    printf '    "profile": "%s",\n' "$(json_string "${BEAM_PROFILE}")"
    printf '    "sigma": '
    if [[ "${BEAM_PROFILE}" == "gaussian" ]]; then
      printf '%s' "$(format_num "${BEAM_SIGMA}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "unit": "%s",\n' "$(json_string "${GRID_UNIT}")"
    printf '    "angular_model": "%s",\n' "$(json_string "${BEAM_ANGULAR_MODEL}")"
    printf '    "divergence_mrad": '
    if [[ "${BEAM_ANGULAR_MODEL}" == "beam2d" ]]; then
      printf '%s' "$(format_num "${BEAM_DIVERGENCE_MRAD}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "direction": "%s"\n' "$(json_string "${BEAM_DIRECTION}")"
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
    printf '  "dimple": {\n'
    printf '    "enabled": %s,\n' "$(if [[ "${DIMPLE_ENABLED}" == "1" ]]; then echo true; else echo false; fi)"
    if [[ "${DIMPLE_ENABLED}" == "1" ]]; then
      printf '    "mode": "hemisphere",\n'
      printf '    "radius": "%s %s",\n' "$(format_num "${DIMPLE_RADIUS}")" "$(json_string "${DIMPLE_UNIT}")"
      printf '    "radius_mm": %s,\n' "$(format_num "${DIMPLE_RADIUS_MM}")"
      printf '    "sipm_mode": "%s",\n' "$(json_string "${DIMPLE_SIPM_MODE}")"
      printf '    "supported_sipm_face": "-Z",\n'
      printf '    "strict_hemisphere": true\n'
    else
      printf '    "mode": null,\n'
      printf '    "radius": null,\n'
      printf '    "radius_mm": null,\n'
      printf '    "sipm_mode": null,\n'
      printf '    "supported_sipm_face": "-Z",\n'
      printf '    "strict_hemisphere": true\n'
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
    printf '  "surface": {\n'
    printf '    "preset": '
    if [[ -n "${surface_preset}" ]]; then
      printf '"%s"' "$(json_string "${surface_preset}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "model": '
    if [[ -n "${surface_model}" ]]; then
      printf '"%s"' "$(json_string "${surface_model}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "type": '
    if [[ -n "${surface_type}" ]]; then
      printf '"%s"' "$(json_string "${surface_type}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "finish": '
    if [[ -n "${surface_finish}" ]]; then
      printf '"%s"' "$(json_string "${surface_finish}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "sigma_alpha": '
    if [[ -n "${surface_sigma_alpha}" ]]; then
      printf '%s' "$(format_num "${surface_sigma_alpha}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "reflectivity": '
    if [[ -n "${surface_reflectivity}" ]]; then
      printf '%s' "$(format_num "${surface_reflectivity}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "reflectivity_energy_min": '
    if [[ -n "${surface_reflectivity_energy_min}" ]]; then
      printf '"%s"' "$(json_string "${surface_reflectivity_energy_min}")"
    else
      printf 'null'
    fi
    printf ',\n'
    printf '    "reflectivity_energy_max": '
    if [[ -n "${surface_reflectivity_energy_max}" ]]; then
      printf '"%s"\n' "$(json_string "${surface_reflectivity_energy_max}")"
    else
      printf 'null\n'
    fi
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
    printf 'tag,x,y,z,unit,events,generated_optical_photons,scintillation_photons,sipm_detected_photons,collection_efficiency,shoot_position_events,shoot_x_mm,shoot_y_mm,shoot_z_mm,hit_position_events,hit_x_mm,hit_y_mm,hit_z_mm,scint_centroid_events,scint_centroid_x_mm,scint_centroid_y_mm,scint_centroid_z_mm,primary_energy_events,primary_energy_mean_mev,primary_energy_rms_mev,primary_energy_min_mev,primary_energy_max_mev,decay_beta_count,decay_beta_energy_mean_mev,decay_beta_energy_rms_mev,decay_beta_energy_min_mev,decay_beta_energy_max_mev,summary_csv,root,log\n'
    read -r _points_header
    while IFS=, read -r tag x y z unit macro root log; do
      summary="${root%.root}_summary.csv"
      if [[ ! -f "${summary}" ]]; then
        echo "Missing scan summary CSV: ${summary}" >&2
        echo "Point: ${tag}" >&2
        if [[ -f "${log}" ]]; then
          echo "Last ${SCAN_LOG_TAIL_LINES} lines from ${log}:" >&2
          tail -n "${SCAN_LOG_TAIL_LINES}" "${log}" >&2 || true
        fi
        exit 1
      fi
      read -r _summary_header < "${summary}"
      read -r events generated scint detected efficiency shoot_events shoot_x shoot_y shoot_z hit_events hit_x hit_y hit_z scint_centroid_events scint_centroid_x scint_centroid_y scint_centroid_z primary_energy_events primary_energy_mean primary_energy_rms primary_energy_min primary_energy_max decay_beta_count decay_beta_mean decay_beta_rms decay_beta_min decay_beta_max < <(
        awk -F, 'NR == 2 {
          print $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27
        }' "${summary}"
      )
      if [[ -z "${events:-}" || -z "${generated:-}" || -z "${scint:-}" || -z "${detected:-}" || -z "${efficiency:-}" ]]; then
        echo "Could not parse scan summary CSV: ${summary}" >&2
        exit 1
      fi
      printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
        "${tag}" "${x}" "${y}" "${z}" "${unit}" \
        "${events}" "${generated}" "${scint}" "${detected}" "${efficiency}" \
        "${shoot_events}" "${shoot_x}" "${shoot_y}" "${shoot_z}" \
        "${hit_events}" "${hit_x}" "${hit_y}" "${hit_z}" \
        "${scint_centroid_events}" "${scint_centroid_x}" "${scint_centroid_y}" "${scint_centroid_z}" \
        "${primary_energy_events}" "${primary_energy_mean}" "${primary_energy_rms}" "${primary_energy_min}" "${primary_energy_max}" \
        "${decay_beta_count}" "${decay_beta_mean}" "${decay_beta_rms}" "${decay_beta_min}" "${decay_beta_max}" \
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
  if ! ln -sfn "${RUN_DIR}" "${LATEST_RUN_LINK}"; then
    echo "Warning: not updating ${LATEST_RUN_LINK}; another scan task likely updated it concurrently." >&2
  fi
else
  echo "Not updating ${LATEST_RUN_LINK}: it exists and is not a symlink." >&2
fi

echo "Scan: ${SCAN_NAME}"
echo "Template: ${TEMPLATE_MACRO}"
echo "Source mode: ${SOURCE_MODE}"
echo "Source model: ${source_model}"
if [[ "${source_model}" == "sr90-spectrum" ]]; then
  echo "Spectrum model: ${SR90_SPECTRUM_MODEL} (${SR90_SPECTRUM_TABLE})"
fi
if [[ "${source_model}" == "sr90-decay" ]]; then
  echo "Decay model: ${SR90_DECAY_MODEL}; GPS ion ${SR90_DECAY_GPS_ION}; RDM threshold ${SR90_DECAY_RDM_TIME_THRESHOLD}"
fi
echo "Run directory: ${RUN_DIR}"
echo "Grid unit: ${GRID_UNIT}; x=($(json_number_array "${XS[@]}")); y=($(json_number_array "${YS[@]}"))"
if [[ "${BEAM_Z_INFERRED}" == "1" ]]; then
  echo "Beam z: $(format_num "${Z0}") ${GRID_UNIT} (inferred)"
else
  echo "Beam z: $(format_num "${Z0}") ${GRID_UNIT}"
fi
if [[ "${BEAM_PROFILE}" == "gaussian" ]]; then
  echo "Beam profile: Gaussian sigma=$(format_num "${BEAM_SIGMA}") ${GRID_UNIT}"
else
  echo "Beam profile: point"
fi
if [[ "${BEAM_ANGULAR_MODEL}" == "beam2d" ]]; then
  echo "Beam angular divergence: beam2d sigma_r=$(format_num "${BEAM_DIVERGENCE_MRAD}") mrad"
else
  echo "Beam angular divergence: pencil"
fi
echo "Events per point: ${N_EVENTS}"
echo "Electron energy mode: ${electron_energy_mode}"
if [[ -n "${tank_size}" ]]; then
  echo "Tank size: ${tank_size}"
fi
if [[ -n "${tank_size_preset}" ]]; then
  echo "Tank size preset: ${tank_size_preset}"
fi
if [[ -n "${surface_preset}" ]]; then
  echo "Surface preset: ${surface_preset} (finish=${surface_finish}, sigma_alpha=${surface_sigma_alpha})"
fi
if [[ "${DIMPLE_ENABLED}" == "1" ]]; then
  echo "Dimple: hemisphere radius=$(format_num "${DIMPLE_RADIUS}") ${DIMPLE_UNIT} (${DIMPLE_SIPM_MODE}, radius_mm=$(format_num "${DIMPLE_RADIUS_MM}"))"
fi
echo "Metadata: ${RUN_CONFIG}, ${POINTS_CSV}"
echo "Latest pointers: ${LATEST_RUN_LINK}, ${LATEST_RUN_CONFIG}, ${LATEST_POINTS_CSV}"

tail -n +2 "${POINTS_CSV}" | while IFS=, read -r tag x y z unit macro root log; do
  outfile="${root%.root}"

  macro_args=(
    --template "${TEMPLATE_MACRO}"
    --out "${macro}"
    --set "/analysis/setFileName=${outfile}"
    --set "${position_cmd}=${x} ${y} ${z} ${unit}"
    --set "${direction_cmd}=${BEAM_DIRECTION}"
    --set "/run/beamOn=${N_EVENTS}"
    --set "/opnovice2/sipm/face=${sipm_face}"
    --set "/opnovice2/sipm/localPosition=${sipm_local}"
    --require "/analysis/setFileName"
    --require "${position_cmd}"
    --require "${direction_cmd}"
    --require "/run/beamOn"
    --require "/opnovice2/sipm/face"
    --require "/opnovice2/sipm/localPosition"
  )

  if [[ "${source_model}" == "sr90-decay" ]]; then
    macro_args+=(
      --set "${particle_cmd}=ion"
      --set "${energy_cmd}=0 eV"
      --set "/gps/ion=${SR90_DECAY_GPS_ION}"
      --set "/process/had/rdm/analogueMC=true"
      --set "/process/had/rdm/thresholdForVeryLongDecayTime=${SR90_DECAY_RDM_TIME_THRESHOLD}"
      --remove "/opnovice2/gun/electronEnergyMode"
      --require "${particle_cmd}"
      --require "/gps/ion"
      --require "${energy_cmd}"
      --require "/process/had/rdm/analogueMC"
      --require "/process/had/rdm/thresholdForVeryLongDecayTime"
      --insert-missing-before "/gps/ion=/gps/pos/type"
      --insert-missing-before "/process/had/rdm/analogueMC=/analysis/setFileName"
      --insert-missing-before "/process/had/rdm/thresholdForVeryLongDecayTime=/analysis/setFileName"
    )
  elif [[ "${source_model}" == "sr90-spectrum" ]]; then
    macro_args+=(
      --remove "${energy_cmd}"
      --remove "/opnovice2/gun/electronEnergyMode"
      --insert-file-before "${SR90_SPECTRUM_GPS_MACRO}=/run/beamOn"
      --require "/gps/ene/type"
      --require "/gps/hist/type"
      --require "/gps/hist/point"
      --require "/gps/hist/inter"
    )
  else
    macro_args+=(
      --set "${energy_cmd}=${primary_energy}"
      --require "${energy_cmd}"
    )
  fi

  if [[ -n "${sipm_cavity_mode}" ]]; then
    macro_args+=(--set "/opnovice2/sipm/cavityMode=${sipm_cavity_mode}")
  fi
  if [[ -n "${TANK_SIZE_OVERRIDE}" ]]; then
    macro_args+=(--set "/opnovice2/tank/size=${tank_size}")
  fi
  if [[ -n "${TANK_SIZE_PRESET_OVERRIDE}" ]]; then
    macro_args+=(--set "/opnovice2/tank/sizePreset=${tank_size_preset}")
  fi
  if [[ "${MODE}" == "full" ]]; then
    macro_args+=(--set "/opnovice2/tank/bottomCavity=false")
  fi
  if [[ "${DIMPLE_ENABLED}" == "1" ]]; then
    macro_args+=(
      --set "/opnovice2/dimple/enabled=true"
      --set "/opnovice2/dimple/radius=$(format_num "${DIMPLE_RADIUS}") ${DIMPLE_UNIT}"
      --set "/opnovice2/dimple/mode=hemisphere"
      --set "/opnovice2/dimple/sipmMode=${DIMPLE_SIPM_MODE}"
      --require "/opnovice2/dimple/enabled"
      --require "/opnovice2/dimple/radius"
      --require "/opnovice2/dimple/mode"
      --require "/opnovice2/dimple/sipmMode"
    )
  fi
  if [[ "${BEAM_PROFILE}" == "gaussian" ]]; then
    macro_args+=(
      --set "/gps/pos/type=Beam"
      --set "/gps/pos/sigma_x=$(format_num "${BEAM_SIGMA}") ${unit}"
      --set "/gps/pos/sigma_y=$(format_num "${BEAM_SIGMA}") ${unit}"
      --require "/gps/pos/type"
      --require "/gps/pos/sigma_x"
      --require "/gps/pos/sigma_y"
      --insert-missing-before "/gps/pos/sigma_x=/gps/direction"
      --insert-missing-before "/gps/pos/sigma_y=/gps/direction"
    )
  fi
  if [[ "${BEAM_ANGULAR_MODEL}" == "beam2d" ]]; then
    angular_insert_anchor="${energy_cmd}"
    if [[ "${source_model}" == "sr90-spectrum" ]]; then
      angular_insert_anchor="/run/beamOn"
    fi
    macro_args+=(
      --set "/gps/ang/type=beam2d"
      --set "/gps/ang/sigma_r=$(format_num "${BEAM_DIVERGENCE_MRAD}") mrad"
      --require "/gps/ang/type"
      --require "/gps/ang/sigma_r"
      --insert-missing-before "/gps/ang/type=${angular_insert_anchor}"
      --insert-missing-before "/gps/ang/sigma_r=${angular_insert_anchor}"
    )
  fi
  if [[ -n "${surface_preset}" ]]; then
    macro_args+=(
      --set "/opnovice2/surfacePreset=${surface_preset}"
      --require "/opnovice2/surfacePreset"
    )
  fi
  if [[ "${SOURCE_MODE}" == "gun" || "${electron_energy_mode}" != "fixed" ]]; then
    macro_args+=(
      --set "/opnovice2/gun/electronEnergyMode=${electron_energy_mode}"
      --require "/opnovice2/gun/electronEnergyMode"
      --insert-missing-before "/opnovice2/gun/electronEnergyMode=/run/beamOn"
    )
  fi

  python3 "${MACRO_GENERATOR}" "${macro_args[@]}"

  if [[ "${DRY_RUN}" == "1" ]]; then
    echo "Prepared ${tag}: x=${x} ${unit}, y=${y} ${unit}"
  else
    echo "Running ${tag}: x=${x} ${unit}, y=${y} ${unit}"
    set +e
    "${OPNOVICE2_EXECUTABLE}" "${macro}" > "${log}" 2>&1
    status=$?
    set -e
    if [[ "${status}" -ne 0 ]]; then
      echo "Simulation failed for ${tag} with exit status ${status}." >&2
      echo "Macro: ${macro}" >&2
      echo "Log: ${log}" >&2
      if [[ -f "${log}" ]]; then
        echo "Last ${SCAN_LOG_TAIL_LINES} lines from ${log}:" >&2
        tail -n "${SCAN_LOG_TAIL_LINES}" "${log}" >&2 || true
      fi
      exit "${status}"
    fi
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
