import jdatetime

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.test import TestCase


if "c2_alias" in settings.DATABASES:
    from accounts.models import Role, UserProfile
    from accounts.signals import ensure_profile
    from catalog.models import (
        DeviationReason,
        Machine,
        MoldOption,
        PhysicalMold,
        Product,
        ProductGroup,
        ProductSubGroup,
        ProductionUnit,
    )
    from planning.models import PlannedProductionPath, WeeklyPlan, WeeklyPlanItem, WeeklyPlanLine

    from .models import ProductionDeviation, ProductionExecutionPath, ProductionTargetDecision
    from .path_execution import (
        activate_execution_path,
        get_or_create_unstarted_execution_path,
        register_path_production,
    )
    from .target_revision_service import decide_target_revision, request_target_revision


    class TargetRevisionAliasIsolationTests(TestCase):
        databases = {"default", "c2_alias"}

        def test_request_and_decision_stay_on_explicit_alias(self):
            alias = "c2_alias"
            User = get_user_model()
            # The legacy profile signal is not alias-aware. Isolate this C2 test
            # from that unrelated behavior and create the profiles explicitly.
            post_save.disconnect(ensure_profile, sender=User)
            try:
                manager = User.objects.db_manager(alias).create_user(username="alias-manager")
                operator = User.objects.db_manager(alias).create_user(username="alias-operator")
            finally:
                post_save.connect(ensure_profile, sender=User)
            for user, role in (
                (manager, Role.PLANNING_MANAGER),
                (operator, Role.PLANNING_EXPERT),
            ):
                profile, _ = UserProfile.objects.using(alias).get_or_create(user_id=user.pk)
                profile.role = role
                profile.save(using=alias, update_fields=["role"])
            group = ProductGroup.objects.using(alias).create(name="Alias group")
            subgroup = ProductSubGroup.objects.using(alias).create(name="Alias subgroup", group=group)
            unit = ProductionUnit.objects.using(alias).create(number=979, name="Alias unit")
            machine = Machine.objects.using(alias).create(number="ALIAS-M", unit=unit)
            product = Product.objects.using(alias).create(code="ALIAS-P", name="Alias product", subgroup=subgroup)
            mold_type = MoldOption.objects.using(alias).create(code="ALIAS-T", label="Alias mold")
            mold = PhysicalMold.objects.using(alias).create(code="ALIAS-MOLD", mold_type=mold_type)
            reason = DeviationReason.objects.using(alias).create(
                label="Alias reason",
                code=DeviationReason.Code.MACHINE_BREAKDOWN,
                applicability=DeviationReason.Applicability.PRODUCTION,
            )
            plan = WeeklyPlan.objects.using(alias).create(
                program_number="ALIAS-PLAN",
                date=jdatetime.date(1406, 6, 1),
                created_by=manager,
            )
            item = WeeklyPlanItem.objects.using(alias).create(
                plan=plan,
                subgroup=subgroup,
                unit=unit,
                machine=machine,
                product=product,
                mold=mold_type,
                mold_change_weekday=0,
                mold_change_date=plan.date,
            )
            line = WeeklyPlanLine.objects.using(alias).create(
                item=item, mold=mold_type, quantity=100, cycle=30
            )
            planned = PlannedProductionPath.objects.using(alias).create(
                requirement=line,
                machine=machine,
                physical_mold=mold,
                planned_share_quantity=100,
                planned_start_date=plan.date,
                planned_end_date=plan.date + jdatetime.timedelta(days=2),
                sequence=1,
            )
            path, _ = get_or_create_unstarted_execution_path(
                planned, actor=manager, using=alias
            )
            activate_execution_path(path, actor=manager, using=alias)
            register_path_production(
                path,
                date=plan.date,
                shots=40,
                active_cavities=1,
                scrap_pieces=0,
                actor=operator,
                using=alias,
            )
            revision = request_target_revision(
                path,
                requested_target=120,
                reason=reason,
                actor=operator,
                using=alias,
            )
            result = decide_target_revision(
                revision,
                outcome=ProductionTargetDecision.Outcome.APPROVED,
                actor=manager,
                using=alias,
            )

            path = ProductionExecutionPath.objects.using(alias).get(pk=path.pk)
            self.assertEqual(path.current_good_target, 120)
            self.assertTrue(
                ProductionTargetDecision.objects.using(alias).filter(pk=result.decision.pk).exists()
            )
            self.assertTrue(
                ProductionDeviation.objects.using(alias).filter(pk=result.deviation.pk).exists()
            )
            self.assertFalse(ProductionTargetDecision.objects.using("default").exists())
            self.assertFalse(ProductionDeviation.objects.using("default").exists())
