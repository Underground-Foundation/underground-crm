from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("underground_crm", "0016_rename_urlredirection_and_peoplefilter"),
    ]

    operations = [
        migrations.DeleteModel(
            name="UrlRedirection",
        ),
    ]
