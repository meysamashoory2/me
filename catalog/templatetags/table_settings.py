"""Display-only numeric localization for the table settings controls."""

from django import template

register = template.Library()


@register.filter
def table_number(value):
    """Keep the visible value Persian; canonical hidden fields remain ASCII."""
    return str(value).translate(str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹٫"))
