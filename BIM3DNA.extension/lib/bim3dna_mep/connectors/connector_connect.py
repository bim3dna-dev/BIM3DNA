# -*- coding: utf-8 -*-

from Autodesk.Revit.DB import ElementTransformUtils, Transaction

from bim3dna_mep.connectors.connector_access import (
    connected_owner_ids,
    connector_origin,
    format_xyz_mm,
    get_element_id_value,
)
from bim3dna_mep.connectors.connector_matching import find_best_connector_pair
from bim3dna_mep.connectors.connector_validation import validate_connector_pair


def validate_connected(connector_a, connector_b):
    owner_b = get_element_id_value(connector_b.Owner)
    return owner_b in connected_owner_ids(connector_a)


def connect_to_elements(doc, elem_a, elem_b):
    result = {
        "ok": False,
        "reason": "",
        "first_element_id": get_element_id_value(elem_a),
        "second_element_id": get_element_id_value(elem_b),
        "moved_second_element": False,
    }
    connector_a, connector_b, match_reason = find_best_connector_pair(
        elem_a, elem_b, open_only=False, require_direction=True
    )
    result["match_reason"] = match_reason
    if connector_a is None or connector_b is None:
        result["reason"] = match_reason
        return result

    valid, reason = validate_connector_pair(connector_a, connector_b, require_direction=True)
    if not valid:
        result["reason"] = reason
        return result

    origin_a = connector_origin(connector_a)
    origin_b = connector_origin(connector_b)
    if origin_a is None or origin_b is None:
        result["reason"] = "Could not resolve connector origins."
        return result

    result["first_connector_origin"] = format_xyz_mm(origin_a)
    result["second_connector_origin_before"] = format_xyz_mm(origin_b)

    transaction = Transaction(doc, "BIM3DNA - Connect To")
    try:
        transaction.Start()
        move_vector = origin_a - origin_b
        if move_vector.GetLength() > 0.0000001:
            ElementTransformUtils.MoveElement(doc, elem_b.Id, move_vector)
            result["moved_second_element"] = True
            doc.Regenerate()
        connector_b.ConnectTo(connector_a)
        doc.Regenerate()
        transaction.Commit()
    except Exception as ex:
        try:
            transaction.RollBack()
        except Exception:
            pass
        result["reason"] = "ConnectTo failed: {}".format(ex)
        return result

    if validate_connected(connector_a, connector_b):
        result["ok"] = True
        result["reason"] = "Connected successfully."
    else:
        result["reason"] = "ConnectTo completed but connection was not validated through AllRefs."
    result["second_connector_origin_after"] = format_xyz_mm(connector_origin(connector_b))
    return result

