# Claude guidance for underground-crm

## What this project is

Underground CRM is a pip-installable Django library providing CRM, CMS, and bulk email
capabilities for political parties and grassroots movements. It is intentionally
open-source and vendor-neutral, designed to be reused by any party, not just Fusion.

During development, we have a companion deployment repo `fusion-underground` (a sibling directory),
which installs this library and adds Fusion-specific configuration and templates.

## Repo layout

```
underground_crm/          Django app (the pip-installable library)
  models/                 Core data models
  migrations/             Owned exclusively by this library — downstream sites never modify these
  management/commands/    CLI-only tools (no UI exposure)
  templates/              Base templates, overridable by downstream sites
  admin.py                Django admin registrations
  wagtail_hooks.py        Wagtail snippet and image format registrations

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
  Never use auto-increment integers for PKs on new models.
- `Person.legacy_id` — integer ID from the previous CRM, used only during migration for
  lookups and deduplication. Nullable, unique. (The only sequential integers are these
  `legacy_*` fields imported from the previous system.)
- `PersonNote.legacy_activity_id` — deduplication key for imported note activities. Nullable, unique.
- Money fields use `django-money` (`MoneyField`), default currency AUD.
- All timestamps are `DateTimeField` with `USE_TZ = True` (Australia/Melbourne).

## Migration philosophy

- This library owns its migrations. Downstream sites (`fusion-underground` etc.) must
  never create migrations that touch `underground_crm` models.
- Downstream sites extend Person via `OneToOneField` or `JSONField`, never by modifying
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
invoked via `python manage.py <command>` from the deployment repo (`fusion-underground`).
They are intentionally CLI-only — none of them are exposed through the Wagtail or
Django admin UI.

Current commands:
- `import_legacy_private_notes <legacy_person_id>` — imports private notes for one person.
  Requires `LEGACY_WEBSITE_URL`, `LEGACY_USER_AGENT`, and `LEGACY_COOKIE_FILE` env vars
  (or `--cookie-file` override). Idempotent via `legacy_activity_id` deduplication.

## Running locally

The deployment project is `../fusion-underground`. From that directory:

```bash
source .venv/bin/activate
python manage.py migrate
python manage.py runserver
```

Database is PostgreSQL (`fusion_underground`). Settings read from environment variables;
copy `.env.example` to `.env` and fill in values.

## Auth

- `AUTH_USER_MODEL = "underground_crm.Person"`
- Login field: email (no username)
- Django admin: `/django-admin/`
- Wagtail CMS: `/cms/`
- Public auth views: `/account/login/`, `/account/signup/`, `/account/logout/`

## Final instructions
For consistency with global coding conventions (not because I prefer it), all
code should be in US English.

Please use US English spelling for the documentation too, but maintain clearer grammar than 
mainstream US parlance. Comments (and your own speech) should always be clear prose, never 
"keyword soup". A "grammar Nazi" should not be able to criticize you for omitting words.
