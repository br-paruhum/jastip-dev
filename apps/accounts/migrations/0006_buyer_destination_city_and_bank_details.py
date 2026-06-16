from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_buyer_invoice_address_and_role'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='buyer_destination_city',
            field=models.CharField(
                blank=True, max_length=80,
                help_text='Your actual city at destination, since it may differ from the destination airport city.',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='buyer_bank_details',
            field=models.TextField(blank=True, help_text='For overpayment refund, if any.'),
        ),
        migrations.AlterField(
            model_name='user',
            name='buyer_invoice_address',
            field=models.TextField(
                blank=True,
                help_text='Reshipment address, if you want the traveler to send your package at your cost — also used as the address data on the customs invoice.',
            ),
        ),
    ]
