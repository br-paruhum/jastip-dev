from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_user_bank_details_user_destination_city_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='user',
            old_name='destination_city',
            new_name='traveler_destination_city',
        ),
        migrations.RenameField(
            model_name='user',
            old_name='address',
            new_name='traveler_address',
        ),
        migrations.RenameField(
            model_name='user',
            old_name='bank_details',
            new_name='traveler_bank_details',
        ),
        migrations.AlterField(
            model_name='user',
            name='traveler_destination_city',
            field=models.CharField(
                blank=True, max_length=80,
                help_text="Your actual city at destination — shown to buyers before they order, since it may differ from the route's listed (often airport) city.",
            ),
        ),
        migrations.AlterField(
            model_name='user',
            name='traveler_address',
            field=models.TextField(
                blank=True,
                help_text="Shown to the buyer if they choose to pick up the package from you instead of requesting reshipment.",
            ),
        ),
        migrations.AlterField(
            model_name='user',
            name='traveler_bank_details',
            field=models.TextField(blank=True, help_text='For invoice disbursement purpose.'),
        ),
    ]
