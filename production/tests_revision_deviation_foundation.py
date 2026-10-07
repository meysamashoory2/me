import jdatetime
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from catalog.models import DeviationReason, Machine, MoldOption, PhysicalMold, Product, ProductGroup, ProductSubGroup, ProductionUnit
from planning.models import WeeklyPlan, WeeklyPlanItem, WeeklyPlanLine
from .models import ProductionDayEntry, ProductionDeviation, ProductionExecutionPath, ProductionRequirementProgram, ProductionTargetRevision


class ProductionEvidenceFoundationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="b3a-production")
        group = ProductGroup.objects.create(name="B3A production group")
        subgroup = ProductSubGroup.objects.create(name="B3A production subgroup", group=group)
        unit = ProductionUnit.objects.create(number=986, name="B3A production unit")
        cls.machine = Machine.objects.create(number="B3A-X", unit=unit)
        product = Product.objects.create(code="B3A-XP", name="B3A execution product", subgroup=subgroup)
        mold_type = MoldOption.objects.create(code="B3A-XT", label="B3A execution mold")
        cls.mold = PhysicalMold.objects.create(code="B3A-XM", mold_type=mold_type)
        plan = WeeklyPlan.objects.create(program_number="B3A-XPLAN", date=jdatetime.date(1405, 12, 1), created_by=cls.user)
        item = WeeklyPlanItem.objects.create(plan=plan, subgroup=subgroup, unit=unit, machine=cls.machine, product=product, mold=mold_type, mold_change_weekday=0, mold_change_date=plan.date)
        cls.line = WeeklyPlanLine.objects.create(item=item, mold=mold_type, quantity=100, cycle=30)
        cls.reason = DeviationReason.objects.create(label="B3A machine", code=DeviationReason.Code.MACHINE_BREAKDOWN, applicability=DeviationReason.Applicability.PRODUCTION)
        cls.other = DeviationReason.objects.create(label="B3A other", code=DeviationReason.Code.OTHER, applicability=DeviationReason.Applicability.BOTH)

    def make_path(self, suffix=""):
        program = ProductionRequirementProgram.objects.create(requirement=self.line, created_by=self.user)
        return ProductionExecutionPath.objects.create(requirement_program=program, machine=self.machine, physical_mold=self.mold, initial_good_target=100, current_good_target=100, created_by=self.user)

    def test_pending_target_revision_is_evidence_only_and_immutable(self):
        path = self.make_path()
        revision = ProductionTargetRevision.objects.create(execution_path=path, original_target=100, previous_effective_target=100, requested_target=120, delta=20, source="manual", requester=self.user, reason=self.reason)
        path.refresh_from_db()
        self.assertEqual(path.current_good_target, 100)
        with self.assertRaises(ValueError):
            revision.save()
        with self.assertRaises(ValueError):
            revision.delete()
        with self.assertRaises(ValueError):
            ProductionTargetRevision.objects.filter(pk=revision.pk).update(note="changed")
        with self.assertRaises(ValueError):
            ProductionTargetRevision.objects.bulk_update([revision], ["note"])
        with self.assertRaises(ValueError):
            ProductionTargetRevision.objects.bulk_create([ProductionTargetRevision(execution_path=path, original_target=100, previous_effective_target=100, requested_target=120, source="manual", requester=self.user, reason=self.reason)])
        with self.assertRaises(ValueError):
            ProductionTargetRevision(execution_path=path, original_target=100, previous_effective_target=100, requested_target=120, source="manual", requester=self.user, reason=self.reason).save_base(raw=True)
        with self.assertRaises(ValueError):
            ProductionTargetRevision.objects.filter(pk=revision.pk).delete()

    def test_approved_and_rejected_evidence_consistency(self):
        path = self.make_path()
        ProductionTargetRevision.objects.create(execution_path=path, original_target=100, previous_effective_target=100, requested_target=120, approved_target=115, delta=15, source="manual", requester=self.user, approver=self.user, approved_at=timezone.now(), reason=self.reason, state="approved")
        ProductionTargetRevision.objects.create(execution_path=path, original_target=100, previous_effective_target=100, requested_target=80, delta=-20, source="manual", requester=self.user, approver=self.user, approved_at=timezone.now(), reason=self.reason, state="rejected")
        with self.assertRaises(ValidationError):
            ProductionTargetRevision.objects.create(execution_path=path, original_target=100, previous_effective_target=100, requested_target=80, source="manual", requester=self.user, reason=self.reason, state="approved")
        with self.assertRaises(ValidationError):
            ProductionTargetRevision.objects.create(execution_path=path, original_target=100, previous_effective_target=100, requested_target=120, approved_target=115, delta=20, source="manual", requester=self.user, approver=self.user, approved_at=timezone.now(), reason=self.reason, state="approved")

    def test_deviation_rejects_cross_path_lineage_and_nonpositive_quantity(self):
        first = self.make_path()
        second_program = ProductionRequirementProgram.objects.get(pk=first.requirement_program_id)
        second = ProductionExecutionPath.objects.create(requirement_program=second_program, machine=self.machine, physical_mold=self.mold, initial_good_target=100, current_good_target=100, status="running", first_production_at=timezone.now())
        target = ProductionTargetRevision.objects.create(execution_path=first, original_target=100, previous_effective_target=100, requested_target=110, delta=10, source="manual", requester=self.user, reason=self.reason)
        entry = ProductionDayEntry(execution_path=first, date=jdatetime.date(1405, 12, 1), shots=10, active_cavities=2, gross_pieces=20, scrap_quantity=1, good_pieces=19)
        entry._execution_service_create = True
        entry.save()
        ProductionDeviation.objects.create(execution_path=first, category="target_change", reason=self.reason, target_revision=target, day_entry=entry, explained_quantity=10, actor=self.user)
        with self.assertRaises(ValidationError):
            ProductionDeviation.objects.create(execution_path=second, category="target_change", reason=self.reason, target_revision=target, actor=self.user)
        with self.assertRaises(ValidationError):
            ProductionDeviation.objects.create(execution_path=second, category="shortfall", reason=self.reason, day_entry=entry, actor=self.user)
        with self.assertRaises(ValidationError):
            ProductionDeviation.objects.create(execution_path=first, category="shortfall", reason=self.reason, explained_quantity=0, actor=self.user)

    def test_reason_scope_note_actor_and_bulk_guards(self):
        path = self.make_path()
        planning_reason = DeviationReason.objects.create(label="B3A planning only", code=DeviationReason.Code.PLANNING_CALCULATION_ERROR, applicability=DeviationReason.Applicability.PLANNING)
        with self.assertRaises(ValidationError):
            ProductionDeviation.objects.create(execution_path=path, category="shortfall", reason=planning_reason, actor=self.user)
        with self.assertRaises(ValidationError):
            ProductionDeviation.objects.create(execution_path=path, category="other", reason=self.reason, note="   ", actor=self.user)
        with self.assertRaises(ValidationError):
            ProductionDeviation.objects.create(execution_path=path, category="operational_change", reason=self.other, note="   ", actor=self.user)
        audit_actor = get_user_model().objects.create_user(username="b3a-production-audit-actor")
        deviation = ProductionDeviation.objects.create(execution_path=path, category="other", reason=self.reason, note="documented", actor=audit_actor)
        with self.assertRaises(ValueError):
            ProductionDeviation.objects.filter(pk=deviation.pk).update(note="changed")
        with self.assertRaises(ValueError):
            ProductionDeviation.objects.bulk_update([deviation], ["note"])
        with self.assertRaises(ValueError):
            ProductionDeviation.objects.bulk_create([ProductionDeviation(execution_path=path, category="shortfall", reason=self.reason, actor=self.user)])
        deviation.note = "mutated"
        with self.assertRaises(ValueError):
            deviation.save()
        with self.assertRaises(ValueError):
            deviation.delete()
        with self.assertRaises(ValueError):
            ProductionDeviation.objects.filter(pk=deviation.pk).delete()
        with self.assertRaises(ValueError):
            ProductionDeviation(execution_path=path, category="shortfall", reason=self.reason, actor=audit_actor).save_base(raw=True)
        with self.assertRaises(ProtectedError):
            audit_actor.delete()
