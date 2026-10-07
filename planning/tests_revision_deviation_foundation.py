import jdatetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from catalog.models import DeviationReason, Machine, MoldOption, PhysicalMold, Product, ProductGroup, ProductSubGroup, ProductionUnit
from .models import FinalProgramRevision, PlannedProductionPath, PlanningDeviation, PlanningRevision, WeeklyPlan, WeeklyPlanItem, WeeklyPlanLine, WeeklyProgramCycle


class PlanningEvidenceFoundationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="b3a-planner")
        cls.group = ProductGroup.objects.create(name="B3A planning group")
        cls.subgroup = ProductSubGroup.objects.create(name="B3A planning subgroup", group=cls.group)
        cls.unit = ProductionUnit.objects.create(number=987, name="B3A planning unit")
        cls.machine = Machine.objects.create(number="B3A-PM", unit=cls.unit)
        cls.product = Product.objects.create(code="B3A-PP", name="B3A product", subgroup=cls.subgroup)
        cls.mold_type = MoldOption.objects.create(code="B3A-PT", label="B3A mold type")
        cls.mold = PhysicalMold.objects.create(code="B3A-PHYSICAL", mold_type=cls.mold_type)
        cls.reason = DeviationReason.objects.create(label="Planning priority", code=DeviationReason.Code.PRIORITY_CHANGE, applicability=DeviationReason.Applicability.PLANNING)
        cls.production_reason = DeviationReason.objects.create(label="Planning invalid production-only", code=DeviationReason.Code.MATERIAL_SHORTAGE, applicability=DeviationReason.Applicability.PRODUCTION)
        cls.other = DeviationReason.objects.create(label="Planning other", code=DeviationReason.Code.OTHER, applicability=DeviationReason.Applicability.BOTH)

    def make_final(self, number):
        date = jdatetime.date(1405, 11, number)
        cycle = WeeklyProgramCycle.objects.create(program_number=1000 + number, program_date=date, created_by=self.user)
        manual = WeeklyPlan.objects.create(program_number=f"M-{number}", date=date, planning_mode="manual", governance_cycle=cycle, created_by=self.user)
        final = WeeklyPlan(program_number=f"F-{number}", date=date, planning_mode="final", governance_cycle=cycle, created_by=self.user)
        final._merge_service_create = True
        final.save()
        item = WeeklyPlanItem.objects.create(plan=final, subgroup=self.subgroup, unit=self.unit, machine=self.machine, product=self.product, mold=self.mold_type, mold_change_weekday=0, mold_change_date=date)
        line = WeeklyPlanLine.objects.create(item=item, mold=self.mold_type, quantity=100, cycle=30)
        path = PlannedProductionPath.objects.create(requirement=line, machine=self.machine, physical_mold=self.mold, planned_share_quantity=100, planned_start_date=date, planned_end_date=date)
        revision = FinalProgramRevision(cycle=cycle, revision_number=1, final_plan=final, manual_source=manual, status="effective", source_review_fingerprint="a" * 64, result_fingerprint="b" * 64, approved_by=self.user)
        revision._merge_service_create = True
        revision.save()
        return cycle, revision, item, path

    def test_revision_without_deviation_is_valid_and_immutable(self):
        cycle, final, item, path = self.make_final(1)
        before = {"q": 1}
        evidence = PlanningRevision.objects.create(cycle=cycle, final_revision=final, final_item=item, planned_path=path, revision_type="path", before_snapshot=before, after_snapshot={"q": 2}, actor=self.user)
        before["q"] = 999
        evidence.refresh_from_db()
        self.assertEqual(evidence.before_snapshot, {"q": 1})
        self.assertFalse(evidence.deviations.exists())
        evidence.context = {"changed": True}
        with self.assertRaises(ValueError):
            evidence.save()
        with self.assertRaises(ValueError):
            PlanningRevision.objects.filter(pk=evidence.pk).update(context={})
        with self.assertRaises(ValueError):
            PlanningRevision.objects.bulk_update([evidence], ["context"])
        with self.assertRaises(ValueError):
            PlanningRevision.objects.filter(pk=evidence.pk).delete()
        with self.assertRaises(ValueError):
            PlanningRevision.objects.bulk_create([PlanningRevision(cycle=cycle, final_revision=final, revision_type="plan", before_snapshot={"q": 1}, after_snapshot={"q": 2}, actor=self.user)])
        with self.assertRaises(ValueError):
            PlanningRevision(cycle=cycle, final_revision=final, revision_type="plan", actor=self.user).save_base(raw=True)
        with self.assertRaises(ValueError):
            evidence.delete()

    def test_cross_cycle_references_are_rejected(self):
        cycle, final, item, path = self.make_final(2)
        other_cycle, other_final, other_item, other_path = self.make_final(3)
        with self.assertRaises(ValidationError):
            PlanningRevision.objects.create(cycle=other_cycle, final_revision=final, revision_type="plan", actor=self.user)
        with self.assertRaises(ValidationError):
            PlanningDeviation.objects.create(cycle=cycle, final_revision=other_final, reason=self.reason, actor=self.user)
        with self.assertRaises(ValidationError):
            PlanningRevision.objects.create(cycle=cycle, final_revision=final, final_item=other_item, revision_type="row", actor=self.user)
        with self.assertRaises(ValidationError):
            PlanningRevision.objects.create(cycle=cycle, final_revision=final, planned_path=other_path, revision_type="path", actor=self.user)

    def test_deviation_validation_and_immutability(self):
        cycle, final, item, path = self.make_final(4)
        deviation = PlanningDeviation.objects.create(cycle=cycle, final_revision=final, final_item=item, planned_path=path, reason=self.reason, explained_quantity=5, actor=self.user)
        deviation.note = "changed"
        with self.assertRaises(ValueError):
            deviation.save()
        with self.assertRaises(ValueError):
            PlanningDeviation.objects.filter(pk=deviation.pk).update(note="changed")
        with self.assertRaises(ValueError):
            PlanningDeviation.objects.bulk_update([deviation], ["note"])
        with self.assertRaises(ValueError):
            PlanningDeviation.objects.filter(pk=deviation.pk).delete()
        with self.assertRaises(ValueError):
            PlanningDeviation.objects.bulk_create([PlanningDeviation(cycle=cycle, final_revision=final, reason=self.reason, actor=self.user)])
        with self.assertRaises(ValueError):
            PlanningDeviation(cycle=cycle, final_revision=final, reason=self.reason, actor=self.user).save_base(raw=True)
        with self.assertRaises(ValueError):
            deviation.delete()
        with self.assertRaises(ValidationError):
            PlanningDeviation.objects.create(cycle=cycle, final_revision=final, reason=self.reason, explained_quantity=0, actor=self.user)
        with self.assertRaises(ValidationError):
            PlanningDeviation.objects.create(cycle=cycle, final_revision=final, reason=self.other, actor=self.user)
        PlanningDeviation.objects.create(cycle=cycle, final_revision=final, reason=self.other, note="specific explanation", actor=self.user)
        with self.assertRaises(ValidationError):
            PlanningDeviation.objects.create(cycle=cycle, final_revision=final, reason=self.other, note="   ", actor=self.user)
        with self.assertRaises(ValidationError):
            PlanningDeviation.objects.create(cycle=cycle, final_revision=final, reason=self.production_reason, actor=self.user)

    def test_linked_context_actor_bulk_guards_and_frozen_path(self):
        cycle, final, item, path = self.make_final(5)
        audit_actor = get_user_model().objects.create_user(username="b3a-planning-audit-actor")
        revision = PlanningRevision.objects.create(cycle=cycle, final_revision=final, final_item=item, planned_path=path, revision_type="path", before_snapshot={"quantity": 100}, after_snapshot={"quantity": 101}, actor=audit_actor)
        with self.assertRaises(ValidationError):
            PlanningDeviation.objects.create(cycle=cycle, final_revision=final, reason=self.reason, actor=self.user, revision=revision)
        with self.assertRaises(ValueError):
            PlanningRevision.objects.bulk_create([PlanningRevision(cycle=cycle, final_revision=final, revision_type="plan", actor=self.user)])
        with self.assertRaises(ProtectedError):
            audit_actor.delete()
        path.planned_share_quantity = 101
        with self.assertRaises(ValueError):
            path.save()
