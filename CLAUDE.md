# Claude guidance for underground-crm

## What this project is

Underground CRM is a pip-installable Django library providing CRM, CMS, and bulk email
capabilities for political parties and grassroots movements. It is intentionally
open-source and vendor-neutral, designed to be reused by any party, not just Fusion.

Downstream **theme projects** install this library and contribute only what is
organisation-specific: a site name, branding templates, and any custom page models.
The sibling repo `fusion-underground` is the reference theme for the Fusion Party.

## Repo layout

```
underground_crm/          Django app (the pip-installable library)
  models/                 Core data models
  migrations/             Owned exclusively by this library — downstream themes never modify these
  management/commands/    CLI-only tools (no UI exposure)
  templates/              Base templates, overridable by downstream themes
  admin.py                Django admin registrations
  wagtail_hooks.py        Wagtail snippet and image format registrations
  settings.py             Base Django settings — theme projects import and extend these
  site_urls.py            Default root URL configuration — theme projects may override if needed

migration/                One-off data migration scripts (not Django management commands)
  config.py               Loads ../.env and exports LEGACY_* constants — import from here
  fetch_all_interactions.py   Fetch contact/interaction records from the legacy CRM API
  fetch_all_private_notes.py  Fetch private notes from the legacy CRM (requires cookie auth)
```

## Key models

| Model | Purpose |
|-------|---------|
| `Person` | Custom auth user model (`AUTH_USER_MODEL`). Email is the login field, no username. |
| `Tag` | M2M tags on Person. |
| `Address` | Reusable address record. Person has several address FKs. |
| `Donation` | Individual donation transactions. Person also holds aggregate totals. |
| `Membership` / `MembershipType` | Party membership records with expiry and suspension. |
| `Engagement` | Single-action engagement log (signup, donated, attended_event, etc.). |
| `Interaction` | Logged staff–person contact (phone call, face-to-face, email, SMS, etc.). |
| `PersonNote` | Freetext staff notes about a person, with optional author FK. |
| `BasicPage` | Wagtail page with StreamField body (RichText, RawHTML, Image, BlockQuote blocks). |

## Important field conventions

- **Primary keys:** All models owned by this library use
  `UUIDField(primary_key=True, default=uuid.uuid4, editable=False)`.
  Never use auto-increment integers for PKs on new models. UUID PKs support
  federated data merging: independent CRM instances can share and merge records
  without integer PK collisions.
- **M2M through tables:** Django auto-generates through tables for `ManyToManyField`
  without an explicit `through=` argument. Any M2M relationship that belongs to this
  library must use an explicit through model that declares its own `UUIDField` PK,
  following the same convention as every other model. `AppConfig.default_auto_field`
  is set to `UUIDAutoField` as a safety net, but it must never be relied upon as a
  substitute for an explicit through model — a missing `through=` would not be caught
  until the mismatch between the migration and the database causes a runtime error.
- **Page models:** Wagtail's `Page` base class uses integer PKs internally (required by
  `treebeard`, the tree library Wagtail uses for page trees). The PK type cannot be
  changed. If federation across CRM instances requires stable page identifiers, add a
  secondary `UUIDField` (non-PK, `unique=True`) to the relevant page model rather than
  attempting to change the PK.
- `Person.legacy_id` — integer ID from the previous CRM, used only during migration for
  lookups and deduplication. Nullable, unique. (The only sequential integers are these
  `legacy_*` fields imported from the previous system.)
- `PersonNote.legacy_activity_id` — deduplication key for imported note activities. Nullable, unique.
- Money fields use `django-money` (`MoneyField`), default currency AUD.
- All timestamps are `DateTimeField` with `USE_TZ = True` (Australia/Melbourne).

## Migration philosophy

- This library owns its migrations. Downstream themes (`fusion-underground` etc.) must
  never create migrations that touch `underground_crm` models.
- Downstream themes extend Person via `OneToOneField` or `JSONField`, never by modifying
  the library's models directly.
- When cleaning up migration history, apply migrations first, then edit the files — Django
  only validates that the current model state matches the current DB state.

## Data migration scripts (`migration/`)

These are standalone Python scripts for one-off imports from a previous CRM. They are
**not** Django management commands and are run directly with `python`.

**The migration flow is intentionally general.** The scripts abstract all
legacy-system specifics behind environment variables (`LEGACY_WEBSITE_URL`,
`LEGACY_API_URL`, `LEGACY_API_TOKEN`, etc.) and should not be written in a way
that is overfit to any particular competitor product. If you find yourself
hardcoding vendor-specific API paths, endpoint names, or data structures as
constants in the business logic, move them behind the `LEGACY_*` env var
abstraction instead.

All scripts import shared config from `config.py`, which reads `../.env`
automatically. The `.env` file is gitignored; see `.env.example` for the
required variables.

Output is newline-delimited JSON to stdout; progress and warnings go to stderr.
This makes it easy to pipe output to a file or directly into an import command.

## Django management commands

Management commands live in `underground_crm/management/commands/` and are
invoked via `python manage.py <command>` from the theme repo (`fusion-underground`).
They are intentionally CLI-only — none of them are exposed through the Wagtail or
Django admin UI.

Current commands:
- `import_legacy_private_notes <legacy_person_id>` — imports private notes for one person.
  Requires `LEGACY_WEBSITE_URL`, `LEGACY_USER_AGENT`, and `LEGACY_ADMIN_COOKIE_FILE` env vars
  (or `--cookie-file` override). Idempotent via `legacy_activity_id` deduplication.

## Process separation

The application is designed to run as three separate processes sharing the
same codebase and database:

1. **Web process** (gunicorn / `manage.py runserver`) — serves CRM routes, CMS
   pages, Wagtail admin, the REST API, and email webhook endpoints.  Webhook
   events are accepted and returned immediately; the actual processing is
   enqueued to the email worker.

2. **CRM worker** (`manage.py qcluster`) — processes lightweight background
   tasks: address geocoding, engagement recording, and RSVP tracking.

3. **Email worker** (`Q_CLUSTER_NAME=email manage.py qcluster`) — processes
   heavy email operations: campaign dispatch, SMTP2Go result polling, and
   webhook event handling (spam / unsubscription).

This separation prevents bulk email campaigns from starving CRM tasks for
worker slots and isolates failures: a crash in the web process does not halt
active email dispatch, and an exception in an email task does not bring down
the public site.

Tasks are routed to the email worker by passing `cluster="email"` to
`async_task()` or `schedule()`.  Tasks without a `cluster` argument are
handled by the default CRM worker.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `Q_CRM_WORKERS` | `2` | Number of worker threads in the default (CRM) cluster |
| `Q_CRM_QUEUE_LIMIT` | `50` | Maximum queued tasks before the CRM cluster stops accepting |
| `Q_CRM_TIMEOUT` | `300` | Task timeout in seconds for CRM tasks |
| `Q_CRM_RETRY` | `600` | Retry delay in seconds for failed CRM tasks |
| `Q_EMAIL_WORKERS` | `1` | Number of worker threads in the email cluster |
| `Q_EMAIL_QUEUE_LIMIT` | `10` | Maximum queued tasks before the email cluster stops accepting |
| `Q_EMAIL_TIMEOUT` | `3600` | Task timeout in seconds for email tasks (1 hour) |
| `Q_EMAIL_RETRY` | `7200` | Retry delay in seconds for failed email tasks (2 hours) |
| `DB_CONN_MAX_AGE` | `600` | Database connection lifetime in seconds (0 for per-request) |

## Addressr (Australian address search)

`docker-compose.yml` runs Addressr backed by OpenSearch. The G-NAF dataset
(~1.7 GB) must be loaded into OpenSearch before address search will work.
Run `update-gnaf.sh` for the initial load and for each quarterly update —
it fetches the latest release URL from data.gov.au, updates
`docker/gnaf-package.json`, and runs the loader in the background. Indexing
~15 million addresses takes 1–2 hours in total.

**Background:** Addressr's loader normally fetches the G-NAF download URL from
data.gov.au's CKAN API (`/api/3/action/package_show`), but that API has been
removed. The `gnaf-api` nginx service in `docker-compose.yml` serves a static
mock of that API response from `docker/gnaf-package.json`, and the addressr
service is pointed at it via `GNAF_PACKAGE_URL=http://gnaf-api/gnaf-package.json`.

`NODE_OPTIONS=--max-old-space-size=8192` is set on the addressr service because
the loader OOMs with Node.js's default heap on large states (NSW has 5M addresses).

## Running locally

The theme project is `../fusion-underground`. From that directory:

```bash
source .venv/bin/activate
python manage.py migrate
python manage.py runserver
```

Database is PostgreSQL. Settings are read from environment variables; copy
`.env.example` to `.env` and fill in values.

## Auth

- `AUTH_USER_MODEL = "underground_crm.Person"`
- Login field: email (no username)
- Django admin: `/django-admin/`
- Wagtail CMS: `/cms/`
- Public auth views: `/account/login/`, `/account/signup/`, `/account/logout/`

## Python style
Please use type hints for Python as much as possible, even though it's not strictly required. 

## CLI argument style

Always use named arguments (e.g. `--slug foo`) rather than positional arguments in
management commands and migration scripts. This applies to new code and to any
arguments added to existing commands.

## Final instructions
For consistency with global coding conventions (not because I prefer it), all
code should be in US English for spelling. Please stick to UK English for grammar though − 
do not shorten words into the wrong conjugation, like "invite" as a noun instead of "invitation", or
"unsubscribe" as a modifier instead of "unsubscription" (e.g. `unsubscription_url`, `unsubscription_view`,
`make_unsubscription_url` — not `unsubscribe_url`, `unsubscribe_view`, `make_unsubscribe_url`). The
verb form is only correct when the identifier literally names the action of unsubscribing (e.g.
`unsubscribe_via_email_campaign`). Apply this principle to all similar verb/noun pairs.

Please use US English spelling for the documentation too, but maintain clearer grammar than
mainstream US parlance. Comments (and your own speech) should always be clear prose, never
"keyword soup". A "grammar Nazi" should not be able to criticize you for omitting words.
