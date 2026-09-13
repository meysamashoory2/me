"""Import the production master-data CSV: product → eligible molds (قالب ۱/۲/۳).

The file provides, per product row: product code + name and up to three molds
(code + name each). This command upserts the mold registry (MoldOption) by code
and links each product to its eligible molds (ProductMold). Machine, cavities
and cycle are NOT in this file — the planner learns those from production
history — so they are intentionally left untouched here.
"""

from __future__ import annotations

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.models import (
    MoldOption,
    Product,
    ProductGroup,
    ProductMold,
    ProductSubGroup,
)


def _norm(text: str) -> str:
    """Normalise Arabic/Persian kaf & yeh and trim, so headers match reliably."""
    return (text or "").replace("\ufeff", "").replace("ك", "ک").replace("ي", "ی").strip()


# Normalised header names as they appear in the master-data file.
COL_GROUP = "گروه اصلی"
COL_SUBGROUP = "زیر گروه"
COL_PRODUCT_CODE = "کد کالا مصرفی"
COL_PRODUCT_NAME = "شرح کالا مصرفی"
MOLD_SLOTS = [("کد قالب 1", "قالب 1"), ("کد قالب 2", "قالب 2"), ("کد قالب 3", "قالب 3")]


class Command(BaseCommand):
    help = "Import product↔mold master data CSV into MoldOption + ProductMold"

    def add_arguments(self, parser):
        parser.add_argument("path", type=str, help="CSV file path")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Parse and report coverage without writing to the database.",
        )
        parser.add_argument(
            "--link-only", action="store_true",
            help="Only link existing products; do not create missing products/subgroups.",
        )

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.is_file():
            raise CommandError(f"فایل یافت نشد: {path}")

        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            try:
                raw_header = next(reader)
            except StopIteration:
                raise CommandError("فایل خالی است.")
            header = [_norm(h) for h in raw_header]
            index = {name: i for i, name in enumerate(header)}
            if COL_PRODUCT_CODE not in index:
                raise CommandError(
                    f"ستون «{COL_PRODUCT_CODE}» یافت نشد. سرستون‌ها: {header}"
                )
            rows = [r for r in reader if any((c or "").strip() for c in r)]

        stats = {
            "rows": 0, "molds_created": 0, "links_created": 0,
            "products_without_mold": 0, "products_created": 0,
            "groups_created": 0, "subgroups_created": 0,
        }
        products_matched: set[str] = set()
        products_unmatched: set[str] = set()
        mold_codes_seen: set[str] = set()
        create_products = not options["link_only"]

        def cell(row, col):
            i = index.get(col)
            return _norm(row[i]) if i is not None and i < len(row) else ""

        @transaction.atomic
        def run():
            product_by_code = {
                (p.code or "").strip(): p for p in Product.objects.all().only("id", "code")
            }
            group_cache: dict[str, ProductGroup] = {}
            subgroup_cache: dict[tuple[str, str], ProductSubGroup] = {}

            def ensure_subgroup(group_name, subgroup_name):
                group_name = group_name or "بدون گروه"
                subgroup_name = subgroup_name or "بدون زیرگروه"
                grp = group_cache.get(group_name)
                if grp is None:
                    grp, gc = ProductGroup.objects.get_or_create(name=group_name)
                    if gc:
                        stats["groups_created"] += 1
                    group_cache[group_name] = grp
                key = (group_name, subgroup_name)
                sub = subgroup_cache.get(key)
                if sub is None:
                    sub, sc = ProductSubGroup.objects.get_or_create(
                        group=grp, name=subgroup_name
                    )
                    if sc:
                        stats["subgroups_created"] += 1
                    subgroup_cache[key] = sub
                return sub

            for row in rows:
                stats["rows"] += 1
                product_code = cell(row, COL_PRODUCT_CODE)
                if not product_code:
                    continue
                product = product_by_code.get(product_code)
                if product is None and create_products:
                    sub = ensure_subgroup(cell(row, COL_GROUP), cell(row, COL_SUBGROUP))
                    product = Product.objects.create(
                        code=product_code,
                        name=cell(row, COL_PRODUCT_NAME) or product_code,
                        subgroup=sub,
                    )
                    product_by_code[product_code] = product
                    stats["products_created"] += 1
                if product is None:
                    products_unmatched.add(product_code)
                else:
                    products_matched.add(product_code)

                any_mold = False
                for slot, (code_col, name_col) in enumerate(MOLD_SLOTS, start=1):
                    mold_code = cell(row, code_col)
                    mold_name = cell(row, name_col)
                    if not mold_code and not mold_name:
                        continue
                    any_mold = True
                    mold_codes_seen.add(mold_code or mold_name)
                    if mold_code:
                        mold, created = MoldOption.objects.get_or_create(
                            code=mold_code, defaults={"label": mold_name or mold_code}
                        )
                    else:
                        mold, created = MoldOption.objects.get_or_create(
                            label=mold_name, code=""
                        )
                    if created:
                        stats["molds_created"] += 1
                    elif mold_name and mold.label != mold_name:
                        mold.label = mold_name
                        mold.save(update_fields=["label"])
                    if product is not None:
                        _, link_created = ProductMold.objects.get_or_create(
                            product=product, mold=mold, defaults={"slot": slot}
                        )
                        if link_created:
                            stats["links_created"] += 1
                if not any_mold:
                    stats["products_without_mold"] += 1

            if options["dry_run"]:
                transaction.set_rollback(True)

        run()

        w = self.stdout.write
        s = self.style
        w(s.SUCCESS("=== ورود دادهٔ قالب‌ها ===" + (" [DRY-RUN]" if options["dry_run"] else "")))
        w(f"ردیف‌های پردازش‌شده: {stats['rows']}")
        w(f"قالب‌های یکتا در فایل: {len(mold_codes_seen)} | قالب‌های جدید ساخته‌شده: {stats['molds_created']}")
        w(f"گروه‌های جدید: {stats['groups_created']} | زیرگروه‌های جدید: {stats['subgroups_created']} | محصولات جدید: {stats['products_created']}")
        w(f"محصولات منطبق: {len(products_matched)} | نامنطبق: {len(products_unmatched)}")
        w(f"پیوند محصول↔قالب ساخته‌شده: {stats['links_created']}")
        w(f"ردیف‌های بدون قالب: {stats['products_without_mold']}")
        if products_unmatched:
            sample = ", ".join(sorted(products_unmatched)[:10])
            w(s.WARNING(f"نمونهٔ کدهای نامنطبق: {sample}"))
