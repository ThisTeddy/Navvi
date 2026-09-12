"""
Navvi — management command: seed_demo

Usage:
    python manage.py seed_demo

Creates a demo patient, a verified nurse, and an admin so today's client
demo doesn't start from an empty database. Safe to re-run — uses
get_or_create throughout.
"""

from django.core.management.base import BaseCommand
from django.contrib.auth.hashers import make_password

from navvi.models import User, PatientProfile, NurseProfile


class Command(BaseCommand):
    help = "Seed demo data for a client walkthrough (patient, verified nurse, admin)."

    def handle(self, *args, **options):
        # --- Admin --------------------------------------------------
        admin_user, created = User.objects.get_or_create(
            username="08000000001",
            defaults=dict(
                phone_number="08000000001",
                first_name="Demo Admin",
                role=User.Role.ADMIN,
                is_staff=True,
                is_superuser=True,
                password=make_password("demopass123"),
            ),
        )
        self.stdout.write(self.style.SUCCESS(f"Admin: 08000000001 / demopass123 {'(created)' if created else '(exists)'}"))

        # --- Patient --------------------------------------------------
        patient_user, created = User.objects.get_or_create(
            username="08000000002",
            defaults=dict(
                phone_number="08000000002",
                first_name="Amaka Demo",
                role=User.Role.PATIENT,
                password=make_password("demopass123"),
            ),
        )
        PatientProfile.objects.get_or_create(user=patient_user)
        self.stdout.write(self.style.SUCCESS(f"Patient: 08000000002 / demopass123 {'(created)' if created else '(exists)'}"))

        # --- Verified nurse --------------------------------------------------
        nurse_user, created = User.objects.get_or_create(
            username="08000000003",
            defaults=dict(
                phone_number="08000000003",
                first_name="Chinwe Demo",
                role=User.Role.NURSE,
                password=make_password("demopass123"),
            ),
        )
        nurse_profile, _ = NurseProfile.objects.get_or_create(
            user=nurse_user,
            defaults=dict(
                nmcn_registration_number="NMCN-DEMO-001",
                government_id_number="GOVID-DEMO-001",
                skills=["antenatal_care", "wound_dressing", "vitals_check"],
            ),
        )
        if nurse_profile.verification_status != NurseProfile.VerificationStatus.VERIFIED:
            nurse_profile.mark_verified(admin_user=admin_user)
        self.stdout.write(self.style.SUCCESS(f"Nurse (verified): 08000000003 / demopass123 {'(created)' if created else '(exists)'}"))

        self.stdout.write(self.style.SUCCESS("\nDemo accounts ready. Log in at /login/ with any of the above."))