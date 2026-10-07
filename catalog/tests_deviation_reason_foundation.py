from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import DeviationReason


class DeviationReasonFoundationTests(TestCase):
    def test_structured_codes_and_applicability_are_stable(self):
        reason = DeviationReason.objects.create(
            label="Machine failure",
            code=DeviationReason.Code.MACHINE_BREAKDOWN,
            applicability=DeviationReason.Applicability.PRODUCTION,
        )
        self.assertEqual(reason.code, "machine_breakdown")
        self.assertEqual(reason.applicability, "production")
        self.assertIn(DeviationReason.Code.OTHER, DeviationReason.Code.values)

    def test_unknown_legacy_reason_remains_valid_and_unclassified(self):
        reason = DeviationReason.objects.create(label="Legacy unknown label")
        self.assertIsNone(reason.code)
        self.assertIsNone(reason.applicability)

    def test_canonical_code_is_unique_and_classification_is_complete(self):
        DeviationReason.objects.create(
            label="First machine failure",
            code=DeviationReason.Code.MACHINE_BREAKDOWN,
            applicability=DeviationReason.Applicability.PRODUCTION,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            DeviationReason.objects.create(
                label="Duplicate machine failure",
                code=DeviationReason.Code.MACHINE_BREAKDOWN,
                applicability=DeviationReason.Applicability.PRODUCTION,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            DeviationReason.objects.create(
                label="Partial classification", code=DeviationReason.Code.OTHER
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            DeviationReason.objects.create(
                label="Blank code", code="", applicability=DeviationReason.Applicability.BOTH
            )
