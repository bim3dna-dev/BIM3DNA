# -*- coding: utf-8 -*-
__title__ = "Slope Solver"
__doc__ = """Version = 2.0
Date    = 17.01.2026
________________________________________________________________
Description:
Apply configured slopes to PIPE CURVES inside a picked scope box,
based on Pipe Type + MEPSystem.Name rules.

Rules (current):
- Pipe Type: NLRS_52_PI_PVC U3 Grijs OD_Dyka
  - MEPSystem contains "51 - VWA"  -> 7.5/1000  (0.0075)
  - MEPSystem contains "50 - HWA"  -> 5.0/1000  (0.0050)

Notes:
- Uses per-element transactions for MEP robustness.
- Uses FailurePreprocessor to rollback on non-warning failures.
- Calls doc.Regenerate() after applying slope and before Commit().
________________________________________________________________
Author: Emin Avdovic
"""

# ==================================================
# Imports (keep minimal, but compatible with your environment)
# ==================================================
from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    BoundingBoxIntersectsFilter,
    ElementId,
    FailureProcessingResult,
    FailureSeverity,
    FilteredElementCollector,
    IFailuresPreprocessor,
    Line,
    LocationCurve,
    Outline,
    SubTransaction,
    Transaction,
    TransactionGroup,
    View3D,
    ViewFamily,
    ViewFamilyType,
    XYZ,
)
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogResult
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException
from collections import defaultdict

from System.Collections.Generic import List as ClrList

import math
import os
import re
import sys


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
if _LIB_DIR and os.path.isdir(_LIB_DIR) and _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

try:
    import module_state
except Exception as ex:
    print("SCOPE MODIFIER IMPORT ERROR -> module_state could not be imported")
    print("script dir:", _THIS_DIR)
    print("extension dir:", _EXT_DIR)
    print("lib dir:", _LIB_DIR)
    print("error:", ex)
    raise

# one-time diagnostics
_SLOPE_DUMPED = False
_SLOPE_GEOM_DEBUG_DONE = False
_LAST_SCOPE_COLLECTION = {}

# ==================================================
# Revit Document Setup
# ==================================================
uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

# ==================================================
# Config
# ==================================================
DEBUG_NO_ROLLBACK = False
USE_SCOPEBOX_Z = False  # Default preference only; UI prompt overrides per run
HORIZ_MIN_FT = 10.0 / 304.8  # 10 mm
SCOPEBOX_Z_TOL_FT = 50.0 / 304.8  # 50 mm

# Match by substring (case-insensitive) within MEPSystem.Name
# SYS_SLOPE_RULES = [
#     ("51 - VWA 2", 0.0075),  # 5%
#     ("50 - HWA 4", 0.005),  # 7.5%
# ]
SLOPE_VWA = 0.0075  # 7.5/1000
SLOPE_HWA = 0.0050  # 5.0/1000

# Validation tolerance (ratio units, e.g. 0.0075 == 7.5/1000)
SLOPE_TOL = 1e-4


# ==================================================
# Selection
# ==================================================
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


class PipeSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            return elem.Category and elem.Category.Id.IntegerValue == int(
                BuiltInCategory.OST_PipeCurves
            )
        except Exception:
            return False

    def AllowReference(self, ref, point):
        return False


def pick_scopebox(prompt):
    try:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element, ScopeBoxSelectionFilter(), prompt
        )
        return doc.GetElement(ref.ElementId)
    except OperationCanceledException:
        return None


# ==================================================
# Geometry helpers (scope box intersection)
# ==================================================
def _to_local_xyz(Tinv, p):
    return Tinv.OfPoint(p)


def _bbox_intersects_scopebox(elem, sb):
    """AABB overlap in scope-box local coord (fast + robust)."""
    try:
        ebb = elem.get_BoundingBox(None)
        if not ebb:
            ebb = elem.get_BoundingBox(doc.ActiveView)

        sbb = sb.get_BoundingBox(None)
        if not ebb or not sbb or not sbb.Transform:
            return False

        Tinv = sbb.Transform.Inverse

        pts = [
            Tinv.OfPoint,
            (XYZ(ebb.Min.X, ebb.Min.Y, ebb.Min.Z)),
            Tinv.OfPoint,
            (XYZ(ebb.Min.X, ebb.Min.Y, ebb.Max.Z)),
            Tinv.OfPoint,
            (XYZ(ebb.Min.X, ebb.Max.Y, ebb.Min.Z)),
            Tinv.OfPoint,
            (XYZ(ebb.Min.X, ebb.Max.Y, ebb.Max.Z)),
            Tinv.OfPoint,
            (XYZ(ebb.Max.X, ebb.Min.Y, ebb.Min.Z)),
            Tinv.OfPoint,
            (XYZ(ebb.Max.X, ebb.Min.Y, ebb.Max.Z)),
            Tinv.OfPoint,
            (XYZ(ebb.Max.X, ebb.Max.Y, ebb.Min.Z)),
            Tinv.OfPoint,
            (XYZ(ebb.Max.X, ebb.Max.Y, ebb.Max.Z)),
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


def _local_aabb_in_scopebox(elem, sb):
    """Return (mn, mx) of elem bbox in scopebox local coordinates."""
    try:
        ebb = elem.get_BoundingBox(None)
        if not ebb:
            ebb = elem.get_BoundingBox(doc.ActiveView)
        sbb = sb.get_BoundingBox(None)
        if not ebb or not sbb or not sbb.Transform:
            return (None, None)
        Tinv = sbb.Transform.Inverse
        pts = [
            Tinv.OfPoint(XYZ(ebb.Min.X, ebb.Min.Y, ebb.Min.Z)),
            Tinv.OfPoint(XYZ(ebb.Min.X, ebb.Min.Y, ebb.Max.Z)),
            Tinv.OfPoint(XYZ(ebb.Min.X, ebb.Max.Y, ebb.Min.Z)),
            Tinv.OfPoint(XYZ(ebb.Min.X, ebb.Max.Y, ebb.Max.Z)),
            Tinv.OfPoint(XYZ(ebb.Max.X, ebb.Min.Y, ebb.Min.Z)),
            Tinv.OfPoint(XYZ(ebb.Max.X, ebb.Min.Y, ebb.Max.Z)),
            Tinv.OfPoint(XYZ(ebb.Max.X, ebb.Max.Y, ebb.Min.Z)),
            Tinv.OfPoint(XYZ(ebb.Max.X, ebb.Max.Y, ebb.Max.Z)),
        ]
        mn = XYZ(min(p.X for p in pts), min(p.Y for p in pts), min(p.Z for p in pts))
        mx = XYZ(max(p.X for p in pts), max(p.Y for p in pts), max(p.Z for p in pts))
        return (mn, mx)
    except Exception:
        return (None, None)


def _scopebox_outline_model(sb):
    sbb = sb.get_BoundingBox(None)
    if not sbb:
        sbb = sb.get_BoundingBox(doc.ActiveView)
    if not sbb:
        return None

    T = sbb.Transform
    mn = sbb.Min
    mx = sbb.Max

    # If transform exists, map 8 corners to model coords and rebuild AABB
    if T:
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
        mn_m = XYZ(min(p.X for p in pts), min(p.Y for p in pts), min(p.Z for p in pts))
        mx_m = XYZ(max(p.X for p in pts), max(p.Y for p in pts), max(p.Z for p in pts))
        return Outline(mn_m, mx_m)

    return Outline(mn, mx)


def _point_inside_scope_local(point, scope_bb, include_all_elev, tol_ft):
    try:
        local = scope_bb.Transform.Inverse.OfPoint(point)
        xy_inside = (
            local.X >= scope_bb.Min.X - tol_ft
            and local.X <= scope_bb.Max.X + tol_ft
            and local.Y >= scope_bb.Min.Y - tol_ft
            and local.Y <= scope_bb.Max.Y + tol_ft
        )
        if not xy_inside or include_all_elev:
            return xy_inside
        return local.Z >= scope_bb.Min.Z - tol_ft and local.Z <= scope_bb.Max.Z + tol_ft
    except Exception:
        return False


def _pipe_inside_or_intersecting_scope(pipe, sb, include_all_elev):
    scope_bb = sb.get_BoundingBox(None)
    if not scope_bb:
        scope_bb = sb.get_BoundingBox(doc.ActiveView)
    if not scope_bb:
        return False, "scope box bounding box unavailable"

    loc = getattr(pipe, "Location", None)
    if isinstance(loc, LocationCurve):
        try:
            curve = loc.Curve
            points = [
                curve.GetEndPoint(0),
                curve.GetEndPoint(1),
                curve.Evaluate(0.5, True),
            ]
            for point in points:
                if _point_inside_scope_local(
                    point, scope_bb, include_all_elev, SCOPEBOX_Z_TOL_FT
                ):
                    return True, "curve endpoint/midpoint inside scope"
        except Exception:
            pass

    local_mn, local_mx = _local_aabb_in_scopebox(pipe, sb)
    if local_mn is None or local_mx is None:
        return False, "pipe bounding box unavailable"
    xy_intersects = not (
        local_mx.X < scope_bb.Min.X
        or local_mn.X > scope_bb.Max.X
        or local_mx.Y < scope_bb.Min.Y
        or local_mn.Y > scope_bb.Max.Y
    )
    if not xy_intersects:
        return False, "outside scope XY"
    if include_all_elev:
        return True, "bounding box intersects scope XY"
    z_intersects = not (
        local_mx.Z < scope_bb.Min.Z - SCOPEBOX_Z_TOL_FT
        or local_mn.Z > scope_bb.Max.Z + SCOPEBOX_Z_TOL_FT
    )
    if z_intersects:
        return True, "bounding box intersects scope volume"
    return False, "outside scope Z"


# ==================================================
# Classification by Pipe Type + MEPSystem.Name
# ==================================================
def get_pipe_type_name(p):
    try:
        t = doc.GetElement(p.GetTypeId())
        if not t:
            return ""

        n = getattr(t, "Name", None)
        if n:
            return n

        pn = t.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if pn:
            s = pnAsString()
            if s:
                return s
    except Exception:
        pass
    return ""


def get_system_name(pipe):
    try:
        p = pipe.get_Parameter(BuiltInParameter.RBS_SYSTEM_NAME_PARAM)
        if p:
            return (p.AsString() or "").strip()
    except Exception:
        pass
    return ""


def _get_system_string(pipe):
    # prefer built-in parameters over MEPSystem object
    parts = []
    try:
        p = pipe.get_Parameter(BuiltInParameter.RBS_PIPING_SYSTEM_NAME_PARAM)
        if p:
            parts.append(p.AsString() or "")
    except Exception:
        pass
    try:
        if hasattr(BuiltInParameter, "RBS_PIPING_SYSTEM_ABBREVIATION_PARAM"):
            p = pipe.get_Parameter(
                BuiltInParameter.RBS_PIPING_SYSTEM_ABBREVIATION_PARAM
            )
            if p:
                parts.append(p.AsString() or "")
    except Exception:
        pass
    try:
        if hasattr(BuiltInParameter, "RBS_SYSTEM_CLASSIFICATION_PARAM"):
            p = pipe.get_Parameter(BuiltInParameter.RBS_SYSTEM_CLASSIFICATION_PARAM)
            if p:
                parts.append(p.AsValueString() or "")
    except Exception:
        pass
    try:
        sysobj = getattr(pipe, "MEPSystem", None)
        if sysobj:
            parts.append(getattr(sysobj, "Name", "") or "")
    except Exception:
        pass
    # join unique non-empty
    out = [s for s in parts if s]
    if not out:
        return ""
    return " | ".join(out)


def classify_target_slope(pipe):
    sysstr = _get_system_string(pipe)
    low = re.sub(r"\s+", " ", (sysstr or "").strip().lower())
    if "vwa" in low or "51 - vwa" in low:
        return (SLOPE_VWA, sysstr)
    if "hwa" in low or "50 - hwa" in low:
        return (SLOPE_HWA, sysstr)
    return (None, sysstr)


def classify_target_slope_details(pipe):
    sysstr = _get_system_string(pipe)
    low = re.sub(r"\s+", " ", (sysstr or "").strip().lower())
    if "vwa" in low or "51 - vwa" in low:
        return (SLOPE_VWA, sysstr, "VWA")
    if "hwa" in low or "50 - hwa" in low:
        return (SLOPE_HWA, sysstr, "HWA")
    return (None, sysstr, None)


# ==================================================
# Slope math + measurement
# ==================================================
def horiz_length(p0, p1):
    dx = p1.X - p0.X
    dy = p1.Y - p0.Y
    return math.sqrt(dx * dx + dy * dy)


def achieved_slope_ratio(elem):
    """Return abs(dZ)/horizontal_length, or None if not measurable"""
    try:
        loc = getattr(elem, "Location", None)
        if not isinstance(loc, LocationCurve):
            return None
        crv = loc.Curve
        p0 = crv.GetEndPoint(0)
        p1 = crv.GetEndPoint(1)
        hl = horiz_length(p0, p1)
        if hl < 1e-6:
            return None
        return abs(p1.Z - p0.Z) / hl
    except Exception:
        return None


def _param(elem, bip):
    try:
        p = elem.get_Parameter(bip)
        return p if p and not p.IsReadOnly else None
    except Exception:
        return None


def _try_set_slope_param(elem, slope_ratio):
    # Built-in candidates (guarded)
    for name in ["RBS_PIPE_SLOPE"]:
        if hasattr(BuiltInParameter, name):
            bip = getattr(BuiltInParameter, name, None)
            try:
                p = _param(elem, bip)
                if p:
                    p.Set(slope_ratio)
                    return True
            except Exception:
                pass

    # Fallback by parameter name (template/locale-dependent)
    for pname in ["Slope"]:
        try:
            p = elem.LookupParameter(pname)
            if p and not p.IsReadOnly:
                p.Set(slope_ratio)
                return True
        except Exception:
            pass
    return False


def _try_set_offsets_for_slope(elem, slope_ratio):
    loc = getattr(elem, "Location", None)
    if not isinstance(loc, LocationCurve):
        return False

    crv = loc.Curve
    p0 = crv.GetEndPoint(0)
    p1 = crv.GetEndPoint(1)

    hl = horiz_length(p0, p1)
    if hl < 1e-6:
        return False

    drop = slope_ratio * hl  # feet

    high_idx = 0 if p0.Z >= p1.Z else 1

    p_start = _param(elem, BuiltInParameter.RBS_START_OFFSET_PARAM)
    p_end = _param(elem, BuiltInParameter.RBS_END_OFFSET_PARAM)
    if not p_start or not p_end:
        return False

    start_off = p_start.AsDouble()
    end_off = p_end.AsDouble()

    if high_idx == 0:
        p_end.Set(start_off - drop)  # enforce
    else:
        p_start.Set(end_off - drop)  # enforce

    return True


def _connector_closest_to_point(pipe, pt):
    try:
        conns = pipe.ConnectorManager.Connectors
    except Exception:
        return None
    best = None
    best_d = None
    for c in conns:
        try:
            d = c.Origin.DistanceTo(pt)
            if best is None or d < best_d:
                best = c
                best_d = d
        except Exception:
            pass
    return best


def _conn_ref_count(conn):
    try:
        return len(list(conn.AllRefs))
    except Exception:
        return 0


def _connector_is_anchor(conn):
    # Anchor if connected to non-pipe, or connected to vertical-ish pipe
    try:
        for r in conn.AllRefs:
            try:
                owner = r.Owner
                if not owner or not owner.IsValidObject:
                    continue
                if owner.Category and owner.Category.Id.IntegerValue == int(
                    BuiltInCategory.OST_PipeCurves
                ):
                    loc = getattr(owner, "Location", None)
                    if isinstance(loc, LocationCurve):
                        crv = loc.Curve
                        if crv:
                            p0 = crv.GetEndPoint(0)
                            p1 = crv.GetEndPoint(1)
                            if horiz_length(p0, p1) < HORIZ_MIN_FT:
                                return True
                else:
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def _select_end_to_move(pipe, p0, p1):
    c0 = _connector_closest_to_point(pipe, p0)
    c1 = _connector_closest_to_point(pipe, p1)
    r0 = _conn_ref_count(c0) if c0 else 0
    r1 = _conn_ref_count(c1) if c1 else 0
    a0 = _connector_is_anchor(c0) if c0 else False
    a1 = _connector_is_anchor(c1) if c1 else False
    if a0 and not a1:
        return 1
    if a1 and not a0:
        return 0
    if r0 < r1:
        return 0
    if r1 < r0:
        return 1
    # tie: move the lower end to keep high end
    return 0 if p0.Z <= p1.Z else 1


def _apply_slope_by_geometry(elem, slope_ratio):
    global _SLOPE_GEOM_DEBUG_DONE
    loc = getattr(elem, "Location", None)
    if not isinstance(loc, LocationCurve):
        return False
    crv = loc.Curve
    if crv is None:
        return False
    p0 = crv.GetEndPoint(0)
    p1 = crv.GetEndPoint(1)
    hl = horiz_length(p0, p1)
    if hl < HORIZ_MIN_FT:
        return False
    dz = slope_ratio * hl
    idx = _select_end_to_move(elem, p0, p1)
    if idx == 0:
        new_p0 = XYZ(p0.X, p0.Y, p1.Z - dz)
        loc.Curve = Line.CreateBound(new_p0, p1)
    else:
        new_p1 = XYZ(p1.X, p1.Y, p0.Z - dz)
        loc.Curve = Line.CreateBound(p0, new_p1)

    if not _SLOPE_GEOM_DEBUG_DONE:
        _SLOPE_GEOM_DEBUG_DONE = True
        label = "{:.1f}/1000".format(slope_ratio * 1000.0)
        print("DEBUG: geom slope sample pipe {}".format(elem.Id.IntegerValue))
        print("  before p0={}, p1={}".format(p0, p1))
        print("  horiz_run_ft={:.6f}, dz_ft={:.6f}".format(hl, dz))
        print(
            "  after p0={}, p1={}".format(
                loc.Curve.GetEndPoint(0), loc.Curve.GetEndPoint(1)
            )
        )
        print("  target label={}".format(label))

    return True


def _get_slope_param_value(elem):
    for name in ["RBS_PIPE_SLOPE"]:
        if hasattr(BuiltInParameter, name):
            bip = getattr(BuiltInParameter, name, None)
            try:
                p = elem.get_Parameter(bip)
                if p:
                    return p.AsDouble()
            except Exception:
                pass
    for pname in ["Slope"]:
        try:
            p = elem.LookupParameter(pname)
            if p:
                return p.AsDouble()
        except Exception:
            pass
    return None


def _offset_slope_ratio(elem):
    try:
        loc = getattr(elem, "Location", None)
        if not isinstance(loc, LocationCurve):
            return None
        crv = loc.Curve
        if crv is None:
            return None
        p0 = crv.GetEndPoint(0)
        p1 = crv.GetEndPoint(1)
        hl = horiz_length(p0, p1)
        if hl < 1e-6:
            return None
        p_start = _param(elem, BuiltInParameter.RBS_START_OFFSET_PARAM)
        p_end = _param(elem, BuiltInParameter.RBS_END_OFFSET_PARAM)
        if not p_start or not p_end:
            return None
        start_off = p_start.AsDouble()
        end_off = p_end.AsDouble()
        return abs(start_off - end_off) / hl
    except Exception:
        return None


def set_curve_slope_keep_high_end(elem, slope_ratio):
    loc = getattr(elem, "Location", None)
    if not isinstance(loc, LocationCurve):
        raise Exception("No LocationCurve")

    crv = loc.Curve
    p0 = crv.GetEndPoint(0)
    p1 = crv.GetEndPoint(1)

    hl = horiz_length(p0, p1)
    if hl < 1e-6:
        raise Exception("Too short (horiz length ~0)")

    drop = slope_ratio * hl  # feet

    if p0.Z >= p1.Z:
        high = p0
        low = p1
        high_idx = 0
    else:
        high = p1
        low = p0
        high_idx = 1

    new_low = XYZ(low.X, low.Y, high.Z - drop)

    if high_idx == 0:
        loc.Curve = Line.CreateBound(high, new_low)
    else:
        loc.Curve = Line.CreateBound(new_low, high)


def is_too_short(elem):
    try:
        loc = elem.Location
        if not isinstance(loc, LocationCurve):
            return True
        crv = loc.Curve
        if crv is None:
            return True
        # Revit tolerance in feet
        tol = doc.Application.ShortCurveTolerance
        if crv.Length < tol * 1.5:
            return True
        p0 = crv.GetEndPoint(0)
        p1 = crv.GetEndPoint(1)
        if horiz_length(p0, p1) < 1e-6:
            return True
        return False
    except Exception:
        return True


def apply_slope_mep_safe(elem, slope_ratio):
    if _try_set_slope_param(elem, slope_ratio):
        return (True, "slope_param")
    if _try_set_offsets_for_slope(elem, slope_ratio):
        return (True, "offsets")
    try:
        set_curve_slope_keep_high_end(elem, slope_ratio)
        return (True, "rewrite_curve")
    except Exception:
        pass
    if _apply_slope_by_geometry(elem, slope_ratio):
        return (True, "rewrite_curve")
    return (False, "rewrite_curve")


def apply_slope_disconnect_retry(elem, slope_ratio):
    # Prefer curve rewrite when disconnected
    try:
        set_curve_slope_keep_high_end(elem, slope_ratio)
        return (True, "disconnect_retry")
    except Exception:
        pass
    if _apply_slope_by_geometry(elem, slope_ratio):
        return (True, "disconnect_retry")
    if _try_set_offsets_for_slope(elem, slope_ratio):
        return (True, "disconnect_retry")
    if _try_set_slope_param(elem, slope_ratio):
        return (True, "disconnect_retry")
    return (False, "disconnect_retry")


# ==================================================
# Failure handling
# ==================================================


class RollbackOnMEPFailures(IFailuresPreprocessor):
    def __init__(self, err_log):
        self.err_log = err_log

    def PreprocessFailures(self, fa):
        try:
            msgs = fa.GetFailureMessages()
            for m in msgs:
                if m.GetSeverity() == FailureSeverity.Warning:
                    try:
                        self.err_log.append(
                            "Warning: {}".format(m.GetDescriptionText())
                        )
                    except Exception:
                        self.err_log.append("Warning (no description).")
                    fa.DeleteWarning(m)
                else:
                    try:
                        self.err_log.append("Error: {}".format(m.GetDescriptionText()))
                    except Exception:
                        self.err_log.append("Error (no description).")
                    return FailureProcessingResult.ProceedWithRollBack
            return FailureProcessingResult.Continue
        except Exception:
            return FailureProcessingResult.ProceedWithRollBack


class LogFailuresPreprocessor(IFailuresPreprocessor):
    def __init__(self, err_log):
        self.err_log = err_log

    def PreprocessFailures(self, fa):
        try:
            msgs = fa.GetFailureMessages()
            for m in msgs:
                try:
                    self.err_log.append(m.GetDescriptionText())
                except Exception:
                    self.err_log.append("Failure message (no description).")
            return FailureProcessingResult.Continue
        except Exception:
            return FailureProcessingResult.Continue


def set_failure_opts(tx, err_log):
    if DEBUG_NO_ROLLBACK:
        # Log only, do not delete warnings or force rollback
        opts = tx.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(LogFailuresPreprocessor(err_log))
        tx.SetFailureHandlingOptions(opts)
        return
    opts = tx.GetFailureHandlingOptions()
    opts.SetFailuresPreprocessor(RollbackOnMEPFailures(err_log))
    opts.SetClearAfterRollback(True)
    tx.SetFailureHandlingOptions(opts)


# ==================================================
# CONNECTIVITY
# ===================================================


def _connector_ids_for_elem(elem):
    out = set()
    try:
        cm = elem.ConnectorManager
    except Exception:
        return out

    try:
        conns = cm.Connectors
    except Exception:
        return out

    for c in conns:
        try:
            refs = c.AllRefs
        except Exception:
            continue
        for r in refs:
            try:
                owner = r.Owner
                if owner and owner.Id and owner.Id != elem.Id:
                    out.add(owner.Id.IntegerValue)
            except Exception:
                pass
    return out


def _end_connected_ids(pipe):
    # return ids of elements connected at ends
    ids = set()
    try:
        conns = _get_end_connectors(pipe)
    except Exception:
        return []
    for c in conns:
        try:
            for r in c.AllRefs:
                try:
                    if r.Owner and r.Owner.Id and r.Owner.Id != pipe.Id:
                        ids.add(r.Owner.Id.IntegerValue)
                except Exception:
                    pass
        except Exception:
            pass
    return sorted(list(ids))


def _is_connectivity_failure(text):
    if not text:
        return False
    low = text.lower()
    keys = [
        "connect",
        "connection",
        "network",
        "constraint",
        "cannot",
        "can't",
        "route",
        "system",
        "mep",
    ]
    return any(k in low for k in keys)


def _get_end_connectors(pipe):
    ends = []
    try:
        conns = pipe.ConnectorManager.Connectors
    except Exception:
        return ends
    for c in conns:
        try:
            if c.ConnectorType.ToString().lower() == "end":
                ends.append(c)
        except Exception:
            pass
    if not ends:
        try:
            return list(conns)
        except Exception:
            return []
    return ends


def _disconnect_all(pipe):
    pairs = []
    try:
        conns = _get_end_connectors(pipe)
    except Exception:
        return pairs

    for c in conns:
        try:
            refs = list(c.AllRefs)
        except Exception:
            continue
        for r in refs:
            try:
                if r and r.Owner and r.Owner.Id != pipe.Id:
                    try:
                        c.DisconnectFrom(r)
                        pairs.append((c, r))
                    except Exception:
                        pass
            except Exception:
                pass
    return pairs


def _reconnect_pairs(pairs):
    failed = 0
    for c, r in pairs:
        try:
            c.ConnectTo(r)
        except Exception:
            failed += 1
    return failed


def _try_disconnect_apply_reconnect(pipe, slope_ratio):
    pairs = _disconnect_all(pipe)
    disconnect_ok = True if pairs is not None else False
    ok, method = apply_slope_disconnect_retry(pipe, slope_ratio)
    try:
        doc.Regenerate()
    except Exception:
        pass
    failed_reconnects = _reconnect_pairs(pairs) if pairs else 0
    reconnect_ok = failed_reconnects == 0
    if not ok:
        return (False, method, "disconnect_required", disconnect_ok, reconnect_ok)
    if failed_reconnects > 0:
        return (True, method, "reconnect_failed", disconnect_ok, reconnect_ok)
    return (True, method, None, disconnect_ok, reconnect_ok)


def build_components(pipes):
    by_id = {p.Id.IntegerValue: p for p in pipes}

    adj = {}
    for pid, p in by_id.items():
        nbrs = _connector_ids_for_elem(p)
        adj[pid] = [nid for nid in nbrs if nid in by_id]

    seen = set()
    comps = []

    for pid in by_id:
        if pid in seen:
            continue
        stack = [pid]
        seen.add(pid)
        comp_ids = []
        while stack:
            cur = stack.pop()
            comp_ids.append(cur)
            for nb in adj.get(cur, []):
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        comps.append([by_id[i] for i in comp_ids])

    return comps


# ==================================================
# COLLECTION
# ==================================================


def collect_target_pipes_in_scopebox(sb):
    global _LAST_SCOPE_COLLECTION
    outline = _scopebox_outline_model(sb)
    if not outline:
        print("DEBUG: scope box has no bounding box; cannot build outline.")
        _LAST_SCOPE_COLLECTION = {
            "bbox_candidates": 0,
            "accepted": 0,
            "rejected_scope": 0,
        }
        return []

    # XY-only outline unless strict Z is requested
    if USE_SCOPEBOX_Z:
        bb_filter = BoundingBoxIntersectsFilter(outline)
    else:
        INF = 1e9
        xy_outline = Outline(
            XYZ(outline.MinimumPoint.X, outline.MinimumPoint.Y, -INF),
            XYZ(outline.MaximumPoint.X, outline.MaximumPoint.Y, INF),
        )
        bb_filter = BoundingBoxIntersectsFilter(xy_outline)

    pipes = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_PipeCurves)
        .WhereElementIsNotElementType()
        .WherePasses(bb_filter)
        .ToElements()
    )

    inside = []
    rejected_scope = 0
    acceptance_reasons = defaultdict(int)
    for p in pipes:
        accepted, reason = _pipe_inside_or_intersecting_scope(p, sb, not USE_SCOPEBOX_Z)
        acceptance_reasons[reason] += 1
        if accepted:
            inside.append(p)
        else:
            rejected_scope += 1
    try:
        scope_type_id = sb.GetTypeId().IntegerValue
    except Exception:
        scope_type_id = None
    print(
        "SLOPE SOLVER DEBUG -> scope box id: {}; scope box type id: {}; "
        "pipe candidates by bbox: {}; pipe candidates inside/intersecting scope: {}; "
        "pipe rejected by scope volume: {}; decisions: {}".format(
            sb.Id.IntegerValue,
            scope_type_id,
            len(pipes),
            len(inside),
            rejected_scope,
            dict(acceptance_reasons),
        )
    )
    _LAST_SCOPE_COLLECTION = {
        "bbox_candidates": len(pipes),
        "accepted": len(inside),
        "rejected_scope": rejected_scope,
    }
    return inside


def _chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _level_info(pipe):
    # Try start level param first
    try:
        p = pipe.get_Parameter(BuiltInParameter.RBS_START_LEVEL_PARAM)
        if p:
            lvl_id = p.AsElementId()
            lvl = doc.GetElement(lvl_id)
            if lvl:
                return (lvl.Name, lvl.Id.IntegerValue)
    except Exception:
        pass
    # Fallback: Reference Level parameter
    try:
        p = pipe.LookupParameter("Reference Level")
        if p and p.AsElementId():
            lvl = doc.GetElement(p.AsElementId())
            if lvl:
                return (lvl.Name, lvl.Id.IntegerValue)
    except Exception:
        pass
    return ("<None>", None)


def _level_elevation(pipe):
    try:
        p = pipe.get_Parameter(BuiltInParameter.RBS_START_LEVEL_PARAM)
        if p:
            lvl = doc.GetElement(p.AsElementId())
            if lvl:
                return getattr(lvl, "Elevation", 0.0)
    except Exception:
        pass
    return 0.0


def _bbox_minmax_z(elem):
    try:
        bb = elem.get_BoundingBox(None)
        if bb:
            return (bb.Min.Z, bb.Max.Z)
    except Exception:
        pass
    return (None, None)


def _get_system_type_id(pipe):
    try:
        p = pipe.get_Parameter(BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
        if p:
            return p.AsElementId()
    except Exception:
        pass
    return ElementId.InvalidElementId


def _param_state(elem, bip):
    if bip is None:
        return (False, False, None)
    try:
        p = elem.get_Parameter(bip)
        if not p:
            return (False, False, None)
        return (True, not p.IsReadOnly, p.AsDouble())
    except Exception:
        return (False, False, None)


def _param_state_by_name(elem, name):
    try:
        p = elem.LookupParameter(name)
        if not p:
            return (False, False, None)
        return (True, not p.IsReadOnly, p.AsDouble())
    except Exception:
        return (False, False, None)


def _pipe_diag(pipe):
    sysname = _get_system_string(pipe)
    sys_type_id = _get_system_type_id(pipe)
    loc = getattr(pipe, "Location", None)
    has_loc = isinstance(loc, LocationCurve)
    horiz = None
    length = None
    below_tol = None
    if has_loc:
        crv = loc.Curve
        if crv:
            p0 = crv.GetEndPoint(0)
            p1 = crv.GetEndPoint(1)
            horiz = horiz_length(p0, p1)
            length = crv.Length
            try:
                below_tol = length < doc.Application.ShortCurveTolerance * 1.5
            except Exception:
                below_tol = None

    slope_param = _param_state(pipe, getattr(BuiltInParameter, "RBS_PIPE_SLOPE", None))
    start_off = _param_state(pipe, BuiltInParameter.RBS_START_OFFSET_PARAM)
    end_off = _param_state(pipe, BuiltInParameter.RBS_END_OFFSET_PARAM)
    slope_lookup = _param_state_by_name(pipe, "Slope")

    return {
        "id": pipe.Id.IntegerValue,
        "name": getattr(pipe, "Name", ""),
        "pinned": getattr(pipe, "Pinned", False),
        "workset": getattr(pipe, "WorksetId", ElementId.InvalidElementId).IntegerValue,
        "view_specific": getattr(pipe, "ViewSpecific", False),
        "system": sysname,
        "system_type_id": (
            sys_type_id.IntegerValue
            if sys_type_id and sys_type_id != ElementId.InvalidElementId
            else None
        ),
        "has_loc": has_loc,
        "horiz_ft": horiz,
        "horiz_mm": horiz * 304.8 if horiz is not None else None,
        "curve_len_ft": length,
        "below_tol": below_tol,
        "achieved": achieved_slope_ratio(pipe),
        "slope_param": slope_param,
        "start_off": start_off,
        "end_off": end_off,
        "slope_lookup": slope_lookup,
    }


def _slope_readonly(diag):
    # slope param present but read-only, and offsets read-only or missing
    sp = diag.get("slope_param", (False, False, None))
    so = diag.get("start_off", (False, False, None))
    eo = diag.get("end_off", (False, False, None))
    slope_ro = sp[0] and (not sp[1])
    offsets_ro = (so[0] and (not so[1])) and (eo[0] and (not eo[1]))
    return slope_ro and offsets_ro


def dump_slope_params_once(sample_pipe):
    global _SLOPE_DUMPED
    if _SLOPE_DUMPED or not sample_pipe:
        return
    _SLOPE_DUMPED = True
    try:
        print(
            "DEBUG: slope parameter candidates for pipe {}".format(
                sample_pipe.Id.IntegerValue
            )
        )
        for p in sample_pipe.Parameters:
            try:
                pname = p.Definition.Name
                if "slope" in pname.lower():
                    st = p.StorageType.ToString()
                    ro = p.IsReadOnly
                    val = None
                    try:
                        if p.StorageType.ToString() == "Double":
                            val = p.AsDouble()
                        else:
                            val = p.AsValueString()
                    except Exception:
                        val = None
                    print(
                        "  - {} | StorageType={} | ReadOnly={} | Value={}".format(
                            pname, st, ro, val
                        )
                    )
            except Exception:
                pass
    except Exception:
        pass


def _lookup_params(pipe, names):
    out = []
    for n in names:
        try:
            p = pipe.LookupParameter(n)
            if p:
                out.append(p)
        except Exception:
            pass
    return out


def _param_display(p):
    try:
        st = p.StorageType.ToString()
        ro = p.IsReadOnly
        val = None
        try:
            if st == "Double":
                val = p.AsDouble()
            else:
                val = p.AsValueString()
        except Exception:
            val = None
        return "name='{}' st={} ro={} val={}".format(p.Definition.Name, st, ro, val)
    except Exception:
        return "param=<?>"


def evaluate_pipe_selection(pipe, sb, include_all_elev):
    # order: XY, Z, type, pinned, location, too short, near vertical, rule
    sb_bb = sb.get_BoundingBox(None)
    local_mn, local_mx = _local_aabb_in_scopebox(pipe, sb)
    if local_mn is None or local_mx is None or not sb_bb:
        return (False, "OUTSIDE_SCOPEBOX_XY")

    sb_mn = sb_bb.Min
    sb_mx = sb_bb.Max
    xy_pass = not (
        local_mx.X < sb_mn.X
        or local_mn.X > sb_mx.X
        or local_mx.Y < sb_mn.Y
        or local_mn.Y > sb_mx.Y
    )
    if not xy_pass:
        return (False, "OUTSIDE_SCOPEBOX_XY")

    z_pass = not (
        local_mx.Z < (sb_mn.Z - SCOPEBOX_Z_TOL_FT)
        or local_mn.Z > (sb_mx.Z + SCOPEBOX_Z_TOL_FT)
    )
    if (not include_all_elev) and (not z_pass):
        return (False, "OUTSIDE_SCOPEBOX_Z")

    try:
        if getattr(pipe, "Pinned", False):
            return (False, "PINNED")
    except Exception:
        pass

    loc = getattr(pipe, "Location", None)
    if not isinstance(loc, LocationCurve):
        return (False, "NO_LOCATION")

    if is_too_short(pipe):
        return (False, "TOO_SHORT")

    try:
        crv = loc.Curve
        if crv:
            p0 = crv.GetEndPoint(0)
            p1 = crv.GetEndPoint(1)
            if horiz_length(p0, p1) < HORIZ_MIN_FT:
                return (False, "NEAR_VERTICAL")
    except Exception:
        pass

    target, sysname, rule = classify_target_slope_details(pipe)
    if target is None:
        return (False, "NO_RULE")

    return (True, "SELECTED")


def force_apply_pipe(pipe, target, err_log):
    # Attempt geometry first, then offsets
    ok = False
    method = "rewrite_curve"
    try:
        ok = _apply_slope_by_geometry(pipe, target)
    except Exception:
        ok = False
    if ok:
        return (True, method)

    try:
        ok = _try_set_offsets_for_slope(pipe, target)
        if ok:
            return (True, "offsets")
    except Exception:
        pass

    return (False, "force_failed")


def _pipe_length(pipe):
    try:
        loc = getattr(pipe, "Location", None)
        if isinstance(loc, LocationCurve) and loc.Curve:
            return loc.Curve.Length
    except Exception:
        pass
    return 0.0


def _unique_view_name(base):
    try:
        names = set()
        for v in FilteredElementCollector(doc).OfClass(View3D).ToElements():
            try:
                names.add(v.Name)
            except Exception:
                pass
        if base not in names:
            return base
        i = 1
        while True:
            n = "{}_{}".format(base, i)
            if n not in names:
                return n
            i += 1
    except Exception:
        return base


# ==================================================
# MAIN
# ==================================================
def main():
    sb = pick_scopebox("Pick scope box (slope solving region)")
    if not sb:
        TaskDialog.Show("slope_solver", "Cancelled by user (no scope box picked).")
        return

    # User option: include all elevations (ignore Z) vs strict Z overlap
    td = TaskDialog("slope_solver")
    td.MainInstruction = "Include all elevations (ignore scope box Z)? (Recommended)"
    td.MainContent = (
        "Yes = use XY-only selection. No = strict Z overlap with tolerance."
    )
    td.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
    res = td.Show()
    include_all_elev = res == TaskDialogResult.Yes
    use_scopebox_z = not include_all_elev

    # override default with UI choice
    global USE_SCOPEBOX_Z
    USE_SCOPEBOX_Z = use_scopebox_z

    candidates = collect_target_pipes_in_scopebox(sb)
    if not candidates:
        if _LAST_SCOPE_COLLECTION.get("bbox_candidates", 0) > 0:
            no_pipe_message = (
                "PipeCurves were found near the scope box, but none passed the "
                "exact scope-volume test. Check scope box rotation, crop extents, "
                "and tolerance."
            )
        else:
            no_pipe_message = (
                "No PipeCurves found inside or intersecting the selected scope box."
            )
        TaskDialog.Show(
            "slope_solver",
            no_pipe_message,
        )
        return

    # One-time dump of slope-related parameters for a sample pipe
    dump_slope_params_once(candidates[0] if candidates else None)

    # Single-pipe forensic mode
    try:
        td_f = TaskDialog("slope_solver")
        td_f.MainInstruction = "Single-pipe forensic mode?"
        td_f.MainContent = "Yes = pick a pipe and print a diagnostic block."
        td_f.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
        if td_f.Show() == TaskDialogResult.Yes:
            ref = uidoc.Selection.PickObject(
                ObjectType.Element, PipeSelectionFilter(), "Pick a Pipe for forensic"
            )
            fp = doc.GetElement(ref.ElementId)
            if fp:
                lvl_name, lvl_id = _level_info(fp)
                ptype = doc.GetElement(fp.GetTypeId())
                sysname = _get_system_string(fp)
                sysobj = getattr(fp, "MEPSystem", None)
                sys_type = None
                try:
                    if sysobj:
                        sys_type = doc.GetElement(sysobj.GetTypeId())
                except Exception:
                    sys_type = None
                target, sysname2, rule = classify_target_slope_details(fp)
                loc = getattr(fp, "Location", None)
                p0 = p1 = None
                run_xy = rise = slope_val = None
                if isinstance(loc, LocationCurve) and loc.Curve:
                    p0 = loc.Curve.GetEndPoint(0)
                    p1 = loc.Curve.GetEndPoint(1)
                    run_xy = horiz_length(p0, p1)
                    rise = abs(p1.Z - p0.Z)
                    slope_val = (rise / run_xy) if run_xy and run_xy > 1e-9 else None

                selected, reason = evaluate_pipe_selection(fp, sb, include_all_elev)
                print("=== FORENSIC PIPE DIAGNOSTIC ===")
                print(
                    "Pipe id={} name='{}' level='{}' lvl_id={}".format(
                        fp.Id.IntegerValue, getattr(fp, "Name", ""), lvl_name, lvl_id
                    )
                )
                print(
                    "Pipe type='{}' type_id={}".format(
                        getattr(ptype, "Name", ""), fp.GetTypeId().IntegerValue
                    )
                )
                print(
                    "MEPSystem={} SystemType={} SystemName='{}' rule_match='{}' target={}".format(
                        getattr(sysobj, "Name", None),
                        getattr(sys_type, "Name", None) if sys_type else None,
                        sysname,
                        rule,
                        target,
                    )
                )
                print("Rule matching string='{}'".format(sysname2))
                print(
                    "Geometry p0={}, p1={}, run_xy={}, rise={}, slope={}".format(
                        p0, p1, run_xy, rise, slope_val
                    )
                )
                print("Selected={} first_filter={}".format(selected, reason))
                print("Parameters:")
                for pname in [
                    "Slope",
                    "Upper End Centerline Elevation",
                    "Lower End Centerline Elevation",
                    "Upper End Invert Elevation",
                    "Lower End Invert Elevation",
                    "Start Offset",
                    "End Offset",
                ]:
                    for p in _lookup_params(fp, [pname]):
                        print("  - {}".format(_param_display(p)))
                for p in [
                    fp.get_Parameter(BuiltInParameter.RBS_START_OFFSET_PARAM),
                    fp.get_Parameter(BuiltInParameter.RBS_END_OFFSET_PARAM),
                ]:
                    if p:
                        print("  - {}".format(_param_display(p)))
                # Connector diagnostics
                try:
                    conns = _get_end_connectors(fp)
                    for c in conns:
                        try:
                            conn_ids = []
                            for r in c.AllRefs:
                                try:
                                    if r.Owner and r.Owner.Id and r.Owner.Id != fp.Id:
                                        conn_ids.append(
                                            "{}({})".format(
                                                r.Owner.Id.IntegerValue,
                                                (
                                                    r.Owner.Category.Name
                                                    if r.Owner.Category
                                                    else "<?>"
                                                ),
                                            )
                                        )
                                except Exception:
                                    pass
                            print(
                                "Conn: is_connected={} originZ={} connected_to={}".format(
                                    c.IsConnected,
                                    c.Origin.Z,
                                    conn_ids,
                                )
                            )
                        except Exception:
                            pass
                except Exception:
                    pass

                td_a = TaskDialog("slope_solver")
                td_a.MainInstruction = "Force apply to this pipe (debug)?"
                td_a.CommonButtons = (
                    TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
                )
                if td_a.Show() == TaskDialogResult.Yes and target is not None:
                    txf = Transaction(doc, "slope_solver: forensic apply")
                    txf.Start()
                    ferr = []
                    set_failure_opts(txf, ferr)
                    try:
                        okf, meth = force_apply_pipe(fp, target, ferr)
                        doc.Regenerate()
                        stf = txf.Commit()
                        print(
                            "Force apply result ok={} method={} tx={}".format(
                                okf, meth, stf.ToString()
                            )
                        )
                    except Exception as exf:
                        try:
                            txf.RollBack()
                        except Exception:
                            pass
                        print("Force apply exception: {}".format(repr(exf)))
                        print(
                            "Failures: {}".format("; ".join(ferr) if ferr else "None")
                        )
                    # re-read geometry
                    try:
                        loc = fp.Location
                        crv = loc.Curve
                        p0b = crv.GetEndPoint(0)
                        p1b = crv.GetEndPoint(1)
                        run_xyb = horiz_length(p0b, p1b)
                        riseb = abs(p1b.Z - p0b.Z)
                        slopeb = (
                            (riseb / run_xyb) if run_xyb and run_xyb > 1e-9 else None
                        )
                        print(
                            "After apply: p0={}, p1={}, run_xy={}, rise={}, slope={}".format(
                                p0b, p1b, run_xyb, riseb, slopeb
                            )
                        )
                    except Exception:
                        pass
    except Exception:
        pass

    # Diagnostics (pre-execution)
    sys_counts = defaultdict(int)
    target_counts = defaultdict(int)
    target_samples = defaultdict(list)
    vwa_wrong_slope_ids = []
    vwa_wrong_details = []
    unmatched_rules = 0
    level_counts = defaultdict(
        lambda: {"candidates": 0, "changed": 0, "failed": 0, "skipped": 0}
    )
    for p in candidates:
        sysname = _get_system_string(p)
        sys_counts[sysname] += 1
        target, _ = classify_target_slope(p)
        if target is None:
            unmatched_rules += 1
            print(
                "DEBUG: why unmatched pipe {} sysstr='{}'".format(
                    p.Id.IntegerValue, sysname
                )
            )
        else:
            target_counts[target] += 1
            if len(target_samples[target]) < 10:
                target_samples[target].append(p.Id.IntegerValue)
        lvl_name, lvl_id = _level_info(p)
        level_counts[lvl_name]["candidates"] += 1

    print("DEBUG: total candidates =", len(candidates))
    print("DEBUG: system counts (incl. empty):")
    for k, v in sorted(sys_counts.items(), key=lambda x: x[0] or ""):
        print("  '{}' : {}".format(k, v))
    print("DEBUG: unmatched system rules =", unmatched_rules)
    print("DEBUG: target buckets:")
    for k, v in target_counts.items():
        print(
            "  target {:.6f}: count={} samples={}".format(k, v, target_samples.get(k))
        )
    print(
        "DEBUG: slopes VWA={:.6f} ({:.1f}/1000), HWA={:.6f} ({:.1f}/1000)".format(
            SLOPE_VWA,
            SLOPE_VWA * 1000.0,
            SLOPE_HWA,
            SLOPE_HWA * 1000.0,
        )
    )

    # Preflight skip logic
    skipped_too_short = 0
    skipped_pinned = 0
    skipped_no_location = 0
    skipped_no_rule = 0
    skipped_near_vertical = 0

    to_process = []
    failure_samples = []
    skipped_ids = []
    for p in candidates:
        lvl_name, lvl_id = _level_info(p)
        minz, maxz = _bbox_minmax_z(p)
        sb_bb = sb.get_BoundingBox(None)
        local_mn, local_mx = _local_aabb_in_scopebox(p, sb)
        if local_mn is None or local_mx is None or not sb_bb:
            xy_pass = False
            z_pass = False
        else:
            sb_mn = sb_bb.Min
            sb_mx = sb_bb.Max
            xy_pass = not (
                local_mx.X < sb_mn.X
                or local_mn.X > sb_mx.X
                or local_mx.Y < sb_mn.Y
                or local_mn.Y > sb_mx.Y
            )
            z_pass = not (
                local_mx.Z < (sb_mn.Z - SCOPEBOX_Z_TOL_FT)
                or local_mn.Z > (sb_mx.Z + SCOPEBOX_Z_TOL_FT)
            )
        selected = xy_pass and (include_all_elev or z_pass)
        print(
            "DEBUG: pipe {} sys='{}' level='{}' lvl_id={} bboxZ=({}, {}) scopeZ=({}, {}) xy_pass={} z_pass={} selected={}".format(
                p.Id.IntegerValue,
                _get_system_string(p),
                lvl_name,
                lvl_id,
                minz,
                maxz,
                sb_bb.Min.Z if sb_bb else None,
                sb_bb.Max.Z if sb_bb else None,
                xy_pass,
                z_pass,
                selected,
            )
        )

        if (not selected) and (not include_all_elev) and (not z_pass):
            skipped_no_rule += 0
            skipped_near_vertical += 0
            skipped_no_location += 0
            skipped_too_short += 0
            skipped_pinned += 0
            level_counts[lvl_name]["skipped"] += 1
            if len(failure_samples) < 30:
                failure_samples.append(
                    "Pipe {} (sys='{}') method=skipped_outside_z reason=OUTSIDE_SCOPEBOX_Z".format(
                        p.Id.IntegerValue, _get_system_string(p)
                    )
                )
            skipped_ids.append(p.Id)
            continue

        if not selected:
            level_counts[lvl_name]["skipped"] += 1
            if len(failure_samples) < 30:
                failure_samples.append(
                    "Pipe {} (sys='{}') method=skipped_xy reason=OUTSIDE_SCOPEBOX_XY".format(
                        p.Id.IntegerValue, _get_system_string(p)
                    )
                )
            skipped_ids.append(p.Id)
            continue

        try:
            if getattr(p, "Pinned", False):
                skipped_pinned += 1
                level_counts[lvl_name]["skipped"] += 1
                if len(failure_samples) < 30:
                    failure_samples.append(
                        "Pipe {} (sys='{}') method=skipped_pinned reason=PINNED".format(
                            p.Id.IntegerValue, _get_system_string(p)
                        )
                    )
                skipped_ids.append(p.Id)
                continue
        except Exception:
            pass

        loc = getattr(p, "Location", None)
        if not isinstance(loc, LocationCurve):
            skipped_no_location += 1
            level_counts[lvl_name]["skipped"] += 1
            if len(failure_samples) < 30:
                failure_samples.append(
                    "Pipe {} (sys='{}') method=skipped_no_location reason=NO_LOCATION".format(
                        p.Id.IntegerValue, _get_system_string(p)
                    )
                )
            skipped_ids.append(p.Id)
            continue

        if is_too_short(p):
            skipped_too_short += 1
            level_counts[lvl_name]["skipped"] += 1
            if len(failure_samples) < 30:
                failure_samples.append(
                    "Pipe {} (sys='{}') method=skipped_too_short reason=TOO_SHORT".format(
                        p.Id.IntegerValue, _get_system_string(p)
                    )
                )
            skipped_ids.append(p.Id)
            continue

        # Near-vertical guardrail
        try:
            crv = loc.Curve
            if crv:
                p0 = crv.GetEndPoint(0)
                p1 = crv.GetEndPoint(1)
                hl = horiz_length(p0, p1)
                if hl < HORIZ_MIN_FT:
                    skipped_near_vertical += 1
                    level_counts[lvl_name]["skipped"] += 1
                    if len(failure_samples) < 30:
                        failure_samples.append(
                            "Pipe {} (sys='{}') method=skipped_near_vertical reason=NEAR_VERTICAL".format(
                                p.Id.IntegerValue, _get_system_string(p)
                            )
                        )
                    skipped_ids.append(p.Id)
                    continue
        except Exception:
            pass

        target, sysname = classify_target_slope(p)
        if target is None:
            skipped_no_rule += 1
            level_counts[lvl_name]["skipped"] += 1
            if len(failure_samples) < 30:
                failure_samples.append(
                    "Pipe {} (sys='{}') method=skipped_no_rule reason=NO_RULE".format(
                        p.Id.IntegerValue, _get_system_string(p)
                    )
                )
            skipped_ids.append(p.Id)
            continue

        to_process.append((p, target, sysname))

    if not to_process:
        TaskDialog.Show(
            "slope_solver",
            "No eligible PipeCurves after preflight skips.\n"
            "Skipped: pinned={}, no_location={}, too_short={}, no_rule={}".format(
                skipped_pinned,
                skipped_no_location,
                skipped_too_short,
                skipped_no_rule,
            ),
        )
        return

    # Stats
    total = len(candidates)
    changed = 0
    attempted = 0
    succeeded = 0
    rolled_back_ids = []
    failed_tx = 0
    failed_no_delta = 0
    disconnect_retries = 0
    reconnect_failures = 0
    failures = []

    # --- Execution (single undo) ---
    tg = TransactionGroup(doc, "slope_solver: Apply Slopes")
    tg.Start()

    try:
        # debug readback per system (limit)
        debug_readback = defaultdict(int)
        passes = 0
        still_off_target = 0
        MAX_PASSES = 5

        def _sort_key(item):
            pipe, target, sysname = item
            return (target, -_level_elevation(pipe), -_pipe_length(pipe))

        while passes < MAX_PASSES:
            passes += 1
            changed_this_pass = 0
            still_off_target = 0

            tx_pass = Transaction(doc, "slope_solver: pass {}".format(passes))
            tx_pass_started = False
            try:
                tx_pass.Start()
                tx_pass_started = True

                for p, target, sysname in sorted(to_process, key=_sort_key):
                    attempted += 1
                    diag = _pipe_diag(p)
                    method = "slope_param"
                    p0_before = p1_before = None
                    try:
                        locb = p.Location
                        crvb = locb.Curve
                        p0_before = crvb.GetEndPoint(0)
                        p1_before = crvb.GetEndPoint(1)
                    except Exception:
                        pass

                    subtx = SubTransaction(doc)
                    subtx_started = False
                    err_log = []
                    try:
                        subtx.Start()
                        subtx_started = True
                        ok, method = apply_slope_mep_safe(p, target)
                        st = subtx.Commit()
                        tx_status = st.ToString()
                        ex = None
                    except Exception as ex:
                        if subtx_started:
                            try:
                                subtx.RollBack()
                            except Exception:
                                pass
                        tx_status = "RolledBack"

                    actual = None
                    if tx_status == "Committed":
                        actual = achieved_slope_ratio(p)
                    else:
                        rolled_back_ids.append(p.Id.IntegerValue)
                        failed_tx += 1
                        lvl_name, _ = _level_info(p)
                        level_counts[lvl_name]["failed"] += 1
                        if len(failure_samples) < 30:
                            conn_ids = _end_connected_ids(p)
                            failure_samples.append(
                                "Pipe {id} ({name}) sys='{sys}' method={m} reason=TX_ROLLBACK tx={tx} ex={ex} failures={f} conn_ids={c} diag={d}".format(
                                    id=diag["id"],
                                    name=diag["name"],
                                    sys=diag["system"],
                                    m=method,
                                    tx=tx_status,
                                    ex=repr(ex),
                                    f="; ".join(err_log) if err_log else "None",
                                    c=conn_ids,
                                    d=diag,
                                )
                            )
                    if actual is not None and abs(actual - target) <= SLOPE_TOL:
                        changed += 1
                        succeeded += 1
                        changed_this_pass += 1
                        lvl_name, _ = _level_info(p)
                        level_counts[lvl_name]["changed"] += 1
                        if debug_readback[sysname] < 3:
                            try:
                                loc = p.Location
                                crv = loc.Curve
                                p0 = crv.GetEndPoint(0)
                                p1 = crv.GetEndPoint(1)
                                hl = horiz_length(p0, p1)
                                rise = abs(p1.Z - p0.Z)
                                print(
                                    "DEBUG: sys='{}' pipe {} rise/run={:.6f}/{:.6f} ratio={:.6f}".format(
                                        sysname,
                                        p.Id.IntegerValue,
                                        rise,
                                        hl,
                                        rise / hl if hl > 1e-9 else 0.0,
                                    )
                                )
                                debug_readback[sysname] += 1
                            except Exception:
                                pass
                        continue
                    orig_ex = repr(ex) if ex else "None"
                    orig_fail = "; ".join(err_log) if err_log else "None"

                    subtx2 = SubTransaction(doc)
                    subtx2_started = False
                    err_log2 = []
                    try:
                        subtx2.Start()
                        subtx2_started = True
                        ok2, method2, reconnect_note, disc_ok, rec_ok = (
                            _try_disconnect_apply_reconnect(p, target)
                        )
                        st2 = subtx2.Commit()
                        tx2_status = st2.ToString()
                    except Exception as ex2:
                        if subtx2_started:
                            try:
                                subtx2.RollBack()
                            except Exception:
                                pass
                        failed_tx += 1
                        if len(failure_samples) < 30:
                            failure_samples.append(
                                "Pipe {id} ({name}) sys='{sys}' method=disconnect_retry tx=RolledBack ex={ex} failures={f} diag={d}".format(
                                    id=diag["id"],
                                    name=diag["name"],
                                    sys=diag["system"],
                                    ex=repr(ex2),
                                    f="; ".join(err_log2) if err_log2 else "None",
                                    d=diag,
                                )
                            )
                        continue

                    disconnect_retries += 1
                    if tx2_status != "Committed":
                        failed_tx += 1
                        lvl_name, _ = _level_info(p)
                        level_counts[lvl_name]["failed"] += 1
                        if len(failure_samples) < 30:
                            conn_ids = _end_connected_ids(p)
                            failure_samples.append(
                                "Pipe {id} ({name}) sys='{sys}' method=disconnect_retry reason=TX_ROLLBACK tx={tx} ex={ex} failures={f} orig_failures={of} disconnect_ok={do} reconnect_ok={ro} conn_ids={c} diag={d}".format(
                                    id=diag["id"],
                                    name=diag["name"],
                                    sys=diag["system"],
                                    tx=tx2_status,
                                    ex=repr(None),
                                    f="; ".join(err_log2) if err_log2 else "None",
                                    of=orig_fail,
                                    do=disc_ok,
                                    ro=rec_ok,
                                    c=conn_ids,
                                    d=diag,
                                )
                            )
                        continue
                    actual2 = achieved_slope_ratio(p)
                    if actual2 is not None and abs(actual2 - target) <= SLOPE_TOL:
                        changed += 1
                        succeeded += 1
                        changed_this_pass += 1
                        lvl_name, _ = _level_info(p)
                        level_counts[lvl_name]["changed"] += 1
                        if reconnect_note == "reconnect_failed":
                            reconnect_failures += 1
                            if len(failure_samples) < 30:
                                conn_ids = _end_connected_ids(p)
                                failure_samples.append(
                                    "Pipe {id} ({name}) sys='{sys}' method=disconnect_retry reason=NETWORK_RECONNECT_FAILED tx=Committed ex=None failures={f} orig_failures={of} disconnect_ok={do} reconnect_ok={ro} conn_ids={c} diag={d}".format(
                                        id=diag["id"],
                                        name=diag["name"],
                                        sys=diag["system"],
                                        f="; ".join(err_log2) if err_log2 else "None",
                                        of=orig_fail,
                                        do=disc_ok,
                                        ro=rec_ok,
                                        c=conn_ids,
                                        d=diag,
                                    )
                                )
                        continue

                    failed_no_delta += 1
                    still_off_target += 1
                    lvl_name, _ = _level_info(p)
                    level_counts[lvl_name]["failed"] += 1
                    if len(failure_samples) < 30:
                        reason = (
                            "SLOPE_READONLY"
                            if _slope_readonly(diag)
                            else "CONSTRAINT_SOLVED_BACK"
                        )
                        conn_ids = _end_connected_ids(p)
                        p0_after = p1_after = None
                        try:
                            loca = p.Location
                            crva = loca.Curve
                            p0_after = crva.GetEndPoint(0)
                            p1_after = crva.GetEndPoint(1)
                        except Exception:
                            pass
                        failure_samples.append(
                            "Pipe {id} ({name}) sys='{sys}' method=disconnect_retry reason={r} target={t:.6f} actual={a} p0_before={p0b} p1_before={p1b} p0_after={p0a} p1_after={p1a} tx=Committed ex=None failures={f} orig_failures={of} disconnect_ok={do} reconnect_ok={ro} conn_ids={c} diag={d}".format(
                                id=diag["id"],
                                name=diag["name"],
                                sys=diag["system"],
                                f="; ".join(err_log2) if err_log2 else "None",
                                of=orig_fail,
                                r=reason,
                                t=target,
                                a=actual2,
                                p0b=p0_before,
                                p1b=p1_before,
                                p0a=p0_after,
                                p1a=p1_after,
                                do=disc_ok,
                                ro=rec_ok,
                                c=conn_ids,
                                d=diag,
                            )
                        )
                        if (
                            target == SLOPE_VWA
                            and actual2 is not None
                            and abs(actual2 - SLOPE_HWA) <= SLOPE_TOL
                        ):
                            vwa_wrong_slope_ids.append(p.Id.IntegerValue)
                            if len(vwa_wrong_details) < 20:
                                vwa_wrong_details.append(
                                    "Pipe {} target={:.6f} final={} method={} rule={} sysstr='{}'".format(
                                        p.Id.IntegerValue,
                                        target,
                                        actual2,
                                        method,
                                        classify_target_slope_details(p)[2],
                                        diag.get("system"),
                                    )
                                )

                if changed_this_pass == 0:
                    try:
                        tx_pass.Commit()
                    except Exception:
                        try:
                            tx_pass.RollBack()
                        except Exception:
                            pass
                    break

                try:
                    tx_pass.Commit()
                except Exception:
                    try:
                        tx_pass.RollBack()
                    except Exception:
                        pass

            finally:
                if tx_pass_started:
                    try:
                        if tx_pass.HasStarted():
                            tx_pass.RollBack()
                    except Exception:
                        pass

        if failure_samples:
            print("DEBUG: failure samples:")
            for s in failure_samples[:30]:
                print("  - {}".format(s))

        # Level summary lines
        lvl_lines = []
        for lvl, stats in level_counts.items():
            lvl_lines.append(
                "{}: c={} ch={} f={} sk={}".format(
                    lvl,
                    stats["candidates"],
                    stats["changed"],
                    stats["failed"],
                    stats["skipped"],
                )
            )

        expected_skips = skipped_pinned + skipped_too_short + skipped_near_vertical
        actual_failures = failed_tx + reconnect_failures + failed_no_delta
        status_line = "OK" if actual_failures == 0 else "ATTENTION"

        msg = (
            "Status: {0}\n"
            "Candidates: {1}\n"
            "Attempted: {2}\n"
            "Succeeded: {3}\n"
            "Changed (validated): {4}\n"
            "Not modified (expected): {5}\n"
            "Actual failures: {6}\n"
            "Failed (tx rollback): {7}\n"
            "Failed (no slope delta): {8}\n"
            "Skipped (pinned): {9}\n"
            "Skipped (no location): {10}\n"
            "Skipped (too short): {11}\n"
            "Skipped (no rule): {12}\n"
            "Skipped (near vertical): {13}\n"
            "Disconnect retries: {14}\n"
            "Reconnect failures: {15}\n"
            "Passes executed: {16}\n"
            "Still off-target (>tol): {17}\n"
            "Rolled-back count: {18}\n"
            "Rolled-back ids (first 20): {19}\n\n"
            "Level summary:\n- {20}\n\n"
            "Slope VWA: {21:.6f} ({22:.1f}/1000)\n"
            "Slope HWA: {23:.6f} ({24:.1f}/1000)\n\n"
            "Skipped / Not modified samples (reasons):\n- {25}\n\n"
            "VWA wrong slope: {26}\n"
            "VWA wrong ids (first 20): {27}\n"
            "VWA wrong details (first 20):\n- {28}"
        ).format(
            status_line,
            total,
            attempted,
            succeeded,
            changed,
            expected_skips,
            actual_failures,
            failed_tx,
            failed_no_delta,
            skipped_pinned,
            skipped_no_location,
            skipped_too_short,
            skipped_no_rule,
            skipped_near_vertical,
            disconnect_retries,
            reconnect_failures,
            passes,
            still_off_target,
            len(rolled_back_ids),
            rolled_back_ids[:20],
            "\n- ".join(lvl_lines) if lvl_lines else "None",
            SLOPE_VWA,
            SLOPE_VWA * 1000.0,
            SLOPE_HWA,
            SLOPE_HWA * 1000.0,
            "\n- ".join(failure_samples[:30]) if failure_samples else "None",
            len(vwa_wrong_slope_ids),
            vwa_wrong_slope_ids[:20],
            "\n- ".join(vwa_wrong_details[:20]) if vwa_wrong_details else "None",
        )

        try:
            TaskDialog.Show("slope_solver", msg)
        except Exception:
            pass

        # Optional isolate skipped pipes in temp 3D view
        try:
            if skipped_ids:
                td2 = TaskDialog("slope_solver")
                td2.MainInstruction = "Isolate skipped pipes in a temporary 3D view?"
                td2.CommonButtons = (
                    TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
                )
                res2 = td2.Show()
                if res2 == TaskDialogResult.Yes:
                    txv = Transaction(doc, "slope_solver: isolate skipped")
                    txv.Start()
                    vft = None
                    for vt in (
                        FilteredElementCollector(doc)
                        .OfClass(ViewFamilyType)
                        .ToElements()
                    ):
                        try:
                            if vt.ViewFamily == ViewFamily.ThreeDimensional:
                                vft = vt
                                break
                        except Exception:
                            pass
                    if vft:
                        v3d = View3D.CreateIsometric(doc, vft.Id)
                        if v3d:
                            v3d.Name = _unique_view_name("slope_solver_skipped_temp")
                            v3d.IsolateElementsTemporary(
                                ClrList[ElementId](skipped_ids)
                            )
                            try:
                                uidoc.ActiveView = v3d
                            except Exception:
                                pass
                    txv.Commit()
        except Exception as ex:
            print("DEBUG: isolate view failed: {}".format(repr(ex)))
            print("DEBUG: intended view name = slope_solver_skipped_temp")
            try:
                txv.RollBack()
            except Exception:
                pass

        try:
            tg.Assimilate()
        except Exception:
            try:
                tg.RollBack()
            except Exception:
                pass

    except Exception as ex:
        try:
            tg.RollBack()
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
