"""Shared unit conversions and small formatting helpers used across services."""

ACRES_PER_HA = 2.47105

DEV_DIGITS = str.maketrans("0123456789.", "०१२३४५६७८९.")


def to_marathi_number(value):
    """Render a number (or numeric string) with Devanagari digits."""
    return str(value).translate(DEV_DIGITS)
