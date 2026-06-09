from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline

from . import workflow
from .constants import Status
from .models import BuyRequest, Payment, RequestItem, TravelPlan, Transaction


class RequestItemInline(TabularInline):
    model = RequestItem
    extra = 0
    fields = ("position", "name", "quantity", "estimated_unit_cost", "actual_unit_cost", "purchased_at")
    readonly_fields = ("purchased_at",)


@admin.register(TravelPlan)
class TravelPlanAdmin(ModelAdmin):
    list_display = ("reference", "route", "travel_date", "available_weight_kg", "status", "traveler")
    list_filter = ("status", "shipment_currency", "travel_date")
    search_fields = ("reference", "from_city", "to_city", "traveler__email")
    date_hierarchy = "travel_date"
    autocomplete_fields = ("traveler",)


@admin.register(BuyRequest)
class BuyRequestAdmin(ModelAdmin):
    list_display = ("reference", "plan", "buyer", "status", "invoice_display", "unpaid_display", "created_at")
    list_filter = ("status",)
    search_fields = ("reference", "buyer__email", "plan__reference")
    inlines = [RequestItemInline]
    autocomplete_fields = ("plan", "buyer")
    readonly_fields = ("reference",)

    @admin.display(description="Invoice")
    def invoice_display(self, obj):
        return f"{obj.invoice_total:,.2f} {obj.currency}"

    @admin.display(description="Unpaid")
    def unpaid_display(self, obj):
        return f"{obj.unpaid_amount:,.2f} {obj.currency}"


class PaymentInline(TabularInline):
    model = Payment
    extra = 0
    fields = ("direction", "kind", "method", "amount", "currency", "status", "proof", "verified_at")
    readonly_fields = ("verified_at",)


@admin.register(Transaction)
class TransactionAdmin(ModelAdmin):
    list_display = ("__str__", "request", "commission_percent", "commission_display", "payout_display")
    inlines = [PaymentInline]
    search_fields = ("request__reference",)

    @admin.display(description="Commission")
    def commission_display(self, obj):
        return f"{obj.commission_amount:,.2f} {obj.currency}"

    @admin.display(description="Payout to traveler")
    def payout_display(self, obj):
        return f"{obj.payout_to_traveler:,.2f} {obj.currency}"


@admin.register(Payment)
class PaymentAdmin(ModelAdmin):
    list_display = ("__str__", "transaction", "direction", "kind", "amount", "currency", "status", "proof_link")
    list_filter = ("status", "direction", "kind", "method")
    search_fields = ("transaction__request__reference", "provider_ref")
    actions = ["verify_payments"]

    @admin.display(description="Proof")
    def proof_link(self, obj):
        if obj.proof:
            return format_html('<a href="{}" target="_blank">view</a>', obj.proof.url)
        return "—"

    @admin.action(description="Verify selected payment(s) and advance the workflow")
    def verify_payments(self, request, queryset):
        advanced = 0
        for payment in queryset.select_related("transaction__request"):
            if payment.status == Payment.PaymentStatus.VERIFIED:
                continue
            payment.mark_verified(by_user=request.user)
            req = payment.transaction.request
            if payment.kind == Payment.Kind.DEPOSIT:
                workflow.on_deposit_verified(req)
                advanced += 1
            elif payment.kind == Payment.Kind.BALANCE and req.status == Status.PACKAGE_ARRIVED:
                workflow.on_balance_verified(req)
                advanced += 1
            # A BALANCE payment while already READY_FOR_PICKUP is an actual-weight
            # top-up: it's just marked verified, no status change.
        self.message_user(request, f"Verified payments and advanced {advanced} transaction(s).")
