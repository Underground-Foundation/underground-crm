import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("underground_crm", "0015_alter_personfilter_criteria"),
        ("wagtailcore", "0094_alter_page_locale"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="urlredirect",
            options={
                "verbose_name": "Redirection",
                "verbose_name_plural": "Redirections",
            },
        ),
        migrations.RenameModel(
            old_name="UrlRedirect",
            new_name="UrlRedirection",
        ),
        migrations.RenameField(
            model_name="urlredirection",
            old_name="redirect_url",
            new_name="destination_url",
        ),
        migrations.RenameField(
            model_name="urlredirection",
            old_name="redirect_page",
            new_name="destination_page",
        ),
        migrations.AlterField(
            model_name="urlredirection",
            name="destination_page",
            field=models.ForeignKey(
                blank=True,
                help_text="The on-site page to redirect visitors to.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="incoming_redirections",
                to="wagtailcore.page",
            ),
        ),
        migrations.AlterField(
            model_name="urlredirection",
            name="is_permanent",
            field=models.BooleanField(
                default=True,
                help_text="Send a 301 permanent redirection. Uncheck to send a 302 temporary redirection instead.",
                verbose_name="Permanent redirection",
            ),
        ),
        migrations.RenameModel(
            old_name="PersonFilter",
            new_name="PeopleFilter",
        ),
    ]
