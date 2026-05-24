# -*- coding: utf-8 -*-
__title__ = "Align \n View"
__doc__ = """Aligns the active 3D view exactly to any selected face, at any arbitrary angle."""

from pyrevit import revit, DB, HOST_APP, forms
from Autodesk.Revit.DB import View3D, ViewOrientation3D, XYZ, UV
from pyrevit import script as pyscript
import System.Windows.Forms as WinForms
from pyrevit import forms

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

def reorient():
    # 1) Let user pick a face
    face = revit.pick_face()
    if not face:
        return

    # 2) Grab current 3D view & preserve its camera distance
    curview = revit.active_view
    ori = curview.GetOrientation()
    try:
        dist = (ori.EyePosition - ori.FocalPoint).GetLength()
        if dist < 1e-3:
            raise Exception()
    except:
        dist = 10.0

    # 3) Compute the face normal & face origin
    if HOST_APP.is_newer_than(2015):
        normal = face.ComputeNormal(UV(0, 0)).Normalize()
    else:
        normal = face.Normal.Normalize()
    origin = face.Origin

    # 4) Choose a “world up” axis & project it onto the face plane
    world_up = XYZ.BasisZ
    if abs(normal.DotProduct(world_up)) > 0.9:
        world_up = XYZ.BasisY
    # in‑plane “up” vector:
    up_plane = world_up.Subtract(normal.Multiply(world_up.DotProduct(normal)))
    up_vec = up_plane.Normalize()

    # 5) Compute a true “right” so we stay orthonormal
    right_vec = up_vec.CrossProduct(normal).Normalize()
    true_up_vec = normal.CrossProduct(right_vec).Normalize()

    # 6) Forward = looking straight at the face
    forward = normal.Negate()

    # 7) Position camera back along the normal by preserved distance
    eye_pos = origin.Add(normal.Multiply(dist))

    # 8) Build & apply the new orientation
    vo = ViewOrientation3D(eye_pos, true_up_vec, forward)
    with revit.Transaction("Align View to Face"):
        if not isinstance(curview, View3D):
            forms.alert("You must be on a 3D view for this tool to work.")
            return
        curview.SetOrientation(vo)

    revit.uidoc.RefreshActiveView()


# Entry point: only run on a 3D view
if isinstance(revit.active_view, View3D):
    reorient()
else:
    forms.alert("You must be on a 3D view for this tool to work.")
