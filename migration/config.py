"""
Shared configuration for migration scripts.

Loads environment variables from ../.env (i.e. underground-crm/.env).
All scripts in this directory should import their config from here
rather than hardcoding values.
"""

import os
import sys
from pathlib import Path

# Load .env from the underground-crm root (parent of this migration/ directory)
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip())


def _require(name):
    val = os.environ.get(name)
    if not val:
        print(f"ERROR: {name} environment variable is not set.", file=sys.stderr)
        print(
            f"       Add it to {_env_path} or export it before running this script.",
            file=sys.stderr,
        )
        sys.exit(1)
    return val


CLOUDFLARE_ACCOUNT_ID = _require("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_SCRAPING_TOKEN = _require("CLOUDFLARE_SCRAPING_TOKEN")
CLOUDFLARE_ASSETS_ENDPOINT = _require("CLOUDFLARE_ASSETS_ENDPOINT")
CLOUDFLARE_ASSETS_BUCKET = _require("CLOUDFLARE_ASSETS_BUCKET")
CLOUDFLARE_PUBLIC_ASSET_URL = _require("CLOUDFLARE_PUBLIC_ASSET_URL").rstrip("/")
LEGACY_API_TOKEN = _require("LEGACY_API_TOKEN")
LEGACY_ADMIN_URL = _require("LEGACY_ADMIN_URL").rstrip("/")
LEGACY_API_URL = _require("LEGACY_API_URL").rstrip("/")
# Every base URL that legacy-uploaded images/documents/theme assets are served from, as
# linked in fetched HTML. A page can link assets from more than one such host (its own
# uploads, plus shared theme/framework assets on a separate host or path) — comma-separated.
LEGACY_ASSET_URLS = tuple(
    url.strip().rstrip("/") for url in _require("LEGACY_ASSET_URLS").split(",") if url.strip()
)
LEGACY_USER_AGENT = _require("LEGACY_USER_AGENT")
LEGACY_ADMIN_COOKIE_FILE = _require("LEGACY_ADMIN_COOKIE_FILE")
LEGACY_VIEWER_COOKIE_FILE = _require("LEGACY_VIEWER_COOKIE_FILE")
