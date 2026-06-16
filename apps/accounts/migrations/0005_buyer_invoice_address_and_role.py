from django.db import migrations, models


def backfill_buyer_invoice_address(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.exclude(traveler_address='').update(buyer_invoice_address=models.F('traveler_address'))


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_rename_traveler_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='buyer_invoice_address',
            field=models.TextField(
                blank=True,
                help_text='Your delivery address, used on customs invoices. Fill this in now or later, once needed.',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='last_role_choice',
            field=models.CharField(
                blank=True, default='', max_length=10,
                choices=[('traveler', 'Traveler'), ('buyer', 'Buyer')],
            ),
        ),
        migrations.RunPython(backfill_buyer_invoice_address, migrations.RunPython.noop),
    ]
