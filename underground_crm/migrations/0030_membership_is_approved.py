from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Add Membership.is_approved. The column is first added with a default of
    True, so that every membership granted before approval existed is
    approved, and the default is then changed to False, so that each new
    membership awaits approval.
    """

    dependencies = [
        ("underground_crm", "0029_alter_person_is_supporter_default"),
    ]

    operations = [
        migrations.AddField(
            model_name="membership",
            name="is_approved",
            field=models.BooleanField(
                default=True,
                help_text="Whether staff have approved this membership.",
                verbose_name="Is approved",
            ),
        ),
        migrations.AlterField(
            model_name="membership",
            name="is_approved",
            field=models.BooleanField(
                default=False,
                help_text="Whether staff have approved this membership.",
                verbose_name="Is approved",
            ),
        ),
    ]
