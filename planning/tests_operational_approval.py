import importlib
from types import SimpleNamespace

import jdatetime

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserProfile
from catalog.models import Machine, MoldMachineCompatibility, PhysicalMold
from production.models import ProductionDayEntry, ProductionExecutionEvent, ProductionExecutionPath
from production.path_execution import register_path_production

from .models import PathOperationalApproval, PhysicalMoldReservation, WeeklyProgramCycle
from .operational_approval import (
    CancelledPathApproval,
    StalePathApproval,
    approve_path,
    cancel_path,
    edit_path,
    preview_path_approval,
)
from .reservation_integrity import PhysicalMoldReservationConflict, activate_reservation
from .tests_path_foundation import PathFoundationTestCase


class OperationalApprovalTests(PathFoundationTestCase):
    confirmations = [MoldMachineCompatibility.Status.UNKNOWN]

    def preview(self, path):
        return preview_path_approval(path, user=self.user, require_permission=True)

    def approve(self, path, **kwargs):
        preview = self.preview(path)
        values = {
            "submitted_fingerprint": preview.fingerprint,
            "actor": self.user,
            "confirmations": self.confirmations,
        }
        values.update(kwargs)
        return approve_path(path.pk, **values)

    def test_permission_is_backend_enforced(self):
        path = self.create_path()
        preview = self.preview(path)
        for role in (Role.PLANNING_CLERK, Role.PLANNING_EXPERT):
            user = get_user_model().objects.create_user(username=f"blocked-{role}")
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role = role
            profile.save(update_fields=["role"])
            with self.subTest(role=role), self.assertRaises(PermissionDenied):
                approve_path(
                    path.pk,
                    submitted_fingerprint=preview.fingerprint,
                    actor=user,
                    confirmations=self.confirmations,
                )
        self.assertTrue(self.approve(path).created)

    def test_governed_source_cannot_become_operational_before_final_merge(self):
        cycle = WeeklyProgramCycle.objects.create(
            program_number=8800,
            program_date=self.plan.date,
            created_by=self.user,
        )
        self.plan.governance_cycle = cycle
        self.plan.save(update_fields=["governance_cycle"])
        path = self.create_path()
        preview = self.preview(path)
        with self.assertRaisesMessage(ValidationError, "Merged/Final"):
            approve_path(
                path.pk,
                submitted_fingerprint=preview.fingerprint,
                actor=self.user,
                confirmations=self.confirmations,
            )
        self.assertFalse(PathOperationalApproval.objects.exists())
        self.assertFalse(PhysicalMoldReservation.objects.exists())
        self.assertFalse(ProductionExecutionPath.objects.exists())

        preparing = PhysicalMoldReservation.objects.create(
            approval_revision=None,
            source_path=path,
            requirement=self.requirement,
            machine=self.machine_a,
            physical_mold=self.mold_a,
            approved_planned_share_quantity=path.planned_share_quantity,
            reserved_start_date=path.planned_start_date,
            reserved_end_date=path.planned_end_date,
            compatibility_status_snapshot=MoldMachineCompatibility.Status.UNKNOWN,
            mold_status_snapshot=PhysicalMold.Status.READY,
            status=PhysicalMoldReservation.Status.PREPARING,
        )
        with self.assertRaisesMessage(ValidationError, "Merged/Final"):
            activate_reservation(preparing)
        preparing.refresh_from_db()
        self.assertEqual(preparing.status, PhysicalMoldReservation.Status.PREPARING)

    def test_approval_is_atomic_operational_and_audited(self):
        path = self.create_path()
        result = self.approve(path)
        self.assertEqual(result.reservation.status, PhysicalMoldReservation.Status.ACTIVE)
        self.assertEqual(result.execution_path.status, ProductionExecutionPath.Status.AWAITING)
        self.assertEqual(result.reservation.execution_path, result.execution_path)
        self.assertEqual(result.approval.action, PathOperationalApproval.Action.APPROVED)
        self.assertEqual(result.approval.actor, self.user)
        self.assertEqual(
            result.execution_path.events.filter(
                event_type=ProductionExecutionEvent.EventType.ACTIVATED
            ).count(),
            1,
        )

    def test_repeated_approval_is_idempotent(self):
        path = self.create_path()
        first = self.approve(path)
        second = self.approve(path)
        self.assertFalse(second.created)
        self.assertEqual(first.approval.pk, second.approval.pk)
        self.assertEqual(PhysicalMoldReservation.objects.count(), 1)
        self.assertEqual(ProductionExecutionPath.objects.count(), 1)
        self.assertEqual(PathOperationalApproval.objects.count(), 1)

    def test_path_scoped_staleness_and_other_approved_path_independence(self):
        path_a = self.create_path(sequence=1)
        path_b = self.create_path(physical_mold=self.mold_b, sequence=2)
        result_a = self.approve(path_a)
        old_b = self.preview(path_b)
        path_b.planned_share_quantity = 123
        path_b.save(update_fields=["planned_share_quantity"])
        with self.assertRaises(StalePathApproval):
            approve_path(
                path_b.pk,
                submitted_fingerprint=old_b.fingerprint,
                actor=self.user,
                confirmations=self.confirmations,
            )
        self.assertEqual(
            PathOperationalApproval.objects.get(pk=result_a.approval.pk).action,
            PathOperationalApproval.Action.APPROVED,
        )
        self.assertEqual(result_a.reservation.status, PhysicalMoldReservation.Status.ACTIVE)

    def test_partial_approval_conflict_isolated_and_later_correction_is_path_only(self):
        mold_c = PhysicalMold.objects.create(code="SLICE-2B4-MOLD-C", mold_type=self.mold_type)
        path_a = self.create_path(sequence=1)
        path_b = self.create_path(machine=self.machine_b, sequence=2)
        path_c = self.create_path(physical_mold=self.mold_b, sequence=3)
        approved_a = self.approve(path_a)
        with self.assertRaises(PhysicalMoldReservationConflict):
            self.approve(path_b)
        self.assertEqual(path_b.operational_approvals.count(), 0)
        approved_c = self.approve(path_c)
        self.assertFalse(path_b.operational_approvals.exists())
        path_b.physical_mold = mold_c
        path_b.save(update_fields=["physical_mold"])
        approved_b = self.approve(path_b)
        self.assertEqual(approved_b.reservation.physical_mold, mold_c)
        self.assertEqual(approved_a.reservation.status, PhysicalMoldReservation.Status.ACTIVE)
        self.assertEqual(approved_c.reservation.status, PhysicalMoldReservation.Status.ACTIVE)
        self.assertEqual(path_a.operational_approvals.count(), 1)
        self.assertEqual(path_c.operational_approvals.count(), 1)

    def test_machine_mold_and_dates_are_in_path_fingerprint(self):
        changes = (
            ("machine", self.machine_b),
            ("physical_mold", self.mold_b),
            ("planned_start_date", jdatetime.date(1405, 2, 2)),
            ("planned_end_date", jdatetime.date(1405, 2, 4)),
        )
        for index, (field, value) in enumerate(changes, start=1):
            path = self.create_path(sequence=index)
            preview = self.preview(path)
            setattr(path, field, value)
            path.save(update_fields=[field])
            with self.subTest(field=field), self.assertRaises(StalePathApproval):
                approve_path(
                    path.pk,
                    submitted_fingerprint=preview.fingerprint,
                    actor=self.user,
                    confirmations=self.confirmations,
                )

    def test_cancel_before_production_preserves_history_and_blocks_reactivation(self):
        path = self.create_path()
        approved = self.approve(path)
        cancelled = cancel_path(path.pk, actor=self.user, reason="optional")
        approved.reservation.refresh_from_db()
        approved.execution_path.refresh_from_db()
        self.assertEqual(approved.reservation.status, PhysicalMoldReservation.Status.CANCELLED)
        self.assertEqual(approved.execution_path.status, ProductionExecutionPath.Status.CANCELLED)
        self.assertEqual(cancelled.approval.reason, "optional")
        self.assertEqual(PathOperationalApproval.objects.count(), 2)
        self.assertEqual(PhysicalMoldReservation.objects.count(), 1)
        with self.assertRaises(CancelledPathApproval):
            self.approve(path)

    def test_cancel_after_first_production_is_rejected(self):
        path = self.create_path()
        approved = self.approve(path)
        register_path_production(
            approved.execution_path,
            date=jdatetime.date(1405, 2, 1),
            shots=1,
            active_cavities=1,
            scrap_pieces=0,
            actor=self.user,
        )
        with self.assertRaises(ValidationError):
            cancel_path(path.pk, actor=self.user)
        approved.reservation.refresh_from_db()
        self.assertEqual(approved.reservation.status, PhysicalMoldReservation.Status.ACTIVE)

    def test_valid_preproduction_edit_preserves_execution_identity_and_old_reservation(self):
        path = self.create_path()
        approved = self.approve(path)
        preview = self.preview(path)
        result = edit_path(
            path.pk,
            submitted_fingerprint=preview.fingerprint,
            actor=self.user,
            machine_id=self.machine_b.pk,
            physical_mold_id=self.mold_b.pk,
            target=15000,
            start_date=jdatetime.date(1405, 2, 4),
            end_date=jdatetime.date(1405, 2, 6),
            confirmations=self.confirmations,
        )
        approved.reservation.refresh_from_db()
        result.execution_path.refresh_from_db()
        path.refresh_from_db()
        self.assertEqual(result.execution_path.pk, approved.execution_path.pk)
        self.assertEqual(approved.reservation.status, PhysicalMoldReservation.Status.SUPERSEDED)
        self.assertEqual(result.reservation.status, PhysicalMoldReservation.Status.ACTIVE)
        self.assertEqual((path.machine_id, path.physical_mold_id), (self.machine_b.pk, self.mold_b.pk))
        self.assertEqual(result.execution_path.current_good_target, 15000)
        self.assertEqual(PathOperationalApproval.objects.count(), 2)

    def test_conflicting_edit_rolls_back_every_previous_value(self):
        path = self.create_path()
        approved = self.approve(path)
        blocker_path = self.create_path(physical_mold=self.mold_b, sequence=2)
        self.approve(blocker_path)
        preview = self.preview(path)
        with self.assertRaises(PhysicalMoldReservationConflict):
            edit_path(
                path.pk,
                submitted_fingerprint=preview.fingerprint,
                actor=self.user,
                machine_id=self.machine_b.pk,
                physical_mold_id=self.mold_b.pk,
                target=99,
                start_date=path.planned_start_date,
                end_date=path.planned_end_date,
                confirmations=self.confirmations,
            )
        path.refresh_from_db()
        approved.execution_path.refresh_from_db()
        approved.reservation.refresh_from_db()
        self.assertEqual(path.machine_id, self.machine_a.pk)
        self.assertEqual(path.physical_mold_id, self.mold_a.pk)
        self.assertEqual(approved.execution_path.current_good_target, 20000)
        self.assertEqual(approved.reservation.status, PhysicalMoldReservation.Status.ACTIVE)
        self.assertEqual(path.reservation_snapshots.count(), 1)

    def test_edit_after_first_production_is_rejected_for_all_operational_values(self):
        path = self.create_path()
        approved = self.approve(path)
        register_path_production(
            approved.execution_path,
            date=jdatetime.date(1405, 2, 1),
            shots=1,
            active_cavities=1,
            scrap_pieces=0,
            actor=self.user,
        )
        preview = self.preview(path)
        with self.assertRaises(ValidationError):
            edit_path(
                path.pk,
                submitted_fingerprint=preview.fingerprint,
                actor=self.user,
                machine_id=self.machine_b.pk,
                physical_mold_id=self.mold_b.pk,
                target=10,
                start_date=path.planned_start_date,
                end_date=path.planned_end_date,
                confirmations=self.confirmations,
            )

    def test_approval_evidence_is_append_only(self):
        evidence = self.approve(self.create_path()).approval
        evidence.reason = "forged"
        with self.assertRaises(ValueError):
            evidence.save()
        with self.assertRaises(ValueError):
            PathOperationalApproval.objects.filter(pk=evidence.pk).update(reason="forged")
        with self.assertRaises(ValueError):
            PathOperationalApproval.objects.filter(pk=evidence.pk).delete()

    def test_approval_evidence_creation_requires_canonical_service(self):
        approved = self.approve(self.create_path())
        values = {
            "planned_path": approved.approval.planned_path,
            "execution_path": approved.execution_path,
            "reservation": approved.reservation,
            "sequence": 2,
            "action": PathOperationalApproval.Action.EDITED,
            "path_fingerprint": approved.approval.path_fingerprint,
            "after_snapshot": approved.approval.after_snapshot,
            "actor": self.user,
        }
        with self.assertRaisesRegex(ValueError, "canonical service"):
            PathOperationalApproval.objects.create(**values)
        instance = PathOperationalApproval(**values)
        with self.assertRaisesRegex(ValueError, "canonical service"):
            instance.save(force_insert=True)
        with self.assertRaisesRegex(ValueError, "canonical service"):
            PathOperationalApproval.objects.bulk_create(
                [PathOperationalApproval(**values)]
            )
        self.assertEqual(PathOperationalApproval.objects.count(), 1)
        self.assertFalse(
            hasattr(approved.approval, "_operational_approval_service_create")
        )

    def test_reservation_provenance_constraints_preserve_valid_lifecycles(self):
        legacy_path = self.create_path(sequence=1)
        legacy = self.create_reservation(legacy_path)
        self.assertIsNotNone(legacy.approval_revision_id)

        preparing_path = self.create_path(physical_mold=self.mold_b, sequence=2)
        preparing = PhysicalMoldReservation.objects.create(
            approval_revision=None,
            source_path=preparing_path,
            requirement=preparing_path.requirement,
            machine=preparing_path.machine,
            physical_mold=preparing_path.physical_mold,
            execution_path=None,
            approved_planned_share_quantity=preparing_path.planned_share_quantity,
            reserved_start_date=preparing_path.planned_start_date,
            reserved_end_date=preparing_path.planned_end_date,
            compatibility_status_snapshot=MoldMachineCompatibility.Status.UNKNOWN,
            mold_status_snapshot=preparing_path.physical_mold.status,
            status=PhysicalMoldReservation.Status.PREPARING,
        )
        self.assertIsNone(preparing.execution_path_id)

        active_path = self.create_path(
            physical_mold=PhysicalMold.objects.create(
                code="SLICE-2B4-PROVENANCE", mold_type=self.mold_type
            ),
            sequence=3,
        )
        active = self.approve(active_path).reservation
        self.assertIsNotNone(active.source_path_id)
        self.assertIsNotNone(active.execution_path_id)

    def test_reservation_without_any_provenance_is_database_rejected(self):
        path = self.create_path()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PhysicalMoldReservation.objects.create(
                approval_revision=None,
                source_path=None,
                requirement=path.requirement,
                machine=path.machine,
                physical_mold=path.physical_mold,
                approved_planned_share_quantity=path.planned_share_quantity,
                reserved_start_date=path.planned_start_date,
                reserved_end_date=path.planned_end_date,
                compatibility_status_snapshot=MoldMachineCompatibility.Status.UNKNOWN,
                mold_status_snapshot=path.physical_mold.status,
                status=PhysicalMoldReservation.Status.PREPARING,
            )

    def test_active_path_reservation_without_execution_is_database_rejected(self):
        path = self.create_path()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PhysicalMoldReservation.objects.create(
                approval_revision=None,
                source_path=path,
                requirement=path.requirement,
                machine=path.machine,
                physical_mold=path.physical_mold,
                execution_path=None,
                approved_planned_share_quantity=path.planned_share_quantity,
                reserved_start_date=path.planned_start_date,
                reserved_end_date=path.planned_end_date,
                compatibility_status_snapshot=MoldMachineCompatibility.Status.UNKNOWN,
                mold_status_snapshot=path.physical_mold.status,
                status=PhysicalMoldReservation.Status.ACTIVE,
                activated_at=timezone.now(),
            )

    def test_0018_reverse_refuses_path_only_reservations_without_data_loss(self):
        approved = self.approve(self.create_path())
        revision_count = approved.reservation.requirement.item.plan.approval_revisions.count()
        reverse = importlib.import_module(
            "planning.migrations.0018_pathoperationalapproval"
        ).make_approval_revision_required
        with self.assertRaisesRegex(RuntimeError, "Cannot reverse planning.0018"):
            reverse(apps, SimpleNamespace(connection=connection))
        self.assertTrue(
            PhysicalMoldReservation.objects.filter(pk=approved.reservation.pk).exists()
        )
        self.assertIsNone(
            PhysicalMoldReservation.objects.get(pk=approved.reservation.pk).approval_revision_id
        )
        self.assertEqual(
            approved.reservation.requirement.item.plan.approval_revisions.count(),
            revision_count,
        )

    def test_operational_state_endpoints_are_strictly_post_only(self):
        path = self.create_path()
        self.client.force_login(self.user)
        urls = (
            reverse("path_operational_approve", args=[path.pk]),
            reverse("path_operational_edit", args=[path.pk]),
            reverse("path_operational_cancel", args=[path.pk]),
        )
        for url in urls:
            with self.subTest(method="GET", url=url):
                self.assertEqual(self.client.get(url).status_code, 405)
            with self.subTest(method="HEAD", url=url):
                self.assertEqual(self.client.head(url).status_code, 405)
        self.assertEqual(PathOperationalApproval.objects.count(), 0)
        self.assertEqual(PhysicalMoldReservation.objects.count(), 0)
        self.assertEqual(ProductionExecutionPath.objects.count(), 0)

    def test_manager_view_endpoint_activates_and_expert_is_forbidden(self):
        path = self.create_path()
        preview = self.preview(path)
        expert = get_user_model().objects.create_user(username="endpoint-expert")
        profile, _ = UserProfile.objects.get_or_create(user=expert)
        profile.role = Role.PLANNING_EXPERT
        profile.save(update_fields=["role"])
        self.client.force_login(expert)
        response = self.client.post(
            reverse("path_operational_approve", args=[path.pk]),
            {"fingerprint": preview.fingerprint, "confirmations": self.confirmations},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(path.operational_approvals.exists())
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("path_operational_approve", args=[path.pk]),
            {"fingerprint": preview.fingerprint, "confirmations": self.confirmations},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(path.operational_approvals.exists())
