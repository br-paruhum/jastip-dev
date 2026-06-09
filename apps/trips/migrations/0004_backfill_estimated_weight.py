from decimal import Decimal

from django.db import migrations

# Statuses where a deposit/invoice has already been issued; their shipment cost
# was based on the plan's full available weight, so preserve that.
ALREADY_ISSUED = [
    "accepted", "deposit_paid", "items_purchased",
    "package_arrived", "ready_for_pickup", "clear", "closed",
]


def backfill(apps, schema_editor):
    BuyRequest = apps.get_model("trips", "BuyRequest")
    qs = BuyRequest.objects.filter(
        status__in=ALREADY_ISSUED, estimated_weight_kg=Decimal("0")
    ).select_related("plan")
    for req in qs:
        req.estimated_weight_kg = req.plan.available_weight_kg
        req.save(update_fields=["estimated_weight_kg"])


class Migration(migrations.Migration):
    dependencies = [("trips", "0003_buyrequest_estimated_weight_kg")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
