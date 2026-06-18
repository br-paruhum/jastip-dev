"""Custom en locale formats: force 24-hour time everywhere (admin, bare
template renders, localized output). Only the time-bearing formats are
overridden; date-only formats fall back to Django's en defaults.
"""
# 24-hour clock (Django default for en is "P" → "3:30 p.m.")
TIME_FORMAT = "H:i"
DATETIME_FORMAT = "N j, Y, H:i"          # en default: "N j, Y, P"
SHORT_DATETIME_FORMAT = "m/d/Y H:i"      # en default: "m/d/Y P"

# Accept 24-hour input when parsing time / datetime fields.
TIME_INPUT_FORMATS = ["%H:%M:%S", "%H:%M"]
DATETIME_INPUT_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
]
