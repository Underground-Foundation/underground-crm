# Underground CRM
This project aims to provide political movements with the following key functionality:
* Customer Relationship Management (CRM) − keeping a database of people and keeping tabs on your ongoing relationship with them
* Content Management System (CMS) − allowing your somewhat technical organisers to publish web pages regularly
* Bulk emails − connect SendGrid to send bulk emails to your members

The service will also come with migration tools to help you move from your pre-existing CRM.

## Setup

Underground CRM is a pip-installable Django library. It is not a standalone
project — you run it by installing it into a **deployment project** that
provides site-specific settings, templates, and static files. The sibling
repo `fusion-underground` is the reference deployment for the Fusion Party,
and is the easiest way to get started.

### Building a new deployment project

If you want to build a deployment for a different organisation rather than
using `fusion-underground`, create a new Django project and wire in the
library:

```bash
mkdir my-deployment && cd my-deployment
python -m venv .venv && source .venv/bin/activate
pip install underground-crm
django-admin startproject my_site .
```

Add the following to `INSTALLED_APPS` in your settings file, and set
`AUTH_USER_MODEL`:

```python
INSTALLED_APPS = [
    "underground_crm",
    "wagtail.contrib.forms",
    "wagtail.contrib.redirects",
    "wagtail.embeds",
    "wagtail.sites",
    "wagtail.users",
    "wagtail.snippets",
    "wagtail.documents",
    "wagtail.images",
    "wagtail.search",
    "wagtail.admin",
    "wagtail",
    "taggit",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

AUTH_USER_MODEL = "underground_crm.Person"
WAGTAIL_SITE_NAME = "My Site"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
```

Then follow the setup steps from fusion-underground.

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

Get the cookie file using eg the Chrome plugin [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc?hl=fr&utm_source=ext_sidebar):

![A screenshot of the cookies extension](./docs/cookies_extension.png)

Get the user agent from eg <https://whatmyuseragent.com/>

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
