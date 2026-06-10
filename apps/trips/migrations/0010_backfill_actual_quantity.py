from django.db import migrations


def backfill(apps, schema_editor):
    RequestItem = apps.get_model("trips", "RequestItem")
    # For items already purchased (have an actual cost or a purchase time),
    # default the actual quantity to the requested quantity so existing
    # invoices keep their actual line totals.
    for item in RequestItem.objects.all():
        if item.actual_quantity == 0 and (item.purchased_at is not None or item.actual_unit_cost > 0):
            item.actual_quantity = item.quantity
            item.save(update_fields=["actual_quantity"])


class Migration(migrations.Migration):
    dependencies = [("trips", "0009_requestitem_actual_quantity")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
