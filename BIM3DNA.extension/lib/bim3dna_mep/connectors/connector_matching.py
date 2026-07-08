# -*- coding: utf-8 -*-

from bim3dna_mep.connectors.connector_access import (
    distance_between_connectors,
    get_connectors,
    is_connector_connected,
    is_physical_connector,
)
from bim3dna_mep.connectors.connector_validation import validate_connector_pair


def get_candidate_connectors(elem, open_only=False):
    candidates = []
    for connector in get_connectors(elem):
        if not is_physical_connector(connector):
            continue
        if open_only and is_connector_connected(connector):
            continue
        candidates.append(connector)
    return candidates


def find_best_connector_pair(elem_a, elem_b, open_only=False, require_direction=True):
    connectors_a = get_candidate_connectors(elem_a, open_only=open_only)
    connectors_b = get_candidate_connectors(elem_b, open_only=open_only)
    if not connectors_a:
        return (None, None, "First element has no usable physical connectors.")
    if not connectors_b:
        return (None, None, "Second element has no usable physical connectors.")

    best = None
    failures = []
    for connector_a in connectors_a:
        for connector_b in connectors_b:
            valid, reason = validate_connector_pair(
                connector_a, connector_b, require_direction=require_direction
            )
            distance = distance_between_connectors(connector_a, connector_b)
            if not valid:
                failures.append(reason)
                continue
            open_bonus = 0
            if not is_connector_connected(connector_a):
                open_bonus -= 1000
            if not is_connector_connected(connector_b):
                open_bonus -= 1000
            score = (distance or 0.0) + open_bonus
            if best is None or score < best[0]:
                best = (score, connector_a, connector_b, distance)
    if best is None:
        if failures:
            return (None, None, failures[0])
        return (None, None, "No compatible connector pair found.")
    return (best[1], best[2], "Matched connector pair at distance {} ft.".format(best[3]))

