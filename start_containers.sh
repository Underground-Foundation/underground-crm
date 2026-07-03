echo "Starting PostgreSQL…"
sudo systemctl start postgresql@18-main

echo "Starting Docker services (Redis, OpenSearch, Addressr)…"
sudo systemctl start docker

COMPOSE_FILE="../underground-crm/docker-compose.yml"
GNAF_JSON="$(dirname "$COMPOSE_FILE")/docker/gnaf-package.json"

# gnaf-api bind-mounts this file, but update-gnaf.sh (the script that normally
# creates it) refuses to run until the addressr container is already up — and
# addressr won't start until gnaf-api does. Create an empty placeholder here
# to break that chicken-and-egg problem on a first run; run ./update-gnaf.sh
# afterwards to replace it with the real G-NAF download URL.
if [ -d "$GNAF_JSON" ]; then
    echo "error: $GNAF_JSON is a directory, not a file." >&2
    echo "This happens when 'docker compose up' runs before the file exists — Docker silently creates a directory for a missing bind-mount source instead of failing." >&2
    echo "Fix with: sudo rmdir '$GNAF_JSON'" >&2
    exit 1
fi
if [ ! -f "$GNAF_JSON" ]; then
    echo "docker/gnaf-package.json not found; creating a placeholder (run ./update-gnaf.sh afterwards to load real G-NAF data)…"
    # The docker/ directory itself isn't tracked in git (only the file inside
    # it used to be), so a fresh clone won't have it yet either.
    mkdir -p "$(dirname "$GNAF_JSON")"
    cat > "$GNAF_JSON" <<'EOF'
{
  "result": {
    "resources": [
      {
        "state": "active",
        "mimetype": "application/zip",
        "url": "",
        "size": null
      }
    ]
  }
}
EOF
fi

docker compose --file "$COMPOSE_FILE" up
echo "Remember to start a qcluster for workers: python manage.py qcluster"