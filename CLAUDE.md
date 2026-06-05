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

## Testing
Do not use "magic numbers" or equivalently, "magic variables" of any sort in tests. If you are expecting
some result to be `2` for instance, that should be a constant that gets fed in as an input to the
function you're testing. Especially when this cannot be done, there should be a message for the assertion,
explaining why this is a valid assertion.

If anyone sees that the test fails, they are going to question the legitimacy of the test. You need to
ensure that like any code, the tests are *justifiable*. It can be seen that they're providing a useful
function and that they can be trusted. Being transparent about what they're doing is a good way of 
building trust.

In ensuring that the tests are useful, you should obviously keep the use of mocks to a minimum. Perhaps you'll
need to add extra arguments to the functions you're testing, to eg override a timestamp being used. This is
much better than mocking anything, as mocks break the intended encapsulation, inherently making assertions
that are beyond their remit.

If you need to use an external service and it's not available, perhaps create one test with a mock of it,
and another one that calls `skip()` when it finds that the service is not available. Some people will
complain that we are creating "unit tests" and that by definition, "unit tests" shouldn't call external
services. I don't care what you call the tests you're creating; they need to be able to verify an actual
system, not a thought experiment.

For the tests, please always use plausible data. Not anything like foo="bar". Humans need to read the tests
and make sense of them. They'll be comparing the examples to real-world scenarios, not algebraic puzzles.

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
