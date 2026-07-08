# -*- coding: utf-8 -*-

import math

from bim3dna_mep.connectors.connector_access import (
    connector_diameter,
    connector_direction,
    connector_domain_label,
    ft_to_mm,
    is_physical_connector,
    mm_to_ft,
)

DISTANCE_TOLERANCE_FT = mm_to_ft(1.0)
SIZE_TOLERANCE_FT = mm_to_ft(1.0)
ANGLE_TOLERANCE_DEGREES = 2.0


def domains_match(connector_a, connector_b):
    return connector_domain_label(connector_a) == connector_domain_label(connector_b)


def size_compatible(connector_a, connector_b):
    dia_a = connector_diameter(connector_a)
    dia_b = connector_diameter(connector_b)
    if dia_a is None or dia_b is None:
        return (True, "Size unavailable on one connector; skipped strict size check.")
    delta = abs(dia_a - dia_b)
    if delta <= SIZE_TOLERANCE_FT:
        return (True, "Sizes compatible; delta {:.2f} mm.".format(ft_to_mm(delta)))
    return (
        False,
        "Connector sizes differ by {:.2f} mm.".format(ft_to_mm(delta)),
    )


def direction_compatible(connector_a, connector_b):
    dir_a = connector_direction(connector_a)
    dir_b = connector_direction(connector_b)
    if dir_a is None or dir_b is None:
        return (True, "Direction unavailable on one connector; skipped strict direction check.")
    try:
        dot = dir_a.Normalize().DotProduct(dir_b.Normalize())
    except Exception:
        return (True, "Could not normalize connector directions; skipped strict direction check.")
    limit = -math.cos(math.radians(ANGLE_TOLERANCE_DEGREES))
    if dot <= limit:
        return (True, "Directions compatible; dot {:.5f}.".format(dot))
    return (
        False,
        "Directions are not opposite within {:.1f} degrees; dot {:.5f}.".format(
            ANGLE_TOLERANCE_DEGREES, dot
        ),
    )


def validate_connector_pair(connector_a, connector_b, require_direction=True):
    if connector_a is None or connector_b is None:
        return (False, "Missing connector.")
    if not is_physical_connector(connector_a) or not is_physical_connector(connector_b):
        return (False, "One or both connectors are not physical connectors.")
    if not domains_match(connector_a, connector_b):
        return (
            False,
            "Connector domains differ: {} vs {}.".format(
                connector_domain_label(connector_a),
                connector_domain_label(connector_b),
            ),
        )
    ok_size, size_reason = size_compatible(connector_a, connector_b)
    if not ok_size:
        return (False, size_reason)
    if require_direction:
        ok_dir, dir_reason = direction_compatible(connector_a, connector_b)
        if not ok_dir:
            return (False, dir_reason)
    return (True, "Connector pair validated.")

