# -*- coding: utf-8 -*-

from Autodesk.Revit.DB import Transaction

from bim3dna_mep.connectors.connector_access import (
    connector_owner_id,
    external_connected_refs,
    get_connectors,
    get_element_id_value,
    is_physical_connector,
)


def collect_external_connections(elem):
    pairs = []
    for connector in get_connectors(elem):
        if not is_physical_connector(connector):
            continue
        for ref in external_connected_refs(connector):
            pairs.append((connector, ref))
    return pairs


def disconnect_element(doc, elem, disconnect_all=False):
    result = {
        "ok": False,
        "reason": "",
        "element_id": get_element_id_value(elem),
        "disconnected_owner_ids": [],
        "disconnect_all": disconnect_all,
    }
    pairs = collect_external_connections(elem)
    if not pairs:
        result["reason"] = "No physical external connector references found."
        return result
    if not disconnect_all:
        pairs = pairs[:1]

    transaction = Transaction(doc, "BIM3DNA - Disconnect From")
    try:
        transaction.Start()
        for connector, ref in pairs:
            owner_id = connector_owner_id(ref)
            connector.DisconnectFrom(ref)
            if owner_id is not None and owner_id not in result["disconnected_owner_ids"]:
                result["disconnected_owner_ids"].append(owner_id)
        doc.Regenerate()
        transaction.Commit()
    except Exception as ex:
        try:
            transaction.RollBack()
        except Exception:
            pass
        result["reason"] = "DisconnectFrom failed: {}".format(ex)
        return result

    result["ok"] = True
    result["reason"] = "Disconnected {} connector reference(s).".format(len(pairs))
    return result

