from django.contrib import admin

# Register your models here.

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (
    User, PatientProfile, NurseProfile, NurseDocument, VerificationEvent,
    PartnerHospital, CareRequest, Assignment, VitalsRecord, ClinicalThreshold,
    EmergencyReferral, Payment, EscrowAccount, Milestone, LedgerEntry, Payout,
    Rating, Dispute, Notification,
)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "phone_number", "role", "is_phone_verified", "is_staff")
    list_filter = ("role", "is_phone_verified", "is_staff")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Navvi", {"fields": ("role", "phone_number", "is_phone_verified")}),
    )


@admin.register(PatientProfile)
class PatientProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "preferred_language", "is_diaspora_booker")


@admin.register(NurseProfile)
class NurseProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "nmcn_registration_number", "verification_status", "reliability_score")
    list_filter = ("verification_status",)
    search_fields = ("nmcn_registration_number", "user__username", "user__first_name")
    actions = ["approve_nurses"]

    @admin.action(description="Approve selected nurses (Verified/Active)")
    def approve_nurses(self, request, queryset):
        for nurse in queryset:
            nurse.mark_verified(admin_user=request.user)


@admin.register(NurseDocument)
class NurseDocumentAdmin(admin.ModelAdmin):
    list_display = ("nurse", "document_type", "uploaded_at")


@admin.register(VerificationEvent)
class VerificationEventAdmin(admin.ModelAdmin):
    list_display = ("nurse", "action", "actor", "created_at")


@admin.register(PartnerHospital)
class PartnerHospitalAdmin(admin.ModelAdmin):
    list_display = ("name", "contact_user", "address")


@admin.register(CareRequest)
class CareRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "care_type", "duration_type", "status", "requested_appointment_time")
    list_filter = ("status", "care_type", "duration_type", "channel")
    search_fields = ("patient__username", "landmark")


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ("care_request", "nurse", "status", "accepted_at", "completed_at")
    list_filter = ("status",)


@admin.register(VitalsRecord)
class VitalsRecordAdmin(admin.ModelAdmin):
    list_display = ("assignment", "recorded_by", "is_high_risk", "recorded_at")
    list_filter = ("is_high_risk", "synced_offline")


@admin.register(ClinicalThreshold)
class ClinicalThresholdAdmin(admin.ModelAdmin):
    list_display = ("field_name", "comparator", "threshold_value", "is_active")


@admin.register(EmergencyReferral)
class EmergencyReferralAdmin(admin.ModelAdmin):
    list_display = ("patient", "partner_hospital", "family_notification_status", "hospital_notification_status")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("gateway_reference", "care_request", "gateway", "amount", "status")
    list_filter = ("gateway", "status")


@admin.register(EscrowAccount)
class EscrowAccountAdmin(admin.ModelAdmin):
    list_display = ("care_request", "commission_rate", "total_held", "total_released")


@admin.register(Milestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display = ("escrow", "sequence_number", "due_date", "status")
    list_filter = ("status",)


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("escrow", "entry_type", "amount", "created_at")
    list_filter = ("entry_type",)


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ("nurse", "amount", "status", "milestone")
    list_filter = ("status",)


@admin.register(Rating)
class RatingAdmin(admin.ModelAdmin):
    list_display = ("assignment", "rated_by", "score")


@admin.register(Dispute)
class DisputeAdmin(admin.ModelAdmin):
    list_display = ("care_request", "raised_by", "status", "created_at")
    list_filter = ("status",)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "channel", "event_type", "status", "is_priority")
    list_filter = ("channel", "event_type", "status")