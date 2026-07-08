# -*- coding: utf-8 -*-
__title__ = "Connect\nTo"
__doc__ = "Move second selected element to the first matched connector and call ConnectTo."

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

from bim3dna_mep.connectors.connector_connect import connect_to_elements  # noqa: E402
from bim3dna_mep.connectors.connector_report import report_result  # noqa: E402


uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document


def pick_element(prompt):
    reference = uidoc.Selection.PickObject(ObjectType.Element, prompt)
    return doc.GetElement(reference.ElementId)


def main():
    try:
        elem_a = pick_element("Select source MEP element connector owner")
        elem_b = pick_element("Select target MEP element to move/connect")
    except Exception:
        TaskDialog.Show("Connect To", "Selection cancelled.")
        return
    result = connect_to_elements(doc, elem_a, elem_b)
    report_result("Connect To", result)


main()

