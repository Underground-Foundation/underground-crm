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

# Two different users need to write here, and only one of them used to be checked.
#
# This script clears the download and URL caches below, and removing entries from a
# directory needs write permission on it for the *current* user — that is what `-w`
# tests. The loader then writes into the same directory as uid 65532, the Distroless
# runtime's nonroot user, which is neither the owner nor in the group, so only the
# "other" permission bits apply to it.
#
# Those two conditions used to coincide by accident: the pre-3.x image ran as the `node`
# user at uid 1000, which on a typical Linux dev machine is the developer's own uid.
# It no longer does, so a `-w` test alone would pass while the loader still fails partway
# through indexing. Both are checked.
if [ ! -d "$ADDRESSR_VOL_DIR" ]; then
    echo "error: $ADDRESSR_VOL_DIR does not exist."
    exit 1
fi
if [ ! -w "$ADDRESSR_VOL_DIR" ] || [ -z "$(find "$ADDRESSR_VOL_DIR" -maxdepth 0 -perm -o+rwx 2>/dev/null)" ]; then
    echo "error: $ADDRESSR_VOL_DIR must be writable both by you (this script clears the"
    echo "cached G-NAF download) and by the loader container's uid 65532."
    echo "Fix with:"
    echo "  sudo chmod -R o+rwx $(realpath "$ADDRESSR_VOL_DIR")"
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
# The loader runs in a container, so a loopback LOADER_ELASTIC_HOST names the container
# itself — never an SSH tunnel's listener, which is bound on this machine. Silently, too:
# the loader just retries "trying to reach elastic search on localhost:9200" forever while
# appearing to have started fine. Catch it here instead, since the value is usually left
# exported in a shell from an earlier attempt.
case "${LOADER_ELASTIC_HOST:-}" in
    localhost | 127.0.0.1 | ::1)
        echo "error: LOADER_ELASTIC_HOST=$LOADER_ELASTIC_HOST can never work."
        echo "The loader runs inside a container, where that address is the container"
        echo "itself. To index into a tunnelled remote OpenSearch, use:"
        echo "  LOADER_ELASTIC_HOST=host.docker.internal $0"
        echo "and bind the tunnel where the container can reach it — see the"
        echo "addressr-loader comments in docker-compose.yml."
        echo ""
        echo "To index into the local opensearch service instead, unset it:"
        echo "  unset LOADER_ELASTIC_HOST"
        exit 1
        ;;
esac

echo "Starting G-NAF data loader in the background..."
echo ""
LOADER_ES_CLEAR_INDEX=true docker compose --profile loader up -d addressr-loader

# Where to reach the index *from this machine*, which is not where the loader reaches it:
# host.docker.internal only resolves inside a container, so report the gateway address it
# maps to. The local-opensearch default is published on localhost by docker-compose.yml.
CHECK_HOST="${LOADER_ELASTIC_HOST:-localhost}"
CHECK_PORT="${LOADER_ELASTIC_PORT:-9200}"
if [ "$CHECK_HOST" = "host.docker.internal" ]; then
    CHECK_HOST=$(
        docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}' \
        2>/dev/null || echo "host.docker.internal"
    )
fi

echo "To watch the loader output:"
echo "  docker compose logs -f addressr-loader"
echo ""
echo "To check how many addresses have been indexed so far:"
echo "  curl http://$CHECK_HOST:$CHECK_PORT/addressr/_count"
