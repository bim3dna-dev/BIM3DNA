# -*- coding: utf-8 -*-
__title__ = "Join/Unjoin \n Analytical"
__doc__ = (
    """Enable or disable join for Walls and Structural Framing (Beams) in Revit."""
)

# ╦╔╦╗╔═╗╔═╗╦═╗╔╦╗╔═╗
# ║║║║╠═╝║ ║╠╦╝ ║ ╚═╗
# ╩╩ ╩╩  ╚═╝╩╚═ ╩ ╚═╝
# ==================================================
# .NET Imports
import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("RevitServices")
clr.AddReference("RevitNodes")


from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Structure import StructuralFramingUtils  # Fixed import for beams
from RevitServices.Persistence import DocumentManager
from pyrevit import revit  # Correct way to access Revit
from pyrevit import script as pyscript
import System.Windows.Forms as WinForms
from pyrevit import forms


# ╦  ╦╔═╗╦═╗╦╔═╗╔╗ ╦  ╔═╗╔═╗
# ╚╗╔╝╠═╣╠╦╝║╠═╣╠╩╗║  ║╣ ╚═╗
#  ╚╝ ╩ ╩╩╚═╩╩ ╩╚═╝╩═╝╚═╝╚═╝
# ==================================================
# Ensure proper Revit document access
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
        WinForms.MessageBoxIcon.None
    )
    if exitscript:
        pyscript.exit()

# Replace pyRevit's alert with our silent version
forms.alert = _silent_alert
# --- end silent override---
# Toggle debugging output
DEBUG = False


def debug_log(message):
    """Helper function to log debug messages."""
    if DEBUG:
        print(message)


# ✅ Define Supported Structural Categories (Physical & Analytical)
SUPPORTED_CATEGORIES = [
    BuiltInCategory.OST_Walls,  # Walls (Physical)
    BuiltInCategory.OST_WallAnalytical,  # Walls (Analytical)
    BuiltInCategory.OST_StructuralFraming,  # Beams & Bracing (Physical)
    BuiltInCategory.OST_BeamAnalytical,  # Beams & Bracing (Analytical)
    BuiltInCategory.OST_StructuralColumns,  # Columns (Physical)
    BuiltInCategory.OST_ColumnAnalytical,  # Columns (Analytical)
    BuiltInCategory.OST_Floors,  # Floors (Physical)
    BuiltInCategory.OST_FloorAnalytical,  # Floors (Analytical)
    BuiltInCategory.OST_StructuralFoundation,  # Foundations (Physical)
    BuiltInCategory.OST_FoundationSlabAnalytical,  # Foundations (Analytical)
]


def get_elements():
    """Get valid selected elements."""
    selection = uidoc.Selection.GetElementIds()
    if not selection:
        debug_log("No elements selected.")
        return []

    selected_elements = [doc.GetElement(el_id) for el_id in selection]
    elements = [
        el
        for el in selected_elements
        if el.Category
        and el.Category.Id.IntegerValue in [int(cat) for cat in SUPPORTED_CATEGORIES]
    ]
    return elements


def is_wall_joined(wall):
    """Check if a wall is joined."""
    return WallUtils.IsWallJoinAllowedAtEnd(
        wall, 0
    ) or WallUtils.IsWallJoinAllowedAtEnd(wall, 1)


def is_beam_joined(beam):
    """Check if a beam is joined."""
    return StructuralFramingUtils.IsJoinAllowedAtEnd(
        beam, 0
    ) or StructuralFramingUtils.IsJoinAllowedAtEnd(beam, 1)


def are_columns_joined(doc, col1, col2):
    """Check if two columns are joined."""
    return JoinGeometryUtils.AreElementsJoined(doc, col1, col2)


def toggle_wall_join(walls, allow):
    """Toggle wall join."""
    count = 0
    for wall in walls:
        try:
            if allow:
                WallUtils.AllowWallJoinAtEnd(wall, 0)
                WallUtils.AllowWallJoinAtEnd(wall, 1)
            else:
                WallUtils.DisallowWallJoinAtEnd(wall, 0)
                WallUtils.DisallowWallJoinAtEnd(wall, 1)
            count += 1
        except Exception as ex:
            debug_log("Error processing wall join: " + str(ex))
    return count


def toggle_beam_join(beams, allow):
    """Toggle beam join."""
    count = 0
    for beam in beams:
        try:
            if allow:
                StructuralFramingUtils.AllowJoinAtEnd(beam, 0)
                StructuralFramingUtils.AllowJoinAtEnd(beam, 1)
            else:
                StructuralFramingUtils.DisallowJoinAtEnd(beam, 0)
                StructuralFramingUtils.DisallowJoinAtEnd(beam, 1)
            count += 1
        except Exception as ex:
            debug_log("Error processing beam join: " + str(ex))
    return count


def toggle_column_join(doc, columns, allow):
    """Toggle column join."""
    count = 0
    for i in range(len(columns)):
        for j in range(i + 1, len(columns)):
            col1 = columns[i]
            col2 = columns[j]
            try:
                if allow:
                    if not are_columns_joined(doc, col1, col2):
                        JoinGeometryUtils.JoinGeometry(doc, col1, col2)
                else:
                    if are_columns_joined(doc, col1, col2):
                        JoinGeometryUtils.UnjoinGeometry(doc, col1, col2)
                count += 1
            except Exception as ex:
                debug_log("Error processing column join: " + str(ex))
    return count


def toggle_floor_join(doc, floors, allow):
    """Toggle floor join."""
    count = 0
    for i in range(len(floors)):
        for j in range(i + 1, len(floors)):
            floor1 = floors[i]
            floor2 = floors[j]
            try:
                if allow:
                    if not JoinGeometryUtils.AreElementsJoined(doc, floor1, floor2):
                        JoinGeometryUtils.JoinGeometry(doc, floor1, floor2)
                else:
                    if JoinGeometryUtils.AreElementsJoined(doc, floor1, floor2):
                        JoinGeometryUtils.UnjoinGeometry(doc, floor1, floor2)
                count += 1
            except Exception as ex:
                debug_log("Error processing floor join: " + str(ex))
    return count


# Main execution
elements = get_elements()

if elements:
    # Separate elements by category
    walls = [
        el
        for el in elements
        if el.Category.Id.IntegerValue == int(BuiltInCategory.OST_Walls)
    ]
    beams = [
        el
        for el in elements
        if el.Category.Id.IntegerValue == int(BuiltInCategory.OST_StructuralFraming)
    ]
    columns = [
        el
        for el in elements
        if el.Category.Id.IntegerValue == int(BuiltInCategory.OST_StructuralColumns)
    ]
    floors = [
        el
        for el in elements
        if el.Category.Id.IntegerValue == int(BuiltInCategory.OST_Floors)
    ]

    # Detect already joined elements
    joined_walls = [wall for wall in walls if is_wall_joined(wall)]
    joined_beams = [beam for beam in beams if is_beam_joined(beam)]
    joined_columns = [col for col in columns]
    joined_floors = [floor for floor in floors]

    # Start transaction
    t = Transaction(doc, "Join/Unjoin Structural Elements")
    try:
        t.Start()

        wall_count = toggle_wall_join(walls, allow=not joined_walls)
        beam_count = toggle_beam_join(beams, allow=not joined_beams)
        column_count = toggle_column_join(doc, columns, allow=not joined_columns)
        floor_count = toggle_floor_join(doc, floors, allow=not joined_floors)

        t.Commit()

    except Exception as e:
        debug_log("Transaction failed: " + str(e))
        t.RollBack()
else:
    debug_log("No valid structural elements selected.")
