#!/bin/bash

while read -r po_file; do
    mo_file="${po_file%.po}.mo"
    echo "Compiling: $po_file ↦ $mo_file"
    if ! msgfmt --strict --check -o "$mo_file" "$po_file"; then
        echo "Error: Failed to compile $po_file" >&2
        exit 1
    fi
done < <(find underground_crm/locale -name "*.po")

echo "Finished compiling translation files"
