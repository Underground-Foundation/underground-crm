# Hand-written. 0002_pages was rewritten (in the "Form progress" commit) to
# fold BlogPost's CreateModel into an already-applied migration, so it was
# never actually run against existing databases. The same rewrite dropped
# the "author" field from BasicPage in favour of BlogPost, but the stray
# column was likewise never removed. This migration only touches the
# database, not migration state: the state already matches these models.
#
# Every statement is written IF [NOT] EXISTS: a database created fresh from
# this migration history never had the stray author_id column or a missing
# blogpost table to begin with (0002_pages already declares the correct
# state), so this migration is only a no-op there — the repair is for
# databases that reached this point via the drifted history above.
#
# The repair runs on PostgreSQL alone, which is the only backend any such
# drifted database can be on: PostgreSQL is what this library is deployed on,
# and the SQLite fallback (used by CI, see settings.py) only ever builds a
# fresh in-memory database, which by the reasoning above needs no repair.
# The statements are PostgreSQL-specific in any case — SQLite supports
# neither DROP COLUMN IF EXISTS nor DEFERRABLE foreign keys.

from typing import Sequence

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

REPAIR_SQL: Sequence[str] = [
    "ALTER TABLE underground_crm_basicpage DROP COLUMN IF EXISTS author_id;",
    """
    CREATE TABLE IF NOT EXISTS underground_crm_blogpost (
        undergroundbasicpage_ptr_id integer NOT NULL PRIMARY KEY
            REFERENCES underground_crm_undergroundbasicpage (basicpage_ptr_id)
            DEFERRABLE INITIALLY DEFERRED,
        author_id uuid NULL
            REFERENCES underground_crm_person (id)
            DEFERRABLE INITIALLY DEFERRED
    );
    """,
    "CREATE INDEX IF NOT EXISTS underground_crm_blogpost_author_id_idx "
    "ON underground_crm_blogpost (author_id);",
]

UNDO_SQL: Sequence[str] = [
    "DROP TABLE IF EXISTS underground_crm_blogpost;",
    """
    ALTER TABLE underground_crm_basicpage ADD COLUMN IF NOT EXISTS author_id uuid NULL
        REFERENCES underground_crm_person (id)
        DEFERRABLE INITIALLY DEFERRED;
    """,
    "CREATE INDEX IF NOT EXISTS underground_crm_basicpage_author_id_idx "
    "ON underground_crm_basicpage (author_id);",
]

POSTGRESQL = "postgresql"


def _run_on_postgresql(schema_editor: BaseDatabaseSchemaEditor, statements: Sequence[str]) -> None:
    if schema_editor.connection.vendor != POSTGRESQL:
        return
    for statement in statements:
        schema_editor.execute(statement)


def repair_blogpost_table(_apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    _run_on_postgresql(schema_editor, REPAIR_SQL)


def undo_blogpost_repair(_apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    _run_on_postgresql(schema_editor, UNDO_SQL)


class Migration(migrations.Migration):

    dependencies = [
        ("underground_crm", "0010_alter_basicpage_body_and_more"),
    ]

    operations = [
        migrations.RunPython(repair_blogpost_table, undo_blogpost_repair),
    ]
