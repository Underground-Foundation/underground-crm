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
        print(f"       Add it to {_env_path} or export it before running this script.", file=sys.stderr)
        sys.exit(1)
    return val


LEGACY_API_TOKEN = _require("LEGACY_API_TOKEN")
LEGACY_WEBSITE_URL = _require("LEGACY_WEBSITE_URL").rstrip("/")
LEGACY_API_URL = _require("LEGACY_API_URL").rstrip("/")
LEGACY_USER_AGENT = _require("LEGACY_USER_AGENT")
LEGACY_COOKIE_FILE = _require("LEGACY_COOKIE_FILE")

CLOUDFLARE_TOKEN = _require("CLOUDFLARE_TOKEN")
CLOUDFLARE_ACCOUNT_ID = _require("CLOUDFLARE_ACCOUNT_ID")
