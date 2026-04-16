# -*- coding: utf-8 -*-
__title__ = "Align \n Box"
__doc__ = """Aligns the section box of the current 3D view to selected face."""

# ╦╔╦╗╔═╗╔═╗╦═╗╔╦╗╔═╗
# ║║║║╠═╝║ ║╠╦╝ ║ ╚═╗
# ╩╩ ╩╩  ╚═╝╩╚═ ╩ ╚═╝
# ==================================================
from Autodesk.Revit.DB import *
from pyrevit.framework import Math
from pyrevit import revit, DB, UI, forms
from Autodesk.Revit.UI.Selection import ObjectType
from pyrevit import script as pyscript
import System.Windows.Forms as WinForms
from pyrevit import forms


# .NET Imports
import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitServices")
from System.Collections.Generic import List


# ╦  ╦╔═╗╦═╗╦╔═╗╔╗ ╦  ╔═╗╔═╗
# ╚╗╔╝╠═╣╠╦╝║╠═╣╠╩╗║  ║╣ ╚═╗
#  ╚╝ ╩ ╩╩╚═╩╩ ╩╚═╝╩═╝╚═╝╚═╝
# ==================================================
app = __revit__.Application
# uidoc = __revit__.ActiveUIDocument
# doc = __revit__.ActiveUIDocument.Document  # type:Document
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
# Debug toggle
DEBUG = False


def debug_log(message):
    """Log debug messages if DEBUG is enabled."""
    if DEBUG:
        print(message)


curview = revit.active_view


def orientsectionbox(view):
    try:
        # Prompt user to pick a face
        face = revit.uidoc.Selection.PickObject(
            UI.Selection.ObjectType.Face, "Pick a face to align to:"
        )
        box = view.GetSectionBox()
        element = revit.doc.GetElement(face)
        geometry_object = element.GetGeometryObjectFromReference(face)
        if isinstance(element, DB.FamilyInstance):
            transform = element.GetTransform()
            local_normal = geometry_object.ComputeNormal(DB.UV(0, 0)).Normalize()
            world_normal = transform.OfVector(local_normal).Normalize()
            norm = world_normal
        else:
            norm = geometry_object.ComputeNormal(DB.UV(0, 0)).Normalize()

        boxNormal = box.Transform.Basis[0].Normalize()
        angle = norm.AngleTo(boxNormal)
        axis = DB.XYZ(0, 0, 1.0)
        origin = DB.XYZ(
            box.Min.X + (box.Max.X - box.Min.X) / 2,
            box.Min.Y + (box.Max.Y - box.Min.Y) / 2,
            0.0,
        )
        if norm.Y * boxNormal.X < 0:
            rotate = DB.Transform.CreateRotationAtPoint(
                axis, Math.PI / 2 - angle, origin
            )
        else:
            rotate = DB.Transform.CreateRotationAtPoint(axis, angle, origin)
        box.Transform = box.Transform.Multiply(rotate)
        with revit.Transaction("Orient Section Box to Face"):
            view.SetSectionBox(box)
            revit.uidoc.RefreshActiveView()
    except Exception as ex:
        forms.alert("Error: " + str(ex))


# Validate the view and section box status
if isinstance(curview, DB.View3D) and curview.IsSectionBoxActive:
    orientsectionbox(curview)
elif isinstance(curview, DB.View3D) and not curview.IsSectionBoxActive:
    forms.alert("The section box for View3D isn't active.")
else:
    forms.alert("You must be on a 3D view for this tool to work.")
