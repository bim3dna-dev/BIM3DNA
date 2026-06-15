# -*- coding: utf-8 -*-
__title__ = "mirror_handler"
__doc__ = """Version = 1.0
Date    = 12.28.2025
________________________________________________________________
Description:

________________________________________________________________
How-To:

________________________________________________________________
Author: Emin Avdovic"""

# ==================================================
# Imports
# ==================================================
from Autodesk.Revit.DB import *
from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    DWGExportOptions,
    ElementId,
    FamilySymbol,
    FamilyInstance,
    FBXExportOptions,
    FilteredElementCollector,
    FormatOptions,
    FilterStringRule,
    FilterStringRuleEvaluator,
    FilterStringBeginsWith,
    FilterStringContains,
    FilterStringEquals,
    XYZ,
    Transaction,
    TransactionGroup,
    TextNote,
    TextNoteType,
    TextNoteOptions,
    IndependentTag,
    ImageExportOptions,
    ImageFileType,
    ImageResolution,
    UV,
    UnitTypeId,
    Reference,
    TagMode,
    TagOrientation,
    ViewSchedule,
    ViewSheet,
    ViewDuplicateOption,
    ViewDiscipline,
    Viewport,
    ParameterValueProvider,
    ParameterFilterElement,
    ScheduleSheetInstance,
    ScheduleFilter,
    ScheduleFilterType,
    ScheduleSortGroupField,
    ScheduleSortOrder,
    StorageType,
    SectionType,
    Category,
)
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.UI import *
from Autodesk.Revit.UI import TaskDialog
from Autodesk.Revit.UI import UIDocument
from Autodesk.Revit.UI import UIApplication
from Autodesk.Revit.DB.Structure import *
from Autodesk.Revit.Exceptions import *
from Autodesk.Revit.Attributes import *
from Autodesk.Revit.Exceptions import ArgumentException
from System.Collections.Generic import List as ClrList
from Autodesk.Revit.DB import (
    IFailuresPreprocessor,
    FailureProcessingResult,
    FailureSeverity,
)

import clr
import System
import System.IO
import os
import subprocess
import datetime
import tempfile
import json
import imp
from collections import defaultdict


clr.AddReference("System")
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
clr.AddReference("RevitServices")
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("WindowsBase")
from RevitServices.Persistence import DocumentManager
from System.Windows.Forms import (
    FormBorderStyle,
    AnchorStyles,
    AutoScaleMode,
    Form,
    ComboBox,
    ListBox,
    PictureBox,
    PictureBoxSizeMode,
    DataGridView,
    DataGridViewTextBoxColumn,
    DataGridViewButtonColumn,
    DataGridViewAutoSizeColumnsMode,
    DataGridViewSelectionMode,
    DockStyle,
    TextBox,
    Button,
    MessageBox,
    DialogResult,
    Label,
    ScrollBars,
    Application,
)
from System.Drawing import Image, Point, Color, Rectangle, Size
from System.IO import MemoryStream
from System.Windows.Forms import DataGridViewButtonColumn
from pyrevit import script

from System import Array
import math, re, sys

def _find_extension_root(start_dir):
    cur = start_dir
    while cur and cur != os.path.dirname(cur):
        if cur.endswith(".extension"):
            return cur
        cur = os.path.dirname(cur)
    return None


_THIS_DIR = os.path.dirname(__file__)
_EXT_DIR = _find_extension_root(_THIS_DIR)
_LIB_DIR = os.path.join(_EXT_DIR, "lib") if _EXT_DIR else None
_MODULE_STATE_PATH = (
    os.path.join(_LIB_DIR, "module_state.py") if _LIB_DIR else None
)

try:
    module_state = imp.load_source(
        "bim3dna_scope_modifier_module_state", _MODULE_STATE_PATH
    )
except Exception as ex:
    print("SCOPE MODIFIER IMPORT ERROR -> module_state could not be imported")
    print("script dir:", _THIS_DIR)
    print("extension dir:", _EXT_DIR)
    print("lib dir:", _LIB_DIR)
    print("error:", ex)
    raise

# ==================================================
# Revit Document Setup
# ==================================================
app = __revit__.Application
uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document
MIRROR_INPUT_INFO = {
    "source_payload": None,
    "raw_loaded_count": 0,
    "skipped": [],
    "skipped_by_reason_category": {},
}

# VERBOSE = False

# def debug(*args):
#     if VERBOSE:
#         print(" ".join([str(a) for a in args]))


# output = script.get_output()
# output.close_others()

try:
    from PIL import Image as PILImage

    PIL_OK = True
except ImportError:
    PIL_OK = False


# ==================================================
# Helpers
# ==================================================

HOST_AWARE_CATEGORIES = set(
    [
        "Roof Drain Plate",
    ]
)
COPYABLE_PAYLOAD_ONLY = True


class ScopeBoxSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            return elem.Category and elem.Category.Id.IntegerValue == int(
                BuiltInCategory.OST_VolumeOfInterest
            )
        except Exception:
            return False

    def AllowReference(self, ref, point):
        return False


def load_collected_ids():
    """Load copyable ids, classifying collected ids only as a fallback."""
    try:
        copyable_path = module_state.get_latest_copyable_payload_path()
        if not copyable_path:
            for directory in module_state._candidate_dirs(doc):
                candidate = os.path.join(directory, "copyable_latest.json")
                if os.path.isfile(candidate):
                    copyable_path = candidate
                    break
        if copyable_path and os.path.isfile(copyable_path):
            with open(copyable_path, "r") as stream:
                data = json.load(stream)
            ids = data.get("copyable_ids", [])
            MIRROR_INPUT_INFO["source_payload"] = copyable_path
            MIRROR_INPUT_INFO["raw_loaded_count"] = data.get(
                "valid_count", len(ids)
            )
            MIRROR_INPUT_INFO["skipped"] = data.get("skipped", [])
            MIRROR_INPUT_INFO["skipped_by_reason_category"] = data.get(
                "skipped_by_reason_category", {}
            )
            elems = [
                doc.GetElement(ElementId(int(value)))
                for value in ids
                if value is not None
            ]
            return [element.Id for element in elems if element and element.IsValidObject]

        collected_path = None
        for d in module_state._candidate_dirs(doc):
            p = os.path.join(d, "collected_latest.json")
            if os.path.isfile(p):
                collected_path = p
                break
        if not collected_path:
            return []
        with open(collected_path, "r") as fp:
            data = json.load(fp)
        ids = data.get("elements", {}).get("valid_ids", []) or data.get(
            "elements", {}
        ).get("ids", [])
        if not ids:
            ids = (
                data.get("source_state", {})
                .get("payload", {})
                .get("elements", {})
                .get("ids", [])
            )
        classification = module_state.classify_copyable_elements(doc, ids)
        MIRROR_INPUT_INFO["source_payload"] = collected_path
        MIRROR_INPUT_INFO["raw_loaded_count"] = len(ids)
        MIRROR_INPUT_INFO["skipped"] = classification["skipped"]
        MIRROR_INPUT_INFO["skipped_by_reason_category"] = classification[
            "skipped_by_reason_category"
        ]
        print(
            "MIRROR HANDLER -> no copyable payload found; "
            "classified collected payload at runtime."
        )
        return [
            ElementId(int(value)) for value in classification["copyable_ids"]
        ]
    except Exception as ex:
        print("MIRROR HANDLER -> input load failed: {}".format(ex))
        return []


def pick_scopebox(prompt):
    ref = uidoc.Selection.PickObject(
        ObjectType.Element, ScopeBoxSelectionFilter(), prompt
    )
    return doc.GetElement(ref.ElementId)


def _to_local_xyz(Tiny, p):
    return Tiny.OfPoint(p)


def _bbox_intersects_scopebox(elem, sb):
    """Fast AABB overlap test in scope box local space coordinates."""
    try:
        ebb = elem.get_BoundingBox(None)
        sbb = sb.get_BoundingBox(None)
        if not ebb or not sbb or not sbb.Transform:
            return False

        Tiny = sbb.Transform.Inverse

        # element bb corners > scope box local
        pts = [
            _to_local_xyz(Tiny, XYZ(ebb.Min.X, ebb.Min.Y, ebb.Min.Z)),
            _to_local_xyz(Tiny, XYZ(ebb.Min.X, ebb.Min.Y, ebb.Max.Z)),
            _to_local_xyz(Tiny, XYZ(ebb.Min.X, ebb.Max.Y, ebb.Min.Z)),
            _to_local_xyz(Tiny, XYZ(ebb.Min.X, ebb.Max.Y, ebb.Max.Z)),
            _to_local_xyz(Tiny, XYZ(ebb.Max.X, ebb.Min.Y, ebb.Min.Z)),
            _to_local_xyz(Tiny, XYZ(ebb.Max.X, ebb.Min.Y, ebb.Max.Z)),
            _to_local_xyz(Tiny, XYZ(ebb.Max.X, ebb.Max.Y, ebb.Min.Z)),
            _to_local_xyz(Tiny, XYZ(ebb.Max.X, ebb.Max.Y, ebb.Max.Z)),
        ]

        mn = XYZ(min(p.X for p in pts), min(p.Y for p in pts), min(p.Z for p in pts))
        mx = XYZ(max(p.X for p in pts), max(p.Y for p in pts), max(p.Z for p in pts))

        # scope local extents
        sb_mn = sbb.Min
        sb_mx = sbb.Max

        # AABB overlap
        if mx.X < sb_mn.X or mn.X > sb_mx.X:
            return False
        if mx.Y < sb_mn.Y or mn.Y > sb_mx.Y:
            return False
        if mx.Z < sb_mn.Z or mn.Z > sb_mx.Z:
            return False
        return True
    except Exception:
        return False


def filter_ids_by_scopebox(ids_clr, src_sb):
    ids_out = []
    for eid in ids_clr:
        e = doc.GetElement(eid)
        if not e or not e.IsValidObject:
            continue
        if _bbox_intersects_scopebox(e, src_sb):
            ids_out.append(eid)
    return ids_out


def scopebox_center_world(sb):
    bb = sb.get_BoundingBox(None)
    if not bb or not bb.Transform:
        raise Exception("Scope box has no bounding box/transform.")
    T = bb.Transform
    mn = bb.Min
    mx = bb.Max
    c_local = XYZ((mn.X + mx.X) * 0.5, (mn.Y + mx.Y) * 0.5, (mn.Z + mx.Z) * 0.5)
    return T.OfPoint(c_local)


def scopebox_axis_world(sb, axis="X", flatten_z=True):
    bb = sb.get_BoundingBox(None)
    if not bb or not bb.Transform:
        raise Exception("Scope box has no bounding box/transform.")

    T = bb.Transform
    a = (axis or "X").upper()

    if a == "X":
        v = T.BasisX
    elif a == "Y":
        v = T.BasisY
    elif a == "Z":
        v = T.BasisZ
    else:
        # defensive fallback
        v = T.BasisX

    if flatten_z:
        v = XYZ(v.X, v.Y, 0.0)

    if v.GetLength() < 1e-9:
        v = XYZ(1, 0, 0)

    return v.Normalize()


def sb_bbox(sb):
    bb = sb.get_BoundingBox(None)
    if not bb or not bb.Transform:
        return None
    return bb


def sb_world_center(bb):
    c_local = XYZ(
        (bb.Min.X + bb.Max.X) * 0.5,
        (bb.Min.Y + bb.Max.Y) * 0.5,
        (bb.Min.Z + bb.Max.Z) * 0.5,
    )
    return bb.Transform.OfPoint(c_local)


def sb_world_face_plane(bb, mode):
    # Build target-space plane from target Transform with world-center origin
    half_x = 0.5 * (bb.Max.X - bb.Min.X)
    n = bb.Transform.BasisX
    if n.GetLength() < 1e-9:
        n = XYZ(1, 0, 0)

    if mode == "center":
        offset = 0.0
        n_world = n.Normalize()
    elif mode == "right_face":
        offset = half_x
        n_world = n.Normalize()
    elif mode == "left_face":
        offset = -half_x
        n_world = n.Normalize().Negate()
    else:
        raise Exception("unknown mode")

    origin_world = sb_world_center(bb) + (n.Normalize() * offset)
    return Plane.CreateByNormalAndOrigin(n_world, origin_world)


def build_target_face_plane(tgt_sb, mode="center"):
    """
    Builds mirror plane from TARGET scopebox face/center in world space.
    Plane passes through target face (no padding).
    """
    bb = sb_bbox(tgt_sb)
    if not bb:
        raise Exception("Scope box has no bounding box/transform.")
    return sb_world_face_plane(bb, mode)


def reflect_point_about_plane(p, plane):
    # p' = p - 2*(n.(p - o)) * n
    o = plane.Origin
    n = plane.Normal.Normalize()
    v = p - o
    d = v.DotProduct(n)
    return p - (2.0 * d) * n


def reflect_vector_about_plane(v, plane):
    n = plane.Normal.Normalize()
    d = v.DotProduct(n)
    return v - (2.0 * d) * n


def scopebox_center_world(sb):
    bb = sb.get_BoundingBox(None)
    if not bb or not bb.Transform:
        raise Exception("Scope box has no bounding box/transform.")
    T = bb.Transform
    mn, mx = bb.Min, bb.Max
    c_local = XYZ((mn.X + mx.X) * 0.5, (mn.Y + mx.Y) * 0.5, (mn.Z + mx.Z) * 0.5)
    return T.OfPoint(c_local)


def collect_groups_in_scopebox(src_sb):
    out = []
    try:
        grps = FilteredElementCollector(doc).OfClass(Group).ToElements()
        for g in grps:
            try:
                if _bbox_intersects_scopebox(g, src_sb):
                    out.append(g.Id)
            except Exception:
                pass
    except Exception:
        pass
    return out


def collect_groups_by_member_intersection(ids_in_src):
    """Return unique group instance ElementIds for any element in ids_in_src that is a group member."""
    gids = []
    seen = set()
    for eid in ids_in_src:
        e = doc.GetElement(eid)
        if not e or not e.IsValidObject:
            continue
        try:
            gid = getattr(e, "GroupId", ElementId.InvalidElementId)
            if gid and gid != ElementId.InvalidElementId:
                if gid.IntegerValue not in seen:
                    gids.append(gid)
                    seen.add(gid.IntegerValue)
        except Exception:
            pass
    return gids


def _bboxxyz_world_aabb(bbxyz):
    """Return world-space (min, max) XYZ for a BoundingBoxXYZ."""
    T = bbxyz.Transform
    mn = bbxyz.Min
    mx = bbxyz.Max
    pts = [
        T.OfPoint(XYZ(mn.X, mn.Y, mn.Z)),
        T.OfPoint(XYZ(mn.X, mn.Y, mx.Z)),
        T.OfPoint(XYZ(mn.X, mx.Y, mn.Z)),
        T.OfPoint(XYZ(mn.X, mx.Y, mx.Z)),
        T.OfPoint(XYZ(mx.X, mn.Y, mn.Z)),
        T.OfPoint(XYZ(mx.X, mn.Y, mx.Z)),
        T.OfPoint(XYZ(mx.X, mx.Y, mn.Z)),
        T.OfPoint(XYZ(mx.X, mx.Y, mx.Z)),
    ]
    wmn = XYZ(min(p.X for p in pts), min(p.Y for p in pts), min(p.Z for p in pts))
    wmx = XYZ(max(p.X for p in pts), max(p.Y for p in pts), max(p.Z for p in pts))
    return wmn, wmx


def section_intersects_scopebox(vs, sb):
    """Test ovelap of ViewSection section box vs scope box in scope-box loxal space."""
    try:
        sbb = sb.get_BoundingBox(None)
        if not sbb or not sbb.Transform:
            return False
        Tiny = sbb.Transform.Inverse

        sec_bb = vs.GetSectionBox()
        if not sec_bb:
            return False

        wmn, wmx = _bboxxyz_world_aabb(sec_bb)

        # convert AABB corners to scopebox local for overlap test
        pts = [
            Tiny.OfPoint(XYZ(wmn.X, wmn.Y, wmn.Z)),
            Tiny.OfPoint(XYZ(wmn.X, wmn.Y, wmx.Z)),
            Tiny.OfPoint(XYZ(wmn.X, wmx.Y, wmn.Z)),
            Tiny.OfPoint(XYZ(wmn.X, wmx.Y, wmx.Z)),
            Tiny.OfPoint(XYZ(wmx.X, wmn.Y, wmn.Z)),
            Tiny.OfPoint(XYZ(wmx.X, wmn.Y, wmx.Z)),
            Tiny.OfPoint(XYZ(wmx.X, wmx.Y, wmn.Z)),
            Tiny.OfPoint(XYZ(wmx.X, wmx.Y, wmx.Z)),
        ]

        mn = XYZ(min(p.X for p in pts), min(p.Y for p in pts), min(p.Z for p in pts))
        mx = XYZ(max(p.X for p in pts), max(p.Y for p in pts), max(p.Z for p in pts))

        sb_mn = sbb.Min
        sb_mx = sbb.Max

        if mx.X < sb_mn.X or mn.X > sb_mx.X:
            return False
        if mx.Y < sb_mn.Y or mn.Y > sb_mx.Y:
            return False
        if mx.Z < sb_mn.Z or mn.Z > sb_mx.Z:
            return False
        return True
    except Exception:
        return False


def point_in_scopebox(p_world, sb):
    """Return True if world point lies inside scope box extents (in scope local coords)."""
    bb = sb.get_BoundingBox(None)
    if not bb or not bb.Transform:
        return False
    Tiny = bb.Transform.Inverse
    pl = Tiny.OfPoint(p_world)
    mn, mx = bb.Min, bb.Max
    return (mn.X <= pl.X <= mx.X) and (mn.Y <= pl.Y <= mx.Y) and (mn.Z <= pl.Z <= mx.Z)


def _safe_name(obj):
    try:
        return obj.Name
    except Exception:
        return ""


def _cat_name(e):
    try:
        if e and e.Category:
            return e.Category.Name
    except Exception:
        pass
    return "Unknown"


def _family_type_name(e):
    try:
        if isinstance(e, FamilyInstance):
            sym = e.Symbol
            if sym:
                return "{} : {}".format(_safe_name(sym.Family), _safe_name(sym))
    except Exception:
        pass
    return ""


def _host_info(e):
    try:
        if isinstance(e, FamilyInstance):
            h = e.Host
            if h and h.IsValidObject:
                return (h.Id.IntegerValue, _cat_name(h))
    except Exception:
        pass
    return (None, "")


def _project_points_on_axis(pts, axis):
    mn = None
    mx = None
    for p in pts:
        v = p.DotProduct(axis)
        if mn is None or v < mn:
            mn = v
        if mx is None or v > mx:
            mx = v
    return mn, mx


def scopebox_world_corners(sb):
    bb = sb.get_BoundingBox(None)
    if not bb or not bb.Transform:
        return []
    T = bb.Transform
    mn = bb.Min
    mx = bb.Max
    return [
        T.OfPoint(XYZ(mn.X, mn.Y, mn.Z)),
        T.OfPoint(XYZ(mn.X, mn.Y, mx.Z)),
        T.OfPoint(XYZ(mn.X, mx.Y, mn.Z)),
        T.OfPoint(XYZ(mn.X, mx.Y, mx.Z)),
        T.OfPoint(XYZ(mx.X, mn.Y, mn.Z)),
        T.OfPoint(XYZ(mx.X, mn.Y, mx.Z)),
        T.OfPoint(XYZ(mx.X, mx.Y, mn.Z)),
        T.OfPoint(XYZ(mx.X, mx.Y, mx.Z)),
    ]


def get_stable_right_axis():
    try:
        v = uidoc.ActiveView.RightDirection
        if v and v.GetLength() > 1e-9:
            return v.Normalize()
    except Exception:
        pass
    return XYZ(1, 0, 0)


def collect_group_instances_in_scopebox(src_sb):
    """Collect both Model Groups and Detail Groups whose insertion point lies in SOURCE scope box"""
    out = []
    seen = set()
    for g in FilteredElementCollector(doc).OfClass(Group).ToElements():
        try:
            loc = g.Location
            p = None
            if isinstance(loc, LocationPoint):
                p = loc.Point
            elif isinstance(loc, LocationCurve):
                crv = loc.Curve
                p = crv.Evaluate(0.5, True)  # mid-point

            if p and point_in_scopebox(p, src_sb):
                if g.Id.IntegerValue not in seen:
                    out.append(g.Id)
                    seen.add(g.Id.IntegerValue)
        except Exception:
            pass
    return out


def map_section_box(sec_bb, plane, move_vec):
    """
    Reflect section BoundingBoxXYZ about plane, then translate by move_vec.
    Ensures returned Transform is right-handed.
    """
    T0 = sec_bb.Transform

    # Map origin
    o0 = T0.Origin
    o1 = reflect_point_about_plane(o0, plane) + move_vec

    # Reflect basis vectors
    bx = reflect_vector_about_plane(T0.BasisX, plane)
    by = reflect_vector_about_plane(T0.BasisY, plane)

    # Ensure non-degenerate
    if bx.GetLength() < 1e-9:
        bx = XYZ(1, 0, 0)
    if by.GetLength() < 1e-9:
        by = XYZ(0, 1, 0)

    bx = bx.Normalize()
    by = by.Normalize()

    # Enforce right-handed coordinate system
    bz = bx.CrossProduct(by)
    if bz.GetLength() < 1e-9:
        # fallback: build an othogonal by from bx and global Z
        tmp = XYZ(0, 0, 1).CrossProduct(bx)
        if tmp.GetLength() < 1e-9:
            tmp = XYZ(0, 1, 0)
        by = tmp.Normalize()
        bz = bx.CrossProduct(by)

    bz = bz.Normalize()
    # Re-orthogonalize by
    by = bz.CrossProduct(bx).Normalize()

    T1 = Transform.Identity
    T1.Origin = o1
    T1.BasisX = bx
    T1.BasisY = by
    T1.BasisZ = bz

    out = BoundingBoxXYZ()
    out.Transform = T1
    out.Min = sec_bb.Min
    out.Max = sec_bb.Max
    return out


def map_section_box_with_map_and_mirror(sec_bb, t_map, plane):
    """
    Map section box from SOURCE local to TARGET local (rigid),
    then mirror about target plane.
    """
    T0 = sec_bb.Transform
    T1 = t_map.Multiply(T0)
    o1 = T1.Origin
    bx1 = T1.BasisX
    by1 = T1.BasisY

    o2 = reflect_point_about_plane(o1, plane)
    bx2 = reflect_vector_about_plane(bx1, plane)
    by2 = reflect_vector_about_plane(by1, plane)

    if bx2.GetLength() < 1e-9:
        bx2 = XYZ(1, 0, 0)
    if by2.GetLength() < 1e-9:
        by2 = XYZ(0, 1, 0)
    bx2 = bx2.Normalize()
    by2 = by2.Normalize()

    bz2 = bx2.CrossProduct(by2)
    if bz2.GetLength() < 1e-9:
        tmp = XYZ(0, 0, 1).CrossProduct(bx2)
        if tmp.GetLength() < 1e-9:
            tmp = XYZ(0, 1, 0)
        by2 = tmp.Normalize()
        bz2 = bx2.CrossProduct(by2)
    bz2 = bz2.Normalize()
    by2 = bz2.CrossProduct(bx2).Normalize()

    T2 = Transform.Identity
    T2.Origin = o2
    T2.BasisX = bx2
    T2.BasisY = by2
    T2.BasisZ = bz2

    out = BoundingBoxXYZ()
    out.Transform = T2
    out.Min = sec_bb.Min
    out.Max = sec_bb.Max
    return out


def copy_section_view_settings(src_vs, new_vs):
    """Best-effort copy of ocmmon view properties. Keep minimal to avoid read-only failures."""
    try:
        new_vs.Scale = src_vs.Scale
    except Exception:
        pass
    try:
        new_vs.DetailLevel = src_vs.DetailLevel
    except Exception:
        pass
    try:
        new_vs.DisplayStyle = src_vs.DisplayStyle
    except Exception:
        pass
    try:
        if (
            src_vs.ViewTemplateId
            and src_vs.ViewTemplateId != ElementId.InvalidElementId
        ):
            new_vs.ViewTemplateId = src_vs.ViewTemplateId
    except Exception:
        pass

    # Crop settings
    try:
        new_vs.CropBoxActive = src_vs.CropBoxActive
    except Exception:
        pass
    try:
        new_vs.CropBoxVisible = src_vs.CropBoxVisible
    except Exception:
        pass


def mirror_plane_from_scopebox(sb, mode="center", offset_ft=0.0):
    bb = sb.get_BoundingBox(None)
    if not bb or not bb.Transform:
        raise Exception("Scope box has no bounding box.")
    T = bb.Transform
    mn = bb.Min
    mx = bb.Max

    xmid = (mn.X + mx.X) * 0.5
    ymid = (mn.Y + mx.Y) * 0.5
    zmid = (mn.Z + mx.Z) * 0.5

    if mode == "left_face":
        xplane = mn.X
    elif mode == "right_face":
        xplane = mx.X
    elif mode == "offset":
        xplane = xmid + offset_ft
    else:
        xplane = xmid

    # plane origin point in local SB coords -> world
    p_local = XYZ(xplane, ymid, zmid)
    p_world = T.OfPoint(p_local)

    # plane normal is SB local X axis in world (flatten Z optional)
    n_world = T.BasisX
    n_world = XYZ(n_world.X, n_world.Y, 0.0)
    if n_world.GetLength() < 1e-9:
        n_world = XYZ(1, 0, 0)
    n_world = n_world.Normalize()

    return Plane.CreateByNormalAndOrigin(n_world, p_world)


def ask_mode():
    td = TaskDialog("mirror_handler")
    td.MainInstruction = "Choose mirror mode"
    td.MainContent = (
        "Mirror relative to scope box X axis.\n"
        "None = no mirror.\n"
        "Center = about scope box centerline.\n"
        "Left/Right = about left/right face."
    )
    td.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, "None")
    td.AddCommandLink(TaskDialogCommandLinkId.CommandLink2, "Centerline")
    td.AddCommandLink(TaskDialogCommandLinkId.CommandLink3, "Left face")
    td.AddCommandLink(TaskDialogCommandLinkId.CommandLink4, "Right face")
    res = td.Show()
    if res == TaskDialogResult.CommandLink1:
        return "none"
    if res == TaskDialogResult.CommandLink2:
        return "center"
    if res == TaskDialogResult.CommandLink3:
        return "left_face"
    if res == TaskDialogResult.CommandLink4:
        return "right_face"
    return "none"


def ask_copy():
    td = TaskDialog("mirror_handler")
    td.MainInstruction = "Create a mirrored copy?"
    td.MainContent = "Yes = mirror and keep original. No = mirror in place."
    td.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
    res = td.Show()
    return res == TaskDialogResult.Yes


def filter_ids(ids):
    ids_ok = ClrList[ElementId]()
    skipped_group_members = 0
    expanded_assembly = 0
    seen = set()

    for eid in ids:
        e = doc.GetElement(eid)
        if not e or not e.IsValidObject:
            continue
        if eid.IntegerValue in seen:
            continue
        ids_ok.Add(eid)
        seen.add(eid.IntegerValue)

    return ids_ok, skipped_group_members, expanded_assembly


def _accum_bbox(created_bbox, bb):
    if created_bbox is None:
        created_bbox = BoundingBoxXYZ()
        created_bbox.Min = XYZ(bb.Min.X, bb.Min.Y, bb.Min.Z)
        created_bbox.Max = XYZ(bb.Max.X, bb.Max.Y, bb.Max.Z)
        return created_bbox

    created_bbox.Min = XYZ(
        min(created_bbox.Min.X, bb.Min.X),
        min(created_bbox.Min.Y, bb.Min.Y),
        min(created_bbox.Min.Z, bb.Min.Z),
    )
    created_bbox.Max = XYZ(
        max(created_bbox.Max.X, bb.Max.X),
        max(created_bbox.Max.Y, bb.Max.Y),
        max(created_bbox.Max.Z, bb.Max.Z),
    )
    return created_bbox


def main():
    preflight_errors = []
    hard_skipped = []
    created_bbox = None
    allowed_ids = []
    mirrored = 0

    mode = ask_mode()
    if mode == "none":
        TaskDialog.Show("mirror_handler", "Mirror cancelled (mode = None).")
        return
    do_copy = ask_copy()

    # --- Scope boxes: SOURCE (what to mirror) + optional TARGET (where to mirror) ---
    try:
        src_sb = pick_scopebox("Pick SOURCE scope box (elements to mirror)")
    except Exception:
        TaskDialog.Show("mirror_handler", "Scope box selection cancelled.")
        return

    tgt_sb = None
    try:
        tgt_sb = pick_scopebox("Pick TARGET scope box (Esc for in-place mirror)")
    except Exception:
        tgt_sb = None

    has_target = tgt_sb is not None
    forced_copy = False

    if has_target and not do_copy:
        # target picked implies copy workflow; in-place mirror does not map to target
        forced_copy = True
        do_copy = True

    if has_target:
        try:
            bb_src = sb_bbox(src_sb)
            bb_tgt = sb_bbox(tgt_sb)
            if not bb_src or not bb_tgt:
                raise Exception("Scope box has no bounding box/transform.")

            plane = sb_world_face_plane(bb_tgt, mode if mode else "center")

            # Build transforms using world-center origins + scopebox basis
            src_center_world = sb_world_center(bb_src)
            tgt_center_world = sb_world_center(bb_tgt)

            T_src = Transform.Identity
            T_src.Origin = src_center_world
            T_src.BasisX = bb_src.Transform.BasisX
            T_src.BasisY = bb_src.Transform.BasisY
            T_src.BasisZ = bb_src.Transform.BasisZ

            T_tgt = Transform.Identity
            T_tgt.Origin = tgt_center_world
            T_tgt.BasisX = bb_tgt.Transform.BasisX
            T_tgt.BasisY = bb_tgt.Transform.BasisY
            T_tgt.BasisZ = bb_tgt.Transform.BasisZ

            # Rigid map from SOURCE local -> TARGET local
            T_map = T_tgt.Multiply(T_src.Inverse)

            # --- Debug prints (mandatory) ---
            mapped_src_center = T_map.OfPoint(src_center_world)
            dist = mapped_src_center.DistanceTo(tgt_center_world)

            print("DEBUG: src_center_world = {}".format(src_center_world))
            print("DEBUG: tgt_center_world = {}".format(tgt_center_world))
            print("DEBUG: mapped_src_center = {}".format(mapped_src_center))
            print("DEBUG: map->tgt center dist = {:.6f}".format(dist))
            print("DEBUG: T_src.Origin = {}".format(T_src.Origin))
            print("DEBUG: T_tgt.Origin = {}".format(T_tgt.Origin))
            print("DEBUG: T_src.BasisX = {}".format(T_src.BasisX))
            print("DEBUG: T_src.BasisY = {}".format(T_src.BasisY))
            print("DEBUG: T_tgt.BasisX = {}".format(T_tgt.BasisX))
            print("DEBUG: T_tgt.BasisY = {}".format(T_tgt.BasisY))

            if dist > 1e-4:
                TaskDialog.Show(
                    "mirror_handler",
                    "Preflight failed: mapped center too far from target center.\n"
                    "Distance: {:.6f}".format(dist),
                )
                return

        except Exception as ex:
            TaskDialog.Show(
                "mirror_handler", "Failed to build target mapping: {}".format(ex)
            )
            return
    else:
        # In-place mirror uses SOURCE scope box plane (legacy behavior)
        try:
            plane = mirror_plane_from_scopebox(src_sb, mode=mode)
        except Exception as ex:
            TaskDialog.Show(
                "mirror_handler", "Failed to build mirror plane: {}".format(ex)
            )
            return
        T_map = Transform.Identity

    ids = load_collected_ids()
    if not ids:
        TaskDialog.Show(
            "mirror_handler",
            "No ids loaded. Run element_collector first.",
        )
        return

    classification = module_state.classify_copyable_elements(doc, ids)
    ids = [ElementId(int(value)) for value in classification["copyable_ids"]]
    combined_skipped = list(MIRROR_INPUT_INFO.get("skipped", []))
    combined_skipped.extend(classification["skipped"])
    combined_breakdown = dict(
        MIRROR_INPUT_INFO.get("skipped_by_reason_category", {})
    )
    for key, count in classification["skipped_by_reason_category"].items():
        combined_breakdown[key] = combined_breakdown.get(key, 0) + count
    print("MIRROR HANDLER INPUT ->")
    print(
        "loaded ids: {}".format(
            MIRROR_INPUT_INFO.get("raw_loaded_count") or len(ids)
        )
    )
    print("copyable ids: {}".format(len(ids)))
    print("skipped non-copyable: {}".format(len(combined_skipped)))
    print("source payload: {}".format(MIRROR_INPUT_INFO.get("source_payload")))
    for key, count in sorted(
        combined_breakdown.items(), key=lambda item: item[1], reverse=True
    )[:20]:
        print(" - {} : {}".format(key, count))

    ids_clr, skipped_group_members, expanded_assembly = filter_ids(ids)
    ids_in_src = list(filter_ids_by_scopebox(ids_clr, src_sb))

    # --- Groups: union of (by location) + (member)
    grp_ids_loc = collect_group_instances_in_scopebox(src_sb)  # robust
    grp_ids_mem = collect_groups_by_member_intersection(ids_in_src)  # opportunistc

    grp_ids_all = []
    seen_gid = set()

    for gid in grp_ids_loc + grp_ids_mem:
        if (
            gid
            and gid != ElementId.InvalidElementId
            and gid.IntegerValue not in seen_gid
        ):
            grp_ids_all.append(gid)
            seen_gid.add(gid.IntegerValue)

    # Safe mode does not introduce group IDs that were absent from copyable input.
    if not COPYABLE_PAYLOAD_ONLY:
        seen = set([x.IntegerValue for x in ids_in_src])
        for gid in grp_ids_all:
            if gid.IntegerValue not in seen:
                ids_in_src.append(gid)
                seen.add(gid.IntegerValue)

    groups_found_loc = len(grp_ids_loc)
    groups_found_mem = len(grp_ids_mem)
    groups_found_all = len(grp_ids_all)

    if not ids_in_src:
        TaskDialog.Show("mirror_handler", "No elements found inside SOURCE scope box.")
        return

    # --- Execution (single undo) ---
    run_errors = []
    preflight_errors = []
    hard_skipped = []
    created_ids = []
    failures = 0
    failed_element_ids = []
    pinned_reset = []
    created_bbox = None  # optional

    class RollbackOnError(IFailuresPreprocessor):
        def __init__(self, err_log):
            self.err_log = err_log

        def PreprocessFailures(self, fa):
            try:
                msgs = fa.GetFailureMessages()
                for m in msgs:
                    sev = m.GetSeverity()
                    if sev == FailureSeverity.Warning:
                        fa.DeleteWarning(m)
                    else:
                        # record something useful
                        try:
                            self.err_log.append(m.GetDescriptionText())
                        except Exception:
                            self.err_log.append("Non-warning failure (no description).")
                        return FailureProcessingResult.ProceedWithRollBack
                return FailureProcessingResult.Continue
            except Exception:
                return FailureProcessingResult.ProceedWithRollBack

    # --- Failure handling helper (use everywhere) ---
    def set_failure_opts(tx, err_log):
        # Only Transaction support failure handling options; SubTransaction do not
        opts = tx.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(RollbackOnError(err_log))
        opts.SetClearAfterRollback(True)
        tx.SetFailureHandlingOptions(opts)

    # Per-element execution (no preflight), required for robust fallback
    allowed_ids = list(ids_in_src)
    if not allowed_ids:
        TaskDialog.Show("mirror_handler", "No elements found inside SOURCE scope box.")
        return

    # ---------------------------------
    # Execution (single undo)
    # - TransactionGroup => ONE undo step
    # - per-element Transaction => failures rollback locally
    # ---------------------------------
    tg = TransactionGroup(doc, "mirror_handler: Mirror Copy")
    tg.Start()

    created_ids = []
    run_errors = []
    failures = 0
    per_cat_fail = defaultdict(int)
    example_failures = []
    cpo = CopyPasteOptions()

    try:
        created_sections = []

        # -----------------------------------------
        # Per-element mirror with fallbacks
        # -----------------------------------------
        for eid in allowed_ids:
            tx = Transaction(doc, "mirror_handler: Mirror One")
            started = False
            try:
                tx.Start()
                started = True
                set_failure_opts(tx, run_errors)

                el = doc.GetElement(eid)
                was_pinned = False
                try:
                    if el and hasattr(el, "Pinned") and el.Pinned:
                        was_pinned = True
                        el.Pinned = False
                except Exception:
                    pass

                created_this = []
                mirror_ok = False

                if has_target:
                    # --- Target workflow: copy with translation, then mirror about TARGET face ---
                    try:
                        res_ids = ElementTransformUtils.CopyElements(
                            doc, ClrList[ElementId]([eid]), doc, T_map, cpo
                        )
                        created_this = list(res_ids)
                        if created_this:
                            ElementTransformUtils.MirrorElements(
                                doc,
                                ClrList[ElementId](created_this),
                                plane,
                                False,
                            )
                            mirror_ok = True
                    except Exception as ex:
                        mirror_ok = False
                        run_errors.append(
                            "Copy(map)+mirror failed ({}): {}".format(
                                eid.IntegerValue, ex
                            )
                        )

                    # --- Host-aware attempt: copy host + element, then mirror ---
                    if (
                        (not mirror_ok)
                        and do_copy
                        and (_cat_name(el) in HOST_AWARE_CATEGORIES)
                    ):
                        try:
                            host = getattr(el, "Host", None)
                            if (
                                host
                                and host.IsValidObject
                                and _bbox_intersects_scopebox(host, src_sb)
                            ):
                                res_ids = ElementTransformUtils.CopyElements(
                                    doc,
                                    ClrList[ElementId]([host.Id, eid]),
                                    doc,
                                    T_map,
                                    cpo,
                                )
                                created_this = list(res_ids)
                                if created_this:
                                    ElementTransformUtils.MirrorElements(
                                        doc,
                                        ClrList[ElementId](created_this),
                                        plane,
                                        False,
                                    )
                                    mirror_ok = True
                        except Exception as ex:
                            run_errors.append(
                                "Host-aware copy+mirror failed ({}): {}".format(
                                    eid.IntegerValue, ex
                                )
                            )
                else:
                    # --- In-place mirror (legacy behavior) ---
                    try:
                        res_ids = ElementTransformUtils.MirrorElements(
                            doc, ClrList[ElementId]([eid]), plane, do_copy
                        )
                        mirror_ok = True
                        if do_copy:
                            created_this = list(res_ids)
                    except Exception as ex:
                        mirror_ok = False
                        run_errors.append(
                            "Mirror failed ({}): {}".format(eid.IntegerValue, ex)
                        )

                # No move_vec needed when using T_map

                if not mirror_ok:
                    raise Exception("All mirror/copy attempts failed.")

                st = tx.Commit()
                if st == TransactionStatus.Committed:
                    if do_copy and mirror_ok:
                        created_ids.extend(created_this)
                    if was_pinned:
                        try:
                            el2 = doc.GetElement(eid)
                            if el2 and el2.IsValidObject:
                                el2.Pinned = True
                        except Exception:
                            pass
                else:
                    failures += 1
                    per_cat_fail[_cat_name(el)] += 1

            except Exception as ex:
                failures += 1
                failed_element_ids.append(eid.IntegerValue)
                el = doc.GetElement(eid)
                per_cat_fail[_cat_name(el)] += 1
                host_id, host_cat = _host_info(el)
                famtype = _family_type_name(el)
                reason = "Per-element ({}): {}".format(eid.IntegerValue, ex)
                if host_id:
                    reason += " | Host {} ({})".format(host_id, host_cat)
                if famtype:
                    reason += " | {}".format(famtype)
                if len(example_failures) < 10:
                    example_failures.append(reason)
                run_errors.append(reason)
                try:
                    if started:
                        tx.RollBack()
                except Exception:
                    pass

        # -----------------------------------------
        # Explicit Section Recreation (always run)
        # -----------------------------------------
        sections_found = 0
        sections_created = 0
        created_sections = []

        try:
            # 1. Collect candidate sections intersecting SOURCE scobe box
            all_secs = (
                []
                if COPYABLE_PAYLOAD_ONLY
                else FilteredElementCollector(doc).OfClass(ViewSection).ToElements()
            )
            sec_candidates = []

            for vs in all_secs:
                try:
                    if vs.IsTemplate:
                        continue

                    # if you want only building sections:
                    if vs.ViewType != ViewType.Section:
                        continue

                    if section_intersects_scopebox(vs, src_sb):
                        sec_candidates.append(vs)
                except Exception:
                    pass

            # >>> Counter #1: right after building candidates
            sections_found = len(sec_candidates)

            if sec_candidates:
                txs = Transaction(doc, "mirror_handler: Recreate Sections")
                txs.Start()
                set_failure_opts(txs, run_errors)

                for src_vs in sec_candidates:
                    try:
                        sec_bb = src_vs.GetSectionBox()
                        if not sec_bb:
                            continue

                        if has_target:
                            new_bb = map_section_box_with_map_and_mirror(
                                sec_bb, T_map, plane
                            )
                        else:
                            new_bb = map_section_box(sec_bb, plane, XYZ(0, 0, 0))

                        vft = doc.GetElement(src_vs.GetTypeId())
                        if not vft:
                            continue

                        new_vs = ViewSection.CreateSection(doc, vft.Id, new_bb)
                        if not new_vs:
                            continue

                        copy_section_view_settings(src_vs, new_vs)

                        try:
                            new_vs.Name = "{}_MIR".format(src_vs.Name)
                        except Exception:
                            pass

                        created_sections.append(new_vs.Id)

                    except Exception as ex:
                        run_errors.append(
                            "Section recreate ({}): {}".format(
                                src_vs.Id.IntegerValue, ex
                            )
                        )
                # >>> Counter 2: right after the creation loop (before commit)
                sections_created = len(created_sections)

                txs.Commit()

        except Exception as ex:
            run_errors.append("Section pass failed: {}".format(ex))

        tg.Assimilate()

    except Exception:
        # hard failure: close the transaction group
        try:
            tg.RollBack()
        except Exception:
            pass
        raise

    mirrored = len(created_ids)

    bbox_note = ""
    if created_bbox:
        cx = (created_bbox.Min.X + created_bbox.Max.X) * 0.5
        cy = (created_bbox.Min.Y + created_bbox.Max.Y) * 0.5
        cz = (created_bbox.Min.Z + created_bbox.Max.Z) * 0.5
        bbox_note = "\nNew bbox center: ({:.3f}, {:.3f}, {:.3f})".format(cx, cy, cz)

    sections_found = 0
    sections_created = 0
    try:
        sections_found = len(sec_candidates)
    except Exception:
        sections_found = 0
    try:
        sections_created = len(created_sections)
    except Exception:
        sections_created = 0

    note = ""
    if forced_copy:
        note = "\nNote: Target picked -> forced Copy mode for mapping+mirror."
    elif not do_copy:
        note = "\nNote: In-place mirror does not apply source->target mapping."

    cat_lines = ""
    if per_cat_fail:
        cat_lines = "\nPer-category failures:\n- " + "\n- ".join(
            ["{}: {}".format(k, v) for k, v in per_cat_fail.items()]
        )

    ex_lines = "None"
    if example_failures:
        ex_lines = "\n- " + "\n- ".join(example_failures[:10])

    result_payload = {
        "doc_identity": module_state.get_doc_identity(doc),
        "source_payload": MIRROR_INPUT_INFO.get("source_payload"),
        "created_ids": [element_id.IntegerValue for element_id in created_ids],
        "failed_ids": failed_element_ids,
        "skipped": combined_skipped,
        "command_mode": "create copy" if do_copy else "in-place mirror",
        "mirror_plane": {
            "origin": [plane.Origin.X, plane.Origin.Y, plane.Origin.Z],
            "normal": [plane.Normal.X, plane.Normal.Y, plane.Normal.Z],
            "mode": mode,
        },
        "category_counts": combined_breakdown,
    }
    result_path = module_state.save_named_payload(
        doc, result_payload, "mirror_created_latest.json"
    )
    print("MIRROR HANDLER RESULT ->")
    print("copyable input: {}".format(len(ids_in_src)))
    print("created: {}".format(len(created_ids)))
    print("failed: {}".format(len(failed_element_ids)))
    print("skipped: {}".format(len(combined_skipped)))
    print("created payload: {}".format(result_path))

    msg = (
        "Mode: {0}\nCopy mode: {1}\n"
        "Created: {2}\n"
        "Failures (rolled back / exceptions): {3}\n"
        "Skipped groups: {4}\n"
        "Assemblies expanded: {5}\n"
        "Hard skipped: {6}\n"
        "Plane normal: ({7:.3f},{8:.3f},{9:.3f}){10}\n"
        "Run error samples: {11}\n"
        "Groups found (by location): {12}\n"
        "Groups found (by member): {13}\n"
        "Groups merged (unique): {14}\n"
        "Sections found: {15}\n"
        "Sections created: {16}\n"
        "{17}"
        "{18}"
        "\nFailure examples:{19}\n"
    ).format(
        mode,
        "Create copy" if do_copy else "In-place",
        mirrored,
        failures,
        skipped_group_members,
        expanded_assembly,
        len(combined_skipped),
        plane.Normal.X,
        plane.Normal.Y,
        plane.Normal.Z,
        bbox_note,
        ("\n- " + "\n- ".join(run_errors[:10])) if run_errors else "None",
        groups_found_loc,
        groups_found_mem,
        groups_found_all,
        sections_found,
        sections_created,
        note,
        cat_lines,
        ex_lines,
    )
    TaskDialog.Show("mirror_handler", msg)


if __name__ == "__main__":
    main()
