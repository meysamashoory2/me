"""Display-only numeric localization for the table settings controls."""

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def table_number(value):
    """Localize a number and omit only insignificant fractional zeroes."""
    raw = str(value).strip()
    try:
        formatted = format(Decimal(raw), "f")
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
    except (InvalidOperation, ValueError):
        formatted = raw
    return formatted.translate(str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹٫"))
