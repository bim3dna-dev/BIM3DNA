# -*- coding: utf-8 -*-
__title__ = "Join/Unjoin \n Geometry"
__doc__ = """Enable join for selected structural framing elements."""

# ╦╔╦╗╔═╗╔═╗╦═╗╔╦╗╔═╗
# ║║║║╠═╝║ ║╠╦╝ ║ ╚═╗
# ╩╩ ╩╩  ╚═╝╩╚═ ╩ ╚═╝
# ==================================================
from Autodesk.Revit.DB import *
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    BuiltInCategory,
    BuiltInParameter,
    Element,
    ElementId,
    Transaction,
    WorksetKind,
    JoinGeometryUtils,
    ElementCategoryFilter,
)
from Autodesk.Revit.DB import Transaction
from Autodesk.Revit.UI import *
from Autodesk.Revit.UI import TaskDialog
from pyrevit import forms
from pyrevit import revit
from pyrevit import script as pyscript
import System.Windows.Forms as WinForms
from System.Windows.Forms import MessageBox, MessageBoxButtons, MessageBoxIcon


# .NET Imports
import sys
import time
import clr

clr.AddReference("System")
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System.Windows.Forms")

# ╦  ╦╔═╗╦═╗╦╔═╗╔╗ ╦  ╔═╗╔═╗
# ╚╗╔╝╠═╣╠╦╝║╠═╣╠╩╗║  ║╣ ╚═╗
#  ╚╝ ╩ ╩╩╚═╩╩ ╩╚═╝╩═╝╚═╝╚═╝
# ==================================================

app = __revit__.Application
# uidoc = __revit__.ActiveUIDocument
uidoc = revit.uidoc
doc = revit.doc

# ╔╦╗╔═╗╦╔╗╔
# ║║║╠═╣║║║║
# ╩ ╩╩ ╩╩╝╚╝
# ==================================================
def _silent_alert(msg, title="pyRevit", exitscript=False, **kwargs):
    # No icon => no Windows system sound
    WinForms.MessageBox.Show(
        str(msg), str(title),
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.None # no beep
    )
    if exitscript:
        pyscript.exit()

# Replace pyRevit's alert with our silent version
forms.alert = _silent_alert

# Silence WinForms MessageBox.Show used here
def _silent_winforms_show(text, caption="pyRevit", buttons=WinForms.MessageBoxButtons.OK, icon=None, *args, **kwargs):
    return WinForms.MessageBox.Show(str(text), str(caption), buttons, WinForms.MessageBoxIcon.None)

class _SilentMessageBox(object):
    def Show(self, *args, **kwargs):
        return _silent_winforms_show(*args, **kwargs)

# Shadow the imported MessageBox class name with out silent proxy
MessageBox = _SilentMessageBox()
# --- end silent override---


# Debug toggle (set to False to suppress console logs)
DEBUG = False


def debug_log(message):
    """Helper function to log debug messages."""
    if DEBUG:
        print(message)


# Supported categories for geometry join
SUPPORTED_CATEGORIES = [
    BuiltInCategory.OST_Walls,
    BuiltInCategory.OST_StructuralFraming,  # Beams
    BuiltInCategory.OST_StructuralColumns,  # Columns
    BuiltInCategory.OST_StructuralFoundation,  # Foundations
    BuiltInCategory.OST_Floors,  # Floors
    BuiltInCategory.OST_Roofs,  # Roofs
    BuiltInCategory.OST_Ceilings,  # Ceilings
    BuiltInCategory.OST_CurtainWallPanels,  # Curtain Panels
    BuiltInCategory.OST_GenericModel,  # Generic Models
]


def select_elements():
    """Prompt user to select valid elements."""
    selection = uidoc.Selection.GetElementIds()

    if len(selection) < 2:
        MessageBox.Show(
            "Please select at least two elements to join/unjoin.",
            "Selection Error",
            MessageBoxButtons.OK,
            MessageBoxIcon.Warning
        )
        return None

    selected_elements = [doc.GetElement(el_id) for el_id in selection]
    valid_elements = [
        el
        for el in selected_elements
        if el.Category
        and el.Category.Id.IntegerValue in [int(cat) for cat in SUPPORTED_CATEGORIES]
    ]

    if not valid_elements:
        MessageBox.Show(
            "No valid elements selected.\nPlease select elements from supported categories.",
            "Selection Error",
            MessageBoxButtons.OK,
            MessageBoxIcon.Warning,
        )
        return None

    return valid_elements


def get_category_priority(category):
    """Define a priority for cutting based on category."""
    priority_map = {
        BuiltInCategory.OST_StructuralColumns: 1,  # Columns have the highest priority
        BuiltInCategory.OST_Walls: 2,
        BuiltInCategory.OST_StructuralFraming: 3,  # Beams
        BuiltInCategory.OST_StructuralFoundation: 4,
        BuiltInCategory.OST_Floors: 5,  # Floors have lower priority
        BuiltInCategory.OST_Roofs: 6,
        BuiltInCategory.OST_GenericModel: 7,
    }
    return priority_map.get(category, 100)


def enforce_switch_order(elem1, elem2):
    """Switch join order to enforce cutting priority if needed."""
    priority1 = get_category_priority(elem1.Category.Id.IntegerValue)
    priority2 = get_category_priority(elem2.Category.Id.IntegerValue)

    if priority1 < priority2:
        debug_log("Ensuring column cuts floor (switching join order if necessary)")
        if not JoinGeometryUtils.IsCuttingElementInJoin(doc, elem1, elem2):
            JoinGeometryUtils.SwitchJoinOrder(doc, elem1, elem2)
            doc.Regenerate()  # Ensure geometry update
            debug_log("Switched join order: {} cuts {}".format(elem1.Id, elem2.Id))


def process_elements(elements):
    """Determine if elements should be joined or unjoined."""
    join_mode = None

    # Check if elements are currently joined
    for i in range(len(elements)):
        for j in range(i + 1, len(elements)):
            elem1 = elements[i]
            elem2 = elements[j]
            if JoinGeometryUtils.AreElementsJoined(doc, elem1, elem2):
                join_mode = "unjoin"
                break
        if join_mode:
            break

    # If no elements were found to be joined, switch to join mode
    if not join_mode:
        join_mode = "join"

    # Process elements based on the determined mode
    if join_mode == "unjoin":
        unjoin_elements(elements)
    else:
        join_elements(elements)


def join_elements(elements):
    """Join selected elements and switch join order if needed."""
    joined = 0
    failed = 0

    for i in range(len(elements)):
        for j in range(i + 1, len(elements)):
            elem1 = elements[i]
            elem2 = elements[j]

            try:
                if not JoinGeometryUtils.AreElementsJoined(doc, elem1, elem2):
                    JoinGeometryUtils.JoinGeometry(doc, elem1, elem2)
                    enforce_switch_order(
                        elem1, elem2
                    )  # Enforce the proper cut priority
                    doc.Regenerate()  # Force geometry update
                    debug_log("Joined elements: {} and {}".format(elem1.Id, elem2.Id))
                    joined += 1
            except Exception as e:
                debug_log(
                    "Error joining elements: {} and {}, Error: {}".format(
                        elem1.Id, elem2.Id, str(e)
                    )
                )
                failed += 1

    return joined, failed


def unjoin_elements(elements):
    """Unjoin selected elements."""
    unjoined = 0
    failed = 0

    for i in range(len(elements)):
        for j in range(i + 1, len(elements)):
            elem1 = elements[i]
            elem2 = elements[j]

            try:
                if JoinGeometryUtils.AreElementsJoined(doc, elem1, elem2):
                    JoinGeometryUtils.UnjoinGeometry(doc, elem1, elem2)
                    doc.Regenerate()  # Force geometry update
                    debug_log("Unjoined elements: {} and {}".format(elem1.Id, elem2.Id))
                    unjoined += 1
            except Exception as e:
                debug_log(
                    "Error unjoining elements: {} and {}, Error: {}".format(
                        elem1.Id, elem2.Id, str(e)
                    )
                )
                failed += 1

    return unjoined, failed


# Main Execution
selection = select_elements()

if selection:
    t = Transaction(doc, "Join/Unjoin Geometry Elements")
    t.Start()

    process_elements(selection)

    t.Commit()

    # Display summary message
    MessageBox.Show(
        "Operation completed successfully.",
        "Join/Unjoin Summary",
        MessageBoxButtons.OK,
        MessageBoxIcon.Information
    )
else:
    debug_log("No valid elements selected.")
