"""Accordion registry for «مدیریت داده‌ها» — groups stay in lockstep with the sidebar."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from catalog.nav import MENU_CONFIG_ITEMS


@dataclass
class SystemItem:
    key: str
    title: str
    admin_changelist: str = ""
    admin_add: str | None = None
    description: str = ""
    count_fn: Callable[[], int] | None = None
    can_add: bool = True
    url_name: str | None = None
    add_url_name: str | None = None
    url_query: str = ""


@dataclass
class SystemGroup:
    key: str
    title: str
    items: list[SystemItem]


def _count(model) -> Callable[[], int]:
    return lambda: model.objects.count()


def build_system_groups() -> list[SystemGroup]:
    from django.contrib.auth.models import Group, User

    from planning.models import PlanningProcessStep, WeeklyPlan, WeeklyPlanItem
    from production.models import (
        FittingProduction,
        ProductionDayEntry,
        ProductionHistoryRecord,
        ProductionProgram,
    )
    from reports.models import PrintForm, ReportParameterDef, SavedReport

    from .models import (
        DeviationReason,
        ExcelTable,
        ExcelUpload,
        FlexibleDataset,
        FlexibleRow,
        Machine,
        MoldOption,
        PipeCalcRule,
        PipeProductLine,
        PlanningDisplaySettings,
        PlanningInsightField,
        Product,
        ProductBomLine,
        ProductConsumable,
        ProductGroup,
        ProductSubGroup,
        ProductionTypeOption,
        ProductionUnit,
        ProgramChangeReason,
        ProgramUidScheme,
        StoppageReason,
        SystemAlarm,
        SystemNamingKey,
        TableLayoutSettings,
    )

    menu_items = [
        SystemItem(
            key=f"menu_{key}",
            title=title,
            url_name="system_menu_config",
            url_query=f"menu={key}",
            can_add=False,
            description="تنظیمات اختصاصی این منو به‌زودی اعلام می‌شود.",
        )
        for key, title in MENU_CONFIG_ITEMS
    ]

    return [
        SystemGroup(
            key="display",
            title="عناوین و ساختار نمایش",
            items=[
                SystemItem(
                    key="naming_keys",
                    title="نام‌گذاری عناوین سیستم",
                    url_name="system_naming_keys",
                    count_fn=_count(SystemNamingKey),
                    can_add=False,
                ),
                SystemItem(
                    key="table_layout",
                    title="تنظیمات جداول",
                    url_name="system_table_layout",
                    can_add=False,
                    count_fn=_count(TableLayoutSettings),
                ),
            ],
        ),
        SystemGroup(
            key="menu_config",
            title="پیکربندی منوها",
            items=menu_items,
        ),
        SystemGroup(
            key="base",
            title="داده‌های پایه",
            items=[
                SystemItem(
                    key="product_groups",
                    title="گروه‌های محصولات",
                    admin_changelist="admin:catalog_productgroup_changelist",
                    admin_add="admin:catalog_productgroup_add",
                    count_fn=_count(ProductGroup),
                ),
                SystemItem(
                    key="product_subgroups",
                    title="زیرگروه محصولات",
                    admin_changelist="admin:catalog_productsubgroup_changelist",
                    admin_add="admin:catalog_productsubgroup_add",
                    count_fn=_count(ProductSubGroup),
                ),
                SystemItem(
                    key="products",
                    title="محصولات و قطعات",
                    admin_changelist="admin:catalog_product_changelist",
                    admin_add="admin:catalog_product_add",
                    count_fn=_count(Product),
                ),
                SystemItem(
                    key="machines",
                    title="مشخصات دستگاه و خطوط",
                    admin_changelist="admin:catalog_machine_changelist",
                    admin_add="admin:catalog_machine_add",
                    count_fn=_count(Machine),
                ),
                SystemItem(
                    key="molds",
                    title="مشخصات قالب",
                    admin_changelist="admin:catalog_moldoption_changelist",
                    admin_add="admin:catalog_moldoption_add",
                    count_fn=_count(MoldOption),
                ),
                SystemItem(
                    key="consumables",
                    title="مشخصات مواد مصرفی",
                    admin_changelist="admin:catalog_productconsumable_changelist",
                    admin_add="admin:catalog_productconsumable_add",
                    count_fn=_count(ProductConsumable),
                ),
                SystemItem(
                    key="units",
                    title="واحد‌های تولید",
                    admin_changelist="admin:catalog_productionunit_changelist",
                    admin_add="admin:catalog_productionunit_add",
                    count_fn=_count(ProductionUnit),
                ),
                SystemItem(
                    key="production_types",
                    title="نوع تولید",
                    admin_changelist="admin:catalog_productiontypeoption_changelist",
                    admin_add="admin:catalog_productiontypeoption_add",
                    count_fn=_count(ProductionTypeOption),
                ),
                SystemItem(
                    key="deviation_reasons",
                    title="دلایل انحراف",
                    admin_changelist="admin:catalog_deviationreason_changelist",
                    admin_add="admin:catalog_deviationreason_add",
                    count_fn=_count(DeviationReason),
                ),
                SystemItem(
                    key="change_reasons",
                    title="دلایل تغییر برنامه",
                    admin_changelist="admin:catalog_programchangereason_changelist",
                    admin_add="admin:catalog_programchangereason_add",
                    count_fn=_count(ProgramChangeReason),
                ),
                SystemItem(
                    key="stop_reasons",
                    title="دلایل توقف",
                    admin_changelist="admin:catalog_stoppagereason_changelist",
                    admin_add="admin:catalog_stoppagereason_add",
                    count_fn=_count(StoppageReason),
                ),
                SystemItem(
                    key="alarms",
                    title="آلارم‌های سیستم",
                    admin_changelist="admin:catalog_systemalarm_changelist",
                    admin_add="admin:catalog_systemalarm_add",
                    count_fn=lambda: SystemAlarm.objects.filter(
                        status=SystemAlarm.Status.OPEN
                    ).count(),
                ),
                SystemItem(
                    key="planning_chrome",
                    title="تنظیمات کادرآبی و شیشه‌ای (برنامه‌ریزی هفتگی)",
                    url_name="system_planning_chrome",
                    can_add=False,
                    count_fn=lambda: (
                        PlanningDisplaySettings.objects.count()
                        + PlanningInsightField.objects.count()
                    ),
                ),
                SystemItem(
                    key="excel_tables",
                    title="جداول اکسل",
                    admin_changelist="admin:catalog_exceltable_changelist",
                    admin_add="admin:catalog_exceltable_add",
                    count_fn=_count(ExcelTable),
                ),
                SystemItem(
                    key="pipe_calc_rules",
                    title="قوانین زمان تولید",
                    admin_changelist="admin:catalog_pipecalcrule_changelist",
                    admin_add="admin:catalog_pipecalcrule_add",
                    count_fn=_count(PipeCalcRule),
                ),
                SystemItem(
                    key="uid_scheme",
                    title="قانون شناسه برنامه‌ریزی",
                    admin_changelist="admin:catalog_programuidscheme_changelist",
                    admin_add=None,
                    can_add=False,
                    count_fn=_count(ProgramUidScheme),
                ),
                SystemItem(
                    key="excel_uploads",
                    title="فایل‌های اکسل / CSV",
                    admin_changelist="admin:catalog_excelupload_changelist",
                    admin_add="admin:catalog_excelupload_add",
                    count_fn=_count(ExcelUpload),
                ),
                SystemItem(
                    key="pipe_calc",
                    title="خطوط و پروفایل محاسبه لوله",
                    admin_changelist="admin:catalog_pipeproductline_changelist",
                    admin_add="admin:catalog_pipeproductline_add",
                    count_fn=_count(PipeProductLine),
                ),
                SystemItem(
                    key="bom",
                    title="ساختار BOM (قطعات — مدل کلاسیک)",
                    admin_changelist="admin:catalog_productbomline_changelist",
                    admin_add="admin:catalog_productbomline_add",
                    count_fn=_count(ProductBomLine),
                ),
                SystemItem(
                    key="planning_display",
                    title="کادر آبی برنامه‌ریزی هفتگی",
                    admin_changelist="admin:catalog_planningdisplaysettings_changelist",
                    admin_add=None,
                    can_add=False,
                    count_fn=_count(PlanningDisplaySettings),
                ),
                SystemItem(
                    key="insight_fields",
                    title="فیلد اطلاعات نوار شیشه‌ای",
                    admin_changelist="admin:catalog_planninginsightfield_changelist",
                    admin_add="admin:catalog_planninginsightfield_add",
                    count_fn=_count(PlanningInsightField),
                ),
                SystemItem(
                    key="product_data_hub",
                    title="جداول پویا مقصد (دیتای محصولات)",
                    admin_changelist="admin:catalog_flexibledataset_changelist",
                    admin_add="admin:catalog_flexibledataset_add",
                    url_query="destination_id__exact=product_data",
                    count_fn=lambda: FlexibleDataset.objects.filter(
                        destination_id="product_data"
                    ).count(),
                ),
                SystemItem(
                    key="vouchers_hub",
                    title="ردیف‌های جداول پویا (دیتای محصولات)",
                    admin_changelist="admin:catalog_flexiblerow_changelist",
                    admin_add="admin:catalog_flexiblerow_add",
                    count_fn=lambda: FlexibleRow.objects.filter(
                        dataset__destination_id="product_data",
                    ).count(),
                ),
                SystemItem(
                    key="transfer_dialog_labels",
                    title="عناوین دیالوگ و مقاصد انتقال داده",
                    url_name="system_naming_keys",
                    url_query="kind=key&page=excel",
                    can_add=False,
                    count_fn=lambda: SystemNamingKey.objects.filter(
                        key__startswith="transfer.", is_active=True
                    ).count(),
                ),
                SystemItem(
                    key="print_forms",
                    title="فرم‌های چاپی",
                    admin_changelist="admin:reports_printform_changelist",
                    admin_add="admin:reports_printform_add",
                    count_fn=_count(PrintForm),
                ),
                SystemItem(
                    key="saved_reports",
                    title="گزارش‌های ذخیره شده",
                    admin_changelist="admin:reports_savedreport_changelist",
                    admin_add="admin:reports_savedreport_add",
                    count_fn=_count(SavedReport),
                ),
                SystemItem(
                    key="report_parameter_defs",
                    title="تعریف پارامترهای گزارش",
                    admin_changelist="admin:reports_reportparameterdef_changelist",
                    admin_add="admin:reports_reportparameterdef_add",
                    count_fn=_count(ReportParameterDef),
                ),
                SystemItem(
                    key="planning_process",
                    title="منطق فرآیند برنامه‌ریزی (مراحل)",
                    url_name="planning_process_list",
                    can_add=False,
                    count_fn=_count(PlanningProcessStep),
                ),
                SystemItem(
                    key="weekly_plans",
                    title="رکوردهای برنامه‌ریزی هفتگی",
                    admin_changelist="admin:planning_weeklyplan_changelist",
                    admin_add="admin:planning_weeklyplan_add",
                    count_fn=_count(WeeklyPlan),
                ),
                SystemItem(
                    key="plan_items",
                    title="کالاهای برنامه",
                    admin_changelist="admin:planning_weeklyplanitem_changelist",
                    admin_add="admin:planning_weeklyplanitem_add",
                    count_fn=_count(WeeklyPlanItem),
                ),
                SystemItem(
                    key="day_entries",
                    title="آمار تولید روزانه",
                    admin_changelist="admin:production_productiondayentry_changelist",
                    admin_add="admin:production_productiondayentry_add",
                    count_fn=_count(ProductionDayEntry),
                ),
                SystemItem(
                    key="programs",
                    title="برنامه‌های تولید",
                    admin_changelist="admin:production_productionprogram_changelist",
                    admin_add="admin:production_productionprogram_add",
                    count_fn=_count(ProductionProgram),
                ),
                SystemItem(
                    key="fittings",
                    title="تولید روزانه اتصالات",
                    admin_changelist="admin:production_fittingproduction_changelist",
                    admin_add="admin:production_fittingproduction_add",
                    count_fn=_count(FittingProduction),
                ),
                SystemItem(
                    key="history",
                    title="سوابق تولید (رکوردها)",
                    admin_changelist="admin:production_productionhistoryrecord_changelist",
                    admin_add="admin:production_productionhistoryrecord_add",
                    count_fn=_count(ProductionHistoryRecord),
                ),
                SystemItem(
                    key="users",
                    title="کاربرها",
                    admin_changelist="admin:auth_user_changelist",
                    admin_add="admin:auth_user_add",
                    count_fn=_count(User),
                ),
                SystemItem(
                    key="groups",
                    title="گروه‌ها",
                    admin_changelist="admin:auth_group_changelist",
                    admin_add="admin:auth_group_add",
                    count_fn=_count(Group),
                ),
            ],
        ),
        SystemGroup(
            key="backup",
            title="پشتیبان‌گیری و بازیابی",
            items=[
                SystemItem(
                    key="backup_standard",
                    title="پشتیبان‌گیری استاندارد",
                    url_name="backup_center",
                    url_query="mode=standard",
                    can_add=False,
                ),
                SystemItem(
                    key="backup_selective",
                    title="پشتیبان‌گیری انتخابی",
                    url_name="backup_center",
                    url_query="mode=selective",
                    can_add=False,
                ),
                SystemItem(
                    key="backup_restore",
                    title="بازیابی",
                    url_name="backup_center",
                    url_query="mode=restore",
                    can_add=False,
                ),
            ],
        ),
    ]
