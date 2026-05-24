# -*- coding: utf-8 -*-
__title__ = "Connect\nPipes"
__doc__ = """Connect Pipes - generate routed pipe connections without moving selected pipe networks."""

import os
import sys

from pyrevit import revit, DB, forms
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter

try:
    from Autodesk.Revit.DB.Plumbing import Pipe
except Exception:
    Pipe = None


SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
LIB_PATH = os.path.join(EXT_ROOT, "lib")
if os.path.isdir(LIB_PATH) and LIB_PATH not in sys.path:
    sys.path.insert(0, LIB_PATH)

# Direct module imports from lib root (no package)
import selection
import connectors
import align_connect
import graph_mep
import pathfinding
import isolate_view

MM_TOL = 1.0
MIN_SEGMENT_MM = 5.0
ROUTE_DEDUPE_TOL_MM = 5.0
MIN_ROUTE_LEG_MM = 100.0
INVALID_EID = DB.ElementId.InvalidElementId

SLOPE_MODE_PRESERVE_ENDPOINTS = "Preserve endpoint elevations only"
SLOPE_MODE_SOURCE = "Inherit source pipe slope"
SLOPE_MODE_TARGET = "Inherit target pipe slope"
SLOPE_MODE_EXPLICIT = "Use explicit slope value mm_per_m"
SLOPE_MODE_FLAT = "Flat / zero slope"

ROUTE_SIMPLE = "simple"
ROUTE_OPENINGS = "openings"


try:
    _IFAILURES_PREPROCESSOR_BASE = DB.IFailuresPreprocessor
except Exception:
    _IFAILURES_PREPROCESSOR_BASE = object


class _RouteFailuresPreprocessor(_IFAILURES_PREPROCESSOR_BASE):
    def PreprocessFailures(self, failures_accessor):
        try:
            messages = list(failures_accessor.GetFailureMessages())
        except Exception:
            messages = []
        has_error = False
        for msg in messages:
            try:
                severity = msg.GetSeverity()
                if severity == DB.FailureSeverity.Warning:
                    failures_accessor.DeleteWarning(msg)
                elif severity == DB.FailureSeverity.Error:
                    has_error = True
            except Exception:
                has_error = True
        if has_error:
            return DB.FailureProcessingResult.ProceedWithRollBack
        return DB.FailureProcessingResult.Continue


def _attach_route_failure_preprocessor(transaction):
    try:
        opts = transaction.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(_RouteFailuresPreprocessor())
        opts.SetClearAfterRollback(True)
        transaction.SetFailureHandlingOptions(opts)
    except Exception as ex:
        print("FAILURE HANDLING DEBUG -> could not attach failure preprocessor: {}".format(ex))


def _module_file(mod):
    try:
        return getattr(mod, "__file__", "<unknown>")
    except Exception:
        return "<unknown>"


print("IMPORT DEBUG -> LIB_PATH: {}".format(LIB_PATH))
print("IMPORT DEBUG -> graph_mep.py: {}".format(_module_file(graph_mep)))
print("IMPORT DEBUG -> connectors.py: {}".format(_module_file(connectors)))
print("IMPORT DEBUG -> align_connect.py: {}".format(_module_file(align_connect)))


def _is_pipe(el):
    try:
        return (
            el
            and el.Category
            and el.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_PipeCurves)
        )
    except Exception:
        return False


def _ft_to_mm(ft):
    return DB.UnitUtils.ConvertFromInternalUnits(ft, DB.UnitTypeId.Millimeters)


def _mm_to_ft(mm):
    return DB.UnitUtils.ConvertToInternalUnits(mm, DB.UnitTypeId.Millimeters)


def _safe_name(el):
    try:
        return el.Name
    except Exception:
        return ""


def _param_string_value(el, param_name):
    try:
        p = el.LookupParameter(param_name)
        if not p:
            return ""
        try:
            return p.AsString() or ""
        except Exception:
            pass
        try:
            return p.AsValueString() or ""
        except Exception:
            pass
    except Exception:
        pass
    return ""


def _ask_choice(options, title, default=None):
    try:
        if default and default in options:
            ordered = [default]
            for opt in options:
                if opt != default:
                    ordered.append(opt)
            options = ordered
        return forms.CommandSwitchWindow.show(options, message=title)
    except Exception:
        return None


def _xyz_mm(pt):
    if pt is None:
        return "None"


def _xyz_mm_tuple(pt):
    if pt is None:
        return None
    try:
        return (_ft_to_mm(pt.X), _ft_to_mm(pt.Y), _ft_to_mm(pt.Z))
    except Exception:
        return None


def _xyz_mm_from_tuple(tup):
    if not tup:
        return "None"
    try:
        return "({:.1f}, {:.1f}, {:.1f})".format(tup[0], tup[1], tup[2])
    except Exception:
        return "None"
    try:
        return "({:.1f}, {:.1f}, {:.1f})".format(
            _ft_to_mm(pt.X), _ft_to_mm(pt.Y), _ft_to_mm(pt.Z)
        )
    except Exception:
        return "None"


def _eid_int(el_or_id):
    try:
        return el_or_id.Id.IntegerValue
    except Exception:
        try:
            return el_or_id.IntegerValue
        except Exception:
            return None


def _is_invalid_id(eid):
    try:
        return eid is None or eid == INVALID_EID or eid.IntegerValue < 0
    except Exception:
        return True


def _is_link_reference(ref):
    try:
        linked_id = ref.LinkedElementId
        return not _is_invalid_id(linked_id)
    except Exception:
        return False


def _is_editable_host_pipe(doc, ref, el):
    if ref is not None and _is_link_reference(ref):
        return False, "Linked elements are not supported."
    if not _is_pipe(el):
        return False, "Selected element is not a host PipeCurve."
    try:
        if el.Document and el.Document.GetHashCode() != doc.GetHashCode():
            return False, "Selected pipe is not in the active host document."
    except Exception:
        pass
    try:
        if el.IsReadOnly:
            return False, "Selected pipe is read-only."
    except Exception:
        pass
    try:
        if el.Pinned:
            return False, "Selected pipe is pinned."
    except Exception:
        pass
    return True, ""


def _connector_is_connected(conn):
    try:
        return bool(conn.IsConnected)
    except Exception:
        return False


def _has_any_connected_connector(el):
    for conn in connectors.iter_connectors(el):
        if _connector_is_connected(conn):
            return True
    return False


def _get_pipe_system_type_id(pipe_el):
    for bip_name in ["RBS_PIPING_SYSTEM_TYPE_PARAM"]:
        try:
            bip = getattr(DB.BuiltInParameter, bip_name)
            p = pipe_el.get_Parameter(bip)
            if p:
                eid = p.AsElementId()
                if not _is_invalid_id(eid):
                    return eid
        except Exception:
            pass
    try:
        if pipe_el.MEPSystem:
            eid = pipe_el.MEPSystem.GetTypeId()
            if not _is_invalid_id(eid):
                return eid
    except Exception:
        pass
    return None


def _get_pipe_level_id(pipe_el):
    for bip_name in [
        "RBS_START_LEVEL_PARAM",
        "RBS_REFERENCE_LEVEL_PARAM",
        "LEVEL_PARAM",
        "INSTANCE_REFERENCE_LEVEL_PARAM",
    ]:
        try:
            bip = getattr(DB.BuiltInParameter, bip_name)
            p = pipe_el.get_Parameter(bip)
            if p:
                eid = p.AsElementId()
                if not _is_invalid_id(eid):
                    return eid
        except Exception:
            pass
    try:
        eid = pipe_el.LevelId
        if not _is_invalid_id(eid):
            return eid
    except Exception:
        pass
    return None


def _get_pipe_diameter(pipe_el):
    try:
        p = pipe_el.get_Parameter(DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
        if p and p.HasValue:
            return p.AsDouble()
    except Exception:
        pass
    return None


def _set_pipe_diameter(pipe_el, diameter):
    if diameter is None:
        return
    try:
        p = pipe_el.get_Parameter(DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
        if p and not p.IsReadOnly:
            p.Set(diameter)
    except Exception:
        pass


def _pipe_location_endpoints(pipe_el):
    try:
        loc = pipe_el.Location
        crv = getattr(loc, "Curve", None)
        if crv:
            return crv.GetEndPoint(0), crv.GetEndPoint(1)
    except Exception:
        pass
    return None, None


def _pipe_revit_slope_diagnostics(pipe_el):
    values = []
    for bip_name in ["RBS_PIPE_SLOPE", "RBS_PIPE_SLOPE_PARAM"]:
        try:
            bip = getattr(DB.BuiltInParameter, bip_name)
            p = pipe_el.get_Parameter(bip)
            if p and p.HasValue:
                val = None
                try:
                    val = p.AsDouble()
                except Exception:
                    pass
                val_str = ""
                try:
                    val_str = p.AsValueString() or ""
                except Exception:
                    pass
                values.append("{}={} ({})".format(bip_name, val, val_str))
        except Exception:
            pass
    if not values:
        values.append("No readable Revit slope parameter found.")
    return "; ".join(values)


def _derive_pipe_slope_ratio(pipe_el):
    p0, p1 = _pipe_location_endpoints(pipe_el)
    if p0 is None or p1 is None:
        return None, "No LocationCurve endpoints."
    dx = p1.X - p0.X
    dy = p1.Y - p0.Y
    dz = p1.Z - p0.Z
    xy = (dx * dx + dy * dy) ** 0.5
    if xy <= _mm_to_ft(MM_TOL):
        return None, "Pipe is vertical or near-vertical; slope ignored."
    return dz / xy, "Derived from LocationCurve endpoints."


def _derive_pipe_slope_ratio_at_connector(pipe_el, conn, entering_pipe=False):
    p0, p1 = _pipe_location_endpoints(pipe_el)
    if p0 is None or p1 is None:
        return None, "No LocationCurve endpoints."
    base_slope, reason = _derive_pipe_slope_ratio(pipe_el)
    if base_slope is None:
        return None, reason
    try:
        d0 = conn.Origin.DistanceTo(p0)
        d1 = conn.Origin.DistanceTo(p1)
        # Outward from the selected end: p0 end reverses the curve direction,
        # p1 end follows it. For target inheritance, entering the target pipe is
        # the opposite of outward from its selected connector.
        sign = -1.0 if d0 <= d1 else 1.0
        if entering_pipe:
            sign = -sign
        return base_slope * sign, "Derived from LocationCurve and selected connector orientation."
    except Exception:
        return base_slope, reason


def _slope_mm_per_m(slope_ratio):
    if slope_ratio is None:
        return None
    return slope_ratio * 1000.0


def _format_slope(slope_ratio):
    if slope_ratio is None:
        return "None"
    return "{:.6f} ({:.3f} mm/m)".format(slope_ratio, _slope_mm_per_m(slope_ratio))


def _prompt_route_slope_mode(source_pipe, target_pipe, source_conn, target_conn):
    source_slope, source_reason = _derive_pipe_slope_ratio_at_connector(source_pipe, source_conn, entering_pipe=False)
    target_slope, target_reason = _derive_pipe_slope_ratio_at_connector(target_pipe, target_conn, entering_pipe=True)
    print("SLOPE DEBUG -> source slope: {}; reason: {}; Revit param: {}".format(
        _format_slope(source_slope), source_reason, _pipe_revit_slope_diagnostics(source_pipe)
    ))
    print("SLOPE DEBUG -> target slope: {}; reason: {}; Revit param: {}".format(
        _format_slope(target_slope), target_reason, _pipe_revit_slope_diagnostics(target_pipe)
    ))

    slope_mode = _ask_choice(
        [
            SLOPE_MODE_PRESERVE_ENDPOINTS,
            SLOPE_MODE_SOURCE,
            SLOPE_MODE_TARGET,
            SLOPE_MODE_EXPLICIT,
            SLOPE_MODE_FLAT,
        ],
        "Choose route slope mode",
        default=SLOPE_MODE_SOURCE,
    )
    if not slope_mode:
        return None

    selected_slope = None
    if slope_mode == SLOPE_MODE_SOURCE:
        selected_slope = source_slope
        if selected_slope is None:
            forms.alert("Source pipe slope cannot be used: {}".format(source_reason))
            return None
    elif slope_mode == SLOPE_MODE_TARGET:
        selected_slope = target_slope
        if selected_slope is None:
            forms.alert("Target pipe slope cannot be used: {}".format(target_reason))
            return None
    elif slope_mode == SLOPE_MODE_EXPLICIT:
        raw = forms.ask_for_string(
            default="5.0",
            prompt="Enter route slope in mm per m. Use negative value for falling route.",
            title="Explicit Route Slope",
        )
        if raw is None:
            return None
        try:
            selected_slope = float(raw.replace(",", ".")) / 1000.0
        except Exception:
            forms.alert("Invalid slope value: {}".format(raw))
            return None
    elif slope_mode == SLOPE_MODE_FLAT:
        selected_slope = 0.0
    else:
        selected_slope = None

    return {
        "mode": slope_mode,
        "source_slope": source_slope,
        "target_slope": target_slope,
        "selected_slope": selected_slope,
    }


def _xy_distance(a, b):
    try:
        dx = b.X - a.X
        dy = b.Y - a.Y
        return (dx * dx + dy * dy) ** 0.5
    except Exception:
        return 0.0


def _segment_slope_ratio(a, b):
    xy = _xy_distance(a, b)
    if xy <= _mm_to_ft(MM_TOL):
        return None
    return (b.Z - a.Z) / xy


def _point_distance_mm(a, b):
    try:
        return _ft_to_mm(a.DistanceTo(b))
    except Exception:
        return 0.0


def _segment_length_mm(a, b):
    return _point_distance_mm(a, b)


def _is_vertical_leg(a, b):
    try:
        return _xy_distance(a, b) <= _mm_to_ft(ROUTE_DEDUPE_TOL_MM) and abs(a.Z - b.Z) > _mm_to_ft(ROUTE_DEDUPE_TOL_MM)
    except Exception:
        return False


def _is_collinear_triplet(a, b, c, tol_mm=5.0):
    try:
        v1 = b - a
        v2 = c - b
        if v1.GetLength() <= _mm_to_ft(tol_mm) or v2.GetLength() <= _mm_to_ft(tol_mm):
            return True
        return v1.CrossProduct(v2).GetLength() <= _mm_to_ft(tol_mm)
    except Exception:
        return False


def _simplify_route_points(raw_points):
    stats = {
        "removed_point_count": 0,
        "removed_short_segment_count": 0,
        "minimum_segment_length_mm": None,
    }
    if not raw_points:
        return [], stats
    if len(raw_points) <= 2:
        return raw_points, stats

    tol_ft = _mm_to_ft(ROUTE_DEDUPE_TOL_MM)
    points = [raw_points[0]]
    for raw_idx, pt in enumerate(raw_points[1:], start=1):
        is_last = raw_idx == len(raw_points) - 1
        if _same_point(points[-1], pt, tol_ft):
            if is_last:
                points.append(pt)
                continue
            stats["removed_point_count"] += 1
            continue
        points.append(pt)

    changed = True
    while changed and len(points) > 2:
        changed = False
        out = [points[0]]
        idx = 1
        while idx < len(points) - 1:
            prev_pt = out[-1]
            cur_pt = points[idx]
            next_pt = points[idx + 1]
            len_prev = _segment_length_mm(prev_pt, cur_pt)
            len_next = _segment_length_mm(cur_pt, next_pt)
            preserve_vertical = _is_vertical_leg(prev_pt, cur_pt) or _is_vertical_leg(cur_pt, next_pt)
            if not preserve_vertical and (len_prev < MIN_ROUTE_LEG_MM or len_next < MIN_ROUTE_LEG_MM):
                stats["removed_point_count"] += 1
                stats["removed_short_segment_count"] += 1
                changed = True
                idx += 1
                continue
            if _is_collinear_triplet(prev_pt, cur_pt, next_pt, tol_mm=ROUTE_DEDUPE_TOL_MM):
                stats["removed_point_count"] += 1
                changed = True
                idx += 1
                continue
            out.append(cur_pt)
            idx += 1
        out.append(points[-1])
        points = out

    min_len = None
    for idx in range(0, len(points) - 1):
        length_mm = _segment_length_mm(points[idx], points[idx + 1])
        if min_len is None or length_mm < min_len:
            min_len = length_mm
    stats["minimum_segment_length_mm"] = min_len
    return points, stats


def _print_route_cleanup_debug(raw_points, simplified_points, stats):
    print("ROUTE CLEANUP DEBUG")
    for idx, pt in enumerate(raw_points or []):
        print("raw route point {} mm: {}".format(idx + 1, _xyz_mm(pt)))
    for idx, pt in enumerate(simplified_points or []):
        print("simplified route point {} mm: {}".format(idx + 1, _xyz_mm(pt)))
    print("removed point count: {}".format(stats.get("removed_point_count")))
    print("removed short segment count: {}".format(stats.get("removed_short_segment_count")))
    print("minimum segment length found mm: {}".format(stats.get("minimum_segment_length_mm")))


def _same_point(a, b, tol_ft):
    try:
        return a.DistanceTo(b) <= tol_ft
    except Exception:
        return False


def _dedupe_route_points(points):
    out = []
    tol = _mm_to_ft(MM_TOL)
    for pt in points:
        if not out or not _same_point(out[-1], pt, tol):
            out.append(pt)
    return out


def _axis_collinear(a, b, tol_ft):
    try:
        same_x = abs(a.X - b.X) <= tol_ft
        same_y = abs(a.Y - b.Y) <= tol_ft
        same_z = abs(a.Z - b.Z) <= tol_ft
        return (same_x and same_y) or (same_x and same_z) or (same_y and same_z)
    except Exception:
        return False


def _build_orthogonal_route_points(source_pt, target_pt):
    tol_ft = _mm_to_ft(MM_TOL)
    if _axis_collinear(source_pt, target_pt, tol_ft):
        return [source_pt, target_pt]

    # v0.1 dogleg: move vertically to target Z, then along X, then Y.
    # This keeps source/target pipes fixed and creates orthogonal generated segments only.
    p0 = source_pt
    p1 = DB.XYZ(source_pt.X, source_pt.Y, target_pt.Z)
    p2 = DB.XYZ(target_pt.X, source_pt.Y, target_pt.Z)
    p3 = target_pt
    return _dedupe_route_points([p0, p1, p2, p3])


def _sloped_leg_end(start_pt, end_xy_pt, slope_ratio):
    xy = _xy_distance(start_pt, end_xy_pt)
    return DB.XYZ(end_xy_pt.X, end_xy_pt.Y, start_pt.Z + (slope_ratio * xy))


def _append_vertical_absorber(points, anchor_pt):
    if not points:
        points.append(anchor_pt)
        return
    last = points[-1]
    if abs(last.Z - anchor_pt.Z) > _mm_to_ft(MM_TOL):
        points.append(DB.XYZ(last.X, last.Y, anchor_pt.Z))


def _build_sloped_route_between_anchors(start_pt, end_pt, slope_ratio):
    tol_ft = _mm_to_ft(MM_TOL)
    if slope_ratio is None:
        raise Exception("Requested slope mode did not provide a usable slope ratio.")

    points = [start_pt]
    if _axis_collinear(start_pt, end_pt, tol_ft):
        if _xy_distance(start_pt, end_pt) > tol_ft:
            sloped_end = _sloped_leg_end(start_pt, end_pt, slope_ratio)
            points.append(sloped_end)
            _append_vertical_absorber(points, end_pt)
        else:
            points.append(end_pt)
        return _dedupe_route_points(points)

    p_xy1 = DB.XYZ(end_pt.X, start_pt.Y, start_pt.Z)
    if _xy_distance(start_pt, p_xy1) > tol_ft:
        points.append(_sloped_leg_end(points[-1], p_xy1, slope_ratio))

    p_xy2 = DB.XYZ(end_pt.X, end_pt.Y, points[-1].Z)
    if _xy_distance(points[-1], p_xy2) > tol_ft:
        points.append(_sloped_leg_end(points[-1], p_xy2, slope_ratio))

    _append_vertical_absorber(points, end_pt)
    points.append(end_pt)
    return _dedupe_route_points(points)


def _build_route_points_with_slope(source_pt, target_pt, waypoint_points, slope_info):
    anchors = [source_pt]
    for wp in waypoint_points or []:
        anchors.append(wp)
    anchors.append(target_pt)

    mode = slope_info["mode"]
    if mode == SLOPE_MODE_PRESERVE_ENDPOINTS:
        points = []
        for idx in range(0, len(anchors) - 1):
            leg = _build_orthogonal_route_points(anchors[idx], anchors[idx + 1])
            if idx > 0 and leg:
                leg = leg[1:]
            points.extend(leg)
        return _dedupe_route_points(points)

    slope_ratio = slope_info["selected_slope"]
    if mode != SLOPE_MODE_FLAT and slope_ratio is None:
        raise Exception("No usable slope ratio for mode '{}'.".format(mode))

    points = []
    for idx in range(0, len(anchors) - 1):
        leg = _build_sloped_route_between_anchors(anchors[idx], anchors[idx + 1], slope_ratio)
        if idx > 0 and leg:
            leg = leg[1:]
        points.extend(leg)
    return _dedupe_route_points(points)


def _segment_length_ok(a, b):
    try:
        return a.DistanceTo(b) >= _mm_to_ft(MIN_SEGMENT_MM)
    except Exception:
        return False


def _find_pipe_end_connector(pipe_el, point):
    return connectors.closest_connector_to_point(pipe_el, point, only_ends=True)


def _connector_origin(conn):
    try:
        return conn.Origin
    except Exception:
        return None


def _connector_owner_id(conn):
    try:
        return conn.Owner.Id.IntegerValue
    except Exception:
        return None


def _connector_direction(conn):
    try:
        return connectors.connector_direction(conn)
    except Exception:
        return None


def _xyz_vec_mm(vec):
    if vec is None:
        return "None"
    try:
        return "({:.3f}, {:.3f}, {:.3f})".format(vec.X, vec.Y, vec.Z)
    except Exception:
        return "None"


def _angle_between_degrees(v1, v2):
    try:
        dot = max(-1.0, min(1.0, v1.Normalize().DotProduct(v2.Normalize())))
        import math
        return math.degrees(math.acos(dot))
    except Exception:
        return None


def _build_route_debug_cache(source_pipe, target_pipe, source_conn, target_conn, raw_points, route_points, slope_info, cleanup_stats):
    cache = {
        "source_element_id": _eid_int(source_pipe),
        "target_element_id": _eid_int(target_pipe),
        "source_connector_owner_id": _connector_owner_id(source_conn),
        "target_connector_owner_id": _connector_owner_id(target_conn),
        "source_connector_origin_mm": _xyz_mm_tuple(_connector_origin(source_conn)),
        "target_connector_origin_mm": _xyz_mm_tuple(_connector_origin(target_conn)),
        "raw_route_points_mm": [_xyz_mm_tuple(p) for p in raw_points or []],
        "route_points_mm": [_xyz_mm_tuple(p) for p in route_points or []],
        "route_segment_slopes": [_segment_slope_ratio(route_points[i], route_points[i + 1]) for i in range(0, max(0, len(route_points or []) - 1))],
        "created_pipe_ids": [],
        "created_fitting_ids": [],
        "created_pipe_final_slopes": [],
        "rollback_completed": False,
        "failure_reason": "",
        "slope_info": slope_info or {},
        "cleanup_stats": cleanup_stats or {},
    }
    return cache


def _create_pipe_segment(doc, source_pipe, start, end):
    if Pipe is None:
        raise Exception("Autodesk.Revit.DB.Plumbing.Pipe could not be imported.")
    system_type_id = _get_pipe_system_type_id(source_pipe)
    pipe_type_id = source_pipe.GetTypeId()
    level_id = _get_pipe_level_id(source_pipe)
    if _is_invalid_id(system_type_id):
        raise Exception("Could not resolve source pipe system type.")
    if _is_invalid_id(pipe_type_id):
        raise Exception("Could not resolve source pipe type.")
    if _is_invalid_id(level_id):
        raise Exception("Could not resolve source pipe level.")

    new_pipe = Pipe.Create(doc, system_type_id, pipe_type_id, level_id, start, end)
    _set_pipe_diameter(new_pipe, _get_pipe_diameter(source_pipe))
    return new_pipe


def _new_fitting_between(doc, conn_a, conn_b):
    origin_a = _connector_origin(conn_a)
    origin_b = _connector_origin(conn_b)
    dist_mm = _point_distance_mm(origin_a, origin_b) if origin_a and origin_b else None
    dir_a = _connector_direction(conn_a)
    dir_b = _connector_direction(conn_b)
    angle = _angle_between_degrees(dir_a, dir_b)
    print("CONNECTOR JOIN DEBUG -> a_owner: {}; b_owner: {}; dist_mm: {}; a_dir: {}; b_dir: {}; angle_deg: {}".format(
        _connector_owner_id(conn_a), _connector_owner_id(conn_b), dist_mm, _xyz_vec_mm(dir_a), _xyz_vec_mm(dir_b), angle
    ))

    if dist_mm is not None and dist_mm <= ROUTE_DEDUPE_TOL_MM:
        try:
            conn_a.ConnectTo(conn_b)
            return None
        except Exception as ex:
            print("CONNECTOR JOIN DEBUG -> coincident ConnectTo failed: {}".format(ex))

    # Try elbow first for angled route changes, then union for collinear joins.
    try:
        fitting = doc.Create.NewElbowFitting(conn_a, conn_b)
        if fitting:
            return fitting
    except Exception as ex:
        print("CONNECTOR JOIN DEBUG -> NewElbowFitting failed: {}".format(ex))
    try:
        fitting = doc.Create.NewUnionFitting(conn_a, conn_b)
        if fitting:
            return fitting
    except Exception as ex:
        print("CONNECTOR JOIN DEBUG -> NewUnionFitting failed: {}".format(ex))
    raise Exception(
        "Could not connect/create fitting between connectors. dist_mm={}, angle_deg={}, a={}, b={}.".format(
            dist_mm,
            angle,
            _xyz_mm(origin_a),
            _xyz_mm(origin_b),
        )
    )


def _join_pipe_chain_with_fittings(
    doc, source_conn, target_conn, new_pipes, route_points
):
    fittings = []
    if not new_pipes:
        raise Exception("No generated pipe segments were created.")

    first_conn = _find_pipe_end_connector(new_pipes[0], route_points[0])
    if not first_conn:
        raise Exception("Could not resolve first generated pipe connector.")
    fitting = _new_fitting_between(doc, source_conn, first_conn)
    if fitting:
        fittings.append(fitting)

    for idx in range(0, len(new_pipes) - 1):
        join_pt = route_points[idx + 1]
        a_conn = _find_pipe_end_connector(new_pipes[idx], join_pt)
        b_conn = _find_pipe_end_connector(new_pipes[idx + 1], join_pt)
        if not a_conn or not b_conn:
            raise Exception(
                "Could not resolve generated pipe connector at route waypoint {}.".format(
                    _xyz_mm(join_pt)
                )
            )
        fitting = _new_fitting_between(doc, a_conn, b_conn)
        if fitting:
            fittings.append(fitting)

    last_conn = _find_pipe_end_connector(new_pipes[-1], route_points[-1])
    if not last_conn:
        raise Exception("Could not resolve last generated pipe connector.")
    fitting = _new_fitting_between(doc, last_conn, target_conn)
    if fitting:
        fittings.append(fitting)
    return fittings


def _print_route_debug(
    source_conn,
    target_conn,
    route_points,
    created_pipes,
    created_fittings,
    slope_info=None,
    failure_reason=None,
    debug_cache=None,
):
    if debug_cache:
        print("GENERATE ROUTED CONNECTION DEBUG")
        print("source element id: {}".format(debug_cache.get("source_element_id")))
        print("target element id: {}".format(debug_cache.get("target_element_id")))
        print("source connector owner id: {}".format(debug_cache.get("source_connector_owner_id")))
        print("target connector owner id: {}".format(debug_cache.get("target_connector_owner_id")))
        print("source connector origin mm: {}".format(_xyz_mm_from_tuple(debug_cache.get("source_connector_origin_mm"))))
        print("target connector origin mm: {}".format(_xyz_mm_from_tuple(debug_cache.get("target_connector_origin_mm"))))
        for idx, pt in enumerate(debug_cache.get("raw_route_points_mm") or []):
            print("raw route point {} mm: {}".format(idx + 1, _xyz_mm_from_tuple(pt)))
        for idx, pt in enumerate(debug_cache.get("route_points_mm") or []):
            print("simplified route point {} mm: {}".format(idx + 1, _xyz_mm_from_tuple(pt)))
        si = debug_cache.get("slope_info") or {}
        if si:
            print("selected slope mode: {}".format(si.get("mode")))
            print("source slope ratio/mm_per_m: {}".format(_format_slope(si.get("source_slope"))))
            print("target slope ratio/mm_per_m: {}".format(_format_slope(si.get("target_slope"))))
            print("selected route slope ratio/mm_per_m: {}".format(_format_slope(si.get("selected_slope"))))
        for idx, slope in enumerate(debug_cache.get("route_segment_slopes") or []):
            print("route segment {} slope: {}".format(idx + 1, _format_slope(slope)))
        cs = debug_cache.get("cleanup_stats") or {}
        print("removed point count: {}".format(cs.get("removed_point_count")))
        print("removed short segment count: {}".format(cs.get("removed_short_segment_count")))
        print("minimum segment length found mm: {}".format(cs.get("minimum_segment_length_mm")))
        print("created pipe ids: {}".format(debug_cache.get("created_pipe_ids") or []))
        print("created fitting ids: {}".format(debug_cache.get("created_fitting_ids") or []))
        print("created pipe final slopes: {}".format(debug_cache.get("created_pipe_final_slopes") or []))
        print("rollback completed: {}".format(debug_cache.get("rollback_completed")))
        if debug_cache.get("failure_reason"):
            print("failure reason: {}".format(debug_cache.get("failure_reason")))
        return

    print("GENERATE ROUTED CONNECTION DEBUG")
    print(
        "source connector origin mm: {}".format(
            _xyz_mm(source_conn.Origin if source_conn else None)
        )
    )
    print(
        "target connector origin mm: {}".format(
            _xyz_mm(target_conn.Origin if target_conn else None)
        )
    )
    for idx, pt in enumerate(route_points or []):
        print("route point {} mm: {}".format(idx + 1, _xyz_mm(pt)))
    if slope_info:
        print("selected slope mode: {}".format(slope_info.get("mode")))
        print("source slope ratio/mm_per_m: {}".format(_format_slope(slope_info.get("source_slope"))))
        print("target slope ratio/mm_per_m: {}".format(_format_slope(slope_info.get("target_slope"))))
        print("selected route slope ratio/mm_per_m: {}".format(_format_slope(slope_info.get("selected_slope"))))
    for idx in range(0, max(0, len(route_points or []) - 1)):
        seg_slope = _segment_slope_ratio(route_points[idx], route_points[idx + 1])
        print("route segment {} slope: {}".format(idx + 1, _format_slope(seg_slope)))
    for pipe in created_pipes or []:
        p0, p1 = _pipe_location_endpoints(pipe)
        print("created pipe {} final slope: {}".format(_eid_int(pipe), _format_slope(_segment_slope_ratio(p0, p1) if p0 and p1 else None)))
    print("created pipe ids: {}".format([_eid_int(p) for p in created_pipes or []]))
    print(
        "created fitting ids: {}".format([_eid_int(f) for f in created_fittings or []])
    )
    if failure_reason:
        print("failure reason: {}".format(failure_reason))


def _pick_host_pipe_end(prompt):
    doc = revit.doc
    ref = selection.pick_point_on_element(prompt=prompt)
    if not ref:
        return None, None, None
    if _is_link_reference(ref):
        forms.alert(
            "Linked elements are not supported for routed connection generation."
        )
        return None, None, None
    el = doc.GetElement(ref.ElementId)
    ok, reason = _is_editable_host_pipe(doc, ref, el)
    if not ok:
        forms.alert(reason)
        return None, None, None
    conn = connectors.closest_connector_to_point(el, ref.GlobalPoint, only_ends=True)
    if not conn:
        forms.alert("Could not resolve an end connector on selected pipe.")
        return None, None, None
    return el, conn, ref.GlobalPoint


class _OpeningSelectionFilter(ISelectionFilter):
    def __init__(self, mode="host"):
        self.mode = mode

    def AllowElement(self, element):
        if self.mode == "manual":
            return True
        if self.mode == "linked":
            return _is_revit_link(element)
        if _is_revit_link(element) or _is_import_instance(element):
            return True
        return _is_opening_candidate(element)

    def AllowReference(self, reference, point):
        return True


class _LinkOrImportSelectionFilter(ISelectionFilter):
    def AllowElement(self, element):
        return _is_revit_link(element) or _is_import_instance(element)

    def AllowReference(self, reference, point):
        return True


def _is_revit_link(el):
    try:
        return isinstance(el, DB.RevitLinkInstance)
    except Exception:
        try:
            return el.GetType().Name == "RevitLinkInstance"
        except Exception:
            return False


def _is_import_instance(el):
    try:
        return isinstance(el, DB.ImportInstance)
    except Exception:
        try:
            return el.GetType().Name == "ImportInstance"
        except Exception:
            return False


def _category_name(el):
    try:
        if el and el.Category:
            return el.Category.Name or ""
    except Exception:
        pass
    return ""


def _element_type_name(el):
    try:
        typ = el.Document.GetElement(el.GetTypeId())
        return _safe_name(typ)
    except Exception:
        return ""


def _family_name(el):
    try:
        typ = el.Document.GetElement(el.GetTypeId())
        fam = getattr(typ, "Family", None)
        if fam:
            return _safe_name(fam)
        return getattr(typ, "FamilyName", "") or ""
    except Exception:
        return ""


def _classify_opening_candidate(el):
    if not el:
        return {"confidence": "REJECTED", "reason": "Missing element."}
    cat = _category_name(el)
    name_bits = [
        _safe_name(el),
        cat,
        _family_name(el),
        _element_type_name(el),
        _param_string_value(el, "Export to IFC As"),
        _param_string_value(el, "IfcExportAs"),
        _param_string_value(el, "ObjectTypeOverride"),
        _param_string_value(el, "IfcName"),
        _param_string_value(el, "IfcDescription"),
    ]
    hay = " ".join(name_bits).lower()
    reject_tokens = [
        "basic wall",
        "wall",
        "floor",
        "slab",
        "beam",
        "column",
        "panel",
        "bollard",
        "model text",
        "text",
    ]
    opening_tokens = [
        "ifcopeningelement",
        "objecttypeoverride opening",
        "opening",
        "void",
        "sleeve",
        "sparing",
        "penetration",
        "durchbruch",
    ]
    high_tokens = ["ifcopeningelement", "objecttypeoverride opening", "opening"]
    if any(tok in hay for tok in reject_tokens) and not any(tok in hay for tok in opening_tokens):
        return {"confidence": "REJECTED", "reason": "Rejected category/name: {}".format(cat)}
    if any(tok in hay for tok in high_tokens):
        return {"confidence": "HIGH", "reason": "High-confidence opening indicator."}
    if any(tok in hay for tok in opening_tokens):
        return {"confidence": "MEDIUM", "reason": "Probable sleeve/penetration/void indicator."}
    return {"confidence": "LOW", "reason": "No semantic opening indicator."}


def _is_opening_candidate(el):
    cls = _classify_opening_candidate(el)
    return cls.get("confidence") in ["HIGH", "MEDIUM"]


def _opening_candidate_label(info, source_conn=None, target_conn=None):
    el = info.get("element")
    cls = info.get("classification") or _classify_opening_candidate(el)
    dist = ""
    if source_conn and target_conn and info.get("center"):
        dist = " | dist {:.1f}mm".format(_ft_to_mm(_distance_to_corridor_xy(info.get("center"), source_conn.Origin, target_conn.Origin)))
    return "{} | {} | {} | {} | {} | {}{}".format(
        cls.get("confidence"),
        info.get("id"),
        _category_name(el),
        _family_name(el) or _element_type_name(el) or info.get("name"),
        _xyz_mm(info.get("center")),
        cls.get("reason"),
        dist,
    )


def _is_rejected_opening_candidate(el):
    return _classify_opening_candidate(el).get("confidence") == "REJECTED"


def _is_low_opening_candidate(el):
    return _classify_opening_candidate(el).get("confidence") == "LOW"


def _is_high_or_medium_opening_candidate(el):
    return _is_opening_candidate(el)


def _bbox_center(el, transform=None):
    try:
        bb = el.get_BoundingBox(None)
        if not bb:
            return None
        center = DB.XYZ(
            (bb.Min.X + bb.Max.X) / 2.0,
            (bb.Min.Y + bb.Max.Y) / 2.0,
            (bb.Min.Z + bb.Max.Z) / 2.0,
        )
        if transform:
            center = transform.OfPoint(center)
        return center
    except Exception:
        return None


def _opening_info_from_ref(doc, ref, mode):
    try:
        if mode == "manual":
            gp = getattr(ref, "GlobalPoint", None)
            if gp:
                return {
                    "id": "manual:{}".format(len(str(gp))),
                    "host_or_linked": "manual-waypoint",
                    "element": None,
                    "center": gp,
                    "name": "Manual waypoint",
                    "classification": {"confidence": "MANUAL", "reason": "User-picked route waypoint."},
                }
            return None

        if getattr(ref, "GlobalPoint", None):
            # Face picks are accepted as user-guided opening waypoints. Use the picked
            # point directly because a face-level bbox is not reliably available here.
            if not _is_link_reference(ref):
                el = doc.GetElement(ref.ElementId)
                if el and (_is_revit_link(el) or _is_import_instance(el)):
                    forms.alert("This is a link/import. Use Pick linked opening elements or Manual waypoint mode.")
                    return None
                if el and _is_opening_candidate(el):
                    return {
                        "id": "{}:face".format(el.Id.IntegerValue),
                        "host_or_linked": "host-face",
                        "element": el,
                        "center": ref.GlobalPoint,
                        "name": _safe_name(el),
                        "classification": _classify_opening_candidate(el),
                    }
                if el:
                    forms.alert("Selected host element is not classified as an opening: {}".format(_safe_name(el)))
                return None
        if _is_link_reference(ref):
            link_inst = doc.GetElement(ref.ElementId)
            if not _is_revit_link(link_inst):
                return None
            link_doc = link_inst.GetLinkDocument()
            if not link_doc:
                return None
            linked_el = link_doc.GetElement(ref.LinkedElementId)
            cls = _classify_opening_candidate(linked_el)
            if cls.get("confidence") not in ["HIGH", "MEDIUM"]:
                use_point = forms.alert(
                    "Linked element is not classified as a semantic opening.\n\n"
                    "Element: {}\n"
                    "Category: {}\n"
                    "Reason: {}\n\n"
                    "Use the picked point as a manual waypoint instead?".format(
                        _safe_name(linked_el), _category_name(linked_el), cls.get("reason")
                    ),
                    yes=True,
                    no=True,
                    title="Linked Opening Selection",
                )
                if use_point and getattr(ref, "GlobalPoint", None):
                    return {
                        "id": "{}:{}:manual".format(link_inst.Id.IntegerValue, ref.LinkedElementId.IntegerValue),
                        "host_or_linked": "linked-manual-waypoint",
                        "element": linked_el,
                        "center": ref.GlobalPoint,
                        "name": _safe_name(linked_el),
                        "classification": {"confidence": "MANUAL", "reason": "Picked point on non-opening linked element."},
                    }
                return None
            center = _bbox_center(linked_el, link_inst.GetTotalTransform())
            if not center:
                return None
            return {
                "id": "{}:{}".format(link_inst.Id.IntegerValue, ref.LinkedElementId.IntegerValue),
                "host_or_linked": "linked",
                "element": linked_el,
                "center": center,
                "name": _safe_name(linked_el),
                "classification": cls,
            }
        el = doc.GetElement(ref.ElementId)
        if el and (_is_revit_link(el) or _is_import_instance(el)):
            forms.alert("This is a link/import. Use Pick linked opening elements or Manual waypoint mode.")
            return None
        cls = _classify_opening_candidate(el)
        if cls.get("confidence") not in ["HIGH", "MEDIUM"]:
            forms.alert("Selected host element is not classified as an opening.\n\nReason: {}".format(cls.get("reason")))
            return None
        center = _bbox_center(el)
        if not center:
            return None
        return {
            "id": str(el.Id.IntegerValue),
            "host_or_linked": "host",
            "element": el,
            "center": center,
            "name": _safe_name(el),
            "classification": cls,
        }
    except Exception:
        return None


def _pick_openings_in_order():
    selection_mode = _ask_choice(
        [
            "Pick host opening elements",
            "Pick host opening faces",
            "Pick linked opening elements",
            "Pick manual route waypoint points/faces",
        ],
        "Choose opening selection mode",
        default="Pick host opening elements",
    )
    if not selection_mode:
        return []

    object_type = ObjectType.Element
    filter_mode = "host"
    if selection_mode == "Pick host opening faces":
        object_type = ObjectType.Face
    elif selection_mode == "Pick linked opening elements":
        filter_mode = "linked"
        try:
            object_type = ObjectType.LinkedElement
        except Exception:
            forms.alert("This Revit API does not expose ObjectType.LinkedElement.")
            return []
    elif selection_mode == "Pick manual route waypoint points/faces":
        filter_mode = "manual"
        object_type = ObjectType.PointOnElement

    refs = []
    try:
        refs = list(revit.uidoc.Selection.PickObjects(
            object_type,
            _OpeningSelectionFilter(filter_mode),
            "Pick opening elements/waypoints in route order, then Finish"
        ))
    except OperationCanceledException:
        return []
    except Exception as ex:
        forms.alert("Opening selection failed: {}".format(ex))
        return []

    infos = []
    for ref in refs:
        info = _opening_info_from_ref(revit.doc, ref, filter_mode)
        if info:
            infos.append(info)
    return infos


def _print_opening_debug(opening_infos):
    print("OPENING ROUTE DEBUG")
    for info in opening_infos or []:
        cls = info.get("classification") or {}
        print("opening/waypoint id: {}; scope: {}; confidence: {}; name: {}; center host mm: {}; reason: {}".format(
            info.get("id"), info.get("host_or_linked"), cls.get("confidence"), info.get("name"), _xyz_mm(info.get("center")), cls.get("reason")
        ))


def run_generate_routed_connection_through_openings():
    doc = revit.doc
    picked = _pick_source_target_open_pipe_ends()
    if not picked:
        return
    source_pipe, source_conn, target_pipe, target_conn = picked

    opening_infos = _pick_openings_in_order()
    if not opening_infos:
        forms.alert("No opening centers were selected/resolved.")
        return
    _print_opening_debug(opening_infos)

    slope_info = _prompt_route_slope_mode(source_pipe, target_pipe, source_conn, target_conn)
    if not slope_info:
        return
    waypoints = [i.get("center") for i in opening_infos if i.get("center")]
    _execute_routed_connection(
        doc,
        source_pipe,
        source_conn,
        target_pipe,
        target_conn,
        waypoints,
        slope_info,
        "Generate Routed Connection Through Selected Opening(s)",
    )


def _collect_rvt_link_opening_candidates(link_inst, limit_count=100):
    out = []
    rejected = []
    low = []
    low_infos = []
    try:
        link_doc = link_inst.GetLinkDocument()
    except Exception:
        link_doc = None
    if not link_doc:
        return out
    try:
        transform = link_inst.GetTotalTransform()
    except Exception:
        transform = None

    cats = [
        DB.BuiltInCategory.OST_GenericModel,
        DB.BuiltInCategory.OST_Windows,
        DB.BuiltInCategory.OST_Doors,
        DB.BuiltInCategory.OST_Walls,
        DB.BuiltInCategory.OST_Floors,
        DB.BuiltInCategory.OST_StructuralFraming,
        DB.BuiltInCategory.OST_StructuralColumns,
    ]
    seen = set()
    for bic in cats:
        try:
            coll = DB.FilteredElementCollector(link_doc).OfCategory(bic).WhereElementIsNotElementType()
            for el in coll:
                if el.Id.IntegerValue in seen:
                    continue
                seen.add(el.Id.IntegerValue)
                cls = _classify_opening_candidate(el)
                if cls.get("confidence") == "REJECTED":
                    rejected.append((el, cls))
                    continue
                if cls.get("confidence") == "LOW":
                    low.append((el, cls))
                    center = _bbox_center(el, transform)
                    if center:
                        low_infos.append({
                            "id": "{}:{}".format(link_inst.Id.IntegerValue, el.Id.IntegerValue),
                            "host_or_linked": "linked-low-debug",
                            "element": el,
                            "center": center,
                            "name": _safe_name(el),
                            "classification": cls,
                        })
                    continue
                center = _bbox_center(el, transform)
                if center:
                    out.append({
                        "id": "{}:{}".format(link_inst.Id.IntegerValue, el.Id.IntegerValue),
                        "host_or_linked": "linked",
                        "element": el,
                        "center": center,
                        "name": _safe_name(el),
                        "classification": cls,
                    })
                if len(out) >= limit_count:
                    return out
        except Exception:
            continue
    print("LINK ROUTE DEBUG -> high/medium candidates: {}; low: {}; rejected: {}".format(len(out), len(low), len(rejected)))
    if not out and low_infos:
        if forms.alert(
            "No high/medium semantic openings were found.\n\nShow low-confidence generic candidates for debug?",
            yes=True,
            no=True,
            title="Selected Link Routing - Experimental",
        ):
            return low_infos
    return out


def _collect_import_opening_candidates(import_inst):
    out = []
    center = _bbox_center(import_inst)
    if center:
        out.append({
            "id": str(import_inst.Id.IntegerValue),
            "host_or_linked": "import-bbox",
            "element": import_inst,
            "center": center,
            "name": _safe_name(import_inst) or "ImportInstance",
        })
    print("LINK ROUTE DEBUG -> ImportInstance geometry semantics are limited; using accessible import bounding box centers only.")
    return out


def _distance_to_corridor_xy(pt, a, b):
    try:
        ax = a.X
        ay = a.Y
        bx = b.X
        by = b.Y
        px = pt.X
        py = pt.Y
        vx = bx - ax
        vy = by - ay
        wx = px - ax
        wy = py - ay
        denom = vx * vx + vy * vy
        if denom <= 1e-12:
            return _xy_distance(pt, a)
        t = max(0.0, min(1.0, (wx * vx + wy * vy) / denom))
        proj = DB.XYZ(ax + t * vx, ay + t * vy, pt.Z)
        return _xy_distance(pt, proj)
    except Exception:
        return 0.0


def _pick_link_or_import():
    try:
        ref = revit.uidoc.Selection.PickObject(
            ObjectType.Element,
            _LinkOrImportSelectionFilter(),
            "Pick a Revit link or IFC/DWG import instance"
        )
        if not ref:
            return None
        return revit.doc.GetElement(ref.ElementId)
    except OperationCanceledException:
        return None
    except Exception as ex:
        forms.alert("Link/import selection failed: {}".format(ex))
        return None


def _select_opening_candidate_from_list(candidates, source_conn=None, target_conn=None):
    if not candidates:
        return None
    labels = []
    by_label = {}
    for idx, info in enumerate(candidates):
        label = "{} | {}".format(idx + 1, _opening_candidate_label(info, source_conn, target_conn))
        labels.append(label)
        by_label[label] = info
    picked = forms.SelectFromList.show(labels, title="Select opening candidate", multiselect=False)
    if not picked:
        return None
    return by_label.get(picked)


def run_generate_routed_connection_using_selected_link():
    doc = revit.doc
    picked = _pick_source_target_open_pipe_ends()
    if not picked:
        return
    source_pipe, source_conn, target_pipe, target_conn = picked

    link_or_import = _pick_link_or_import()
    if not link_or_import:
        return

    candidates = []
    if _is_revit_link(link_or_import):
        candidates = _collect_rvt_link_opening_candidates(link_or_import, limit_count=200)
    elif _is_import_instance(link_or_import):
        forms.alert(
            "DWG/IFC pipe recreation requires layer/object mapping and is not semantic MEP conversion.\n\n"
            "Use manual waypoint routing or RVT PipeCurve links.",
            title="Selected Link Routing - Experimental",
        )
        return

    if not candidates:
        use_manual = forms.alert(
            "No semantic opening candidates were found in the selected link.\n\n"
            "Use manual waypoint mode instead?",
            yes=True,
            no=True,
            title="Selected Link Routing - Experimental",
        )
        if use_manual:
            opening_infos = _pick_openings_in_order()
            if not opening_infos:
                return
            slope_info = _prompt_route_slope_mode(source_pipe, target_pipe, source_conn, target_conn)
            if not slope_info:
                return
            _execute_routed_connection(
                doc,
                source_pipe,
                source_conn,
                target_pipe,
                target_conn,
                [i.get("center") for i in opening_infos if i.get("center")],
                slope_info,
                "Generate Routed Connection Using Selected Link - Manual Waypoints",
            )
        return

    candidates.sort(key=lambda i: _distance_to_corridor_xy(i.get("center"), source_conn.Origin, target_conn.Origin))
    nearest = candidates[:30]
    print("LINK ROUTE DEBUG -> opening candidates found: {}; showing nearest: {}".format(len(candidates), len(nearest)))
    for info in nearest:
        print("LINK ROUTE CANDIDATE -> id: {}; center host mm: {}; dist corridor mm: {:.1f}".format(
            info.get("id"), _xyz_mm(info.get("center")), _ft_to_mm(_distance_to_corridor_xy(info.get("center"), source_conn.Origin, target_conn.Origin))
        ))

    selected = _select_opening_candidate_from_list(nearest, source_conn, target_conn)
    if not selected:
        return

    slope_info = _prompt_route_slope_mode(source_pipe, target_pipe, source_conn, target_conn)
    if not slope_info:
        return

    _print_opening_debug([selected])
    _execute_routed_connection(
        doc,
        source_pipe,
        source_conn,
        target_pipe,
        target_conn,
        [selected.get("center")],
        slope_info,
        "Generate Routed Connection Using Selected Link - Experimental",
    )


def _collect_by_class_name(doc, class_name):
    out = []
    try:
        for el in DB.FilteredElementCollector(doc).WhereElementIsElementType():
            try:
                if el.GetType().Name == class_name:
                    out.append(el)
            except Exception:
                pass
    except Exception:
        pass
    return out


def _collect_levels(doc):
    try:
        return list(DB.FilteredElementCollector(doc).OfClass(DB.Level))
    except Exception:
        return []


def _select_named_element(elements, title, label_func=None):
    labels = []
    by_label = {}
    for el in elements:
        try:
            name = label_func(el) if label_func else _safe_name(el)
            label = "{} | {}".format(name, el.Id.IntegerValue)
            labels.append(label)
            by_label[label] = el
        except Exception:
            continue
    labels.sort()
    picked = forms.SelectFromList.show(labels, title=title, multiselect=False)
    if not picked:
        return None
    return by_label.get(picked)


def _linked_pipe_diameter(link_pipe):
    return _get_pipe_diameter(link_pipe)


def _collect_linked_pipe_curve_data(link_inst):
    data = []
    skipped = []
    try:
        link_doc = link_inst.GetLinkDocument()
    except Exception:
        link_doc = None
    if not link_doc:
        return data, ["Selected RVT link document is not loaded or accessible."]
    try:
        transform = link_inst.GetTotalTransform()
    except Exception:
        transform = None
    try:
        coll = DB.FilteredElementCollector(link_doc).OfCategory(DB.BuiltInCategory.OST_PipeCurves).WhereElementIsNotElementType()
        for pipe_el in coll:
            p0, p1 = _pipe_location_endpoints(pipe_el)
            if p0 is None or p1 is None:
                skipped.append("Pipe {} skipped: no line endpoints.".format(pipe_el.Id.IntegerValue))
                continue
            if transform:
                p0 = transform.OfPoint(p0)
                p1 = transform.OfPoint(p1)
            if not _segment_length_ok(p0, p1):
                skipped.append("Pipe {} skipped: transformed segment too short.".format(pipe_el.Id.IntegerValue))
                continue
            data.append({
                "linked_id": pipe_el.Id.IntegerValue,
                "start": p0,
                "end": p1,
                "diameter": _linked_pipe_diameter(pipe_el),
            })
    except Exception as ex:
        skipped.append("Pipe collection failed: {}".format(ex))
    return data, skipped


def _create_host_pipe_from_link_data(doc, system_type_id, pipe_type_id, level_id, pipe_data):
    if Pipe is None:
        raise Exception("Autodesk.Revit.DB.Plumbing.Pipe could not be imported.")
    new_pipe = Pipe.Create(doc, system_type_id, pipe_type_id, level_id, pipe_data["start"], pipe_data["end"])
    _set_pipe_diameter(new_pipe, pipe_data.get("diameter"))
    return new_pipe


def run_generate_host_pipes_from_rvt_link():
    doc = revit.doc
    link_inst = _pick_link_or_import()
    if not link_inst:
        return
    if not _is_revit_link(link_inst):
        forms.alert("This command currently supports RVT links only. IFC/DWG pipe recreation is intentionally not implemented.")
        return

    pipe_data, skipped = _collect_linked_pipe_curve_data(link_inst)
    report = [
        "Generate Host Pipes From RVT Link - Dry Run",
        "Selected link id: {}".format(link_inst.Id.IntegerValue),
        "Valid linked pipe curves: {}".format(len(pipe_data)),
        "Skipped elements: {}".format(len(skipped)),
        "Proposed created pipe count: {}".format(len(pipe_data)),
    ]
    for line in skipped[:20]:
        print("RVT LINK PIPE RECREATE SKIP -> {}".format(line))
    forms.alert("\n".join(report), title="Generate Host Pipes From RVT Link - Experimental")
    if not pipe_data:
        return
    if not forms.alert("Create {} host pipes from selected RVT link?".format(len(pipe_data)), yes=True, no=True):
        return

    system_types = _collect_by_class_name(doc, "PipingSystemType")
    pipe_types = _collect_by_class_name(doc, "PipeType")
    levels = _collect_levels(doc)
    system_type = _select_named_element(system_types, "Select host piping system type")
    if not system_type:
        return
    pipe_type = _select_named_element(pipe_types, "Select host pipe type")
    if not pipe_type:
        return
    level = _select_named_element(levels, "Select host pipe level")
    if not level:
        return

    created = []
    failure_reason = None
    tg = DB.TransactionGroup(doc, "BIM3DNA Generate Host Pipes From RVT Link")
    tg_started = False
    t = None
    t_started = False
    try:
        tg.Start()
        tg_started = True
        t = DB.Transaction(doc, "Generate Host Pipes From RVT Link")
        _attach_route_failure_preprocessor(t)
        t.Start()
        t_started = True
        for item in pipe_data:
            created.append(_create_host_pipe_from_link_data(doc, system_type.Id, pipe_type.Id, level.Id, item))
        t.Commit()
        t_started = False
        tg.Assimilate()
        tg_started = False
    except Exception as ex:
        failure_reason = str(ex)
        try:
            if t and t_started:
                t.RollBack()
        except Exception:
            pass
        try:
            if tg and tg_started:
                tg.RollBack()
        except Exception:
            pass
        print("HOST PIPE RECREATE FAILURE -> {}".format(failure_reason))
        forms.alert("Host pipe recreation failed and was rolled back.\n\n{}".format(failure_reason))
        return

    forms.alert(
        "Created successfully.\n\nCreated host pipe ids: {}".format([_eid_int(p) for p in created]),
        title="Generate Host Pipes From RVT Link - Experimental",
    )


def _execute_routed_connection(doc, source_pipe, source_conn, target_pipe, target_conn, waypoint_points, slope_info, command_title):
    try:
        raw_route_points = _build_route_points_with_slope(
            source_conn.Origin, target_conn.Origin, waypoint_points, slope_info
        )
    except Exception as ex:
        forms.alert("Could not build route.\n\n{}".format(ex), title=command_title)
        return False, [], [], str(ex)

    route_points, cleanup_stats = _simplify_route_points(raw_route_points)
    _print_route_cleanup_debug(raw_route_points, route_points, cleanup_stats)
    debug_cache = _build_route_debug_cache(
        source_pipe, target_pipe, source_conn, target_conn, raw_route_points, route_points, slope_info, cleanup_stats
    )

    if len(route_points) < 2:
        forms.alert("Could not build a valid route between selected pipe ends.", title=command_title)
        return False, [], [], "Route has fewer than two points."

    for idx in range(0, len(route_points) - 1):
        seg_len = _segment_length_mm(route_points[idx], route_points[idx + 1])
        if seg_len < MIN_SEGMENT_MM:
            reason = "Route segment {} remains too short after cleanup: {:.1f} mm.".format(idx + 1, seg_len)
            debug_cache["failure_reason"] = reason
            _print_route_debug(None, None, None, None, None, debug_cache=debug_cache)
            forms.alert(reason, title=command_title)
            return False, [], [], reason

    if slope_info["mode"] != SLOPE_MODE_FLAT and slope_info["mode"] != SLOPE_MODE_PRESERVE_ENDPOINTS:
        has_sloped_horizontal = False
        for idx in range(0, len(route_points) - 1):
            if _xy_distance(route_points[idx], route_points[idx + 1]) > _mm_to_ft(MM_TOL):
                seg_slope = _segment_slope_ratio(route_points[idx], route_points[idx + 1])
                if seg_slope is not None and abs(seg_slope) > 1e-9:
                    has_sloped_horizontal = True
        if not has_sloped_horizontal:
            reason = "Requested non-flat slope could not be applied to any horizontal route segment."
            debug_cache["failure_reason"] = reason
            _print_route_debug(None, None, None, None, None, debug_cache=debug_cache)
            forms.alert(reason, title=command_title)
            return False, [], [], reason

    created_pipes = []
    created_fittings = []
    failure_reason = None
    tg = DB.TransactionGroup(doc, "BIM3DNA Generate Routed Pipe Connection")
    tg_started = False
    t = None
    t_started = False
    try:
        tg.Start()
        tg_started = True
        t = DB.Transaction(doc, command_title)
        _attach_route_failure_preprocessor(t)
        t.Start()
        t_started = True

        for idx in range(0, len(route_points) - 1):
            p0 = route_points[idx]
            p1 = route_points[idx + 1]
            if not _segment_length_ok(p0, p1):
                raise Exception(
                    "Route segment {} is too short: {} -> {}".format(
                        idx + 1, _xyz_mm(p0), _xyz_mm(p1)
                    )
                )
            created_pipes.append(_create_pipe_segment(doc, source_pipe, p0, p1))
            debug_cache["created_pipe_ids"].append(_eid_int(created_pipes[-1]))
            seg_slope = _segment_slope_ratio(p0, p1)
            debug_cache["created_pipe_final_slopes"].append(_format_slope(seg_slope))

        doc.Regenerate()
        created_fittings = _join_pipe_chain_with_fittings(
            doc, source_conn, target_conn, created_pipes, route_points
        )
        debug_cache["created_fitting_ids"] = [_eid_int(f) for f in created_fittings if f]
        doc.Regenerate()

        t.Commit()
        t_started = False
        tg.Assimilate()
        tg_started = False
    except Exception as ex:
        failure_reason = str(ex)
        debug_cache["failure_reason"] = failure_reason
        try:
            if t and t_started:
                t.RollBack()
        except Exception:
            pass
        try:
            if tg and tg_started:
                tg.RollBack()
                debug_cache["rollback_completed"] = True
        except Exception:
            pass
        _print_route_debug(
            None,
            None,
            None,
            None,
            None,
            debug_cache=debug_cache,
        )
        forms.alert(
            "{} failed and was rolled back.\n\n"
            "Source element id: {}\n"
            "Target element id: {}\n"
            "Source connector mm: {}\n"
            "Target connector mm: {}\n"
            "Route points mm:\n{}\n"
            "Rollback completed: {}\n\n"
            "Reason: {}".format(
                command_title,
                debug_cache.get("source_element_id"),
                debug_cache.get("target_element_id"),
                _xyz_mm_from_tuple(debug_cache.get("source_connector_origin_mm")),
                _xyz_mm_from_tuple(debug_cache.get("target_connector_origin_mm")),
                "\n".join(["  {}".format(_xyz_mm_from_tuple(p)) for p in debug_cache.get("route_points_mm") or []]),
                debug_cache.get("rollback_completed"),
                failure_reason,
            ),
            title=command_title,
        )
        return False, [], [], failure_reason

    _print_route_debug(
        None, None, None, None, None, debug_cache=debug_cache
    )
    forms.alert(
        "Created successfully.\n\n"
        "Original source element id: {}\n"
        "Original target element id: {}\n"
        "Created pipe ids: {}\n"
        "Created fitting ids: {}".format(
            source_pipe.Id.IntegerValue,
            target_pipe.Id.IntegerValue,
            [_eid_int(p) for p in created_pipes],
            [_eid_int(f) for f in created_fittings],
        ),
        title=command_title,
    )
    return True, created_pipes, created_fittings, ""


def _pick_source_target_open_pipe_ends():
    doc = revit.doc

    source_pipe, source_conn, source_pick = _pick_host_pipe_end(
        "Pick SOURCE pipe end (point on pipe end)"
    )
    if not source_pipe:
        return
    target_pipe, target_conn, target_pick = _pick_host_pipe_end(
        "Pick TARGET pipe end (point on pipe end)"
    )
    if not target_pipe:
        return

    if source_pipe.Id.IntegerValue == target_pipe.Id.IntegerValue:
        forms.alert("Source and target must be different pipe elements.")
        return
    if _connector_is_connected(source_conn):
        forms.alert(
            "Selected source pipe end is already connected. Pick an open pipe end."
        )
        return
    if _connector_is_connected(target_conn):
        forms.alert(
            "Selected target pipe end is already connected. Pick an open pipe end."
        )
        return None

    return source_pipe, source_conn, target_pipe, target_conn


def run_generate_routed_connection():
    doc = revit.doc
    picked = _pick_source_target_open_pipe_ends()
    if not picked:
        return
    source_pipe, source_conn, target_pipe, target_conn = picked

    slope_info = _prompt_route_slope_mode(source_pipe, target_pipe, source_conn, target_conn)
    if not slope_info:
        return

    _execute_routed_connection(
        doc,
        source_pipe,
        source_conn,
        target_pipe,
        target_conn,
        [],
        slope_info,
        "Generate Routed Connection",
    )


def run_align_connect_debug():
    # pick source element + point
    src_ref = selection.pick_point_on_element(
        prompt="Pick source element (point on element)"
    )
    if not src_ref:
        return
    if _is_link_reference(src_ref):
        forms.alert("Linked elements are not supported.")
        return
    src_el = revit.doc.GetElement(src_ref.ElementId)
    src_pt = src_ref.GlobalPoint

    # pick target element + point
    tgt_ref = selection.pick_point_on_element(
        prompt="Pick target element (point on element)"
    )
    if not tgt_ref:
        return
    if _is_link_reference(tgt_ref):
        forms.alert("Linked elements are not supported.")
        return
    tgt_el = revit.doc.GetElement(tgt_ref.ElementId)
    tgt_pt = tgt_ref.GlobalPoint

    if _has_any_connected_connector(tgt_el):
        forms.alert(
            "Blocked: target element has at least one connected connector.\n\n"
            "Use Generate Routed Connection for connected or production pipe work.",
            title="Move Target To Source - Debug Only",
        )
        return

    src_conn = connectors.closest_connector_to_point(
        src_el, src_pt, only_ends=_is_pipe(src_el)
    )
    tgt_conn = connectors.closest_connector_to_point(
        tgt_el, tgt_pt, only_ends=_is_pipe(tgt_el)
    )

    if not src_conn or not tgt_conn:
        forms.alert("Could not resolve connectors on one or both elements.")
        return

    d0 = src_conn.Origin.DistanceTo(tgt_conn.Origin)
    forms.alert(
        "Source connector (mm): {}\n"
        "Target connector (mm): {}\n"
        "Pre-move connector gap: {:.1f} mm".format(
            _xyz_mm(src_conn.Origin), _xyz_mm(tgt_conn.Origin), _ft_to_mm(d0)
        ),
        title="DEBUG",
    )

    include_connected = forms.alert(
        "Move connected network with target?", yes=True, no=True
    )
    allow_rotate = False

    res = align_connect.align_connect(
        source_el=src_el,
        target_el=tgt_el,
        source_conn=src_conn,
        target_conn=tgt_conn,
        include_connected=include_connected,
        allow_rotate=allow_rotate,
    )

    forms.alert(
        res.to_text() if hasattr(res, "to_text") else str(res),
        title="Move Target To Source - Debug Only",
    )


def gather_mep_elements_in_view(doc, view):
    if hasattr(graph_mep, "gather_mep_elements_in_view"):
        try:
            return graph_mep.gather_mep_elements_in_view(doc, view)
        except Exception as ex:
            print(
                "TRACE DEBUG -> graph_mep.gather_mep_elements_in_view failed: {}".format(
                    ex
                )
            )

    categories = [
        DB.BuiltInCategory.OST_PipeCurves,
        DB.BuiltInCategory.OST_PipeFitting,
        DB.BuiltInCategory.OST_PipeAccessory,
        DB.BuiltInCategory.OST_DuctCurves,
        DB.BuiltInCategory.OST_DuctFitting,
        DB.BuiltInCategory.OST_DuctAccessory,
        DB.BuiltInCategory.OST_MechanicalEquipment,
        DB.BuiltInCategory.OST_PlumbingFixtures,
    ]
    elems = []
    seen = set()
    for bic in categories:
        try:
            collector = (
                DB.FilteredElementCollector(doc, view.Id)
                .OfCategory(bic)
                .WhereElementIsNotElementType()
            )
            for el in collector:
                eid = el.Id.IntegerValue
                if eid not in seen:
                    seen.add(eid)
                    elems.append(el)
        except Exception:
            continue
    print(
        "TRACE DEBUG -> fallback gather_mep_elements_in_view collected {} elements.".format(
            len(elems)
        )
    )
    return elems


def run_trace_existing_mep_path_debug():
    doc = revit.doc
    view = doc.ActiveView

    a_ref = selection.pick_point_on_element(
        prompt="Pick start element (point on element)"
    )
    if not a_ref:
        return
    if _is_link_reference(a_ref):
        forms.alert("Linked elements are not supported.")
        return
    a_el = doc.GetElement(a_ref.ElementId)
    a_pt = a_ref.GlobalPoint
    a_conn = connectors.closest_connector_to_point(a_el, a_pt, only_ends=False)

    b_ref = selection.pick_point_on_element(
        prompt="Pick end element (point on element)"
    )
    if not b_ref:
        return
    if _is_link_reference(b_ref):
        forms.alert("Linked elements are not supported.")
        return
    b_el = doc.GetElement(b_ref.ElementId)
    b_pt = b_ref.GlobalPoint
    b_conn = connectors.closest_connector_to_point(b_el, b_pt, only_ends=False)

    if not a_conn or not b_conn:
        forms.alert("Could not resolve connectors on one or both elements.")
        return

    if not hasattr(graph_mep, "build_connector_graph") or not hasattr(
        graph_mep, "nodes_to_element_ids"
    ):
        forms.alert(
            "Trace debug is unavailable because graph_mep.py does not provide build_connector_graph/nodes_to_element_ids.",
            title="Trace Existing MEP Path - Debug",
        )
        return
    if not hasattr(pathfinding, "dijkstra"):
        forms.alert(
            "Trace debug is unavailable because pathfinding.py does not provide dijkstra.",
            title="Trace Existing MEP Path - Debug",
        )
        return

    elems = gather_mep_elements_in_view(doc, view)
    graph = graph_mep.build_connector_graph(elems)

    start_node = connectors.connector_key(a_conn)
    end_node = connectors.connector_key(b_conn)

    path_nodes, cost = pathfinding.dijkstra(graph, start_node, end_node)
    if not path_nodes:
        forms.alert("No path found between selected connectors.")
        return

    path_ids = graph_mep.nodes_to_element_ids(graph, path_nodes)
    isolate = forms.alert("Isolate path in view?", yes=True, no=True)

    if hasattr(isolate_view, "highlight_elements"):
        isolate_view.highlight_elements(view, path_ids)
    if isolate and hasattr(isolate_view, "isolate_elements_temporarily"):
        isolate_view.isolate_elements_temporarily(view, path_ids)

    forms.alert(
        "Path elements: {}\nCost (internal units): {}".format(len(path_ids), cost),
        title="Trace Existing MEP Path - Debug",
    )


def main():
    choice = forms.CommandSwitchWindow.show(
        [
            "Generate Routed Connection",
            "Generate Routed Connection Through Selected Opening(s)",
            "Generate Routed Connection Using Selected Link - Experimental",
            "Generate Host Pipes From RVT Link - Experimental",
            "Move Target To Source - Debug Only",
            "Trace Existing MEP Path - Debug",
        ],
        message="Choose operation",
    )
    if not choice:
        return
    if choice == "Generate Routed Connection":
        run_generate_routed_connection()
    elif choice == "Generate Routed Connection Through Selected Opening(s)":
        run_generate_routed_connection_through_openings()
    elif choice == "Generate Routed Connection Using Selected Link - Experimental":
        run_generate_routed_connection_using_selected_link()
    elif choice == "Generate Host Pipes From RVT Link - Experimental":
        run_generate_host_pipes_from_rvt_link()
    elif choice == "Move Target To Source - Debug Only":
        run_align_connect_debug()
    else:
        run_trace_existing_mep_path_debug()


if __name__ == "__main__":
    main()
