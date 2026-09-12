"""
Navvi — models.py

Design notes:
- No services.py: business logic lives on the models themselves (fat models)
  or as querysets/managers. Views/DRF serializers call these methods directly.
- Split across logical sections below. In a real project you'd likely break
  these into separate apps (accounts, nurses, bookings, dispatch, vitals,
  payments, notifications, disputes) sharing a common `core` app for
  base classes/choices — but this single file gives you the full schema
  to start from and split later without changing field definitions.
- UUID primary keys are used for anything that crosses API boundaries
  (safer than exposing sequential IDs for patient/payment records).
- All money fields use DecimalField, never FloatField.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models, transaction
from django.utils import timezone


# ---------------------------------------------------------------------------
# Base / shared
# ---------------------------------------------------------------------------

class BaseModel(models.Model):
    """Common audit fields for every table."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# Accounts / roles
# ---------------------------------------------------------------------------

class User(AbstractUser):
    """
    Custom user model. Role determines which profile (NurseProfile,
    PatientProfile, PartnerHospital) is attached, and drives
    role-based access control at the permission-class / view level.
    """

    class Role(models.TextChoices):
        PATIENT = "patient", "Patient / Family"
        NURSE = "nurse", "Nurse / Midwife"
        ADMIN = "admin", "Internal Admin / Auditor"
        PARTNER = "partner", "Partner Hospital / Telemedicine"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.CharField(max_length=20, choices=Role.choices)
    phone_number = models.CharField(max_length=20, unique=True, db_index=True)
    is_phone_verified = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.role})"


class PatientProfile(BaseModel):
    """Extra profile data for patient/family accounts."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="patient_profile")
    preferred_language = models.CharField(max_length=20, blank=True)
    is_diaspora_booker = models.BooleanField(
        default=False, help_text="True if this account typically books care for relatives remotely."
    )

    def __str__(self):
        return f"PatientProfile<{self.user.get_full_name()}>"


# ---------------------------------------------------------------------------
# Nurse verification / anti-quackery
# ---------------------------------------------------------------------------

class NurseProfile(BaseModel):
    class VerificationStatus(models.TextChoices):
        PENDING = "pending", "Pending Verification"
        VERIFIED = "verified", "Verified / Active"
        SUSPENDED = "suspended", "Suspended"
        REJECTED = "rejected", "Rejected"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="nurse_profile")
    nmcn_registration_number = models.CharField(max_length=50, unique=True, db_index=True)
    government_id_number = models.CharField(max_length=50)

    verification_status = models.CharField(
        max_length=20, choices=VerificationStatus.choices, default=VerificationStatus.PENDING
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="nurses_verified"
    )
    suspension_reason = models.TextField(blank=True)
    license_expiry_date = models.DateField(null=True, blank=True)

    # Matching inputs
    skills = models.JSONField(default=list, help_text="e.g. ['antenatal_care', 'wound_dressing', 'vitals_check']")
    reliability_score = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal("5.00"),
        validators=[MinValueValidator(0), MaxValueValidator(5)],
    )
    current_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    current_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["verification_status"]),
        ]

    def __str__(self):
        return f"Nurse<{self.user.get_full_name()}, {self.verification_status}>"

    # --- state machine -----------------------------------------------
    def mark_verified(self, admin_user):
        self.verification_status = self.VerificationStatus.VERIFIED
        self.verified_at = timezone.now()
        self.verified_by = admin_user
        self.save(update_fields=["verification_status", "verified_at", "verified_by", "updated_at"])
        VerificationEvent.objects.create(
            nurse=self, action=VerificationEvent.Action.APPROVED, actor=admin_user
        )

    def mark_rejected(self, admin_user, reason=""):
        self.verification_status = self.VerificationStatus.REJECTED
        self.suspension_reason = reason
        self.save(update_fields=["verification_status", "suspension_reason", "updated_at"])
        VerificationEvent.objects.create(
            nurse=self, action=VerificationEvent.Action.REJECTED, actor=admin_user, notes=reason
        )

    def suspend(self, admin_user, reason):
        self.verification_status = self.VerificationStatus.SUSPENDED
        self.suspension_reason = reason
        self.save(update_fields=["verification_status", "suspension_reason", "updated_at"])
        VerificationEvent.objects.create(
            nurse=self, action=VerificationEvent.Action.SUSPENDED, actor=admin_user, notes=reason
        )

    @property
    def can_accept_jobs(self):
        return self.verification_status == self.VerificationStatus.VERIFIED


class NurseDocument(BaseModel):
    """Uploaded credential/ID documents for a nurse."""

    class DocumentType(models.TextChoices):
        NMCN_LICENSE = "nmcn_license", "NMCN License"
        GOVERNMENT_ID = "government_id", "Government ID"
        OTHER = "other", "Other"

    nurse = models.ForeignKey(NurseProfile, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    file = models.FileField(upload_to="nurse_documents/%Y/%m/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.document_type} — {self.nurse}"


class VerificationEvent(BaseModel):
    """Audit trail of verification decisions (required for administrative auditability)."""

    class Action(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SUSPENDED = "suspended", "Suspended"
        REINSTATED = "reinstated", "Reinstated"

    nurse = models.ForeignKey(NurseProfile, on_delete=models.CASCADE, related_name="verification_events")
    action = models.CharField(max_length=20, choices=Action.choices)
    actor = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="verification_actions")
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.nurse} — {self.action}"


# ---------------------------------------------------------------------------
# Partner hospitals / telemedicine
# ---------------------------------------------------------------------------

class PartnerHospital(BaseModel):
    name = models.CharField(max_length=255)
    contact_user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="partner_profile", null=True, blank=True
    )
    emergency_dashboard_endpoint = models.URLField(
        blank=True, help_text="Webhook/API endpoint used to push emergency referral data."
    )
    address = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Booking / matching / dispatch
# ---------------------------------------------------------------------------

class CareType(models.TextChoices):
    ANTENATAL = "antenatal_care", "Antenatal Care"
    WOUND_DRESSING = "wound_dressing", "Wound Dressing"
    VITALS_CHECK = "vitals_check", "General Vitals Check"
    OTHER = "other", "Other"


class BookingChannel(models.TextChoices):
    WEB = "web", "Web"
    WHATSAPP = "whatsapp", "WhatsApp"
    IVR = "ivr", "IVR"


class CareRequestQuerySet(models.QuerySet):
    def awaiting_dispatch(self):
        return self.filter(status=CareRequest.Status.PAID)

    def for_nurse_candidates(self):
        return self.filter(status__in=[CareRequest.Status.PAID, CareRequest.Status.OFFERED])


class CareRequest(BaseModel):
    class Status(models.TextChoices):
        CREATED = "created", "Created"
        AWAITING_PAYMENT = "awaiting_payment", "Awaiting Payment"
        PAID = "paid", "Paid"
        OFFERED = "offered", "Offered"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        ASSIGNED = "assigned", "Assigned"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class DurationType(models.TextChoices):
        SHORT_TERM = "short_term", "Short-term (1-3 days)"
        LONG_TERM = "long_term", "Long-term (1-2 months)"

    patient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="care_requests")
    booked_by = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="bookings_made",
        help_text="May differ from patient (e.g. diaspora family member booking on their behalf).",
    )
    channel = models.CharField(max_length=20, choices=BookingChannel.choices, default=BookingChannel.WEB)
    care_type = models.CharField(max_length=30, choices=CareType.choices)
    duration_type = models.CharField(max_length=20, choices=DurationType.choices, default=DurationType.SHORT_TERM)

    symptoms_notes = models.TextField(blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    landmark = models.CharField(max_length=255, blank=True)
    requested_appointment_time = models.DateTimeField()

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED)

    # WhatsApp/IVR raw ingestion trace
    external_conversation_id = models.CharField(max_length=100, blank=True, db_index=True)
    raw_channel_payload = models.JSONField(null=True, blank=True)

    objects = CareRequestQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["requested_appointment_time"]),
        ]

    def __str__(self):
        return f"CareRequest<{self.id}, {self.care_type}, {self.status}>"

    def transition_to(self, new_status):
        """Central place to enforce valid state transitions if/when you add rules."""
        self.status = new_status
        self.save(update_fields=["status", "updated_at"])


class Assignment(BaseModel):
    """A nurse offered/accepted a specific CareRequest. Enforces one active claim per request."""

    class Status(models.TextChoices):
        OFFERED = "offered", "Offered"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETED = "completed", "Completed"

    care_request = models.OneToOneField(CareRequest, on_delete=models.CASCADE, related_name="assignment")
    nurse = models.ForeignKey(NurseProfile, on_delete=models.CASCADE, related_name="assignments")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OFFERED)

    # Matching score breakdown, useful for admin debugging/auditing of dispatch decisions
    distance_km = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    skill_match_score = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    reliability_score_snapshot = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)

    accepted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Assignment<{self.care_request_id} -> {self.nurse}>"

    @classmethod
    @transaction.atomic
    def claim(cls, care_request: CareRequest, nurse: NurseProfile, **score_fields):
        """
        Concurrency-safe claim: locks the CareRequest row so two nurses
        can't simultaneously accept the same job.
        """
        locked_request = CareRequest.objects.select_for_update().get(pk=care_request.pk)
        if locked_request.status not in (CareRequest.Status.PAID, CareRequest.Status.OFFERED):
            raise ValueError("Care request is no longer available for assignment.")
        if hasattr(locked_request, "assignment"):
            raise ValueError("Care request already has an assignment.")

        assignment = cls.objects.create(
            care_request=locked_request, nurse=nurse, status=cls.Status.ACCEPTED,
            accepted_at=timezone.now(), **score_fields,
        )
        locked_request.transition_to(CareRequest.Status.ASSIGNED)
        return assignment


# ---------------------------------------------------------------------------
# Vitals / emergency referral
# ---------------------------------------------------------------------------

class VitalsRecord(BaseModel):
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="vitals_records")
    recorded_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="vitals_recorded")

    systolic_bp = models.PositiveSmallIntegerField(null=True, blank=True)
    diastolic_bp = models.PositiveSmallIntegerField(null=True, blank=True)
    heart_rate = models.PositiveSmallIntegerField(null=True, blank=True)
    oxygen_saturation = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    temperature_celsius = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)

    is_high_risk = models.BooleanField(default=False)
    synced_offline = models.BooleanField(
        default=False, help_text="True if this record was captured offline and synced later."
    )
    recorded_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Vitals<{self.assignment_id}, high_risk={self.is_high_risk}>"

    def evaluate_thresholds(self):
        """
        Evaluate against configurable ClinicalThreshold rows rather than
        hard-coded values. Returns True if high-risk, and creates an
        EmergencyReferral if so.
        """
        thresholds = ClinicalThreshold.objects.filter(is_active=True)
        triggered = False
        for t in thresholds:
            value = getattr(self, t.field_name, None)
            if value is None:
                continue
            if t.comparator == ClinicalThreshold.Comparator.GT and value > t.threshold_value:
                triggered = True
            elif t.comparator == ClinicalThreshold.Comparator.LT and value < t.threshold_value:
                triggered = True

        self.is_high_risk = triggered
        self.save(update_fields=["is_high_risk", "updated_at"])

        if triggered and not hasattr(self, "emergency_referral"):
            EmergencyReferral.objects.create(vitals_record=self, patient=self.assignment.care_request.patient)
        return triggered


class ClinicalThreshold(BaseModel):
    """
    Configurable safety thresholds (e.g. systolic_bp > 140, oxygen_saturation < 92)
    so clinical rules aren't hard-coded and can be updated by an approved
    clinical/product authority via the admin.
    """

    class Comparator(models.TextChoices):
        GT = "gt", ">"
        LT = "lt", "<"

    field_name = models.CharField(max_length=50, help_text="Must match a VitalsRecord field name.")
    comparator = models.CharField(max_length=5, choices=Comparator.choices)
    threshold_value = models.DecimalField(max_digits=6, decimal_places=2)
    is_active = models.BooleanField(default=True)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f"{self.field_name} {self.comparator} {self.threshold_value}"


class EmergencyReferral(BaseModel):
    class NotificationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    vitals_record = models.OneToOneField(VitalsRecord, on_delete=models.CASCADE, related_name="emergency_referral")
    patient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="emergency_referrals")
    partner_hospital = models.ForeignKey(
        PartnerHospital, null=True, blank=True, on_delete=models.SET_NULL, related_name="emergency_referrals"
    )

    family_notification_status = models.CharField(
        max_length=10, choices=NotificationStatus.choices, default=NotificationStatus.PENDING
    )
    hospital_notification_status = models.CharField(
        max_length=10, choices=NotificationStatus.choices, default=NotificationStatus.PENDING
    )
    destination_detail = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"EmergencyReferral<{self.patient}, {self.created_at}>"


# ---------------------------------------------------------------------------
# Payments / escrow / payouts
# ---------------------------------------------------------------------------

class Payment(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESSFUL = "successful", "Successful"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    class Gateway(models.TextChoices):
        PAYSTACK = "paystack", "Paystack"
        FLUTTERWAVE = "flutterwave", "Flutterwave"

    care_request = models.ForeignKey(CareRequest, on_delete=models.CASCADE, related_name="payments")
    gateway = models.CharField(max_length=20, choices=Gateway.choices, default=Gateway.PAYSTACK)
    gateway_reference = models.CharField(max_length=100, unique=True, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    raw_webhook_payload = models.JSONField(null=True, blank=True)
    reconciled_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Payment<{self.gateway_reference}, {self.status}>"

    @transaction.atomic
    def mark_successful(self):
        if self.status == self.Status.SUCCESSFUL:
            return  # idempotent — guards against duplicate webhook callbacks
        self.status = self.Status.SUCCESSFUL
        self.reconciled_at = timezone.now()
        self.save(update_fields=["status", "reconciled_at", "updated_at"])
        self.care_request.transition_to(CareRequest.Status.PAID)
        EscrowAccount.objects.get_or_create(care_request=self.care_request)


class EscrowAccount(BaseModel):
    """One escrow ledger per care request, holding funds until release conditions are met."""

    care_request = models.OneToOneField(CareRequest, on_delete=models.CASCADE, related_name="escrow_account")
    commission_rate = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal("0.15"),
        validators=[MinValueValidator(Decimal("0.10")), MaxValueValidator(Decimal("0.20"))],
        help_text="Navvi commission, 10%-20% per PRD.",
    )
    total_held = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_released = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    def __str__(self):
        return f"Escrow<{self.care_request_id}>"

    @property
    def nurse_share_rate(self):
        return Decimal("1.00") - self.commission_rate

    @transaction.atomic
    def release_short_term(self):
        """Short-term (1-3 day) care: single release on completion confirmation."""
        if self.care_request.duration_type != CareRequest.DurationType.SHORT_TERM:
            raise ValueError("Not a short-term care request.")
        amount = self.total_held - self.total_released
        nurse_amount = (amount * self.nurse_share_rate).quantize(Decimal("0.01"))
        commission_amount = amount - nurse_amount
        LedgerEntry.objects.create(
            escrow=self, entry_type=LedgerEntry.EntryType.RELEASE,
            amount=nurse_amount, note="Short-term full release",
        )
        LedgerEntry.objects.create(
            escrow=self, entry_type=LedgerEntry.EntryType.COMMISSION,
            amount=commission_amount, note="Navvi commission",
        )
        self.total_released += amount
        self.save(update_fields=["total_released", "updated_at"])
        Payout.objects.create(
            nurse=self.care_request.assignment.nurse, escrow=self, amount=nurse_amount
        )


class Milestone(BaseModel):
    """Long-term care (1-2 months): 4 bi-weekly milestones, 25% release each."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ELIGIBLE = "eligible", "Eligible for Release"
        RELEASED = "released", "Released"

    escrow = models.ForeignKey(EscrowAccount, on_delete=models.CASCADE, related_name="milestones")
    sequence_number = models.PositiveSmallIntegerField(help_text="1-4")
    due_date = models.DateField()
    release_percentage = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("0.25"))
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    vitals_submitted = models.BooleanField(default=False)
    patient_checked_in = models.BooleanField(default=False)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("escrow", "sequence_number")
        ordering = ["sequence_number"]

    def __str__(self):
        return f"Milestone<{self.escrow_id} #{self.sequence_number}, {self.status}>"

    @property
    def is_release_eligible(self):
        return self.vitals_submitted and self.patient_checked_in

    @transaction.atomic
    def release(self):
        if not self.is_release_eligible:
            raise ValueError("Milestone release conditions not met.")
        if self.status == self.Status.RELEASED:
            return  # idempotent
        gross_amount = (self.escrow.total_held * self.release_percentage).quantize(Decimal("0.01"))
        nurse_amount = (gross_amount * self.escrow.nurse_share_rate).quantize(Decimal("0.01"))
        commission_amount = gross_amount - nurse_amount

        LedgerEntry.objects.create(
            escrow=self.escrow, entry_type=LedgerEntry.EntryType.RELEASE,
            amount=nurse_amount, note=f"Milestone {self.sequence_number} release",
        )
        LedgerEntry.objects.create(
            escrow=self.escrow, entry_type=LedgerEntry.EntryType.COMMISSION,
            amount=commission_amount, note=f"Milestone {self.sequence_number} commission",
        )
        self.status = self.Status.RELEASED
        self.released_at = timezone.now()
        self.save(update_fields=["status", "released_at", "updated_at"])

        self.escrow.total_released += gross_amount
        self.escrow.save(update_fields=["total_released", "updated_at"])

        Payout.objects.create(
            nurse=self.escrow.care_request.assignment.nurse, escrow=self, amount=nurse_amount
        )


class LedgerEntry(BaseModel):
    """Immutable audit record for every debit, hold, release, commission and payout."""

    class EntryType(models.TextChoices):
        HOLD = "hold", "Hold"
        RELEASE = "release", "Release"
        COMMISSION = "commission", "Commission"
        REFUND = "refund", "Refund"
        PAYOUT_FAILURE = "payout_failure", "Payout Failure"

    escrow = models.ForeignKey(EscrowAccount, on_delete=models.CASCADE, related_name="ledger_entries")
    entry_type = models.CharField(max_length=20, choices=EntryType.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.entry_type}: {self.amount} ({self.escrow_id})"


class Payout(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"

    nurse = models.ForeignKey(NurseProfile, on_delete=models.CASCADE, related_name="payouts")
    escrow = models.ForeignKey(EscrowAccount, on_delete=models.CASCADE, related_name="payouts")
    milestone = models.ForeignKey(Milestone, null=True, blank=True, on_delete=models.SET_NULL, related_name="payouts")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    gateway_reference = models.CharField(max_length=100, blank=True)
    failure_reason = models.TextField(blank=True)

    def __str__(self):
        return f"Payout<{self.nurse}, {self.amount}, {self.status}>"


# ---------------------------------------------------------------------------
# Ratings / disputes
# ---------------------------------------------------------------------------

class Rating(BaseModel):
    assignment = models.OneToOneField(Assignment, on_delete=models.CASCADE, related_name="rating")
    rated_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="ratings_given")
    score = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)

    def __str__(self):
        return f"Rating<{self.assignment_id}, {self.score}>"


class Dispute(BaseModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        UNDER_REVIEW = "under_review", "Under Review"
        RESOLVED = "resolved", "Resolved"
        DISMISSED = "dismissed", "Dismissed"

    care_request = models.ForeignKey(CareRequest, on_delete=models.CASCADE, related_name="disputes")
    raised_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="disputes_raised")
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    resolution_notes = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="disputes_resolved"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Dispute<{self.care_request_id}, {self.status}>"


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

class Notification(BaseModel):
    class Channel(models.TextChoices):
        WHATSAPP = "whatsapp", "WhatsApp"
        SMS = "sms", "SMS"
        PUSH = "push", "Push"
        EMAIL = "email", "Email"

    class EventType(models.TextChoices):
        BOOKING_CONFIRMATION = "booking_confirmation", "Booking Confirmation"
        APPOINTMENT_UPDATE = "appointment_update", "Appointment Update"
        NURSE_ASSIGNMENT = "nurse_assignment", "Nurse Assignment"
        PAYMENT_EVENT = "payment_event", "Payment Event"
        CARE_COMPLETION = "care_completion", "Care Completion"
        EMERGENCY_ALERT = "emergency_alert", "Emergency Alert"
        PAYOUT_EVENT = "payout_event", "Payout Event"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    channel = models.CharField(max_length=20, choices=Channel.choices)
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    is_priority = models.BooleanField(default=False, help_text="Emergency notifications should be prioritized.")
    payload = models.JSONField(default=dict)
    provider_reference = models.CharField(max_length=100, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "is_priority"]),
        ]

    def __str__(self):
        return f"Notification<{self.recipient}, {self.event_type}, {self.status}>"