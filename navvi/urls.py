"""
Navvi — urls.py

Wires up every view in views.py. Assumes this is included from the project's
root urls.py, e.g.:

    # project/urls.py
    urlpatterns = [
        path("admin/", admin.site.urls),
        path("", include("navvi.urls")),
    ]

Adjust the app label/namespace to match your actual app name once you split
into multiple apps — for now this assumes a single app.
"""

from django.urls import path

from . import views



urlpatterns = [
    path("", views.home, name="home"),
    # -----------------------------------------------------------------
    # A. Nurse onboarding / verification
    # -----------------------------------------------------------------
    path("nurses/register/", views.nurse_register, name="nurse_register"),
    path("nurses/documents/upload/", views.nurse_document_upload, name="nurse_document_upload"),
    path("nurses/verification-status/", views.nurse_verification_status, name="nurse_verification_status"),

    path("admin-ops/nurses/queue/", views.admin_nurse_verification_queue, name="admin_nurse_verification_queue"),
    path(
        "admin-ops/nurses/<uuid:nurse_id>/decision/",
        views.admin_nurse_verification_decision,
        name="admin_nurse_verification_decision",
    ),

    # -----------------------------------------------------------------
    # B. Booking (patient/family)
    # -----------------------------------------------------------------
    path("bookings/create/", views.care_request_create, name="care_request_create"),
    path("bookings/", views.my_care_requests, name="my_care_requests"),
    path("bookings/<uuid:care_request_id>/", views.care_request_detail, name="care_request_detail"),

    # -----------------------------------------------------------------
    # C. Dispatch / matching (nurse side)
    # -----------------------------------------------------------------
    path("nurses/jobs/", views.available_jobs, name="available_jobs"),
    path("bookings/<uuid:care_request_id>/claim/", views.assignment_claim, name="assignment_claim"),
    path("bookings/<uuid:care_request_id>/reject/", views.assignment_reject, name="assignment_reject"),
    path("assignments/<uuid:assignment_id>/complete/", views.assignment_complete, name="assignment_complete"),

    # -----------------------------------------------------------------
    # D. Vitals / emergency referral
    # -----------------------------------------------------------------
    path("assignments/<uuid:assignment_id>/vitals/", views.vitals_record_create, name="vitals_record_create"),
    path("vitals/<uuid:vitals_id>/high-risk-status/", views.vitals_high_risk_status, name="vitals_high_risk_status"),
    path("emergency/referrals/", views.emergency_referral_list, name="emergency_referral_list"),

    # -----------------------------------------------------------------
    # E. Payments / escrow / payouts
    # -----------------------------------------------------------------
    path("bookings/<uuid:care_request_id>/pay/", views.payment_initiate, name="payment_initiate"),
    path("webhooks/paystack/", views.paystack_webhook, name="paystack_webhook"),
    path(
        "bookings/<uuid:care_request_id>/escrow/release-short-term/",
        views.escrow_release_short_term,
        name="escrow_release_short_term",
    ),
    path("milestones/<uuid:milestone_id>/release/", views.milestone_release, name="milestone_release"),
    path(
        "admin-ops/bookings/<uuid:care_request_id>/escrow/ledger/",
        views.escrow_ledger,
        name="escrow_ledger",
    ),
    path("nurses/wallet/", views.nurse_wallet, name="nurse_wallet"),

    # -----------------------------------------------------------------
    # F. Ratings / disputes
    # -----------------------------------------------------------------
    path("assignments/<uuid:assignment_id>/rate/", views.rating_create, name="rating_create"),
    path("bookings/<uuid:care_request_id>/disputes/create/", views.dispute_create, name="dispute_create"),
    path("admin-ops/disputes/", views.admin_dispute_list, name="admin_dispute_list"),
    path("admin-ops/disputes/<uuid:dispute_id>/resolve/", views.admin_dispute_resolve, name="admin_dispute_resolve"),

    # -----------------------------------------------------------------
    # G. Notifications
    # -----------------------------------------------------------------
    path("notifications/", views.my_notifications, name="my_notifications"),

    # -----------------------------------------------------------------
    # H. Admin operations — dispatch monitor / system status
    # -----------------------------------------------------------------
    path("admin-ops/dispatch-monitor/", views.admin_dispatch_monitor, name="admin_dispatch_monitor"),
]