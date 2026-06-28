from django.db import migrations
from django.db.migrations.operations.base import Operation


def _swap_refund_base(state, old, new):
    """Repoint the `Refund` proxy model's base from `old` to `new` in the
    migration state. RenameModel doesn't rewrite the bases of proxy children
    (the base is stored as the string 'trips.buyrequest'), so we fix it here."""
    key = ("trips", "refund")
    model_state = state.models.get(key)
    if model_state is None:
        return
    model_state.bases = tuple(new if b == old else b for b in model_state.bases)
    state.reload_model("trips", "refund", delay=True)


class RepointRefundProxyBase(Operation):
    """State-only: fix the Refund proxy base after the BuyRequest -> Order rename.
    No database effect (Refund is a proxy; it has no table of its own)."""

    reversible = True
    reduces_to_sql = False

    def state_forwards(self, app_label, state):
        _swap_refund_base(state, "trips.buyrequest", "trips.order")

    def state_backwards(self, app_label, state):
        _swap_refund_base(state, "trips.order", "trips.buyrequest")

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        pass

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        pass

    def describe(self):
        return "Repoint Refund proxy base to Order"


class Migration(migrations.Migration):
    """Task 26(b): rename the BuyRequest model -> Order in code and migration
    state, but keep the existing DB table (db_table='trips_buyrequest'). This is
    a state-only change — no SQL runs, so there is zero risk to existing rows on
    any environment. The admin already reads "Order" (verbose_name, 0055)."""

    dependencies = [
        ("trips", "0056_remove_buyrequest_traveler_cleared"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RenameModel(old_name="BuyRequest", new_name="Order"),
                migrations.AlterModelTable(name="order", table="trips_buyrequest"),
                RepointRefundProxyBase(),
            ],
        ),
    ]
