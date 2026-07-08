# -*- coding: utf-8 -*-
__title__ = "Disconnect\nFrom"
__doc__ = "Disconnect first or all physical external connector references from one element."

import os
import sys

from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogResult


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

from bim3dna_mep.connectors.connector_disconnect import disconnect_element  # noqa: E402
from bim3dna_mep.connectors.connector_report import report_result  # noqa: E402


uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document


def ask_disconnect_all():
    dialog = TaskDialog("Disconnect From")
    dialog.MainInstruction = "Disconnect connector references?"
    dialog.MainContent = "Yes = disconnect all external physical connections.\nNo = disconnect first found connection.\nCancel = do nothing."
    dialog.CommonButtons = (
        TaskDialogCommonButtons.Yes
        | TaskDialogCommonButtons.No
        | TaskDialogCommonButtons.Cancel
    )
    result = dialog.Show()
    if result == TaskDialogResult.Cancel:
        return None
    return result == TaskDialogResult.Yes


def main():
    try:
        reference = uidoc.Selection.PickObject(
            ObjectType.Element, "Select MEP element to disconnect"
        )
    except Exception:
        TaskDialog.Show("Disconnect From", "Selection cancelled.")
        return
    disconnect_all = ask_disconnect_all()
    if disconnect_all is None:
        TaskDialog.Show("Disconnect From", "Cancelled; no model changes.")
        return
    elem = doc.GetElement(reference.ElementId)
    result = disconnect_element(doc, elem, disconnect_all=disconnect_all)
    report_result("Disconnect From", result)


main()

