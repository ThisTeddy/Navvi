"""
Navvi — views.py (plain Django, PWA-oriented, no django.forms)

Design notes:
- No services.py: views call model methods directly (Assignment.claim(),
  EscrowAccount.release_short_term(), Milestone.release(), etc.).
- No DRF, no django.forms/ModelForm. Input is read straight from
  request.POST / request.FILES / a parsed JSON body, validated by hand,
  and model-level constraints are enforced via full_clean() before save()
  wherever the model already carries the right validators (e.g.
  Rating.score's MinValueValidator/MaxValueValidator).
- Pages that render UI use render()/redirect(). Interactions the PWA
  drives via fetch() (job claiming, vitals sync, payment webhook, polling
  for high-risk status) return JsonResponse so they work cleanly from a
  service worker / offline queue.
- Role checks are plain decorators instead of DRF permission classes.
- Frontend is NEVER the authority here: every state-changing action is
  re-validated server-side regardless of what the client claims.
"""

import json
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET

from .models import (
    User, PatientProfile, NurseProfile, NurseDocument, VerificationEvent,
    CareRequest, Assignment, VitalsRecord, EmergencyReferral,
    Payment, EscrowAccount, Milestone, Payout,
    Rating, Dispute, Notification,
)

def home(request):
    return render(request, "home.html")

# ---------------------------------------------------------------------------
# Role-check decorators
# ---------------------------------------------------------------------------

def _has_role(user, role):
    return user.is_authenticated and user.role == role

nurse_required = user_passes_test(lambda u: _has_role(u, User.Role.NURSE))
patient_required = user_passes_test(lambda u: _has_role(u, User.Role.PATIENT))
admin_required = user_passes_test(lambda u: _has_role(u, User.Role.ADMIN))
partner_required = user_passes_test(lambda u: _has_role(u, User.Role.PARTNER))


def verified_nurse_required(view_func):
    @nurse_required
    def wrapper(request, *args, **kwargs):
        if not request.user.nurse_profile.can_accept_jobs:
            return HttpResponseForbidden("Nurse account is not verified/active.")
        return view_func(request, *args, **kwargs)
    return wrapper


def admin_or_partner_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_authenticated and request.user.role in (User.Role.ADMIN, User.Role.PARTNER)):
            return HttpResponseForbidden()
        return view_func(request, *args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Small manual-validation helpers (replace what ModelForm/Form used to do)
# ---------------------------------------------------------------------------

def _get_body(request):
    """Return POST-style dict whether the client sent form-encoded or JSON."""
    if request.content_type == "application/json":
        try:
            return json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return None
    return request.POST


def _require_fields(data, *field_names):
    """Returns a list of missing/blank field names."""
    return [name for name in field_names if not data.get(name)]


def _to_decimal(value, field_name, errors):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        errors[field_name] = "Must be a valid number."
        return None


def _to_int(value, field_name, errors):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        errors[field_name] = "Must be a whole number."
        return None


def _to_bool(value):
    return str(value).lower() in ("1", "true", "yes", "on")


def _wants_json(request):
    """True when the request came from our own fetch() calls (navviFetch always
    sets this header), so the view can return JSON instead of a redirect/render."""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@require_GET
def service_worker(request):
    """
    Serves sw.js at the site root (not /static/sw.js) so its scope covers
    the whole app. STATIC_ROOT/sw.js is the source file — see urls.py.
    """
    with open(settings.BASE_DIR / "static" / "sw.js", "rb") as f:
        return HttpResponse(f.read(), content_type="application/javascript")


# ===========================================================================
# Auth (login/logout — nurse registration lives in section A below;
# patient/admin/partner registration isn't built yet)
# ===========================================================================

def _post_login_redirect(user):
    """Send each role to a sensible landing page after login."""
    if user.role == User.Role.NURSE:
        return redirect("nurse_verification_status")
    if user.role == User.Role.PATIENT:
        return redirect("my_care_requests")
    if user.role == User.Role.ADMIN:
        return redirect("admin_dispatch_monitor")
    if user.role == User.Role.PARTNER:
        return redirect("emergency_referral_list")
    return redirect("/")


def login_view(request):
    """Login by phone number + password (USERNAME_FIELD is 'username', set to phone_number at registration)."""
    if request.user.is_authenticated:
        return _post_login_redirect(request.user)

    error = None
    if request.method == "POST":
        phone_number = request.POST.get("phone_number", "").strip()
        password = request.POST.get("password", "")
        if not phone_number or not password:
            error = "Phone number and password are required."
        else:
            user = authenticate(request, username=phone_number, password=password)
            if user is not None:
                login(request, user)
                if _wants_json(request):
                    return JsonResponse({"redirect": _post_login_redirect(user).url})
                return _post_login_redirect(user)
            error = "Invalid phone number or password."

    if _wants_json(request):
        return JsonResponse({"error": error}, status=400)
    return render(request, "auth/login.html", {"error": error})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")


def patient_register(request):
    """Public — a patient/family account. No verification step needed, unlike nurses."""
    errors = {}
    if request.method == "POST":
        data = request.POST
        required = ["full_name", "phone_number", "password"]
        for field in _require_fields(data, *required):
            errors[field] = "This field is required."

        if not errors:
            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        username=data["phone_number"].strip(),
                        phone_number=data["phone_number"].strip(),
                        password=data["password"],
                        first_name=data["full_name"].strip(),
                        role=User.Role.PATIENT,
                    )
                    PatientProfile.objects.create(
                        user=user,
                        is_diaspora_booker=_to_bool(data.get("is_diaspora_booker", False)),
                    )
            except IntegrityError:
                errors["phone_number"] = "An account with this phone number already exists."
            else:
                login(request, user)
                if _wants_json(request):
                    return JsonResponse({"redirect": redirect("my_care_requests").url})
                return redirect("my_care_requests")

    if _wants_json(request):
        return JsonResponse({"errors": errors}, status=400 if errors else 200)
    return render(request, "patients/register.html", {"errors": errors, "data": request.POST if request.method == "POST" else {}})


# ===========================================================================
# A. Nurse onboarding / verification
# ===========================================================================

def nurse_register(request):
    """Public — a nurse registers and lands in Pending Verification."""
    errors = {}
    if request.method == "POST":
        data = request.POST
        required = ["full_name", "phone_number", "nmcn_registration_number", "government_id_number", "password"]
        for field in _require_fields(data, *required):
            errors[field] = "This field is required."

        nmcn_number = data.get("nmcn_registration_number", "").strip()
        if nmcn_number and NurseProfile.objects.filter(nmcn_registration_number=nmcn_number).exists():
            errors["nmcn_registration_number"] = "This NMCN registration number is already in use."

        if not errors:
            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        username=data["phone_number"].strip(),
                        phone_number=data["phone_number"].strip(),
                        password=data["password"],
                        first_name=data["full_name"].strip(),
                        role=User.Role.NURSE,
                    )
                    nurse = NurseProfile.objects.create(
                        user=user,
                        nmcn_registration_number=nmcn_number,
                        government_id_number=data["government_id_number"].strip(),
                    )
                    VerificationEvent.objects.create(nurse=nurse, action=VerificationEvent.Action.SUBMITTED)
            except IntegrityError:
                errors["phone_number"] = "An account with this phone number already exists."
            else:
                login(request, user)
                if _wants_json(request):
                    return JsonResponse({"redirect": redirect("nurse_verification_status").url})
                return redirect("nurse_verification_status")

    if _wants_json(request):
        return JsonResponse({"errors": errors}, status=400 if errors else 200)
    return render(request, "nurses/register.html", {"errors": errors, "data": request.POST if request.method == "POST" else {}})


@nurse_required
def nurse_document_upload(request):
    """Nurse uploads credential/ID documents (any time, including while pending)."""
    errors = {}
    if request.method == "POST":
        document_type = request.POST.get("document_type")
        uploaded_file = request.FILES.get("file")

        if document_type not in NurseDocument.DocumentType.values:
            errors["document_type"] = "Invalid document type."
        if not uploaded_file:
            errors["file"] = "A file is required."

        if not errors:
            NurseDocument.objects.create(
                nurse=request.user.nurse_profile,
                document_type=document_type,
                file=uploaded_file,
            )
            return redirect("nurse_verification_status")

    return render(request, "nurses/document_upload.html", {
        "errors": errors, "document_types": NurseDocument.DocumentType.choices,
    })


@nurse_required
def nurse_verification_status(request):
    """Nurse checks their own verification status."""
    nurse = request.user.nurse_profile
    if request.GET.get("format") == "json":
        return JsonResponse({
            "verification_status": nurse.verification_status,
            "suspension_reason": nurse.suspension_reason,
            "reliability_score": str(nurse.reliability_score),
            "license_expiry_date": nurse.license_expiry_date.isoformat() if nurse.license_expiry_date else None,
        })
    return render(request, "nurses/verification_status.html", {"nurse": nurse})


@admin_required
def admin_nurse_verification_queue(request):
    """Admin — searchable queue of nurses pending verification."""
    qs = NurseProfile.objects.filter(verification_status=NurseProfile.VerificationStatus.PENDING)
    search = request.GET.get("search")
    if search:
        qs = qs.filter(nmcn_registration_number__icontains=search)
    return render(request, "admin/nurse_queue.html", {"nurses": qs})


@admin_required
@require_POST
def admin_nurse_verification_decision(request, nurse_id):
    """Admin — approve, reject, or suspend a nurse."""
    nurse = get_object_or_404(NurseProfile, pk=nurse_id)
    action = request.POST.get("action")
    reason = request.POST.get("reason", "")

    if action == "approve":
        nurse.mark_verified(admin_user=request.user)
    elif action == "reject":
        nurse.mark_rejected(admin_user=request.user, reason=reason)
    elif action == "suspend":
        nurse.suspend(admin_user=request.user, reason=reason)
    else:
        return HttpResponseBadRequest("action must be one of: approve, reject, suspend")

    return redirect("admin_nurse_verification_queue")


# ===========================================================================
# B. Booking (patient/family)
# ===========================================================================

@patient_required
def care_request_create(request):
    """Patient/family creates a booking. Starts life as 'created' until payment succeeds."""
    errors = {}
    if request.method == "POST":
        data = request.POST
        required = ["care_type", "duration_type", "latitude", "longitude", "requested_appointment_time"]
        for field in _require_fields(data, *required):
            errors[field] = "This field is required."

        care_type = data.get("care_type")
        if care_type and care_type not in dict(CareRequest._meta.get_field("care_type").choices):
            errors["care_type"] = "Invalid care type."

        duration_type = data.get("duration_type")
        if duration_type and duration_type not in dict(CareRequest._meta.get_field("duration_type").choices):
            errors["duration_type"] = "Invalid duration type."

        latitude = _to_decimal(data.get("latitude"), "latitude", errors)
        longitude = _to_decimal(data.get("longitude"), "longitude", errors)

        appointment_time = None
        if data.get("requested_appointment_time"):
            appointment_time = timezone.datetime.fromisoformat(data["requested_appointment_time"]) \
                if _is_valid_isoformat(data["requested_appointment_time"]) else None
            if appointment_time is None:
                errors["requested_appointment_time"] = "Must be a valid ISO datetime."

        if not errors:
            care_request = CareRequest(
                patient=request.user,
                booked_by=request.user,
                care_type=care_type,
                duration_type=duration_type,
                symptoms_notes=data.get("symptoms_notes", ""),
                latitude=latitude,
                longitude=longitude,
                landmark=data.get("landmark", ""),
                requested_appointment_time=appointment_time,
            )
            try:
                care_request.full_clean()
            except ValidationError as exc:
                errors.update(exc.message_dict)
            else:
                care_request.save()
                if _wants_json(request):
                    return JsonResponse({"redirect": redirect("care_request_detail", care_request_id=care_request.id).url})
                return redirect("care_request_detail", care_request_id=care_request.id)

    if _wants_json(request):
        return JsonResponse({"errors": errors}, status=400 if errors else 200)
    return render(request, "bookings/create.html", {"errors": errors})


def _is_valid_isoformat(value):
    try:
        timezone.datetime.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


@patient_required
def my_care_requests(request):
    """Patient/family views their own bookings and statuses."""
    requests_qs = CareRequest.objects.filter(booked_by=request.user).order_by("-created_at")
    if request.GET.get("format") == "json":
        return JsonResponse({
            "care_requests": [
                {
                    "id": str(cr.id),
                    "care_type": cr.care_type,
                    "care_type_display": cr.get_care_type_display(),
                    "status": cr.status,
                    "status_display": cr.get_status_display(),
                    "requested_appointment_time": cr.requested_appointment_time.isoformat(),
                }
                for cr in requests_qs
            ]
        })
    return render(request, "bookings/list.html", {"care_requests": requests_qs})


@patient_required
def care_request_detail(request, care_request_id):
    """Status tracking for a single booking (patient sees nurse assignment/status here)."""
    care_request = get_object_or_404(CareRequest, pk=care_request_id, booked_by=request.user)
    if request.GET.get("format") == "json":
        return JsonResponse({
            "status": care_request.status,
            "status_display": care_request.get_status_display(),
        })
    return render(request, "bookings/detail.html", {"care_request": care_request})


# ===========================================================================
# C. Dispatch / matching (nurse side)
# ===========================================================================

@verified_nurse_required
def available_jobs(request):
    """
    Nurse's job-dispatch screen. Renders a page, but the PWA can also poll
    this via ?format=json for background refresh without a full reload.
    NOTE: real matching (proximity + skill + reliability ranking) belongs in
    a dedicated matching module — this only applies the skill filter today.
    """
    nurse = request.user.nurse_profile
    candidates = CareRequest.objects.for_nurse_candidates()
    jobs = [cr for cr in candidates if cr.care_type in nurse.skills or not nurse.skills]

    if request.GET.get("format") == "json":
        return JsonResponse({
            "jobs": [
                {
                    "id": str(cr.id), "care_type": cr.care_type,
                    "latitude": str(cr.latitude), "longitude": str(cr.longitude),
                    "landmark": cr.landmark,
                    "requested_appointment_time": cr.requested_appointment_time.isoformat(),
                    "duration_type": cr.duration_type,
                }
                for cr in jobs
            ]
        })
    return render(request, "nurses/available_jobs.html", {"jobs": jobs})


@verified_nurse_required
@require_POST
def assignment_claim(request, care_request_id):
    """
    Nurse accepts a specific care request. Concurrency-safe — first claim wins.
    Returns JSON so the PWA can call this via fetch() and update the UI
    without a full page reload (important on flaky connections).
    """
    care_request = get_object_or_404(CareRequest, pk=care_request_id)
    try:
        assignment = Assignment.claim(care_request=care_request, nurse=request.user.nurse_profile)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"assignment_id": str(assignment.id), "status": assignment.status}, status=201)


@verified_nurse_required
@require_POST
def assignment_reject(request, care_request_id):
    """Nurse declines an offered job (does not affect other nurses' ability to claim it)."""
    care_request = get_object_or_404(CareRequest, pk=care_request_id)
    Notification.objects.create(
        recipient=request.user, channel=Notification.Channel.PUSH,
        event_type=Notification.EventType.NURSE_ASSIGNMENT,
        payload={"care_request_id": str(care_request.id), "action": "rejected"},
    )
    return JsonResponse({"status": "rejected"})


@verified_nurse_required
@require_POST
def assignment_complete(request, assignment_id):
    """Nurse marks a visit/care episode complete, triggering short-term escrow release eligibility."""
    assignment = get_object_or_404(Assignment, pk=assignment_id, nurse=request.user.nurse_profile)
    with transaction.atomic():
        assignment.status = Assignment.Status.COMPLETED
        assignment.completed_at = timezone.now()
        assignment.save(update_fields=["status", "completed_at", "updated_at"])
        assignment.care_request.transition_to(CareRequest.Status.COMPLETED)
    return JsonResponse({"status": assignment.status})


# ===========================================================================
# D. Vitals / emergency referral
# ===========================================================================

@verified_nurse_required
@require_POST
def vitals_record_create(request, assignment_id):
    """
    Nurse submits vitals for a home visit. Accepts form-encoded or JSON body
    so the PWA's offline queue can replay a saved payload once back online.
    Threshold evaluation runs server-side on save.
    """
    assignment = get_object_or_404(Assignment, pk=assignment_id, nurse=request.user.nurse_profile)

    data = _get_body(request)
    if data is None:
        return HttpResponseBadRequest("Invalid JSON.")

    errors = {}
    systolic_bp = _to_int(data.get("systolic_bp"), "systolic_bp", errors)
    diastolic_bp = _to_int(data.get("diastolic_bp"), "diastolic_bp", errors)
    heart_rate = _to_int(data.get("heart_rate"), "heart_rate", errors)
    oxygen_saturation = _to_decimal(data.get("oxygen_saturation"), "oxygen_saturation", errors)
    temperature_celsius = _to_decimal(data.get("temperature_celsius"), "temperature_celsius", errors)
    synced_offline = _to_bool(data.get("synced_offline", False))

    if errors:
        return JsonResponse({"errors": errors}, status=400)

    record = VitalsRecord(
        assignment=assignment,
        recorded_by=request.user,
        systolic_bp=systolic_bp,
        diastolic_bp=diastolic_bp,
        heart_rate=heart_rate,
        oxygen_saturation=oxygen_saturation,
        temperature_celsius=temperature_celsius,
        synced_offline=synced_offline,
    )
    try:
        record.full_clean(exclude=["is_high_risk"])
    except ValidationError as exc:
        return JsonResponse({"errors": exc.message_dict}, status=400)

    record.save()
    record.evaluate_thresholds()
    return JsonResponse({"vitals_id": str(record.id), "is_high_risk": record.is_high_risk}, status=201)


@verified_nurse_required
@require_GET
def vitals_high_risk_status(request, vitals_id):
    """Nurse app polls this to know whether to show the red high-risk banner."""
    record = get_object_or_404(VitalsRecord, pk=vitals_id, assignment__nurse=request.user.nurse_profile)
    return JsonResponse({"is_high_risk": record.is_high_risk})


@admin_or_partner_required
def emergency_referral_list(request):
    """Partner hospital / admin view of active emergency referrals."""
    referrals = EmergencyReferral.objects.select_related("patient", "partner_hospital").order_by("-created_at")
    return render(request, "emergency/referral_list.html", {"referrals": referrals})


# ===========================================================================
# E. Payments / escrow / payouts
# ===========================================================================

@patient_required
@require_POST
def payment_initiate(request, care_request_id):
    """
    Patient initiates payment for a care request. Calls Paystack's real
    initialize-transaction endpoint (test mode, using PAYSTACK_SECRET_KEY)
    and returns the authorization_url so the PWA can redirect the patient
    straight to Paystack's hosted checkout page — this is what makes the
    demo payment flow actually work end to end.
    """
    care_request = get_object_or_404(CareRequest, pk=care_request_id, booked_by=request.user)
    amount_raw = request.POST.get("amount")
    if not amount_raw:
        return HttpResponseBadRequest("amount is required.")
    errors = {}
    amount = _to_decimal(amount_raw, "amount", errors)
    if errors or amount <= 0:
        return JsonResponse({"errors": errors or {"amount": "Must be greater than zero."}}, status=400)

    reference = f"navvi_{care_request.id}_{Payment.objects.filter(care_request=care_request).count() + 1}"

    paystack_response = requests.post(
        "https://api.paystack.co/transaction/initialize",
        headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
        json={
            "email": request.user.email or f"{request.user.phone_number}@navvi.demo",
            # Paystack expects amount in kobo (lowest currency unit).
            "amount": int(amount * 100),
            "reference": reference,
            "callback_url": request.build_absolute_uri(f"/bookings/{care_request.id}/"),
        },
        timeout=10,
    )

    if paystack_response.status_code != 200 or not paystack_response.json().get("status"):
        return JsonResponse({"error": "Could not initialize payment with Paystack."}, status=502)

    paystack_data = paystack_response.json()["data"]

    payment = Payment.objects.create(
        care_request=care_request,
        gateway=Payment.Gateway.PAYSTACK,
        gateway_reference=reference,
        amount=amount,
    )
    care_request.transition_to(CareRequest.Status.AWAITING_PAYMENT)
    return JsonResponse({
        "payment_id": str(payment.id),
        "gateway_reference": payment.gateway_reference,
        "authorization_url": paystack_data["authorization_url"],
    }, status=201)


@csrf_exempt  # Paystack calls this directly — signature verification protects it instead of CSRF
@require_POST
def paystack_webhook(request):
    """
    Paystack webhook callback. Verifies the x-paystack-signature header
    against PAYSTACK_SECRET_KEY before trusting the payload.
    """
    import hashlib
    import hmac

    signature = request.headers.get("x-paystack-signature", "")
    expected = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode("utf-8"), request.body, hashlib.sha512
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return HttpResponseForbidden("Invalid signature.")

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON.")

    event = body.get("event")
    reference = body.get("data", {}).get("reference")
    if not reference:
        return HttpResponseBadRequest("Missing payment reference.")

    payment = get_object_or_404(Payment, gateway_reference=reference)
    payment.raw_webhook_payload = body
    payment.save(update_fields=["raw_webhook_payload", "updated_at"])

    if event == "charge.success":
        payment.mark_successful()
    elif event == "charge.failed":
        payment.status = Payment.Status.FAILED
        payment.save(update_fields=["status", "updated_at"])

    return JsonResponse({"received": True})


@require_POST
def escrow_release_short_term(request, care_request_id):
    """Triggered on patient/family completion confirmation for short-term care."""
    if not (request.user.is_authenticated and request.user.role in (User.Role.PATIENT, User.Role.ADMIN)):
        return HttpResponseForbidden()
    care_request = get_object_or_404(CareRequest, pk=care_request_id)
    escrow = get_object_or_404(EscrowAccount, care_request=care_request)
    try:
        escrow.release_short_term()
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"total_released": str(escrow.total_released)})


@admin_required
@require_POST
def milestone_release(request, milestone_id):
    """Admin (or an automated scheduled job) triggers a milestone release once eligible."""
    milestone = get_object_or_404(Milestone, pk=milestone_id)
    try:
        milestone.release()
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"status": milestone.status})


@admin_required
def escrow_ledger(request, care_request_id):
    """Admin — escrow wallet ledger view for a given care request."""
    escrow = get_object_or_404(EscrowAccount, care_request_id=care_request_id)
    entries = escrow.ledger_entries.order_by("-created_at")
    return render(request, "admin/escrow_ledger.html", {"escrow": escrow, "entries": entries})


@nurse_required
def nurse_wallet(request):
    """Nurse earnings/wallet screen — balance, payout status, transaction history."""
    nurse = request.user.nurse_profile
    payouts = Payout.objects.filter(nurse=nurse).order_by("-created_at")
    available_balance = sum(p.amount for p in payouts.filter(status=Payout.Status.PAID))
    if request.GET.get("format") == "json":
        return JsonResponse({
            "available_balance": str(available_balance),
            "payouts": [
                {
                    "id": str(p.id),
                    "amount": str(p.amount),
                    "status": p.status,
                    "is_milestone": p.milestone_id is not None,
                    "created_at": p.created_at.isoformat(),
                }
                for p in payouts
            ],
        })
    return render(request, "nurses/wallet.html", {"available_balance": available_balance, "payouts": payouts})


# ===========================================================================
# F. Ratings / disputes
# ===========================================================================

@patient_required
def rating_create(request, assignment_id):
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    errors = {}
    if request.method == "POST":
        score = _to_int(request.POST.get("score"), "score", errors)
        rating = Rating(
            assignment=assignment,
            rated_by=request.user,
            score=score,
            comment=request.POST.get("comment", ""),
        )
        if not errors:
            try:
                rating.full_clean()
            except ValidationError as exc:
                errors.update(exc.message_dict)
        if not errors:
            rating.save()
            return redirect("care_request_detail", care_request_id=assignment.care_request_id)

    return render(request, "ratings/create.html", {"errors": errors, "assignment": assignment})


@login_required
def dispute_create(request, care_request_id):
    care_request = get_object_or_404(CareRequest, pk=care_request_id)
    errors = {}
    if request.method == "POST":
        reason = request.POST.get("reason", "").strip()
        if not reason:
            errors["reason"] = "This field is required."
        if not errors:
            Dispute.objects.create(care_request=care_request, raised_by=request.user, reason=reason)
            return redirect("care_request_detail", care_request_id=care_request.id)

    return render(request, "disputes/create.html", {"errors": errors, "care_request": care_request})


@admin_required
def admin_dispute_list(request):
    disputes = Dispute.objects.order_by("-created_at")
    return render(request, "admin/dispute_list.html", {"disputes": disputes})


@admin_required
@require_POST
def admin_dispute_resolve(request, dispute_id):
    dispute = get_object_or_404(Dispute, pk=dispute_id)
    resolution = request.POST.get("resolution_notes", "")
    outcome = request.POST.get("status")
    if outcome not in (Dispute.Status.RESOLVED, Dispute.Status.DISMISSED):
        return HttpResponseBadRequest("status must be 'resolved' or 'dismissed'.")

    dispute.status = outcome
    dispute.resolution_notes = resolution
    dispute.resolved_by = request.user
    dispute.resolved_at = timezone.now()
    dispute.save(update_fields=["status", "resolution_notes", "resolved_by", "resolved_at", "updated_at"])
    return redirect("admin_dispute_list")


# ===========================================================================
# G. Notifications
# ===========================================================================

@login_required
def my_notifications(request):
    """Any authenticated user views their own notification history."""
    notifications = Notification.objects.filter(recipient=request.user).order_by("-created_at")
    if request.GET.get("format") == "json":
        return JsonResponse({
            "notifications": [
                {
                    "id": str(n.id), "channel": n.channel, "event_type": n.event_type,
                    "status": n.status, "created_at": n.created_at.isoformat(),
                }
                for n in notifications
            ]
        })
    return render(request, "notifications/list.html", {"notifications": notifications})


# ===========================================================================
# H. Admin operations — dispatch monitor / system status
# ===========================================================================

@admin_required
def admin_dispatch_monitor(request):
    """Admin — current jobs and their assignment/status information."""
    qs = CareRequest.objects.exclude(
        status__in=[CareRequest.Status.COMPLETED, CareRequest.Status.CANCELLED]
    ).order_by("-created_at")
    status_filter = request.GET.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)
    return render(request, "admin/dispatch_monitor.html", {"care_requests": qs})