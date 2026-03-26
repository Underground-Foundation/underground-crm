# Underground CRM

Underground CRM is an open-source Django library that gives political movements
three core capabilities:

- **CRM** — a database of people and a full record of your ongoing relationship
  with each of them
- **CMS** — Wagtail-powered page editing for your organisers
- **Bulk email** — SendGrid integration for member communications

Migration tooling is included to help you move from a previous CRM.

## Architecture

Underground CRM is a pip-installable Django library, not a standalone project.
You run it by creating a **theme project** that installs the library and provides
a site name, branding templates, and any organisation-specific page models.

The sibling repo `fusion-underground` is the reference theme for the Fusion
Party and is the easiest way to see a working deployment.

## Setting up a theme project

```bash
mkdir my-theme && cd my-theme
python -m venv .venv && source .venv/bin/activate
pip install underground-crm
django-admin startproject my_site .
```

In `my_site/settings.py`, replace the generated contents with:

```python
import os
from pathlib import Path
from underground_crm.settings import *  # noqa: F401, F403

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost 127.0.0.1").split()

INSTALLED_APPS = ["my_app"] + INSTALLED_APPS  # noqa: F405

WSGI_APPLICATION = "my_site.wsgi.application"

STATIC_ROOT = BASE_DIR / "static"
MEDIA_ROOT = BASE_DIR / "media"

WAGTAIL_SITE_NAME = "My Organisation"
```

The base settings supply everything else: installed apps, middleware, URL
routing, auth model, database config (via `PG*` env vars), Wagtail, and
timezone. See `underground_crm/settings.py` for the full list.

Then apply migrations and create a superuser:

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

- Django admin: `/django-admin/`
- Wagtail CMS: `/cms/`
- Login: `/account/login/`

---

## Data migration

If you are migrating people and activity records from a previous CRM, fill
in the `LEGACY_*` environment variables in your `.env`:

| Variable | Description |
|---|---|
| `LEGACY_WEBSITE_URL` | Base URL of the legacy CRM (no trailing slash). |
| `LEGACY_API_URL` | Base URL of the legacy REST API. |
| `LEGACY_API_TOKEN` | Bearer token for the legacy API. |
| `LEGACY_USER_AGENT` | Browser user-agent string for cookie-authenticated requests. |
| `LEGACY_COOKIE_FILE` | Path to a Netscape-format cookie file exported from your browser. |

### Import people from a CSV export

```bash
python manage.py import_people_csv people.csv
```

To also import interactions and private notes from the live legacy CRM during
the same run:

```bash
python manage.py import_people_csv people.csv --with-interactions --with-notes
```

Pass `--dry-run` to preview what would be imported without writing anything.
The command is idempotent — it is safe to run multiple times.

### Import private notes for a single person

```bash
python manage.py import_legacy_private_notes <legacy_person_id>
```

Requires a valid cookie file pointed to by `LEGACY_COOKIE_FILE` (or passed
via `--cookie-file`). The legacy admin session must still be active.

Get the cookie file using e.g. the Chrome extension
[Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc?hl=fr&utm_source=ext_sidebar):

![A screenshot of the cookies extension](./docs/cookies_extension.png)

Get the user agent from e.g. <https://whatmyuseragent.com/>

### Standalone fetch scripts

The `migration/` directory contains standalone Python scripts for fetching
data from the legacy CRM and writing newline-delimited JSON to stdout:

```bash
cd migration
python fetch_all_interactions.py          # all interactions
python fetch_all_interactions.py 12345    # one person
python fetch_all_private_notes.py         # all private notes
python fetch_all_private_notes.py 12345   # one person
```

Pipe the output to a file for later import:

```bash
python fetch_all_interactions.py > interactions.ndjson
```

These scripts read their configuration from `../.env` automatically.
