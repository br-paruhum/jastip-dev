from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.notifications.services import send_whatsapp
from apps.trips.constants import Status
from apps.trips.models import BuyRequest, TravelPlan

from .forms import OTPForm, ProfileForm


@login_required
def profile(request):
    user = request.user

    # Traveler side
    my_plans = TravelPlan.objects.filter(traveler=user).prefetch_related("buy_requests")
    open_plans = [p for p in my_plans if not p.is_closed]
    closed_plans = [p for p in my_plans if p.is_closed]

    # Buyer side
    my_requests = BuyRequest.objects.filter(buyer=user).select_related("plan")
    open_requests = [r for r in my_requests if r.status != Status.CLOSED]
    closed_requests = [r for r in my_requests if r.status == Status.CLOSED]

    form = ProfileForm(instance=user)
    return render(
        request,
        "accounts/profile.html",
        {
            "profile_form": form,
            "otp_form": OTPForm(),
            "open_plans": open_plans,
            "closed_plans": closed_plans,
            "open_requests": open_requests,
            "closed_requests": closed_requests,
            # A plan id to auto-open the buyer request form for (set after Block).
            "block_plan_id": request.GET.get("block"),
        },
    )


@login_required
@require_POST
def profile_update(request):
    form = ProfileForm(request.POST, instance=request.user)
    if form.is_valid():
        form.save()
        messages.success(request, "Profile saved. Please verify your WhatsApp number.")
    else:
        for field, errors in form.errors.items():
            for err in errors:
                messages.error(request, f"{field}: {err}")
    return redirect("accounts:profile")


@login_required
@require_POST
def send_otp(request):
    user = request.user
    if not user.phone_e164:
        return JsonResponse({"ok": False, "error": "Add a phone number first."}, status=400)
    code = user.generate_phone_otp()
    send_whatsapp(
        to_user=user,
        text=f"Your Jastip.me verification code is {code}. It expires in 10 minutes.",
        event="phone_otp",
    )
    messages.info(request, "Verification code sent to your WhatsApp.")
    return redirect("accounts:profile")


@login_required
@require_POST
def verify_otp(request):
    form = OTPForm(request.POST)
    user = request.user
    if form.is_valid() and user.otp_is_valid(form.cleaned_data["code"]):
        user.phone_verified = True
        user.phone_otp = ""
        user.save(update_fields=["phone_verified", "phone_otp"])
        messages.success(request, "WhatsApp number verified. You can now post offers and requests.")
    else:
        messages.error(request, "Invalid or expired code. Please try again.")
    return redirect("accounts:profile")
