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
def add_decimal(value, arg):
    """Add two Decimal values safely (avoids Django's built-in add which truncates to int)."""
    try:
        return Decimal(str(value)) + Decimal(str(arg))
    except (InvalidOperation, TypeError, ValueError):
        return value


@register.filter
def by_currency(value, currency):
    """Thousand-separated amount with decimals chosen by currency: IDR shows
    whole numbers (decimals are noise), every other currency shows 2 places so a
    buyer paying in their local currency transfers the exact amount."""
    try:
        v = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return value
    if (currency or "").upper() == "IDR":
        return intcomma(v.quantize(Decimal("1")))
    return intcomma(v.quantize(Decimal("0.01")))


@register.filter
def accounting(value, places=2):
    """Thousand-separated amount; negatives shown in parentheses (overpaid).

    e.g. 1234.5 -> "1,234.50"  ·  -1234.5 -> "(1,234.50)"
    Pass places=0 to round to whole numbers (e.g. 1234.5 -> "1,235").
    """
    try:
        v = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return value
    if int(places) == 0:
        v = v.quantize(Decimal("1"))
    if v < 0:
        return f"({intcomma(-v)})"
    return intcomma(v)
