from django.contrib import admin, messages
from django.shortcuts import render
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline

from . import workflow
from .constants import OfferStatus, Status
from .models import (
    BuyRequest,
    ExchangeRate,
    LegPayment,
    LegTransaction,
    Message,
    Payment,
    ProxyBuyer,
    Refund,
    RequestItem,
    TravelerOffer,
    TravelPlan,
    Transaction,
)


class RequestItemInline(TabularInline):
    model = RequestItem
    extra = 0
    fields = ("position", "name", "quantity", "estimated_unit_cost", "actual_unit_cost", "purchased_at")
    readonly_fields = ("purchased_at",)


@admin.register(TravelPlan)
class TravelPlanAdmin(ModelAdmin):
    list_display = ("reference", "route", "travel_date", "available_weight_kg", "carrier_only", "status", "traveler")
    list_filter = ("status", "carrier_only", "shipment_currency", "travel_date")
    search_fields = ("reference", "from_city", "to_city", "traveler__email")
    date_hierarchy = "travel_date"
    autocomplete_fields = ("traveler",)


class MessageInline(TabularInline):
    model = Message
    extra = 0
    fields = ("created_at", "sender", "body", "photo")
    readonly_fields = ("created_at", "sender", "body", "photo")
    can_delete = False
    ordering = ("created_at",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(BuyRequest)
class BuyRequestAdmin(ModelAdmin):
    """Read-only order browser. All buyer-payment verification (deposit and
    final/balance) is done on the Payments page — the single source of truth —
    and overpaid refunds live under the dedicated Refunds section.

    The only money actions here are the Flow-1 OUTBOUND disbursements (proxy 1st
    & 2nd 50%, carrier payout): once you've actually transferred the funds, run
    the matching action to stamp it Released — that's what the proxy/traveler
    "Your Disbursement"/"Your Payout" tabs reflect, and it sends the notice."""

    list_display = ("reference", "buyer", "status", "invoice_display", "proxy_net_col", "carrier_payout_col", "disbursement_state", "created_at")
    list_filter = ("status",)
    search_fields = ("reference", "buyer__email", "plan__reference")
    inlines = [RequestItemInline, MessageInline]
    autocomplete_fields = ("plan", "buyer")
    readonly_fields = ("reference", "proxy_disbursement_display", "traveler_payout_display")
    actions = ["mark_proxy_first_disbursed", "mark_proxy_second_disbursed", "mark_traveler_paid"]

    @admin.display(description="Invoice")
    def invoice_display(self, obj):
        return f"{obj.invoice_total:,.2f} {obj.currency}"

    @admin.display(description="Proxy disbursement — to pay out")
    def proxy_disbursement_display(self, obj):
        if not obj.is_proxy_buyer_first or not obj.items_actual_total:
            return "—"
        return format_html(
            "Net: <b>{} {}</b><br>1st half: {} {} ({})<br>2nd half: {} {} ({})",
            obj.currency, f"{obj.proxy_disbursement_total:,.0f}",
            obj.currency, f"{obj.proxy_disbursement_first_half:,.0f}",
            "released" if obj.proxy_first_disbursed_at else ("ready" if obj.proxy_first_disbursable else "held"),
            obj.currency, f"{obj.proxy_disbursement_second_half:,.0f}",
            "released" if obj.proxy_second_disbursed_at else ("ready" if obj.proxy_second_disbursable else "held"),
        )

    @admin.display(description="Carrier payout — to pay out")
    def traveler_payout_display(self, obj):
        if not (obj.is_proxy_buyer_first or obj.plan_id) or not hasattr(obj, "transaction"):
            return "—"
        state = "paid" if obj.traveler_paid_at else ("ready" if obj.traveler_payout_disbursable else "pending")
        return format_html("<b>{} {}</b> ({})", obj.currency, f"{obj.transaction.payout_to_traveler:,.0f}", state)

    @admin.display(description="Unpaid / (Overpaid)")
    def unpaid_display(self, obj):
        u = obj.unpaid_amount
        return f"({-u:,.2f}) {obj.currency}" if u < 0 else f"{u:,.2f} {obj.currency}"

    @admin.display(description="Proxy net")
    def proxy_net_col(self, obj):
        if not obj.is_proxy_buyer_first or not obj.items_actual_total:
            return "—"
        return f"{obj.proxy_disbursement_total:,.0f} {obj.currency}"

    @admin.display(description="Carrier payout")
    def carrier_payout_col(self, obj):
        if not (obj.is_proxy_buyer_first or obj.plan_id) or not hasattr(obj, "transaction"):
            return "—"
        state = "paid" if obj.traveler_paid_at else ("ready" if obj.traveler_payout_disbursable else "pending")
        return f"{obj.transaction.payout_to_traveler:,.0f} {obj.currency} ({state})"

    @admin.display(description="Disbursement (P1/P2/Trv)")
    def disbursement_state(self, obj):
        if not obj.is_proxy_buyer_first:
            return "—"
        def mark(paid, eligible):
            return "✅" if paid else ("⏳" if eligible else "·")
        return "{} {} {}".format(
            mark(obj.proxy_first_disbursed_at, obj.proxy_first_disbursable),
            mark(obj.proxy_second_disbursed_at, obj.proxy_second_disbursable),
            mark(obj.traveler_paid_at, obj.traveler_payout_disbursable),
        )

    @admin.action(description="Proxy: mark 1st half disbursed (released)")
    def mark_proxy_first_disbursed(self, request, queryset):
        done = skipped = 0
        for req in queryset:
            if req.proxy_first_disbursable and not req.proxy_first_disbursed_at:
                req.proxy_first_disbursed_at = timezone.now()
                req.save(update_fields=["proxy_first_disbursed_at", "updated_at"])
                workflow.notify_proxy_first_disbursed(req)
                done += 1
            else:
                skipped += 1
        self.message_user(
            request, f"Marked {done} proxy 1st-half disbursement(s) released; skipped {skipped} "
            "(not eligible yet or already released).", messages.SUCCESS if done else messages.WARNING)

    @admin.action(description="Proxy: mark 2nd half disbursed (released)")
    def mark_proxy_second_disbursed(self, request, queryset):
        done = skipped = 0
        for req in queryset:
            if req.proxy_second_disbursable and not req.proxy_second_disbursed_at:
                req.proxy_second_disbursed_at = timezone.now()
                req.save(update_fields=["proxy_second_disbursed_at", "updated_at"])
                workflow.notify_proxy_second_disbursed(req)
                done += 1
            else:
                skipped += 1
        self.message_user(
            request, f"Marked {done} proxy 2nd-half disbursement(s) released; skipped {skipped} "
            "(buyer not cleared yet or already released).", messages.SUCCESS if done else messages.WARNING)

    @admin.action(description="Traveler: mark carrier payout paid")
    def mark_traveler_paid(self, request, queryset):
        done = skipped = 0
        for req in queryset:
            if req.traveler_payout_disbursable and not req.traveler_paid_at:
                req.traveler_paid_at = timezone.now()
                req.save(update_fields=["traveler_paid_at", "updated_at"])
                workflow.notify_traveler_paid(req)
                done += 1
            else:
                skipped += 1
        self.message_user(
            request, f"Marked {done} carrier payout(s) paid; skipped {skipped} "
            "(buyer not cleared yet or already paid).", messages.SUCCESS if done else messages.WARNING)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Refund)
class RefundAdmin(ModelAdmin):
    """Overpaid orders awaiting a refund. Selecting 'Refund Processed' records
    the outbound refund to the buyer and advances the order to Ready for Pickup."""

    list_display = ("reference", "plan", "buyer", "status", "invoice_display", "refund_due_display", "refund_processed")
    search_fields = ("reference", "buyer__email", "plan__reference")
    readonly_fields = ("reference",)
    actions = ["mark_refund_processed"]

    def get_queryset(self, request):
        """Only show package-arrived orders that are actually overpaid."""
        qs = super().get_queryset(request)
        pks = [r.pk for r in qs.filter(status=Status.PACKAGE_ARRIVED) if r.refund_due > 0]
        return qs.filter(pk__in=pks)

    def has_add_permission(self, request):
        return False

    @admin.display(description="Invoice")
    def invoice_display(self, obj):
        return f"{obj.invoice_total:,.2f} {obj.currency}"

    @admin.display(description="Refund due")
    def refund_due_display(self, obj):
        return f"{obj.refund_due:,.2f} {obj.currency}"

    @admin.action(description="Refund Processed")
    def mark_refund_processed(self, request, queryset):
        refunded = advanced = 0
        for req in queryset:
            amount = req.refund_due  # current outstanding overpaid, net of prior refunds
            if amount > 0:
                tx, _ = Transaction.objects.get_or_create(request=req)
                Payment.objects.create(
                    transaction=tx,
                    direction=Payment.Direction.OUTBOUND,
                    kind=Payment.Kind.REFUND,
                    method=Payment.Method.MANUAL,
                    currency=req.currency,
                    amount=amount,
                    status=Payment.PaymentStatus.VERIFIED,
                    verified_by=request.user,
                    verified_at=timezone.now(),
                    note="Overpaid refund to buyer",
                )
                refunded += 1
            req.refund_processed = True
            req.save(update_fields=["refund_processed"])
            if req.status == Status.PACKAGE_ARRIVED:
                workflow.on_balance_verified(req)  # -> Ready for Pickup
                advanced += 1
        self.message_user(
            request,
            f"Recorded {refunded} refund payment(s); advanced {advanced} to Ready for Pickup.",
        )


@admin.register(TravelerOffer)
class TravelerOfferAdmin(ModelAdmin):
    """Browser for buyer-first offers. Doubles as the place to manually
    reassign a leg's deposit to a replacement traveler during the
    drop-off grace period (see PLAN-buyer-first-orders.md §6)."""

    list_display = (
        "__str__", "order", "traveler", "ask_cost_per_kg", "avail_kg",
        "offer_status", "allocated_weight_kg", "leg_status", "payout_col", "travel_date",
    )
    list_filter = ("offer_status", "leg_status", "travel_date")
    search_fields = ("order__reference", "traveler__email")
    autocomplete_fields = ("order", "traveler")
    actions = ["reassign_deposit", "mark_leg_payout_paid"]

    def has_add_permission(self, request):
        return False

    @admin.display(description="Carrier payout — to pay out")
    def payout_col(self, obj):
        if not obj.order.is_cargo or not hasattr(obj, "transaction"):
            return "—"
        state = "paid" if obj.payout_paid_at else ("ready" if obj.payout_disbursable else "pending")
        return format_html("<b>{} {}</b> ({})", obj.order.currency, f"{obj.transaction.payout_to_traveler:,.0f}", state)

    @admin.action(description="Mark carrier leg payout paid")
    def mark_leg_payout_paid(self, request, queryset):
        done = skipped = 0
        for leg in queryset:
            if leg.payout_disbursable and not leg.payout_paid_at:
                leg.payout_paid_at = timezone.now()
                leg.save(update_fields=["payout_paid_at", "updated_at"])
                done += 1
            else:
                skipped += 1
        self.message_user(
            request, f"Marked {done} leg payout(s) paid; skipped {skipped} "
            "(leg not cleared yet or already paid).", messages.SUCCESS if done else messages.WARNING)

    def _ineligible_reason(self, leg) -> str:
        today = timezone.localtime().date()
        if leg.offer_status != OfferStatus.SELECTED or leg.leg_status is not None:
            return "must be a selected leg that hasn't been dropped off yet"
        if leg.drop_off_deadline >= today:
            return "hasn't missed its drop-off deadline yet"
        if leg.order.max_acceptable_date < today:
            return "grace period has already expired — expire_missed_dropoffs will auto-cancel and refund it"
        return ""

    @admin.action(description="Reassign deposit to a replacement traveler")
    def reassign_deposit(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one leg to reassign.", level=messages.ERROR)
            return None

        leg = queryset.first()
        reason = self._ineligible_reason(leg)
        if reason:
            self.message_user(request, f"Can't reassign {leg}: {reason}.", level=messages.ERROR)
            return None

        replacements = TravelerOffer.objects.filter(
            order=leg.order, offer_status=OfferStatus.PENDING
        ).exclude(pk=leg.pk)

        if request.POST.get("confirm"):
            replacement_id = request.POST.get("replacement_offer")
            replacement = replacements.filter(pk=replacement_id).first()
            if replacement is None:
                self.message_user(request, "Invalid replacement traveler selected.", level=messages.ERROR)
                return None
            if replacement.avail_kg < (leg.allocated_weight_kg or 0):
                self.message_user(
                    request,
                    f"{replacement.traveler} only has {replacement.avail_kg} kg available, "
                    f"need {leg.allocated_weight_kg} kg.",
                    level=messages.ERROR,
                )
                return None

            new_tx, _ = LegTransaction.objects.get_or_create(leg=replacement)
            moved = 0
            if hasattr(leg, "transaction"):
                moved = leg.transaction.payments.filter(
                    kind=LegPayment.Kind.DEPOSIT, direction=LegPayment.Direction.INBOUND,
                ).update(transaction=new_tx)

            leg.offer_status = OfferStatus.WITHDRAWN
            leg.save(update_fields=["offer_status", "updated_at"])

            replacement.offer_status = OfferStatus.SELECTED
            replacement.allocated_weight_kg = leg.allocated_weight_kg
            replacement.save(update_fields=["offer_status", "allocated_weight_kg", "updated_at"])

            leg.order.recompute_status()
            self.message_user(
                request,
                f"Reassigned {leg.order.reference}: moved {moved} deposit payment(s) from "
                f"{leg.traveler} to {replacement.traveler}.",
            )
            return None

        return render(
            request,
            "admin/trips/reassign_deposit_confirmation.html",
            {
                "leg": leg,
                "replacements": replacements,
                "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
                "opts": self.model._meta,
                "title": "Reassign deposit",
            },
        )


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
    list_display = ("__str__", "transaction", "direction", "kind", "amount_display", "currency", "idr_equivalent_display", "status", "proof_link")
    list_filter = ("status", "direction", "kind", "method")
    search_fields = ("transaction__request__reference", "provider_ref")
    actions = ["verify_payments"]

    @admin.display(description="Amount", ordering="amount")
    def amount_display(self, obj):
        return f"{obj.amount:,.2f}"

    @admin.display(description="IDR Equiv.")
    def idr_equivalent_display(self, obj):
        if obj.currency == "IDR":
            return f"IDR {obj.amount:,.0f}"
        try:
            rate = ExchangeRate.objects.get(code=obj.currency, is_active=True)
            return f"IDR {obj.amount * rate.sell_rate:,.0f}"
        except ExchangeRate.DoesNotExist:
            return "—"

    @admin.display(description="Proof")
    def proof_link(self, obj):
        if obj.proof:
            return format_html('<a href="{}" target="_blank">view</a>', obj.proof.url)
        return "—"

    @admin.action(description="Selected Payment(s) Verified")
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


class LegPaymentInline(TabularInline):
    model = LegPayment
    extra = 0
    fields = ("direction", "kind", "method", "amount", "currency", "status", "proof", "verified_at")
    readonly_fields = ("verified_at",)


@admin.register(LegTransaction)
class LegTransactionAdmin(ModelAdmin):
    list_display = (
        "__str__", "leg", "commission_percent",
        "extra_due_display", "refund_due_display", "payout_display",
    )
    readonly_fields = ("refund_bank_details_display",)
    inlines = [LegPaymentInline]
    search_fields = ("leg__order__reference", "leg__traveler__email")

    @admin.display(description="Balance due")
    def extra_due_display(self, obj):
        return f"{obj.leg.extra_due:,.2f} {obj.currency}"

    @admin.display(description="Refund due")
    def refund_due_display(self, obj):
        return f"{obj.leg.refund_due:,.2f} {obj.currency}"

    @admin.display(description="Payout to traveler")
    def payout_display(self, obj):
        return f"{obj.payout_to_traveler:,.2f} {obj.currency}"

    @admin.display(description="Buyer's refund bank details")
    def refund_bank_details_display(self, obj):
        return obj.leg.refund_bank_details or "—"


@admin.register(LegPayment)
class LegPaymentAdmin(ModelAdmin):
    list_display = ("__str__", "transaction", "direction", "kind", "amount_display", "currency", "status", "proof_link")
    list_filter = ("status", "direction", "kind", "method")
    search_fields = ("transaction__leg__order__reference",)
    actions = ["verify_payments"]

    @admin.display(description="Amount", ordering="amount")
    def amount_display(self, obj):
        return f"{obj.amount:,.2f}"

    @admin.display(description="Proof")
    def proof_link(self, obj):
        if obj.proof:
            return format_html('<a href="{}" target="_blank">view</a>', obj.proof.url)
        return "—"

    @admin.action(description="Selected Payment(s) Verified")
    def verify_payments(self, request, queryset):
        verified = 0
        for payment in queryset.select_related("transaction__leg__order"):
            if payment.status == LegPayment.PaymentStatus.VERIFIED:
                continue
            payment.mark_verified(by_user=request.user)
            verified += 1
        self.message_user(request, f"Verified {verified} leg payment(s).")


@admin.register(Message)
class MessageAdmin(ModelAdmin):
    list_display = ("created_at", "request", "sender", "short_body")
    list_filter = ("created_at",)
    search_fields = ("request__reference", "sender__email", "body")
    readonly_fields = ("request", "sender", "body", "photo", "created_at")
    date_hierarchy = "created_at"

    @admin.display(description="Message")
    def short_body(self, obj):
        return (obj.body or "")[:80]

    def has_add_permission(self, request):
        return False


@admin.register(ProxyBuyer)
class ProxyBuyerAdmin(ModelAdmin):
    list_display = ("name", "country", "margin_range", "email", "whatsapp", "user", "is_active", "sequence")
    list_editable = ("margin_range", "is_active", "sequence")
    list_filter = ("is_active", "country")
    search_fields = ("name", "country", "email")
    autocomplete_fields = ("user",)
    ordering = ("sequence", "country", "name")

    def save_model(self, request, obj, form, change):
        """A Proxy Buyer always operates the order through its own login, so we
        guarantee a linked account. If admin didn't pick one, create (or reuse) a
        Buyer account from the proxy's email and link it. The proxy is
        admin-approved, so the email is pre-verified and no WhatsApp OTP is needed
        — they only set a password via 'Forgot password' to start operating."""
        super().save_model(request, obj, form, change)
        if obj.user_id or not obj.email:
            return
        from allauth.account.models import EmailAddress

        from apps.accounts.models import User

        user = User.objects.filter(email__iexact=obj.email).first()
        created = user is None
        if created:
            # password=None -> unusable; the proxy sets one via password reset.
            user = User.objects.create_user(
                email=obj.email, password=None,
                full_name=obj.name, last_role_choice="buyer",
                phone_verified=True,  # admin-approved: no self-verification required
            )
        EmailAddress.objects.get_or_create(
            user=user, email=user.email,
            defaults={"verified": True, "primary": True},
        )
        obj.user = user
        obj.save(update_fields=["user"])
        self.message_user(
            request,
            f"{'Created and linked' if created else 'Linked existing'} login {user.email}. "
            "Ask them to set a password via 'Forgot password?' on the sign-in page.",
            level=messages.SUCCESS,
        )


@admin.register(ExchangeRate)
class ExchangeRateAdmin(ModelAdmin):
    list_display = (
        "code", "name", "sell_rate", "is_active", "sequence",
        "apply_to_countries", "updated_at",
    )
    list_editable = ("name", "is_active", "sequence", "apply_to_countries")
    list_filter = ("is_active",)
    search_fields = ("code", "name", "apply_to_countries")
    readonly_fields = ("updated_at",)
    ordering = ("sequence", "code")
