from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="address",
            field=models.TextField(
                blank=True,
                help_text="Full delivery address (street, city, postal code, country)",
            ),
        ),
    ]
