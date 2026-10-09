#!/usr/bin/env python3
# ==============================================================================
# unit_convert.py
#
# @describe Convert between units (length: m, km, mi, ft; weight: kg, lb).
# @option --value! <NUM> Value to convert.
# @option --from-unit! <TEXT> Source unit.
# @option --to-unit! <TEXT> Target unit.
# ==============================================================================


def run(value: float, from_unit: str, to_unit: str):
    """
    Convert between units (length: m, km, mi, ft; weight: kg, lb).
    """
    conversions = {
        "m_km": 0.001,
        "km_m": 1000,
        "m_ft": 3.28084,
        "ft_m": 0.3048,
        "km_mi": 0.621371,
        "mi_km": 1.60934,
        "kg_lb": 2.20462,
        "lb_kg": 0.453592,
    }
    key = f"{from_unit}_{to_unit}"
    if key in conversions:
        return f"{value} {from_unit} = {value * conversions[key]} {to_unit}"
    return "Unsupported conversion."
