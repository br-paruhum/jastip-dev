"""Money display filters."""

from decimal import Decimal, InvalidOperation

from django import template
from django.contrib.humanize.templatetags.humanize import intcomma

register = template.Library()


@register.filter
def idr_num(value):
    """Indonesian locale format: point=thousands, comma=decimal. e.g. 17.953,00"""
    try:
        v = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return value
    formatted = f"{v:,.2f}"
    return formatted.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


@register.filter
def accounting(value):
    """Thousand-separated amount; negatives shown in parentheses (overpaid).

    e.g. 1234.5 -> "1,234.50"  ·  -1234.5 -> "(1,234.50)"
    """
    try:
        v = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return value
    if v < 0:
        return f"({intcomma(-v)})"
    return intcomma(v)
