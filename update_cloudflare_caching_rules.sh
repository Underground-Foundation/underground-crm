#!/bin/sh
# Creates or updates the Cloudflare Cache Rule that lets Cloudflare cache an
# underground-crm deployment's pages.
#
# By default Cloudflare caches only files with a static extension (images, CSS, JS) and
# never consults the Cache-Control header of an HTML or JSON response. This rule makes the
# deployment's pages eligible for caching, and then leaves the decision to the headers that
# underground-crm sends (see PageWithMetadata.cache_time and the /health/ view):
#
#   * Edge TTL "bypass_by_default": a response is cached for as long as its Cache-Control
#     header allows, and not at all if it has no Cache-Control header. Responses marked
#     no-store or private, and responses that set a cookie, are never cached.
#   * Browser TTL "respect_origin": browsers follow the same header.
#   * Logged-in visitors are served from the same cache as everyone else. Pages are the
#     same for every visitor; whatever is specific to a visitor is fetched at runtime by
#     the React islands from /api/, which the rule excludes.
#   * Paths that serve the admin interfaces, account management, the API and payments are
#     excluded outright. They do not send public Cache-Control headers either; this only
#     keeps them out of the cache if one of them ever did.
#
# Other Cache Rules in the zone are left untouched: this script finds its own rule by its
# `ref` (or, failing that, its description) and replaces only that rule.
#
# The API token needs one permission, "Zone > Cache Rules > Edit". The steps for creating
# one are in token_instructions() below, which prints them whenever no token is supplied.
#
# Usage:
#   CLOUDFLARE_ZONE_ID=<zone ID> update_cloudflare_caching_rules.sh -H HOSTNAME [-n]
#
#   -H   The hostname whose pages are cached, e.g. www.example.org.
#   -n   Validate the rule with Cloudflare (dry_run=true) without deploying it.
#
# Environment:
#   CLOUDFLARE_ZONE_ID     Required. The ID of the zone that serves HOSTNAME, shown under
#                          "API" on the zone's Overview page in the dashboard.
#   CLOUDFLARE_API_TOKEN   The API token. Prompted for if unset, so that it appears neither
#                          on the command line, where `ps` would show it, nor in shell
#                          history.

set -eu

CACHED_HOSTNAME=""
DRY_RUN=0

usage() {
  echo "Usage: CLOUDFLARE_ZONE_ID=<zone ID> $0 -H HOSTNAME [-n]" >&2
  exit 1
}

while getopts "H:n" opt; do
  case "$opt" in
    H) CACHED_HOSTNAME="$OPTARG" ;;
    n) DRY_RUN=1 ;;
    *) usage ;;
  esac
done

[ -n "$CACHED_HOSTNAME" ] || usage

if [ -z "${CLOUDFLARE_ZONE_ID:-}" ]; then
  cat >&2 <<'EOF'
CLOUDFLARE_ZONE_ID is not set. To find the zone ID, open the zone in
https://dash.cloudflare.com, and copy "Zone ID" from the "API" section of its
Overview page.
EOF
  usage
fi

token_instructions() {
  cat >&2 <<'EOF'
No Cloudflare API token was supplied in CLOUDFLARE_API_TOKEN. To create one:

  1. Open https://dash.cloudflare.com/profile/api-tokens and choose "Create Token".
  2. Choose "Create Custom Token". None of the templates, such as "Edit zone DNS",
     grants access to Cache Rules.
  3. Under "Permissions", add: Zone | Cache Rules | Edit
     This is the only permission that this script needs.
  4. Under "Zone Resources", choose: Include | Specific zone | <the zone of -H>
  5. Choose "Continue to summary", then "Create Token", and copy the token. Cloudflare
     shows it only once.

EOF
}

if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
  token_instructions
  if [ ! -t 0 ]; then
    echo "Set CLOUDFLARE_API_TOKEN, or run this script from a terminal to be prompted for it." >&2
    exit 1
  fi
  printf "Cloudflare API token: " >&2
  stty -echo
  trap 'stty echo' EXIT INT TERM
  read -r CLOUDFLARE_API_TOKEN
  stty echo
  trap - EXIT INT TERM
  echo >&2
fi

CLOUDFLARE_API_TOKEN="$CLOUDFLARE_API_TOKEN" \
CLOUDFLARE_ZONE_ID="$CLOUDFLARE_ZONE_ID" \
CACHED_HOSTNAME="$CACHED_HOSTNAME" \
DRY_RUN="$DRY_RUN" \
python3 - <<'PY'
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.cloudflare.com/client/v4"
PHASE = "http_request_cache_settings"
# Identifies this script's rule among any others in the zone's Cache Rules.
RULE_REF = "underground_crm_page_caching"
EXCLUDED_PATH_PREFIXES = (
    "/cms/",
    "/django-admin/",
    "/account/",
    "/api/",
    "/payments/",
)

zone_id = os.environ["CLOUDFLARE_ZONE_ID"]
hostname = os.environ["CACHED_HOSTNAME"]
dry_run = os.environ["DRY_RUN"] == "1"


def quoted(value: str) -> str:
    return json.dumps(value)


expression = " and ".join(
    [
        f"(http.host eq {quoted(hostname)})",
        *(
            f"not starts_with(http.request.uri.path, {quoted(prefix)})"
            for prefix in EXCLUDED_PATH_PREFIXES
        ),
    ]
)

rule = {
    "ref": RULE_REF,
    "description": f"underground-crm: cache {hostname} as its Cache-Control headers allow",
    "expression": expression,
    "action": "set_cache_settings",
    "action_parameters": {
        "cache": True,
        "edge_ttl": {"mode": "bypass_by_default"},
        "browser_ttl": {"mode": "respect_origin"},
    },
    "enabled": True,
}


def call(method: str, path: str, body=None, *, missing_ok: bool = False):
    query = "?dry_run=true" if dry_run and method != "GET" else ""
    request = urllib.request.Request(
        f"{API}{path}{query}",
        method=method,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)["result"]
    except urllib.error.HTTPError as error:
        if missing_ok and error.code == 404:
            return None
        try:
            errors = json.load(error).get("errors")
        except ValueError:
            errors = None
        sys.exit(f"{method} {path} failed with HTTP {error.code}: {errors or error.reason}")
    except urllib.error.URLError as error:
        sys.exit(f"{method} {path} failed: {error.reason}")


print("Rule expression:")
print(f"  {expression}")

zone = urllib.parse.quote(zone_id, safe="")
entrypoint = call(
    "GET", f"/zones/{zone}/rulesets/phases/{PHASE}/entrypoint", missing_ok=True
)

if entrypoint is None:
    # The zone has no Cache Rules yet. A PUT to the entry point creates the ruleset; it
    # replaces every rule in the phase, which is safe only because there are none.
    call("PUT", f"/zones/{zone}/rulesets/phases/{PHASE}/entrypoint", {"rules": [rule]})
    outcome = "create the zone's Cache Rules with this rule"
else:
    ruleset_id = urllib.parse.quote(entrypoint["id"], safe="")
    existing = next(
        (
            r
            for r in entrypoint.get("rules", [])
            if r.get("ref") == RULE_REF or r.get("description") == rule["description"]
        ),
        None,
    )
    if existing is None:
        call("POST", f"/zones/{zone}/rulesets/{ruleset_id}/rules", rule)
        outcome = "add the rule to the zone's Cache Rules"
    else:
        rule_id = urllib.parse.quote(existing["id"], safe="")
        call("PATCH", f"/zones/{zone}/rulesets/{ruleset_id}/rules/{rule_id}", rule)
        outcome = "replace the existing rule in the zone's Cache Rules"

if dry_run:
    print(f"Cloudflare accepted the request to {outcome}. Nothing was deployed (-n).")
else:
    print(f"Done: {outcome}.")
PY
