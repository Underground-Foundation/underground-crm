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

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("underground_crm", "0010_alter_basicpage_body_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
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
                "CREATE INDEX IF NOT EXISTS underground_crm_blogpost_author_id_idx ON underground_crm_blogpost (author_id);",
            ],
            reverse_sql=[
                "DROP TABLE IF EXISTS underground_crm_blogpost;",
                """
                ALTER TABLE underground_crm_basicpage ADD COLUMN IF NOT EXISTS author_id uuid NULL
                    REFERENCES underground_crm_person (id)
                    DEFERRABLE INITIALLY DEFERRED;
                """,
                "CREATE INDEX IF NOT EXISTS underground_crm_basicpage_author_id_idx ON underground_crm_basicpage (author_id);",
            ],
        ),
    ]
