#!/usr/bin/env bash
# Refresh bundled code without replacing the user's Brain activation catalog.
set -euo pipefail
runtime_source="$1"
runtime_destination="$2"
mkdir -p "$runtime_destination"
runtime_catalog="$runtime_destination/automation/brains/catalog.json"
runtime_catalog_backup=""
restore_catalog() {
  if [ -n "$runtime_catalog_backup" ]; then
    cp -- "$runtime_catalog_backup" "$runtime_catalog"
    rm -f -- "$runtime_catalog_backup"
  fi
}
if [ -f "$runtime_catalog" ]; then
  runtime_catalog_backup="$(mktemp)"
  cp -- "$runtime_catalog" "$runtime_catalog_backup"
fi
trap restore_catalog EXIT
cp -a -- "$runtime_source/." "$runtime_destination/"
