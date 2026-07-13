# Hand-written so it stays scoped to retiring the primary_address role: a
# person's addresses are now only the concrete roles (home, mailing,
# registered, billing), and the legacy CRM's "primary" address is folded into
# those on import (see import_people_csv.assign_addresses).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("underground_crm", "0003_seed_data"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="person",
            name="primary_address",
        ),
        migrations.RemoveField(
            model_name="historicalperson",
            name="primary_address",
        ),
    ]
