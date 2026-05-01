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
#   4. Runs addressr-loader in the background with ES_CLEAR_INDEX=true,
#      which wipes the OpenSearch index before re-indexing from scratch
#
# During re-indexing (roughly 1-2 hours) the address index is empty and
# address searches will return no results. Once complete, the index holds
# only addresses from the new G-NAF release — no stale records from
# demolished or renumbered properties carry over.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

GNAF_JSON="docker/gnaf-package.json"
GNAF_DATA_DIR="data/addressr/gnaf"
GNAF_URL_CACHE="data/addressr/keyv-file.msgpack"

# Check the addressr container is running
if ! docker compose ps addressr 2>/dev/null | grep -qiE "running|up"; then
    echo "error: the addressr container is not running. Start it with:"
    echo "  docker compose up -d"
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
    echo "Visit https://data.gov.au/data/dataset/geocoded-national-address-file-g-naf"
    echo "copy the GDA94 ZIP download URL, edit docker/gnaf-package.json manually,"
    echo "then re-run this script."
    exit 1
fi

# Compare with the URL already in docker/gnaf-package.json
CURRENT_URL=$(python3 -c "
import json
with open('$GNAF_JSON') as f:
    print(json.load(f)['result']['resources'][0]['url'])
")

if [ "$NEW_URL" = "$CURRENT_URL" ]; then
    echo "G-NAF is already at the latest release: $(basename "$NEW_URL")"
    echo "No update needed."
    exit 0
fi

echo "New G-NAF release found:"
echo "  Current: $(basename "$CURRENT_URL")"
echo "  New:     $(basename "$NEW_URL")"
echo ""

# Fetch the file size for the progress display inside the loader
NEW_SIZE=$(curl -sI "$NEW_URL" | grep -i "^content-length:" | tr -d '\r' | awk '{print $2}')

# Update docker/gnaf-package.json with the new URL and size
echo "Updating docker/gnaf-package.json..."
python3 - <<PYEOF
import json
with open('$GNAF_JSON') as f:
    d = json.load(f)
d['result']['resources'][0]['url'] = '$NEW_URL'
d['result']['resources'][0]['size'] = int('$NEW_SIZE') if '$NEW_SIZE'.strip() else None
with open('$GNAF_JSON', 'w') as f:
    json.dump(d, f, indent=2)
    f.write('\n')
PYEOF

# Clear the loader's 24-hour cache of the package URL so it reads the
# updated docker/gnaf-package.json immediately rather than the cached old URL
rm -f "$GNAF_URL_CACHE"

# Remove the previously downloaded ZIP and extracted data
echo "Clearing old G-NAF download cache..."
rm -rf "$GNAF_DATA_DIR"

# Run the loader in the background.
# ES_CLEAR_INDEX=true drops the OpenSearch index before re-indexing so that
# addresses removed from the new G-NAF release do not linger in search results.
# The downside is that the geocoding service would therefore be empty for hours.
echo "Starting G-NAF data loader in the background..."
echo "Address searches will return no results during re-indexing (~1-2 hours)."
echo ""
docker compose exec -d addressr \
    sh -c 'ES_CLEAR_INDEX=false addressr-loader > /home/node/gnaf-loader.log 2>&1; echo "EXIT:$?" >> /home/node/gnaf-loader.log'

echo "To watch the loader output:"
echo "  docker compose exec addressr tail -f /home/node/gnaf-loader.log"
echo ""
echo "To check how many addresses have been indexed so far:"
echo "  curl http://localhost:9200/addressr/_count"
