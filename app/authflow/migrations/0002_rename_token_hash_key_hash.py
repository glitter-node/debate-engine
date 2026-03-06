from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("authflow", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="emailauthtoken",
            old_name="token_hash",
            new_name="key_hash",
        ),
    ]
