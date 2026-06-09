#!/usr/bin/env bash
set -euo pipefail

N_EVENTS=100
TEMPLATE_MACRO="ej200_sipm_test.mac"
SCAN_NAME="5x5 near-field bottom-center scan"
GRID_UNIT="mm"
BEAM_DIRECTION="0 0 -1"

# Near-field scan around bottom-center SiPM.
# Units are mm to avoid decimal coordinates and decimal file names.
Z0_MM="4"

mkdir -p scan_macros scan_outputs scan_logs

# Grid settings in mm.
# For a 3 mm x 3 mm SiPM, step = 3 mm.
# Only the center point (0,0) is directly above the SiPM footprint.
XS_MM=(-6 -3 0 3 6)
YS_MM=(-6 -3 0 3 6)

label_int() {
  local v="$1"
  if [[ "$v" == -* ]]; then
    echo "m${v#-}"
  else
    echo "p${v}"
  fi
}

json_number_array() {
  local first=1
  local v
  printf "["
  for v in "$@"; do
    if [[ "${first}" -eq 0 ]]; then
      printf ", "
    fi
    printf "%s" "${v}"
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
    printf '    "beam_z": "%s %s"\n' "${Z0_MM}" "${GRID_UNIT}"
    printf '  },\n'
    printf '  "sipm": {\n'
    printf '    "face": "%s",\n' "${sipm_face}"
    printf '    "local_position": "%s",\n' "${sipm_local}"
    printf '    "size": "%s"\n' "${sipm_size}"
    printf '  },\n'
    printf '  "grid": {\n'
    printf '    "unit": "%s",\n' "${GRID_UNIT}"
    printf '    "x": '
    json_number_array "${XS_MM[@]}"
    printf ',\n'
    printf '    "y": '
    json_number_array "${YS_MM[@]}"
    printf '\n'
    printf '  },\n'
    printf '  "outputs": {\n'
    printf '    "macro_dir": "scan_macros",\n'
    printf '    "root_dir": "scan_outputs",\n'
    printf '    "log_dir": "scan_logs"\n'
    printf '  },\n'
    printf '  "points": [\n'

    local first_point=1
    local x_mm y_mm lx ly tag macro outfile log
    for x_mm in "${XS_MM[@]}"; do
      for y_mm in "${YS_MM[@]}"; do
        lx=$(label_int "${x_mm}")
        ly=$(label_int "${y_mm}")
        tag="x${lx}mm_y${ly}mm"
        macro="scan_macros/sipm_scan_${tag}.mac"
        outfile="scan_outputs/sipm_scan_${tag}.root"
        log="scan_logs/sipm_scan_${tag}.log"

        if [[ "${first_point}" -eq 0 ]]; then
          printf ',\n'
        fi
        printf '    {"tag": "%s", "x": %s, "y": %s, "z": %s, "unit": "%s", "macro": "%s", "root": "%s", "log": "%s"}' \
          "${tag}" "${x_mm}" "${y_mm}" "${Z0_MM}" "${GRID_UNIT}" "${macro}" "${outfile}" "${log}"
        first_point=0
      done
    done

    printf '\n'
    printf '  ]\n'
    printf '}\n'
  } > "${config}"
}

write_run_config "run_config.json"
cp "run_config.json" "scan_outputs/run_config.json"
echo "Wrote run metadata to run_config.json and scan_outputs/run_config.json"

for x_mm in "${XS_MM[@]}"; do
  for y_mm in "${YS_MM[@]}"; do
    lx=$(label_int "$x_mm")
    ly=$(label_int "$y_mm")
    tag="x${lx}mm_y${ly}mm"

    macro="scan_macros/sipm_scan_${tag}.mac"
    outfile="scan_outputs/sipm_scan_${tag}"
    log="scan_logs/sipm_scan_${tag}.log"

    sed \
      -e "s|/analysis/setFileName ej200_sipm_test|/analysis/setFileName ${outfile}|" \
      -e "s|/gun/position -6 4 0 cm|/gun/position ${x_mm} ${y_mm} ${Z0_MM} ${GRID_UNIT}|" \
      -e "s|/gun/direction 1 0 0|/gun/direction ${BEAM_DIRECTION}|" \
      -e "s|/run/beamOn 100|/run/beamOn ${N_EVENTS}|" \
      "${TEMPLATE_MACRO}" > "${macro}"

    echo "Running ${tag}: x=${x_mm} mm, y=${y_mm} mm"
    ./build/OpNovice2 "${macro}" > "${log}" 2>&1
  done
done
