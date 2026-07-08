# -*- coding: utf-8 -*-
__title__ = "Connector\nInspector"
__doc__ = "Report connector metadata for one Revit MEP element. No model changes."

import os
import sys

from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.UI import TaskDialog


def _add_extension_lib_to_path():
    path = os.path.dirname(__file__)
    while path and os.path.basename(path) != "BIM3DNA.extension":
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    lib_path = os.path.join(path, "lib")
    if os.path.isdir(lib_path) and lib_path not in sys.path:
        sys.path.insert(0, lib_path)


_add_extension_lib_to_path()

from bim3dna_mep.connectors.connector_access import (  # noqa: E402
    connector_summary,
    get_connectors,
    get_element_category_name,
    get_element_id_value,
)
from bim3dna_mep.connectors.connector_report import print_report, show_report  # noqa: E402


uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document


def main():
    try:
        reference = uidoc.Selection.PickObject(
            ObjectType.Element, "Select one MEP element to inspect connectors"
        )
    except Exception:
        TaskDialog.Show("Connector Inspector", "Selection cancelled.")
        return

    elem = doc.GetElement(reference.ElementId)
    connectors = get_connectors(elem)
    lines = []
    lines.append("Owner element id: {}".format(get_element_id_value(elem)))
    lines.append("Category: {}".format(get_element_category_name(elem)))
    lines.append("Connector count: {}".format(len(connectors)))
    lines.append("")
    for index, connector in enumerate(connectors):
        summary = connector_summary(connector, index=index)
        lines.append("Connector {}".format(index + 1))
        lines.append("  origin: {}".format(summary["origin_mm"]))
        lines.append("  domain: {}".format(summary["domain"]))
        lines.append("  shape: {}".format(summary["shape"]))
        lines.append("  type: {}".format(summary["type"]))
        lines.append("  direction basis Z: {}".format(summary["direction"]))
        lines.append(
            "  diameter mm: {}".format(
                "{:.2f}".format(summary["diameter_mm"])
                if summary["diameter_mm"] is not None
                else "-"
            )
        )
        lines.append("  is connected: {}".format(summary["is_connected"]))
        lines.append("  connected owner ids: {}".format(summary["connected_owner_ids"]))
        lines.append("  physical: {}".format(summary["physical"]))
    print_report("Connector Inspector", lines)
    show_report("Connector Inspector", lines)


main()

