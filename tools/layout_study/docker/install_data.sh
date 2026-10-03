#!/usr/bin/env bash
# Run inside the pinned Docker/Apptainer image with a writable /g4data mount.
set -euo pipefail
mode=${1:-install}
if [[ "$mode" != install && "$mode" != verify ]]; then
  echo "Usage: bash install_data.sh [install|verify]" >&2
  exit 2
fi
export GEANT4_DATA_DIR=/g4data
config=/opt/geant4/bin/geant4-config
[[ "$($config --version)" == 11.4.2 ]] || { echo "Geant4 11.4.2 required" >&2; exit 1; }
data_root=/g4data
# geant4-config downloads to its compiled dataset paths. Validate those paths
# before it can create or download anything, independently of runtime env vars.
datasets=$("$config" --datasets)
[[ -n "$datasets" ]] || { echo "Geant4 dataset list is empty" >&2; exit 1; }
while read -r name variable directory; do
  [[ "$directory" == /g4data/* ]] || { echo "Pinned image has unexpected dataset path: $directory" >&2; exit 1; }
done <<< "$datasets"
manifest="$data_root/layout-study-SHA256SUMS"
mkdir -p "$data_root"
if [[ -f "$manifest" ]]; then
  if [[ "$mode" == install ]]; then chmod 644 "$manifest"; fi
  sha256sum --check --status "$manifest"
  echo "Geant4 11.4.2 dataset file checksums verified."
  exit 0
fi
if [[ "$mode" == verify ]]; then
  echo "Missing dataset receipt. Run install using a new empty data directory first." >&2
  exit 1
fi
if [[ -n "$(find "$data_root" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo "No receipt exists and /g4data is not empty. Use a new empty volume/directory; existing data are not overwritten." >&2
  exit 1
fi
# The pinned Geant4 tool downloads its versioned datasets and checks each
# archive against the MD5 recorded in its installation metadata.
"$config" --install-datasets
"$config" --check-datasets | awk '{ print; if ($2 != "INSTALLED") bad=1 } END { exit bad }'
printf '%s\n' "$datasets" > "$data_root/layout-study-datasets.txt"
temporary=$(mktemp "$data_root/.layout-study-sha.XXXXXX")
trap 'rm -f "$temporary"' EXIT
while read -r name variable directory; do
  [[ "$directory" == /g4data/* && -d "$directory" ]] || { echo "Unexpected dataset path: $directory" >&2; exit 1; }
  find "$directory" -type f -print0
done < "$data_root/layout-study-datasets.txt" | sort -z | xargs -0 sha256sum > "$temporary"
[[ -s "$temporary" ]] || { echo "Dataset file manifest is empty" >&2; exit 1; }
chmod 644 "$temporary"
mv "$temporary" "$manifest"
sha256sum --check --status "$manifest"
echo "Geant4 11.4.2 datasets installed and checksummed at /g4data."
