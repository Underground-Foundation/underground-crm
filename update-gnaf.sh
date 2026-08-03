#!/usr/bin/env bash
# Updates the G-NAF dataset in Addressr.
#
# As per https://data.gov.au/data/dataset/geocoded-national-address-file-g-naf,
# G-NAF is released quarterly. Run this script after each new release to
# keep address search up to date.
#
# What this script does:
#   1. Fetches the latest GDA94 download URL from data.gov.au
#   2. Updates docker/gnaf-package.json with the new URL
#   3. Removes the cached download and the loader's URL cache
#   4. Starts the addressr-loader compose service (profile: loader) in the
#      background to re-index from scratch
#
# addressr-loader is a separate compose service from the always-on addressr
# query server — see the comments in docker-compose.yml. By default it loads
# into the local `opensearch` service, but it can be pointed at a remote
# OpenSearch instance by setting LOADER_ELASTIC_HOST/LOADER_ELASTIC_PORT
# before running this script.
#
# During re-indexing (roughly 1-2 hours) the address index is empty and
# address searches will return no results.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

GNAF_JSON="docker/gnaf-package.json"
ADDRESSR_VOL_DIR="data/addressr"
GNAF_DATA_DIR="$ADDRESSR_VOL_DIR/gnaf"
GNAF_URL_CACHE="$ADDRESSR_VOL_DIR/keyv-file.msgpack"

# Check that the volume directory is writable. It is committed to the repo
# so Docker will never create it as root, but guard against it just in case.
if [ ! -w "$ADDRESSR_VOL_DIR" ]; then
    echo "error: $ADDRESSR_VOL_DIR is not writable by the current user."
    echo "Fix with:"
    echo "  sudo chmod o+rwx $(realpath "$ADDRESSR_VOL_DIR")"
    exit 1
fi

# Fetch the latest GDA94 download URL from the data.gov.au dataset page
echo "Checking data.gov.au for the latest G-NAF release..."
NEW_URL=$(
    curl -sf "https://data.gov.au/data/dataset/geocoded-national-address-file-g-naf" \
    | grep -oE 'href="[^"]*g-naf_[^"]*gda94[^"]*\.zip"' \
    | head -1 \
    | cut -d'"' -f2
)

if [ -z "$NEW_URL" ]; then
    echo "error: could not find a G-NAF GDA94 ZIP link on data.gov.au."
    echo "Visit https://data.gov.au/data/dataset/geocoded-national-address-file-g-naf and"
    echo "confirm the page still lists a GDA94 ZIP download, then re-run this script."
    exit 1
fi

# Compare with the URL already in docker/gnaf-package.json, if the file exists.
# The file is not committed to the repo; this script creates it on first run.
URL_CHANGED=true
CURRENT_URL="(none)"
if [ -f "$GNAF_JSON" ]; then
    CURRENT_URL=$(python3 -c "
import json
with open('$GNAF_JSON') as f:
    print(json.load(f)['result']['resources'][0]['url'])
")
    if [ "$NEW_URL" = "$CURRENT_URL" ]; then
        URL_CHANGED=false
        echo "G-NAF is already at the latest release: $(basename "$NEW_URL")"
        # Still run the loader if the G-NAF data has never been downloaded — this
        # happens when a previous load attempt failed before any data was indexed.
        if [ ! -d "$GNAF_DATA_DIR" ] || [ -z "$(ls -A "$GNAF_DATA_DIR" 2>/dev/null)" ]; then
            echo "G-NAF data not yet downloaded; running the loader."
        else
            echo "No update needed."
            exit 0
        fi
    fi
fi

if [ "$URL_CHANGED" = true ]; then
    if [ "$CURRENT_URL" = "(none)" ]; then
        echo "Creating docker/gnaf-package.json for the first time..."
    else
        echo "New G-NAF release found:"
        echo "  Current: $(basename "$CURRENT_URL")"
        echo "  New:     $(basename "$NEW_URL")"
        echo ""
    fi

    # Fetch the file size for the progress display inside the loader
    NEW_SIZE=$(curl -sI "$NEW_URL" | grep -i "^content-length:" | tr -d '\r' | awk '{print $2}')

    # Write docker/gnaf-package.json. The file is gitignored; this script is
    # the sole source of truth for its contents.
    python3 - <<PYEOF
import json, os

path = '$GNAF_JSON'
if os.path.exists(path):
    with open(path) as f:
        d = json.load(f)
else:
    d = {'result': {'resources': [{'state': 'active', 'mimetype': 'application/zip', 'url': '', 'size': None}]}}
d['result']['resources'][0]['url'] = '$NEW_URL'
d['result']['resources'][0]['size'] = int('$NEW_SIZE') if '$NEW_SIZE'.strip() else None
with open(path, 'w') as f:
    json.dump(d, f, indent=2)
    f.write('\n')
PYEOF

    # Clear the loader's 24-hour cache of the package URL so it reads the
    # updated docker/gnaf-package.json immediately rather than the cached old URL
    rm -f "$GNAF_URL_CACHE"

    # Remove the previously downloaded ZIP and extracted data
    echo "Clearing old G-NAF download cache..."
    rm -rf "$GNAF_DATA_DIR"
fi

# Run the loader in the background. It exits on its own once indexing
# finishes; check its status with `docker compose ps addressr-loader`.
#
# LOADER_ES_CLEAR_INDEX=true drops the OpenSearch index before re-indexing,
# so addresses removed from the new G-NAF release (demolished or renumbered
# properties) do not linger in search results. The downside is that address
# search returns no results for the ~1-2 hours re-indexing takes. This is the
# right trade-off for update-gnaf.sh's quarterly refresh; a manual, ad-hoc
# `docker compose --profile loader up -d addressr-loader` still defaults to
# false (see docker-compose.yml) so it doesn't blank the index unexpectedly.
echo "Starting G-NAF data loader in the background..."
echo ""
LOADER_ES_CLEAR_INDEX=true docker compose --profile loader up -d addressr-loader

echo "To watch the loader output:"
echo "  docker compose logs -f addressr-loader"
echo ""
echo "To check how many addresses have been indexed so far:"
echo "  curl http://localhost:9200/addressr/_count"
