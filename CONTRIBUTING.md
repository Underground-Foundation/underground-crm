# Contributing to Underground CRM

Thank you for your interest in contributing to Underground CRM! We welcome contributions from the community to help make this toolkit a robust, secure, and vendor-neutral solution for grassroots movements and political organizations.

Please read through this document to understand our development workflow, coding standards, and contribution process.

---

## Development Setup

Underground CRM is designed to be installed as a library inside a downstream theme project (such as our reference theme, `fusion-underground`). 

### 1. Clone the Repository
Clone this repository alongside your theme project directory:
```bash
git clone https://github.com/fusion-party/underground-crm.git
cd underground-crm
```

### 2. Set Up a Virtual Environment
Create and activate a Python virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies
Install Underground CRM in editable mode along with development and migration dependencies:
```bash
pip install -e .[dev,migration]
```

### 4. Set Up Accompanying Services
Underground CRM requires Redis, PostgreSQL, and OpenSearch. A `docker-compose.yml` file is provided in this repository to run these services locally:
```bash
# Start Redis, OpenSearch, and Addressr autocomplete services
docker compose up -d
```

To load Australian Address data (G-NAF) into OpenSearch:
```bash
./update-gnaf.sh
```
*Note: The G-NAF download and indexing process takes approximately 1 to 2 hours to complete.*

---

## Coding Standards

To maintain consistency and code quality across the codebase, we enforce the following rules:

### Python Conventions
* **Type Hints**: Please use type hints in Python functions and class definitions as much as possible.
* **Code Style**: Code must be formatted using [Black](https://github.com/psf/black) with a line length limit of 100 characters.
* **Linting**: We use `pylint` (configured with `pylint-django`) to check for errors and style issues. Run the linter before submitting code.

### Database Design and Migrations
* **Primary Keys**: All models owned by this library must use a UUID primary key:
  ```python
  import uuid
  from django.db import models

  class MyModel(models.Model):
      id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
  ```
  Never use auto-incrementing integers for primary keys on new models.
* **Many-to-Many Relationships**: Any `ManyToManyField` added to this library must specify an explicit `through` model. The through model must declare its own UUID primary key. Do not rely on Django's auto-generated through tables.
* **Migrations**: This library owns its migrations. Downstream themes must never modify or create migrations that target `underground_crm` models.

### CLI and Script Arguments
* Always use named arguments (e.g., `--slug foo`) instead of positional arguments in Django management commands and migration scripts.

### Spelling and Grammar Style Guidelines
We adhere to a specific blend of spelling and grammatical rules:
* **US English Spelling**: All code, documentation, and comments must use US English spelling (e.g., `behavior`, `color`, `organization`, `deduplication`).
* **UK English Grammar**: We use UK English grammar for word conjugations. Do not shorten nouns into verbs or modify words into incorrect conjugations. Specifically:
  * Use the noun form for modifiers and identifiers: `unsubscription_url`, `unsubscription_view`, or `make_unsubscription_url` (do **not** use `unsubscribe_url` or `unsubscribe_view`).
  * The verb form is only permitted when the identifier literally describes the action of unsubscribing (e.g., `unsubscribe_via_email_campaign`).
  * Use correct noun forms such as `invitation` instead of `invite` (e.g., `invitation_code` rather than `invite_code`).
* **Clear Prose**: Do not use "keyword soup" in your code comments, documentation, or commit messages. Write in clear, complete sentences with proper punctuation.

---

## Testing

We use `pytest` as our testing framework. You can configure testing environment variables in `pytest.ini` and pytest fixtures in `conftest.py`.

To run the full test suite, run:
```bash
pytest
```

Ensure all tests pass and your changes are fully covered by unit tests before submitting a pull request.

---

## Submission Process

1. **Create a Branch**: Create a descriptive feature branch from `master` (or `main`):
   ```bash
   git checkout -b feature/add-new-capability
   ```
2. **Format and Lint**: Format your changes using Black and verify they pass lint checks:
   ```bash
   black .
   pylint underground_crm
   ```
3. **Submit a Pull Request**: Push your branch and open a pull request. In the description, clearly explain:
   * What problem the change solves.
   * How the change was tested.
   * Any impacts on downstream themes.
