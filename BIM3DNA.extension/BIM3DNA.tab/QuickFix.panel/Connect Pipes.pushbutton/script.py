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

DEBUG = False
MM_TOL = 1.0
MIN_SEGMENT_MM = 5.0
HARDENED_MIN_SEGMENT_MM = 10.0
FLAT_Z_WARNING_TOL_MM = 5.0
ROUTE_DEDUPE_TOL_MM = 5.0
MIN_ROUTE_LEG_MM = 100.0
OPENING_SHAFT_XY_TOL_MM = 250.0
OPENING_SHAFT_RELAXED_XY_TOL_MM = 750.0
INVALID_EID = DB.ElementId.InvalidElementId

SLOPE_MODE_PRESERVE_ENDPOINTS = "Preserve endpoint elevations only"
SLOPE_MODE_SOURCE = "Inherit source pipe slope"
SLOPE_MODE_TARGET = "Inherit target pipe slope"
SLOPE_MODE_EXPLICIT = "Use explicit slope value mm_per_m"
SLOPE_MODE_FLAT = "Flat / zero slope"

DIAMETER_DECISION_CANCEL = "Cancel routed connection"
DIAMETER_DECISION_SOURCE = "Continue using source diameter"
DIAMETER_DECISION_TARGET = "Continue using target diameter"

ROUTE_SIMPLE = "simple"
ROUTE_OPENINGS = "openings"
OPENING_ROUTE_ORTHOGONAL = "Orthogonal route through waypoints"
OPENING_ROUTE_SIMPLE_SHAFT = "Simple shaft route through openings"
SHAFT_XY_AVERAGE = "Average grouped opening XY"
SHAFT_XY_FIRST = "Use first opening XY"
SHAFT_XY_LAST = "Use last opening XY"
SHAFT_XY_CLOSEST_SOURCE = "Use opening XY closest to source"
SHAFT_XY_CLOSEST_TARGET = "Use opening XY closest to target"


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
        if DEBUG:
            print(
                "FAILURE HANDLING DEBUG -> could not attach failure preprocessor: {}".format(
                    ex
                )
            )


def _commit_transaction_or_raise(transaction, label):
    status = transaction.Commit()
    if status != DB.TransactionStatus.Committed:
        raise Exception(
            "{} transaction did not commit; status={}.".format(label, status)
        )


def _assimilate_group_or_raise(group, label):
    status = group.Assimilate()
    if status != DB.TransactionStatus.Committed:
        raise Exception(
            "{} transaction group did not assimilate; status={}.".format(label, status)
        )


def _module_file(mod):
    try:
        return getattr(mod, "__file__", "<unknown>")
    except Exception:
        return "<unknown>"


if DEBUG:
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
    try:
        return "({:.1f}, {:.1f}, {:.1f})".format(
            _ft_to_mm(pt.X), _ft_to_mm(pt.Y), _ft_to_mm(pt.Z)
        )
    except Exception:
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
        return (
            base_slope * sign,
            "Derived from LocationCurve and selected connector orientation.",
        )
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
    source_slope, source_reason = _derive_pipe_slope_ratio_at_connector(
        source_pipe, source_conn, entering_pipe=False
    )
    target_slope, target_reason = _derive_pipe_slope_ratio_at_connector(
        target_pipe, target_conn, entering_pipe=True
    )
    print(
        "SLOPE DEBUG -> source slope: {}; reason: {}; Revit param: {}".format(
            _format_slope(source_slope),
            source_reason,
            _pipe_revit_slope_diagnostics(source_pipe),
        )
    )
    print(
        "SLOPE DEBUG -> target slope: {}; reason: {}; Revit param: {}".format(
            _format_slope(target_slope),
            target_reason,
            _pipe_revit_slope_diagnostics(target_pipe),
        )
    )

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
        return _xy_distance(a, b) <= _mm_to_ft(ROUTE_DEDUPE_TOL_MM) and abs(
            a.Z - b.Z
        ) > _mm_to_ft(ROUTE_DEDUPE_TOL_MM)
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
            preserve_vertical = _is_vertical_leg(prev_pt, cur_pt) or _is_vertical_leg(
                cur_pt, next_pt
            )
            if not preserve_vertical and (
                len_prev < MIN_ROUTE_LEG_MM or len_next < MIN_ROUTE_LEG_MM
            ):
                stats["removed_point_count"] += 1
                stats["removed_short_segment_count"] += 1
                changed = True
                idx += 1
                continue
            if _is_collinear_triplet(
                prev_pt, cur_pt, next_pt, tol_mm=ROUTE_DEDUPE_TOL_MM
            ):
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
    if not DEBUG:
        return
    print("ROUTE CLEANUP DEBUG")
    for idx, pt in enumerate(raw_points or []):
        print("raw route point {} mm: {}".format(idx + 1, _xyz_mm(pt)))
    for idx, pt in enumerate(simplified_points or []):
        print("simplified route point {} mm: {}".format(idx + 1, _xyz_mm(pt)))
    print("removed point count: {}".format(stats.get("removed_point_count")))
    print(
        "removed short segment count: {}".format(
            stats.get("removed_short_segment_count")
        )
    )
    print(
        "minimum segment length found mm: {}".format(
            stats.get("minimum_segment_length_mm")
        )
    )


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
        leg = _build_sloped_route_between_anchors(
            anchors[idx], anchors[idx + 1], slope_ratio
        )
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


def _connector_domain_text(conn):
    try:
        return str(conn.Domain)
    except Exception:
        return ""


def _has_valid_mep_connectors(el):
    try:
        return any([conn is not None for conn in connectors.iter_connectors(el)])
    except Exception:
        return False


def _resolved_pipe_creation_data(doc, source_pipe):
    data = {
        "system_type_id": _get_pipe_system_type_id(source_pipe),
        "pipe_type_id": source_pipe.GetTypeId() if source_pipe else None,
        "level_id": _get_pipe_level_id(source_pipe),
        "diameter": _get_pipe_diameter(source_pipe),
    }
    for key in ["system_type_id", "pipe_type_id", "level_id"]:
        eid = data.get(key)
        if _is_invalid_id(eid) or doc.GetElement(eid) is None:
            data[key] = None
    try:
        pipe_type = doc.GetElement(data.get("pipe_type_id"))
        if (
            pipe_type is None
            or pipe_type.Category is None
            or pipe_type.Category.Id.IntegerValue
            != int(DB.BuiltInCategory.OST_PipeCurves)
        ):
            data["pipe_type_id"] = None
    except Exception:
        data["pipe_type_id"] = None
    try:
        if not isinstance(doc.GetElement(data.get("level_id")), DB.Level):
            data["level_id"] = None
    except Exception:
        data["level_id"] = None
    return data


def _pipe_diameter_mm(pipe_el):
    diameter = _get_pipe_diameter(pipe_el)
    if diameter is None:
        return None
    try:
        return _ft_to_mm(diameter)
    except Exception:
        return None


def _collect_pipe_fitting_ids(doc):
    ids = set()
    try:
        collector = (
            DB.FilteredElementCollector(doc)
            .OfCategory(DB.BuiltInCategory.OST_PipeFitting)
            .WhereElementIsNotElementType()
        )
        for fitting in collector:
            ids.add(fitting.Id.IntegerValue)
    except Exception as ex:
        if DEBUG:
            print("FITTING SNAPSHOT DEBUG -> collection failed: {}".format(ex))
        return None
    return ids


def _preflight_routed_connection(
    doc,
    source_pipe,
    source_conn,
    target_pipe,
    target_conn,
    route_points,
    slope_info,
):
    warnings = []
    errors = []
    resolved = _resolved_pipe_creation_data(doc, source_pipe)
    source_diameter_mm = _pipe_diameter_mm(source_pipe)
    target_diameter_mm = _pipe_diameter_mm(target_pipe)

    if source_conn is None:
        errors.append("Source connector was not resolved.")
    if target_conn is None:
        errors.append("Target connector was not resolved.")
    if not _is_pipe(source_pipe) and not _has_valid_mep_connectors(source_pipe):
        errors.append(
            "Source element is not a PipeCurve and has no valid MEP connectors."
        )
    if not _is_pipe(target_pipe) and not _has_valid_mep_connectors(target_pipe):
        errors.append(
            "Target element is not a PipeCurve and has no valid MEP connectors."
        )

    source_domain = _connector_domain_text(source_conn)
    target_domain = _connector_domain_text(target_conn)
    if source_conn is not None and target_conn is not None:
        if not source_domain or not target_domain:
            errors.append("Could not resolve source/target connector domains.")
        elif source_domain != target_domain:
            errors.append(
                "Connector domains do not match: source={} target={}.".format(
                    source_domain, target_domain
                )
            )

    if resolved.get("pipe_type_id") is None:
        errors.append("Resolved pipe type id is invalid.")
    if resolved.get("system_type_id") is None:
        errors.append("Resolved system type id is invalid.")
    if resolved.get("level_id") is None:
        errors.append("Resolved level id is invalid.")
    if resolved.get("diameter") is None or resolved.get("diameter") <= 0:
        errors.append("Resolved source pipe diameter is invalid.")

    for idx in range(0, max(0, len(route_points or []) - 1)):
        length_mm = _segment_length_mm(route_points[idx], route_points[idx + 1])
        if length_mm <= HARDENED_MIN_SEGMENT_MM:
            errors.append(
                "Route segment {} must be greater than {:.1f} mm; resolved {:.1f} mm.".format(
                    idx + 1, HARDENED_MIN_SEGMENT_MM, length_mm
                )
            )

    flat_different_z = False
    flat_z_delta_mm = None
    source_origin = _connector_origin(source_conn)
    target_origin = _connector_origin(target_conn)
    if source_origin is not None and target_origin is not None:
        flat_z_delta_mm = abs(_ft_to_mm(source_origin.Z - target_origin.Z))
    if (
        source_origin is not None
        and target_origin is not None
        and slope_info
        and slope_info.get("mode") == SLOPE_MODE_FLAT
    ):
        flat_different_z = flat_z_delta_mm > FLAT_Z_WARNING_TOL_MM
        if flat_different_z:
            warnings.append(
                "Flat / zero slope selected while endpoint elevations differ by {:.1f} mm; vertical absorber segment(s) will be required.".format(
                    flat_z_delta_mm
                )
            )

    diameter_mismatch = False
    if source_diameter_mm is not None and target_diameter_mm is not None:
        diameter_mismatch = abs(source_diameter_mm - target_diameter_mm) > 1.0
        if diameter_mismatch:
            warnings.append(
                "Source diameter {:.1f} mm differs from target diameter {:.1f} mm.".format(
                    source_diameter_mm, target_diameter_mm
                )
            )

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "flat_different_z": flat_different_z,
        "flat_z_delta_mm": flat_z_delta_mm,
        "source_diameter_mm": source_diameter_mm,
        "target_diameter_mm": target_diameter_mm,
        "diameter_mismatch": diameter_mismatch,
        "source_domain": source_domain,
        "target_domain": target_domain,
        "resolved": resolved,
    }


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


def _build_route_debug_cache(
    source_pipe,
    target_pipe,
    source_conn,
    target_conn,
    raw_points,
    route_points,
    slope_info,
    cleanup_stats,
):
    cache = {
        "source_element_id": _eid_int(source_pipe),
        "target_element_id": _eid_int(target_pipe),
        "source_connector_owner_id": _connector_owner_id(source_conn),
        "target_connector_owner_id": _connector_owner_id(target_conn),
        "source_connector_origin_mm": _xyz_mm_tuple(_connector_origin(source_conn)),
        "target_connector_origin_mm": _xyz_mm_tuple(_connector_origin(target_conn)),
        "raw_route_points_mm": [_xyz_mm_tuple(p) for p in raw_points or []],
        "route_points_mm": [_xyz_mm_tuple(p) for p in route_points or []],
        "route_segment_slopes": [
            _segment_slope_ratio(route_points[i], route_points[i + 1])
            for i in range(0, max(0, len(route_points or []) - 1))
        ],
        "created_pipe_ids": [],
        "created_fitting_ids": [],
        "fitting_ids_before_count": None,
        "fitting_ids_after_count": None,
        "fitting_detection_status": "",
        "created_pipe_final_slopes": [],
        "fitting_connect_attempts": [],
        "warnings": [],
        "resolved_system_type_id": None,
        "resolved_pipe_type_id": None,
        "resolved_level_id": None,
        "resolved_diameter_mm": None,
        "source_diameter_mm": None,
        "target_diameter_mm": None,
        "diameter_mismatch_decision": "Source diameter (no mismatch)",
        "flat_z_delta_mm": None,
        "opening_element_ids": [],
        "opening_waypoints_mm": [],
        "opening_waypoint_details": [],
        "opening_waypoint_mode": "",
        "opening_waypoint_alignment_mode": "",
        "opening_shaft_xy_tolerance_mm": OPENING_SHAFT_XY_TOL_MM,
        "opening_shaft_relaxed_xy_tolerance_mm": OPENING_SHAFT_RELAXED_XY_TOL_MM,
        "opening_shaft_groups": [],
        "opening_shaft_grouping_decisions": [],
        "opening_shaft_xy_mode": "",
        "opening_route_warnings": [],
        "opening_route_shape_mode": "",
        "opening_route_shape_detail": "",
        "direct_route_points_before_cleanup_mm": [],
        "direct_route_points_after_cleanup_mm": [],
        "inserted_horizontal_entry_point_mm": None,
        "inserted_horizontal_exit_point_mm": None,
        "route_simplification_reason": "",
        "waypoint_count": 0,
        "source_connector_domain": "",
        "target_connector_domain": "",
        "transaction_started": False,
        "rollback_completed": False,
        "failure_reason": "",
        "slope_info": slope_info or {},
        "cleanup_stats": cleanup_stats or {},
    }
    return cache


def _create_pipe_segment(doc, source_pipe, start, end, resolved_data=None):
    if Pipe is None:
        raise Exception("Autodesk.Revit.DB.Plumbing.Pipe could not be imported.")
    resolved_data = resolved_data or {}
    system_type_id = resolved_data.get("system_type_id") or _get_pipe_system_type_id(
        source_pipe
    )
    pipe_type_id = resolved_data.get("pipe_type_id") or source_pipe.GetTypeId()
    level_id = resolved_data.get("level_id") or _get_pipe_level_id(source_pipe)
    diameter = (
        resolved_data.get("diameter")
        if resolved_data.get("diameter") is not None
        else _get_pipe_diameter(source_pipe)
    )
    if _is_invalid_id(system_type_id):
        raise Exception("Could not resolve source pipe system type.")
    if _is_invalid_id(pipe_type_id):
        raise Exception("Could not resolve source pipe type.")
    if _is_invalid_id(level_id):
        raise Exception("Could not resolve source pipe level.")

    new_pipe = Pipe.Create(doc, system_type_id, pipe_type_id, level_id, start, end)
    _set_pipe_diameter(new_pipe, diameter)
    return new_pipe


def _new_fitting_between(doc, conn_a, conn_b, diagnostics=None, context=""):
    origin_a = _connector_origin(conn_a)
    origin_b = _connector_origin(conn_b)
    dist_mm = _point_distance_mm(origin_a, origin_b) if origin_a and origin_b else None
    dir_a = _connector_direction(conn_a)
    dir_b = _connector_direction(conn_b)
    angle = _angle_between_degrees(dir_a, dir_b)
    if DEBUG:
        print(
            "CONNECTOR JOIN DEBUG -> a_owner: {}; b_owner: {}; dist_mm: {}; a_dir: {}; b_dir: {}; angle_deg: {}".format(
                _connector_owner_id(conn_a),
                _connector_owner_id(conn_b),
                dist_mm,
                _xyz_vec_mm(dir_a),
                _xyz_vec_mm(dir_b),
                angle,
            )
        )
    attempt = {
        "context": context or "connector join",
        "a_owner_id": _connector_owner_id(conn_a),
        "b_owner_id": _connector_owner_id(conn_b),
        "distance_mm": dist_mm,
        "angle_degrees": angle,
        "methods": [],
        "result": "",
        "failure": "",
    }
    if diagnostics is not None:
        diagnostics.append(attempt)

    if dist_mm is not None and dist_mm <= ROUTE_DEDUPE_TOL_MM:
        attempt["methods"].append("ConnectTo")
        try:
            conn_a.ConnectTo(conn_b)
            attempt["result"] = "ConnectTo succeeded"
            return None
        except Exception as ex:
            attempt["failure"] = "ConnectTo: {}".format(ex)
            if DEBUG:
                print(
                    "CONNECTOR JOIN DEBUG -> coincident ConnectTo failed: {}".format(ex)
                )

    # Try elbow first for angled route changes, then union for collinear joins.
    attempt["methods"].append("NewElbowFitting")
    try:
        fitting = doc.Create.NewElbowFitting(conn_a, conn_b)
        if fitting:
            attempt["result"] = "NewElbowFitting succeeded; fitting id {}".format(
                _eid_int(fitting)
            )
            return fitting
    except Exception as ex:
        attempt["failure"] = "{}; NewElbowFitting: {}".format(
            attempt["failure"], ex
        ).strip("; ")
        if DEBUG:
            print("CONNECTOR JOIN DEBUG -> NewElbowFitting failed: {}".format(ex))
    attempt["methods"].append("NewUnionFitting")
    try:
        fitting = doc.Create.NewUnionFitting(conn_a, conn_b)
        if fitting:
            attempt["result"] = "NewUnionFitting succeeded; fitting id {}".format(
                _eid_int(fitting)
            )
            return fitting
    except Exception as ex:
        attempt["failure"] = "{}; NewUnionFitting: {}".format(
            attempt["failure"], ex
        ).strip("; ")
        if DEBUG:
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
    doc,
    source_conn,
    target_conn,
    new_pipes,
    route_points,
    diagnostics=None,
):
    fittings = []
    if not new_pipes:
        raise Exception("No generated pipe segments were created.")

    first_conn = _find_pipe_end_connector(new_pipes[0], route_points[0])
    if not first_conn:
        if diagnostics is not None:
            diagnostics.append(
                {
                    "context": "source to first generated pipe",
                    "methods": [],
                    "result": "",
                    "failure": "Could not resolve first generated pipe connector.",
                }
            )
        raise Exception("Could not resolve first generated pipe connector.")
    fitting = _new_fitting_between(
        doc, source_conn, first_conn, diagnostics, "source to first generated pipe"
    )
    if fitting:
        fittings.append(fitting)

    for idx in range(0, len(new_pipes) - 1):
        join_pt = route_points[idx + 1]
        a_conn = _find_pipe_end_connector(new_pipes[idx], join_pt)
        b_conn = _find_pipe_end_connector(new_pipes[idx + 1], join_pt)
        if not a_conn or not b_conn:
            if diagnostics is not None:
                diagnostics.append(
                    {
                        "context": "generated pipe segment {} to {}".format(
                            idx + 1, idx + 2
                        ),
                        "methods": [],
                        "result": "",
                        "failure": "Could not resolve generated pipe connector at route waypoint {}.".format(
                            _xyz_mm(join_pt)
                        ),
                    }
                )
            raise Exception(
                "Could not resolve generated pipe connector at route waypoint {}.".format(
                    _xyz_mm(join_pt)
                )
            )
        fitting = _new_fitting_between(
            doc,
            a_conn,
            b_conn,
            diagnostics,
            "generated pipe segment {} to {}".format(idx + 1, idx + 2),
        )
        if fitting:
            fittings.append(fitting)

    last_conn = _find_pipe_end_connector(new_pipes[-1], route_points[-1])
    if not last_conn:
        if diagnostics is not None:
            diagnostics.append(
                {
                    "context": "last generated pipe to target",
                    "methods": [],
                    "result": "",
                    "failure": "Could not resolve last generated pipe connector.",
                }
            )
        raise Exception("Could not resolve last generated pipe connector.")
    fitting = _new_fitting_between(
        doc, last_conn, target_conn, diagnostics, "last generated pipe to target"
    )
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
        print(
            "source connector owner id: {}".format(
                debug_cache.get("source_connector_owner_id")
            )
        )
        print(
            "target connector owner id: {}".format(
                debug_cache.get("target_connector_owner_id")
            )
        )
        print(
            "source connector origin mm: {}".format(
                _xyz_mm_from_tuple(debug_cache.get("source_connector_origin_mm"))
            )
        )
        print(
            "target connector origin mm: {}".format(
                _xyz_mm_from_tuple(debug_cache.get("target_connector_origin_mm"))
            )
        )
        si = debug_cache.get("slope_info") or {}
        if si:
            print("selected slope mode: {}".format(si.get("mode")))
            print(
                "selected route slope ratio/mm_per_m: {}".format(
                    _format_slope(si.get("selected_slope"))
                )
            )
        print(
            "resolved system type id: {}".format(
                debug_cache.get("resolved_system_type_id")
            )
        )
        print(
            "resolved pipe type id: {}".format(debug_cache.get("resolved_pipe_type_id"))
        )
        print("resolved level id: {}".format(debug_cache.get("resolved_level_id")))
        print(
            "resolved diameter mm: {}".format(debug_cache.get("resolved_diameter_mm"))
        )
        print("source diameter mm: {}".format(debug_cache.get("source_diameter_mm")))
        print("target diameter mm: {}".format(debug_cache.get("target_diameter_mm")))
        print(
            "diameter mismatch decision: {}".format(
                debug_cache.get("diameter_mismatch_decision")
            )
        )
        print("flat endpoint Z delta mm: {}".format(debug_cache.get("flat_z_delta_mm")))
        print(
            "opening element ids: {}".format(
                debug_cache.get("opening_element_ids") or []
            )
        )
        print(
            "opening waypoint coordinates mm: {}".format(
                [
                    _xyz_mm_from_tuple(point)
                    for point in debug_cache.get("opening_waypoints_mm") or []
                ]
            )
        )
        print(
            "opening waypoint mode: {}".format(
                debug_cache.get("opening_waypoint_mode") or "-"
            )
        )
        print(
            "opening waypoint alignment mode: {}".format(
                debug_cache.get("opening_waypoint_alignment_mode") or "-"
            )
        )
        print(
            "opening shaft strict grouping tolerance mm: {}".format(
                debug_cache.get("opening_shaft_xy_tolerance_mm")
            )
        )
        print(
            "opening shaft relaxed grouping tolerance mm: {}".format(
                debug_cache.get("opening_shaft_relaxed_xy_tolerance_mm")
            )
        )
        print(
            "opening shaft groups: {}".format(
                debug_cache.get("opening_shaft_groups") or []
            )
        )
        print(
            "opening shaft grouping decisions: {}".format(
                debug_cache.get("opening_shaft_grouping_decisions") or []
            )
        )
        print(
            "shaft XY mode: {}".format(debug_cache.get("opening_shaft_xy_mode") or "-")
        )
        print(
            "opening route warnings: {}".format(
                debug_cache.get("opening_route_warnings") or []
            )
        )
        print(
            "opening route shape mode: {}".format(
                debug_cache.get("opening_route_shape_mode") or "-"
            )
        )
        print(
            "opening route shape detail: {}".format(
                debug_cache.get("opening_route_shape_detail") or "-"
            )
        )
        print(
            "inserted horizontal entry point mm: {}".format(
                _xyz_mm_from_tuple(
                    debug_cache.get("inserted_horizontal_entry_point_mm")
                )
            )
        )
        print(
            "inserted horizontal exit point mm: {}".format(
                _xyz_mm_from_tuple(debug_cache.get("inserted_horizontal_exit_point_mm"))
            )
        )
        print(
            "direct route points before cleanup mm: {}".format(
                [
                    _xyz_mm_from_tuple(point)
                    for point in debug_cache.get(
                        "direct_route_points_before_cleanup_mm"
                    )
                    or []
                ]
            )
        )
        print(
            "direct route points after cleanup mm: {}".format(
                [
                    _xyz_mm_from_tuple(point)
                    for point in debug_cache.get("direct_route_points_after_cleanup_mm")
                    or []
                ]
            )
        )
        print(
            "route simplification reason: {}".format(
                debug_cache.get("route_simplification_reason") or "-"
            )
        )
        for detail in debug_cache.get("opening_waypoint_details") or []:
            print(
                "opening waypoint detail: source={}; host element id={}; "
                "link instance id={}; linked element id={}; linked document={}; "
                "raw picked point mm={}; opening center mm={}; "
                "original waypoint mm={}; aligned waypoint mm={}; "
                "final waypoint mm={}; grouped ids={}; grouped XY delta mm={}; "
                "shaft XY mode={}; selected shaft XY mm={}; "
                "original grouped XY values mm={}; shaft XY reason={}; "
                "resolution={}".format(
                    detail.get("source_type"),
                    detail.get("host_element_id"),
                    detail.get("link_instance_id"),
                    detail.get("linked_element_id"),
                    detail.get("linked_document_title") or "-",
                    _xyz_mm_from_tuple(detail.get("raw_picked_point_mm")),
                    _xyz_mm_from_tuple(detail.get("opening_center_mm")),
                    _xyz_mm_from_tuple(detail.get("original_waypoint_mm")),
                    _xyz_mm_from_tuple(detail.get("aligned_waypoint_mm")),
                    _xyz_mm_from_tuple(detail.get("final_waypoint_mm")),
                    detail.get("grouped_waypoint_ids") or [],
                    detail.get("grouped_xy_delta_mm"),
                    detail.get("shaft_xy_mode") or "-",
                    detail.get("selected_shaft_xy_mm"),
                    detail.get("original_grouped_waypoint_xy_mm") or [],
                    detail.get("shaft_xy_reason") or "-",
                    detail.get("waypoint_resolution") or "-",
                )
            )
        print("waypoint count: {}".format(debug_cache.get("waypoint_count") or 0))
        print(
            "source connector domain: {}".format(
                debug_cache.get("source_connector_domain")
            )
        )
        print(
            "target connector domain: {}".format(
                debug_cache.get("target_connector_domain")
            )
        )
        print(
            "route segment count: {}".format(
                len(debug_cache.get("route_segment_slopes") or [])
            )
        )
        print("created pipe ids: {}".format(debug_cache.get("created_pipe_ids") or []))
        print(
            "created fitting ids: {}".format(
                debug_cache.get("created_fitting_ids") or []
            )
        )
        print(
            "pipe fitting snapshot counts before/after: {} / {}".format(
                debug_cache.get("fitting_ids_before_count"),
                debug_cache.get("fitting_ids_after_count"),
            )
        )
        print(
            "pipe fitting detection status: {}".format(
                debug_cache.get("fitting_detection_status") or "-"
            )
        )
        print(
            "created pipe final slopes: {}".format(
                debug_cache.get("created_pipe_final_slopes") or []
            )
        )
        print("warnings: {}".format(debug_cache.get("warnings") or []))
        print(
            "fitting/connect attempt count: {}".format(
                len(debug_cache.get("fitting_connect_attempts") or [])
            )
        )
        if DEBUG:
            for idx, pt in enumerate(debug_cache.get("raw_route_points_mm") or []):
                print(
                    "raw route point {} mm: {}".format(idx + 1, _xyz_mm_from_tuple(pt))
                )
            for idx, pt in enumerate(debug_cache.get("route_points_mm") or []):
                print(
                    "simplified route point {} mm: {}".format(
                        idx + 1, _xyz_mm_from_tuple(pt)
                    )
                )
            print(
                "source slope ratio/mm_per_m: {}".format(
                    _format_slope(si.get("source_slope"))
                )
            )
            print(
                "target slope ratio/mm_per_m: {}".format(
                    _format_slope(si.get("target_slope"))
                )
            )
            for idx, slope in enumerate(debug_cache.get("route_segment_slopes") or []):
                print(
                    "route segment {} slope: {}".format(idx + 1, _format_slope(slope))
                )
            cs = debug_cache.get("cleanup_stats") or {}
            print("removed point count: {}".format(cs.get("removed_point_count")))
            print(
                "removed short segment count: {}".format(
                    cs.get("removed_short_segment_count")
                )
            )
            print(
                "minimum segment length found mm: {}".format(
                    cs.get("minimum_segment_length_mm")
                )
            )
            print("fitting/connect attempts and failures:")
            for attempt in debug_cache.get("fitting_connect_attempts") or []:
                print("  {}".format(attempt))
        print("transaction started: {}".format(debug_cache.get("transaction_started")))
        if debug_cache.get("transaction_started"):
            print(
                "rollback completed: {}".format(debug_cache.get("rollback_completed"))
            )
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
        print(
            "source slope ratio/mm_per_m: {}".format(
                _format_slope(slope_info.get("source_slope"))
            )
        )
        print(
            "target slope ratio/mm_per_m: {}".format(
                _format_slope(slope_info.get("target_slope"))
            )
        )
        print(
            "selected route slope ratio/mm_per_m: {}".format(
                _format_slope(slope_info.get("selected_slope"))
            )
        )
    for idx in range(0, max(0, len(route_points or []) - 1)):
        seg_slope = _segment_slope_ratio(route_points[idx], route_points[idx + 1])
        print("route segment {} slope: {}".format(idx + 1, _format_slope(seg_slope)))
    for pipe in created_pipes or []:
        p0, p1 = _pipe_location_endpoints(pipe)
        print(
            "created pipe {} final slope: {}".format(
                _eid_int(pipe),
                _format_slope(_segment_slope_ratio(p0, p1) if p0 and p1 else None),
            )
        )
    print("created pipe ids: {}".format([_eid_int(p) for p in created_pipes or []]))
    print(
        "created fitting ids: {}".format([_eid_int(f) for f in created_fittings or []])
    )
    if failure_reason:
        print("failure reason: {}".format(failure_reason))


def _show_hardened_early_failure(command_title, reason, debug_cache=None):
    if debug_cache is not None:
        debug_cache["failure_reason"] = reason
        _print_route_debug(None, None, None, None, None, debug_cache=debug_cache)
    else:
        print("GENERATE ROUTED CONNECTION DEBUG -> failure reason: {}".format(reason))
    forms.alert(
        "FAILED\n\n"
        "Created pipe ids: []\n"
        "Warnings: {}\n"
        "Reason: {}\n\n"
        "Structured diagnostics were printed to the pyRevit output.".format(
            (
                "; ".join(debug_cache.get("warnings") or [])
                if debug_cache is not None
                else "None"
            )
            or "None",
            reason,
        ),
        title=command_title,
    )


def _show_hardened_cancelled(command_title, reason, debug_cache=None):
    if debug_cache is not None:
        debug_cache["failure_reason"] = reason
        debug_cache["transaction_started"] = False
        print("GENERATE ROUTED CONNECTION RESULT -> CANCELLED")
        _print_route_debug(None, None, None, None, None, debug_cache=debug_cache)
    else:
        print("GENERATE ROUTED CONNECTION RESULT -> CANCELLED")
        print("transaction started: False")
        print("reason: {}".format(reason))
    forms.alert(
        "CANCELLED\n\n"
        "Reason: {}\n"
        "No geometry created.\n\n"
        "Cancelled before transaction; no geometry created.".format(reason),
        title=command_title,
    )


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


class _HostOpeningElementSelectionFilter(ISelectionFilter):
    def AllowElement(self, element):
        if _is_revit_link(element) or _is_import_instance(element):
            return False
        return _is_opening_candidate(element)

    def AllowReference(self, reference, point):
        return True


class _LinkedOpeningElementSelectionFilter(ISelectionFilter):
    def AllowElement(self, element):
        return _is_revit_link(element)

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
    if any(tok in hay for tok in reject_tokens) and not any(
        tok in hay for tok in opening_tokens
    ):
        return {
            "confidence": "REJECTED",
            "reason": "Rejected category/name: {}".format(cat),
        }
    if any(tok in hay for tok in high_tokens):
        return {"confidence": "HIGH", "reason": "High-confidence opening indicator."}
    if any(tok in hay for tok in opening_tokens):
        return {
            "confidence": "MEDIUM",
            "reason": "Probable sleeve/penetration/void indicator.",
        }
    return {"confidence": "LOW", "reason": "No semantic opening indicator."}


def _is_opening_candidate(el):
    cls = _classify_opening_candidate(el)
    return cls.get("confidence") in ["HIGH", "MEDIUM"]


def _opening_candidate_label(info, source_conn=None, target_conn=None):
    el = info.get("element")
    cls = info.get("classification") or _classify_opening_candidate(el)
    dist = ""
    if source_conn and target_conn and info.get("center"):
        dist = " | dist {:.1f}mm".format(
            _ft_to_mm(
                _distance_to_corridor_xy(
                    info.get("center"), source_conn.Origin, target_conn.Origin
                )
            )
        )
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


def _reference_global_point(reference):
    try:
        return getattr(reference, "GlobalPoint", None)
    except Exception:
        return None


def _opening_element_center(el, active_view):
    try:
        location = el.Location
        point = getattr(location, "Point", None)
        if point is not None:
            return point, "host Location.Point"
    except Exception:
        pass
    try:
        bb = el.get_BoundingBox(active_view)
        if bb:
            return (
                DB.XYZ(
                    (bb.Min.X + bb.Max.X) / 2.0,
                    (bb.Min.Y + bb.Max.Y) / 2.0,
                    (bb.Min.Z + bb.Max.Z) / 2.0,
                ),
                "host bounding box center",
            )
    except Exception:
        pass
    center = _bbox_center(el)
    if center is not None:
        return center, "host bounding box center"
    return None, ""


def _linked_opening_element_center(linked_el, link_instance):
    point = None
    resolution = ""
    try:
        location = linked_el.Location
        point = getattr(location, "Point", None)
        if point is not None:
            resolution = "linked Location.Point transformed"
    except Exception:
        point = None
    if point is None:
        point = _bbox_center(linked_el)
        if point is not None:
            resolution = "linked bounding box center transformed"
    if point is None:
        return None, ""
    try:
        transform = link_instance.GetTransform()
        return transform.OfPoint(point), resolution
    except Exception:
        return None, ""


def _resolve_opening_waypoints(opening_infos, waypoint_mode):
    for info in opening_infos or []:
        picked = info.get("raw_picked_point")
        opening_center = info.get("opening_center")
        final_point = None
        resolution = ""

        if waypoint_mode == "Use picked point exactly":
            if picked is not None:
                final_point = picked
                if info.get("source_type") == "LINK":
                    resolution = "picked point on linked element"
                else:
                    resolution = "picked point on host element"
            elif opening_center is not None:
                final_point = opening_center
                resolution = "{}; picked point unavailable".format(
                    info.get("opening_center_resolution") or "opening center"
                )
        elif waypoint_mode == "Use picked XY, opening center Z":
            if picked is not None and opening_center is not None:
                final_point = DB.XYZ(picked.X, picked.Y, opening_center.Z)
                resolution = "picked XY, opening center Z"
            elif opening_center is not None:
                final_point = opening_center
                resolution = "{}; picked point unavailable".format(
                    info.get("opening_center_resolution") or "opening center"
                )
            elif picked is not None:
                final_point = picked
                resolution = "picked point; opening center unavailable"
        else:
            if opening_center is not None:
                final_point = opening_center
                resolution = info.get("opening_center_resolution") or "opening center"
            elif picked is not None:
                final_point = picked
                resolution = "picked point; opening center unavailable"

        if final_point is None:
            return (
                False,
                "Could not resolve a final waypoint for opening {}.".format(
                    info.get("id")
                ),
            )
        info["center"] = final_point
        info["waypoint_resolution"] = resolution
    return True, ""


def _align_opening_waypoints(opening_infos, alignment_mode):
    infos = list(opening_infos or [])
    groups = []
    decisions = []
    warnings = []
    for info in infos:
        info["original_waypoint"] = info.get("center")
        info["aligned_waypoint"] = info.get("center")
        info["grouped_waypoint_ids"] = []
        info["grouped_xy_delta_mm"] = None
        info["grouping_decision"] = ""

    if (
        alignment_mode != "Align close opening waypoints into vertical shaft"
        or len(infos) < 2
    ):
        return groups, decisions, warnings

    grouped_indexes = []
    current_group = [0]
    for idx in range(1, len(infos)):
        previous = infos[idx - 1].get("center")
        current = infos[idx].get("center")
        xy_delta_mm = _ft_to_mm(_xy_distance(previous, current))
        previous_id = infos[idx - 1].get("id")
        current_id = infos[idx].get("id")
        decision = ""
        should_group = False
        if xy_delta_mm <= OPENING_SHAFT_XY_TOL_MM:
            decision = "automatic strict grouping"
            should_group = True
        elif xy_delta_mm <= OPENING_SHAFT_RELAXED_XY_TOL_MM:
            choice = _ask_choice(
                [
                    "Yes, align into shaft",
                    "No, keep separate",
                ],
                (
                    "Opening waypoints are {:.1f} mm apart in XY. "
                    "Treat as one vertical shaft?"
                ).format(xy_delta_mm),
                default="Yes, align into shaft",
            )
            if choice == "Yes, align into shaft":
                decision = "user-confirmed relaxed grouping"
                should_group = True
            else:
                decision = "user declined relaxed grouping"
        else:
            decision = "not grouped, beyond relaxed tolerance"
            warnings.append(
                "Opening waypoints too far apart for shaft grouping: {:.1f} mm.".format(
                    xy_delta_mm
                )
            )
        decisions.append(
            {
                "waypoint_ids": [previous_id, current_id],
                "grouped_xy_delta_mm": xy_delta_mm,
                "grouping_decision": decision,
            }
        )
        if should_group:
            current_group.append(idx)
        else:
            grouped_indexes.append(current_group)
            current_group = [idx]
    grouped_indexes.append(current_group)

    for indexes in grouped_indexes:
        if len(indexes) < 2:
            continue
        points = [infos[idx].get("center") for idx in indexes]
        avg_x = sum([point.X for point in points]) / float(len(points))
        avg_y = sum([point.Y for point in points]) / float(len(points))
        group_ids = [infos[idx].get("id") for idx in indexes]
        adjacent_deltas = []
        for point_idx in range(1, len(points)):
            adjacent_deltas.append(
                _ft_to_mm(_xy_distance(points[point_idx - 1], points[point_idx]))
            )
        grouped_xy_delta_mm = max(adjacent_deltas) if adjacent_deltas else 0.0

        group_detail = {
            "grouped_waypoint_ids": list(group_ids),
            "grouped_xy_delta_mm": grouped_xy_delta_mm,
            "average_xy_mm": (_ft_to_mm(avg_x), _ft_to_mm(avg_y)),
            "grouping_decisions": [
                decision
                for decision in decisions
                if decision.get("waypoint_ids")[0] in group_ids
                and decision.get("waypoint_ids")[1] in group_ids
            ],
        }
        groups.append(group_detail)
        group_decision_text = ", ".join(
            [
                decision.get("grouping_decision")
                for decision in group_detail.get("grouping_decisions") or []
            ]
        )
        for idx in indexes:
            info = infos[idx]
            original = info.get("center")
            aligned = DB.XYZ(avg_x, avg_y, original.Z)
            info["center"] = aligned
            info["aligned_waypoint"] = aligned
            info["grouped_waypoint_ids"] = list(group_ids)
            info["grouped_xy_delta_mm"] = grouped_xy_delta_mm
            info["grouping_decision"] = group_decision_text
            info["waypoint_resolution"] = "{}; aligned into vertical shaft".format(
                info.get("waypoint_resolution") or "resolved waypoint"
            )
    return groups, decisions, warnings


def _apply_shaft_xy_mode(
    opening_infos, shaft_groups, shaft_xy_mode, source_point, target_point
):
    infos_by_id = {}
    for info in opening_infos or []:
        infos_by_id[info.get("id")] = info

    for group in shaft_groups or []:
        group_ids = list(group.get("grouped_waypoint_ids") or [])
        group_infos = [
            infos_by_id.get(group_id)
            for group_id in group_ids
            if infos_by_id.get(group_id) is not None
        ]
        if not group_infos:
            continue
        originals = [
            info.get("original_waypoint") or info.get("center") for info in group_infos
        ]
        selected = originals[0]
        reason = "first opening XY"

        if shaft_xy_mode == SHAFT_XY_AVERAGE:
            selected_x = sum([point.X for point in originals]) / float(len(originals))
            selected_y = sum([point.Y for point in originals]) / float(len(originals))
            reason = "average grouped opening XY"
        elif shaft_xy_mode == SHAFT_XY_LAST:
            selected = originals[-1]
            selected_x = selected.X
            selected_y = selected.Y
            reason = "last opening XY"
        elif shaft_xy_mode == SHAFT_XY_CLOSEST_SOURCE:
            selected = min(
                originals, key=lambda point: _xy_distance(point, source_point)
            )
            selected_x = selected.X
            selected_y = selected.Y
            reason = "closest to source"
        elif shaft_xy_mode == SHAFT_XY_CLOSEST_TARGET:
            selected = min(
                originals, key=lambda point: _xy_distance(point, target_point)
            )
            selected_x = selected.X
            selected_y = selected.Y
            reason = "closest to target"
        else:
            selected_x = selected.X
            selected_y = selected.Y

        original_xy_values_mm = [
            (_ft_to_mm(point.X), _ft_to_mm(point.Y)) for point in originals
        ]
        selected_xy_mm = (_ft_to_mm(selected_x), _ft_to_mm(selected_y))
        group["shaft_xy_mode"] = shaft_xy_mode
        group["selected_shaft_xy_mm"] = selected_xy_mm
        group["original_grouped_waypoint_xy_mm"] = original_xy_values_mm
        group["shaft_xy_reason"] = reason

        for info in group_infos:
            original = info.get("original_waypoint") or info.get("center")
            aligned = DB.XYZ(selected_x, selected_y, original.Z)
            info["center"] = aligned
            info["aligned_waypoint"] = aligned
            info["shaft_xy_mode"] = shaft_xy_mode
            info["selected_shaft_xy_mm"] = selected_xy_mm
            info["original_grouped_waypoint_xy_mm"] = list(original_xy_values_mm)
            info["shaft_xy_reason"] = reason
    return shaft_groups


def _linked_document_title(link_doc):
    try:
        return link_doc.Title or ""
    except Exception:
        return ""


def _pick_opening_waypoints_in_order():
    infos = []
    no_selection_message = "No opening waypoint selected; no geometry created."
    options = [
        "Select host opening(s)",
        "Select linked opening(s)",
        "Finish opening selection",
    ]
    while True:
        mode = _ask_choice(
            options,
            "Choose opening selection mode. Selected waypoints: {}".format(len(infos)),
            default="Select host opening(s)",
        )
        if not mode or mode == "Finish opening selection":
            if infos:
                return infos, ""
            return [], no_selection_message

        try:
            if mode == "Select linked opening(s)":
                try:
                    object_type = ObjectType.LinkedElement
                except Exception:
                    return (
                        [],
                        "This Revit API does not expose linked-element selection.",
                    )
                ref = revit.uidoc.Selection.PickObject(
                    object_type,
                    _LinkedOpeningElementSelectionFilter(),
                    "Pick one linked opening waypoint; ESC finishes selection",
                )
                link_instance = revit.doc.GetElement(ref.ElementId)
                if not _is_revit_link(link_instance):
                    return [], "Selected reference is not a Revit link instance."
                link_doc = link_instance.GetLinkDocument()
                if link_doc is None:
                    return [], "Could not access the selected Revit link document."
                linked_el = link_doc.GetElement(ref.LinkedElementId)
                if linked_el is None:
                    return [], "Could not resolve the selected linked opening element."
                classification = _classify_opening_candidate(linked_el)
                if classification.get("confidence") not in ["HIGH", "MEDIUM"]:
                    return (
                        [],
                        "Linked element {} is not classified as an opening: {}".format(
                            ref.LinkedElementId.IntegerValue,
                            classification.get("reason"),
                        ),
                    )
                picked_point = _reference_global_point(ref)
                opening_center, center_resolution = _linked_opening_element_center(
                    linked_el, link_instance
                )
                if picked_point is None and opening_center is None:
                    return (
                        [],
                        "Could not resolve a waypoint for linked element {}.".format(
                            ref.LinkedElementId.IntegerValue
                        ),
                    )
                infos.append(
                    {
                        "id": "{}:{}".format(
                            link_instance.Id.IntegerValue,
                            ref.LinkedElementId.IntegerValue,
                        ),
                        "host_or_linked": "LINK",
                        "source_type": "LINK",
                        "host_element_id": None,
                        "link_instance_id": link_instance.Id.IntegerValue,
                        "linked_element_id": ref.LinkedElementId.IntegerValue,
                        "linked_document_title": _linked_document_title(link_doc),
                        "element": linked_el,
                        "center": None,
                        "raw_picked_point": picked_point,
                        "opening_center": opening_center,
                        "opening_center_resolution": center_resolution,
                        "name": _safe_name(linked_el),
                        "classification": classification,
                        "waypoint_resolution": "",
                    }
                )
            else:
                ref = revit.uidoc.Selection.PickObject(
                    ObjectType.Element,
                    _HostOpeningElementSelectionFilter(),
                    "Pick one host opening waypoint; ESC finishes selection",
                )
                el = revit.doc.GetElement(ref.ElementId)
                if el is None:
                    return [], "Could not resolve selected opening element."
                picked_point = _reference_global_point(ref)
                opening_center, center_resolution = _opening_element_center(
                    el, revit.doc.ActiveView
                )
                if picked_point is None and opening_center is None:
                    return (
                        [],
                        "Could not resolve a waypoint for element {}.".format(
                            el.Id.IntegerValue
                        ),
                    )
                infos.append(
                    {
                        "id": str(el.Id.IntegerValue),
                        "host_or_linked": "HOST",
                        "source_type": "HOST",
                        "host_element_id": el.Id.IntegerValue,
                        "link_instance_id": None,
                        "linked_element_id": None,
                        "linked_document_title": "",
                        "element": el,
                        "center": None,
                        "raw_picked_point": picked_point,
                        "opening_center": opening_center,
                        "opening_center_resolution": center_resolution,
                        "name": _safe_name(el),
                        "classification": _classify_opening_candidate(el),
                        "waypoint_resolution": "",
                    }
                )
        except OperationCanceledException:
            if infos:
                return infos, ""
            return [], no_selection_message
        except Exception as ex:
            return [], "Opening selection failed: {}".format(ex)


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
                    "classification": {
                        "confidence": "MANUAL",
                        "reason": "User-picked route waypoint.",
                    },
                }
            return None

        if getattr(ref, "GlobalPoint", None):
            # Face picks are accepted as user-guided opening waypoints. Use the picked
            # point directly because a face-level bbox is not reliably available here.
            if not _is_link_reference(ref):
                el = doc.GetElement(ref.ElementId)
                if el and (_is_revit_link(el) or _is_import_instance(el)):
                    forms.alert(
                        "This is a link/import. Use Pick linked opening elements or Manual waypoint mode."
                    )
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
                    forms.alert(
                        "Selected host element is not classified as an opening: {}".format(
                            _safe_name(el)
                        )
                    )
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
                        _safe_name(linked_el),
                        _category_name(linked_el),
                        cls.get("reason"),
                    ),
                    yes=True,
                    no=True,
                    title="Linked Opening Selection",
                )
                if use_point and getattr(ref, "GlobalPoint", None):
                    return {
                        "id": "{}:{}:manual".format(
                            link_inst.Id.IntegerValue, ref.LinkedElementId.IntegerValue
                        ),
                        "host_or_linked": "linked-manual-waypoint",
                        "element": linked_el,
                        "center": ref.GlobalPoint,
                        "name": _safe_name(linked_el),
                        "classification": {
                            "confidence": "MANUAL",
                            "reason": "Picked point on non-opening linked element.",
                        },
                    }
                return None
            center = _bbox_center(linked_el, link_inst.GetTotalTransform())
            if not center:
                return None
            return {
                "id": "{}:{}".format(
                    link_inst.Id.IntegerValue, ref.LinkedElementId.IntegerValue
                ),
                "host_or_linked": "linked",
                "element": linked_el,
                "center": center,
                "name": _safe_name(linked_el),
                "classification": cls,
            }
        el = doc.GetElement(ref.ElementId)
        if el and (_is_revit_link(el) or _is_import_instance(el)):
            forms.alert(
                "This is a link/import. Use Pick linked opening elements or Manual waypoint mode."
            )
            return None
        cls = _classify_opening_candidate(el)
        if cls.get("confidence") not in ["HIGH", "MEDIUM"]:
            forms.alert(
                "Selected host element is not classified as an opening.\n\nReason: {}".format(
                    cls.get("reason")
                )
            )
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
        refs = list(
            revit.uidoc.Selection.PickObjects(
                object_type,
                _OpeningSelectionFilter(filter_mode),
                "Pick opening elements/waypoints in route order, then Finish",
            )
        )
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


def _print_opening_debug(
    opening_infos,
    waypoint_mode=None,
    alignment_mode=None,
    shaft_groups=None,
    grouping_decisions=None,
    alignment_warnings=None,
    shaft_xy_mode=None,
):
    print("OPENING ROUTE DEBUG")
    print("opening waypoint mode: {}".format(waypoint_mode or "-"))
    if alignment_mode is not None:
        print("opening waypoint alignment mode: {}".format(alignment_mode))
        print("strict shaft tolerance mm: {}".format(OPENING_SHAFT_XY_TOL_MM))
        print("relaxed shaft tolerance mm: {}".format(OPENING_SHAFT_RELAXED_XY_TOL_MM))
        print("shaft groups: {}".format(shaft_groups or []))
        print("shaft grouping decisions: {}".format(grouping_decisions or []))
        print("shaft alignment warnings: {}".format(alignment_warnings or []))
        print("shaft XY mode: {}".format(shaft_xy_mode or "-"))
    for info in opening_infos or []:
        cls = info.get("classification") or {}
        if alignment_mode is None:
            print(
                "opening/waypoint id: {}; source: {}; host element id: {}; "
                "link instance id: {}; linked element id: {}; linked document: {}; "
                "confidence: {}; name: {}; raw picked point mm: {}; "
                "opening center mm: {}; final waypoint mm: {}; resolution: {}; "
                "reason: {}".format(
                    info.get("id"),
                    info.get("source_type") or info.get("host_or_linked"),
                    info.get("host_element_id"),
                    info.get("link_instance_id"),
                    info.get("linked_element_id"),
                    info.get("linked_document_title") or "-",
                    cls.get("confidence"),
                    info.get("name"),
                    _xyz_mm(info.get("raw_picked_point")),
                    _xyz_mm(info.get("opening_center")),
                    _xyz_mm(info.get("center")),
                    info.get("waypoint_resolution") or "-",
                    cls.get("reason"),
                )
            )
            continue
        print(
            "opening/waypoint id: {}; source: {}; host element id: {}; "
            "link instance id: {}; linked element id: {}; linked document: {}; "
            "confidence: {}; name: {}; raw picked point mm: {}; "
            "opening center mm: {}; original waypoint mm: {}; "
            "aligned waypoint mm: {}; final waypoint mm: {}; "
            "grouped ids: {}; grouped XY delta mm: {}; grouping decision: {}; "
            "shaft XY mode: {}; selected shaft XY mm: {}; "
            "original grouped XY values mm: {}; shaft XY reason: {}; "
            "resolution: {}; reason: {}".format(
                info.get("id"),
                info.get("source_type") or info.get("host_or_linked"),
                info.get("host_element_id"),
                info.get("link_instance_id"),
                info.get("linked_element_id"),
                info.get("linked_document_title") or "-",
                cls.get("confidence"),
                info.get("name"),
                _xyz_mm(info.get("raw_picked_point")),
                _xyz_mm(info.get("opening_center")),
                _xyz_mm(info.get("original_waypoint")),
                _xyz_mm(info.get("aligned_waypoint")),
                _xyz_mm(info.get("center")),
                info.get("grouped_waypoint_ids") or [],
                info.get("grouped_xy_delta_mm"),
                info.get("grouping_decision") or "-",
                info.get("shaft_xy_mode") or "-",
                info.get("selected_shaft_xy_mm"),
                info.get("original_grouped_waypoint_xy_mm") or [],
                info.get("shaft_xy_reason") or "-",
                info.get("waypoint_resolution") or "-",
                cls.get("reason"),
            )
        )


def run_generate_routed_connection_through_openings():
    doc = revit.doc
    command_title = "Generate Routed Connection Through Selected Opening(s)"

    source_pipe, source_conn, source_pick = _pick_host_pipe_end(
        "Pick SOURCE pipe end (point on pipe end)"
    )
    if not source_pipe:
        return
    if _connector_is_connected(source_conn):
        forms.alert(
            "Selected source pipe end is already connected. Pick an open pipe end.",
            title=command_title,
        )
        return

    opening_infos, opening_error = _pick_opening_waypoints_in_order()
    if not opening_infos:
        reason = opening_error or "No opening waypoint selected; no geometry created."
        print("OPENING ROUTE RESULT -> CANCELLED")
        print("opening element ids: []")
        print("opening waypoint coordinates mm: []")
        print("waypoint count: 0")
        print("transaction started: False")
        print("reason: {}".format(reason))
        forms.alert(
            "CANCELLED\n\n{}\n\nNo geometry created.".format(reason),
            title=command_title,
        )
        return

    waypoint_mode = _ask_choice(
        [
            "Use picked point exactly",
            "Use opening center",
            "Use picked XY, opening center Z",
        ],
        "Choose how opening waypoints are resolved",
        default="Use opening center",
    )
    if not waypoint_mode:
        _show_hardened_cancelled(
            command_title,
            "User cancelled opening waypoint mode selection.",
        )
        return
    resolved, resolution_error = _resolve_opening_waypoints(
        opening_infos, waypoint_mode
    )
    if not resolved:
        _show_hardened_cancelled(command_title, resolution_error)
        return

    alignment_mode = _ask_choice(
        [
            "Keep waypoint coordinates as selected/resolved",
            "Align close opening waypoints into vertical shaft",
        ],
        "Opening waypoint alignment",
        default="Align close opening waypoints into vertical shaft",
    )
    if not alignment_mode:
        _show_hardened_cancelled(
            command_title,
            "User cancelled opening waypoint alignment selection.",
        )
        return
    (
        shaft_groups,
        shaft_grouping_decisions,
        opening_route_warnings,
    ) = _align_opening_waypoints(opening_infos, alignment_mode)

    default_route_shape = OPENING_ROUTE_ORTHOGONAL
    if shaft_groups:
        default_route_shape = OPENING_ROUTE_SIMPLE_SHAFT
    route_shape_mode = _ask_choice(
        [
            OPENING_ROUTE_ORTHOGONAL,
            OPENING_ROUTE_SIMPLE_SHAFT,
        ],
        "Opening route shape",
        default=default_route_shape,
    )
    if not route_shape_mode:
        _show_hardened_cancelled(
            command_title,
            "User cancelled opening route shape selection.",
        )
        return
    if (
        route_shape_mode == OPENING_ROUTE_SIMPLE_SHAFT
        and len(opening_infos) >= 2
        and not shaft_groups
    ):
        opening_route_warnings.append(
            "Simple shaft route selected but opening waypoints were not aligned "
            "into a shaft; route may include diagonal segment."
        )

    target_pipe, target_conn, target_pick = _pick_host_pipe_end(
        "Pick TARGET pipe end (point on pipe end)"
    )
    if not target_pipe:
        return
    if source_pipe.Id.IntegerValue == target_pipe.Id.IntegerValue:
        forms.alert(
            "Source and target must be different pipe elements.",
            title=command_title,
        )
        return
    if _connector_is_connected(target_conn):
        forms.alert(
            "Selected target pipe end is already connected. Pick an open pipe end.",
            title=command_title,
        )
        return

    shaft_xy_mode = ""
    if shaft_groups:
        shaft_xy_mode = _ask_choice(
            [
                SHAFT_XY_AVERAGE,
                SHAFT_XY_FIRST,
                SHAFT_XY_LAST,
                SHAFT_XY_CLOSEST_SOURCE,
                SHAFT_XY_CLOSEST_TARGET,
            ],
            "Shaft XY location",
            default=SHAFT_XY_AVERAGE,
        )
        if not shaft_xy_mode:
            _show_hardened_cancelled(
                command_title,
                "User cancelled shaft XY location selection.",
            )
            return
        _apply_shaft_xy_mode(
            opening_infos,
            shaft_groups,
            shaft_xy_mode,
            source_conn.Origin,
            target_conn.Origin,
        )
    _print_opening_debug(
        opening_infos,
        waypoint_mode,
        alignment_mode,
        shaft_groups,
        shaft_grouping_decisions,
        opening_route_warnings,
        shaft_xy_mode,
    )

    slope_info = _prompt_route_slope_mode(
        source_pipe, target_pipe, source_conn, target_conn
    )
    if not slope_info:
        _show_hardened_cancelled(
            command_title,
            "User cancelled route slope selection.",
        )
        return
    waypoints = [info.get("center") for info in opening_infos]
    raw_route_points_override = None
    route_simplification_reason = "orthogonal route through waypoints used"
    route_shape_detail = "orthogonal route through waypoints"
    horizontal_entry_point = None
    horizontal_exit_point = None
    if route_shape_mode == OPENING_ROUTE_SIMPLE_SHAFT:
        shaft_top = waypoints[0]
        shaft_bottom = waypoints[-1]
        horizontal_entry_point = DB.XYZ(shaft_top.X, shaft_top.Y, source_conn.Origin.Z)
        horizontal_exit_point = DB.XYZ(
            target_conn.Origin.X, target_conn.Origin.Y, shaft_bottom.Z
        )
        raw_route_points_override = [
            source_conn.Origin,
            horizontal_entry_point,
            shaft_top,
        ]
        if len(waypoints) >= 2:
            raw_route_points_override.append(shaft_bottom)
        raw_route_points_override.extend(
            [
                horizontal_exit_point,
                target_conn.Origin,
            ]
        )
        route_simplification_reason = "simple shaft route used"
        route_shape_detail = "simple shaft route with horizontal entry/exit"
    opening_diagnostics = {
        "opening_element_ids": [info.get("id") for info in opening_infos],
        "opening_waypoints_mm": [_xyz_mm_tuple(point) for point in waypoints],
        "opening_waypoint_mode": waypoint_mode,
        "opening_waypoint_alignment_mode": alignment_mode,
        "opening_shaft_xy_tolerance_mm": OPENING_SHAFT_XY_TOL_MM,
        "opening_shaft_relaxed_xy_tolerance_mm": (OPENING_SHAFT_RELAXED_XY_TOL_MM),
        "opening_shaft_groups": list(shaft_groups),
        "opening_shaft_grouping_decisions": list(shaft_grouping_decisions),
        "opening_shaft_xy_mode": shaft_xy_mode,
        "opening_route_warnings": list(opening_route_warnings),
        "opening_route_shape_mode": route_shape_mode,
        "opening_route_shape_detail": route_shape_detail,
        "route_simplification_reason": route_simplification_reason,
        "inserted_horizontal_entry_point_mm": _xyz_mm_tuple(horizontal_entry_point),
        "inserted_horizontal_exit_point_mm": _xyz_mm_tuple(horizontal_exit_point),
        "direct_route_points_before_cleanup_mm": [
            _xyz_mm_tuple(point) for point in raw_route_points_override or []
        ],
        "opening_waypoint_details": [
            {
                "source_type": info.get("source_type") or info.get("host_or_linked"),
                "host_element_id": info.get("host_element_id"),
                "link_instance_id": info.get("link_instance_id"),
                "linked_element_id": info.get("linked_element_id"),
                "linked_document_title": info.get("linked_document_title") or "",
                "raw_picked_point_mm": _xyz_mm_tuple(info.get("raw_picked_point")),
                "opening_center_mm": _xyz_mm_tuple(info.get("opening_center")),
                "original_waypoint_mm": _xyz_mm_tuple(info.get("original_waypoint")),
                "aligned_waypoint_mm": _xyz_mm_tuple(info.get("aligned_waypoint")),
                "final_waypoint_mm": _xyz_mm_tuple(info.get("center")),
                "grouped_waypoint_ids": list(info.get("grouped_waypoint_ids") or []),
                "grouped_xy_delta_mm": info.get("grouped_xy_delta_mm"),
                "grouping_decision": info.get("grouping_decision") or "",
                "shaft_xy_mode": info.get("shaft_xy_mode") or "",
                "selected_shaft_xy_mm": info.get("selected_shaft_xy_mm"),
                "original_grouped_waypoint_xy_mm": list(
                    info.get("original_grouped_waypoint_xy_mm") or []
                ),
                "shaft_xy_reason": info.get("shaft_xy_reason") or "",
                "waypoint_resolution": info.get("waypoint_resolution") or "",
            }
            for info in opening_infos
        ],
        "waypoint_count": len(waypoints),
    }
    print(
        "OPENING ROUTE DIAGNOSTICS -> opening element ids: {}; waypoint mode: {}; alignment mode: {}; shaft XY mode: {}; route shape: {}; route shape detail: {}; simplification: {}; strict shaft tolerance mm: {}; relaxed shaft tolerance mm: {}; shaft groups: {}; grouping decisions: {}; warnings: {}; horizontal entry point mm: {}; horizontal exit point mm: {}; direct route points before cleanup mm: {}; waypoint coordinates mm: {}; waypoint count: {}; details: {}".format(
            opening_diagnostics["opening_element_ids"],
            opening_diagnostics["opening_waypoint_mode"],
            opening_diagnostics["opening_waypoint_alignment_mode"],
            opening_diagnostics["opening_shaft_xy_mode"],
            opening_diagnostics["opening_route_shape_mode"],
            opening_diagnostics["opening_route_shape_detail"],
            opening_diagnostics["route_simplification_reason"],
            opening_diagnostics["opening_shaft_xy_tolerance_mm"],
            opening_diagnostics["opening_shaft_relaxed_xy_tolerance_mm"],
            opening_diagnostics["opening_shaft_groups"],
            opening_diagnostics["opening_shaft_grouping_decisions"],
            opening_diagnostics["opening_route_warnings"],
            _xyz_mm_from_tuple(
                opening_diagnostics["inserted_horizontal_entry_point_mm"]
            ),
            _xyz_mm_from_tuple(
                opening_diagnostics["inserted_horizontal_exit_point_mm"]
            ),
            [
                _xyz_mm_from_tuple(point)
                for point in opening_diagnostics[
                    "direct_route_points_before_cleanup_mm"
                ]
            ],
            [
                _xyz_mm_from_tuple(point)
                for point in opening_diagnostics["opening_waypoints_mm"]
            ],
            opening_diagnostics["waypoint_count"],
            opening_diagnostics["opening_waypoint_details"],
        )
    )
    _execute_routed_connection(
        doc,
        source_pipe,
        source_conn,
        target_pipe,
        target_conn,
        waypoints,
        slope_info,
        command_title,
        hardened=True,
        route_diagnostics=opening_diagnostics,
        raw_route_points_override=raw_route_points_override,
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
            coll = (
                DB.FilteredElementCollector(link_doc)
                .OfCategory(bic)
                .WhereElementIsNotElementType()
            )
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
                        low_infos.append(
                            {
                                "id": "{}:{}".format(
                                    link_inst.Id.IntegerValue, el.Id.IntegerValue
                                ),
                                "host_or_linked": "linked-low-debug",
                                "element": el,
                                "center": center,
                                "name": _safe_name(el),
                                "classification": cls,
                            }
                        )
                    continue
                center = _bbox_center(el, transform)
                if center:
                    out.append(
                        {
                            "id": "{}:{}".format(
                                link_inst.Id.IntegerValue, el.Id.IntegerValue
                            ),
                            "host_or_linked": "linked",
                            "element": el,
                            "center": center,
                            "name": _safe_name(el),
                            "classification": cls,
                        }
                    )
                if len(out) >= limit_count:
                    return out
        except Exception:
            continue
    print(
        "LINK ROUTE DEBUG -> high/medium candidates: {}; low: {}; rejected: {}".format(
            len(out), len(low), len(rejected)
        )
    )
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
        out.append(
            {
                "id": str(import_inst.Id.IntegerValue),
                "host_or_linked": "import-bbox",
                "element": import_inst,
                "center": center,
                "name": _safe_name(import_inst) or "ImportInstance",
            }
        )
    print(
        "LINK ROUTE DEBUG -> ImportInstance geometry semantics are limited; using accessible import bounding box centers only."
    )
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
            "Pick a Revit link or IFC/DWG import instance",
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
        label = "{} | {}".format(
            idx + 1, _opening_candidate_label(info, source_conn, target_conn)
        )
        labels.append(label)
        by_label[label] = info
    picked = forms.SelectFromList.show(
        labels, title="Select opening candidate", multiselect=False
    )
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
        candidates = _collect_rvt_link_opening_candidates(
            link_or_import, limit_count=200
        )
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
            slope_info = _prompt_route_slope_mode(
                source_pipe, target_pipe, source_conn, target_conn
            )
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

    candidates.sort(
        key=lambda i: _distance_to_corridor_xy(
            i.get("center"), source_conn.Origin, target_conn.Origin
        )
    )
    nearest = candidates[:30]
    print(
        "LINK ROUTE DEBUG -> opening candidates found: {}; showing nearest: {}".format(
            len(candidates), len(nearest)
        )
    )
    for info in nearest:
        print(
            "LINK ROUTE CANDIDATE -> id: {}; center host mm: {}; dist corridor mm: {:.1f}".format(
                info.get("id"),
                _xyz_mm(info.get("center")),
                _ft_to_mm(
                    _distance_to_corridor_xy(
                        info.get("center"), source_conn.Origin, target_conn.Origin
                    )
                ),
            )
        )

    selected = _select_opening_candidate_from_list(nearest, source_conn, target_conn)
    if not selected:
        return

    slope_info = _prompt_route_slope_mode(
        source_pipe, target_pipe, source_conn, target_conn
    )
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
        coll = (
            DB.FilteredElementCollector(link_doc)
            .OfCategory(DB.BuiltInCategory.OST_PipeCurves)
            .WhereElementIsNotElementType()
        )
        for pipe_el in coll:
            p0, p1 = _pipe_location_endpoints(pipe_el)
            if p0 is None or p1 is None:
                skipped.append(
                    "Pipe {} skipped: no line endpoints.".format(
                        pipe_el.Id.IntegerValue
                    )
                )
                continue
            if transform:
                p0 = transform.OfPoint(p0)
                p1 = transform.OfPoint(p1)
            if not _segment_length_ok(p0, p1):
                skipped.append(
                    "Pipe {} skipped: transformed segment too short.".format(
                        pipe_el.Id.IntegerValue
                    )
                )
                continue
            data.append(
                {
                    "linked_id": pipe_el.Id.IntegerValue,
                    "start": p0,
                    "end": p1,
                    "diameter": _linked_pipe_diameter(pipe_el),
                }
            )
    except Exception as ex:
        skipped.append("Pipe collection failed: {}".format(ex))
    return data, skipped


def _create_host_pipe_from_link_data(
    doc, system_type_id, pipe_type_id, level_id, pipe_data
):
    if Pipe is None:
        raise Exception("Autodesk.Revit.DB.Plumbing.Pipe could not be imported.")
    new_pipe = Pipe.Create(
        doc,
        system_type_id,
        pipe_type_id,
        level_id,
        pipe_data["start"],
        pipe_data["end"],
    )
    _set_pipe_diameter(new_pipe, pipe_data.get("diameter"))
    return new_pipe


def run_generate_host_pipes_from_rvt_link():
    doc = revit.doc
    link_inst = _pick_link_or_import()
    if not link_inst:
        return
    if not _is_revit_link(link_inst):
        forms.alert(
            "This command currently supports RVT links only. IFC/DWG pipe recreation is intentionally not implemented."
        )
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
    forms.alert(
        "\n".join(report), title="Generate Host Pipes From RVT Link - Experimental"
    )
    if not pipe_data:
        return
    if not forms.alert(
        "Create {} host pipes from selected RVT link?".format(len(pipe_data)),
        yes=True,
        no=True,
    ):
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
            created.append(
                _create_host_pipe_from_link_data(
                    doc, system_type.Id, pipe_type.Id, level.Id, item
                )
            )
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
        forms.alert(
            "Host pipe recreation failed and was rolled back.\n\n{}".format(
                failure_reason
            )
        )
        return

    forms.alert(
        "Created successfully.\n\nCreated host pipe ids: {}".format(
            [_eid_int(p) for p in created]
        ),
        title="Generate Host Pipes From RVT Link - Experimental",
    )


def _execute_routed_connection(
    doc,
    source_pipe,
    source_conn,
    target_pipe,
    target_conn,
    waypoint_points,
    slope_info,
    command_title,
    hardened=False,
    route_diagnostics=None,
    raw_route_points_override=None,
):
    if hardened:
        initial_errors = []
        if source_conn is None:
            initial_errors.append("Source connector was not resolved.")
        if target_conn is None:
            initial_errors.append("Target connector was not resolved.")
        if not _is_pipe(source_pipe) and not _has_valid_mep_connectors(source_pipe):
            initial_errors.append(
                "Source element is not a PipeCurve and has no valid MEP connectors."
            )
        if not _is_pipe(target_pipe) and not _has_valid_mep_connectors(target_pipe):
            initial_errors.append(
                "Target element is not a PipeCurve and has no valid MEP connectors."
            )
        if initial_errors:
            reason = "Preflight failed:\n- {}".format("\n- ".join(initial_errors))
            print("GENERATE ROUTED CONNECTION PREFLIGHT -> {}".format(reason))
            _show_hardened_early_failure(command_title, reason)
            return False, [], [], reason
    if raw_route_points_override is not None:
        raw_route_points = list(raw_route_points_override)
    else:
        try:
            raw_route_points = _build_route_points_with_slope(
                source_conn.Origin, target_conn.Origin, waypoint_points, slope_info
            )
        except Exception as ex:
            reason = "Could not build route: {}".format(ex)
            if hardened:
                _show_hardened_early_failure(command_title, reason)
            else:
                forms.alert(
                    "Could not build route.\n\n{}".format(ex), title=command_title
                )
            return False, [], [], reason

    route_points, cleanup_stats = _simplify_route_points(raw_route_points)
    _print_route_cleanup_debug(raw_route_points, route_points, cleanup_stats)
    debug_cache = _build_route_debug_cache(
        source_pipe,
        target_pipe,
        source_conn,
        target_conn,
        raw_route_points,
        route_points,
        slope_info,
        cleanup_stats,
    )
    if route_diagnostics:
        debug_cache["opening_element_ids"] = list(
            route_diagnostics.get("opening_element_ids") or []
        )
        debug_cache["opening_waypoints_mm"] = list(
            route_diagnostics.get("opening_waypoints_mm") or []
        )
        debug_cache["opening_waypoint_details"] = list(
            route_diagnostics.get("opening_waypoint_details") or []
        )
        debug_cache["opening_waypoint_mode"] = (
            route_diagnostics.get("opening_waypoint_mode") or ""
        )
        debug_cache["opening_waypoint_alignment_mode"] = (
            route_diagnostics.get("opening_waypoint_alignment_mode") or ""
        )
        debug_cache["opening_shaft_xy_tolerance_mm"] = route_diagnostics.get(
            "opening_shaft_xy_tolerance_mm", OPENING_SHAFT_XY_TOL_MM
        )
        debug_cache["opening_shaft_relaxed_xy_tolerance_mm"] = route_diagnostics.get(
            "opening_shaft_relaxed_xy_tolerance_mm",
            OPENING_SHAFT_RELAXED_XY_TOL_MM,
        )
        debug_cache["opening_shaft_groups"] = list(
            route_diagnostics.get("opening_shaft_groups") or []
        )
        debug_cache["opening_shaft_grouping_decisions"] = list(
            route_diagnostics.get("opening_shaft_grouping_decisions") or []
        )
        debug_cache["opening_shaft_xy_mode"] = (
            route_diagnostics.get("opening_shaft_xy_mode") or ""
        )
        debug_cache["opening_route_warnings"] = list(
            route_diagnostics.get("opening_route_warnings") or []
        )
        debug_cache["warnings"].extend(debug_cache["opening_route_warnings"])
        debug_cache["opening_route_shape_mode"] = (
            route_diagnostics.get("opening_route_shape_mode") or ""
        )
        debug_cache["opening_route_shape_detail"] = (
            route_diagnostics.get("opening_route_shape_detail") or ""
        )
        debug_cache["inserted_horizontal_entry_point_mm"] = route_diagnostics.get(
            "inserted_horizontal_entry_point_mm"
        )
        debug_cache["inserted_horizontal_exit_point_mm"] = route_diagnostics.get(
            "inserted_horizontal_exit_point_mm"
        )
        debug_cache["route_simplification_reason"] = (
            route_diagnostics.get("route_simplification_reason") or ""
        )
        debug_cache["direct_route_points_before_cleanup_mm"] = list(
            route_diagnostics.get("direct_route_points_before_cleanup_mm") or []
        )
        if raw_route_points_override is not None:
            debug_cache["direct_route_points_after_cleanup_mm"] = [
                _xyz_mm_tuple(point) for point in route_points
            ]
        debug_cache["waypoint_count"] = int(
            route_diagnostics.get("waypoint_count") or 0
        )

    if len(route_points) < 2:
        reason = "Route has fewer than two points."
        if hardened:
            _show_hardened_early_failure(command_title, reason, debug_cache)
        else:
            forms.alert(
                "Could not build a valid route between selected pipe ends.",
                title=command_title,
            )
        return False, [], [], reason

    for idx in range(0, len(route_points) - 1):
        seg_len = _segment_length_mm(route_points[idx], route_points[idx + 1])
        minimum_length = HARDENED_MIN_SEGMENT_MM if hardened else MIN_SEGMENT_MM
        if seg_len <= minimum_length:
            reason = (
                "Route segment {} remains too short after cleanup: {:.1f} mm.".format(
                    idx + 1, seg_len
                )
            )
            debug_cache["failure_reason"] = reason
            if hardened:
                _show_hardened_early_failure(command_title, reason, debug_cache)
            else:
                _print_route_debug(
                    None, None, None, None, None, debug_cache=debug_cache
                )
                forms.alert(reason, title=command_title)
            return False, [], [], reason

    resolved_data = None
    if hardened:
        preflight = _preflight_routed_connection(
            doc,
            source_pipe,
            source_conn,
            target_pipe,
            target_conn,
            route_points,
            slope_info,
        )
        resolved_data = preflight.get("resolved") or {}
        debug_cache["warnings"] = list(preflight.get("warnings") or [])
        debug_cache["warnings"].extend(debug_cache.get("opening_route_warnings") or [])
        debug_cache["source_connector_domain"] = preflight.get("source_domain") or ""
        debug_cache["target_connector_domain"] = preflight.get("target_domain") or ""
        debug_cache["resolved_system_type_id"] = _eid_int(
            resolved_data.get("system_type_id")
        )
        debug_cache["resolved_pipe_type_id"] = _eid_int(
            resolved_data.get("pipe_type_id")
        )
        debug_cache["resolved_level_id"] = _eid_int(resolved_data.get("level_id"))
        diameter = resolved_data.get("diameter")
        debug_cache["resolved_diameter_mm"] = (
            _ft_to_mm(diameter) if diameter is not None else None
        )
        debug_cache["source_diameter_mm"] = preflight.get("source_diameter_mm")
        debug_cache["target_diameter_mm"] = preflight.get("target_diameter_mm")
        debug_cache["flat_z_delta_mm"] = preflight.get("flat_z_delta_mm")
        print(
            "GENERATE ROUTED CONNECTION PREFLIGHT -> {}".format(
                {
                    "ok": preflight.get("ok"),
                    "errors": preflight.get("errors"),
                    "warnings": preflight.get("warnings"),
                    "source_domain": preflight.get("source_domain"),
                    "target_domain": preflight.get("target_domain"),
                    "system_type_id": debug_cache.get("resolved_system_type_id"),
                    "pipe_type_id": debug_cache.get("resolved_pipe_type_id"),
                    "level_id": debug_cache.get("resolved_level_id"),
                    "diameter_mm": debug_cache.get("resolved_diameter_mm"),
                    "source_diameter_mm": debug_cache.get("source_diameter_mm"),
                    "target_diameter_mm": debug_cache.get("target_diameter_mm"),
                    "flat_z_delta_mm": debug_cache.get("flat_z_delta_mm"),
                    "route_segment_count": len(route_points) - 1,
                }
            )
        )
        if not preflight.get("ok"):
            reason = "Preflight failed:\n- {}".format(
                "\n- ".join(preflight.get("errors") or [])
            )
            _show_hardened_early_failure(command_title, reason, debug_cache)
            return False, [], [], reason
        if preflight.get("diameter_mismatch"):
            diameter_choice = _ask_choice(
                [
                    DIAMETER_DECISION_CANCEL,
                    DIAMETER_DECISION_SOURCE,
                    DIAMETER_DECISION_TARGET,
                ],
                (
                    "Source diameter {:.1f} mm differs from target diameter {:.1f} mm.\n"
                    "Choose the generated route diameter."
                ).format(
                    preflight.get("source_diameter_mm"),
                    preflight.get("target_diameter_mm"),
                ),
            )
            if not diameter_choice or diameter_choice == DIAMETER_DECISION_CANCEL:
                debug_cache["diameter_mismatch_decision"] = DIAMETER_DECISION_CANCEL
                reason = "User cancelled routed connection at diameter selection."
                _show_hardened_cancelled(command_title, reason, debug_cache)
                return False, [], [], reason
            if diameter_choice == DIAMETER_DECISION_TARGET:
                target_diameter = _get_pipe_diameter(target_pipe)
                if target_diameter is None or target_diameter <= 0:
                    reason = "Target pipe diameter could not be resolved."
                    _show_hardened_early_failure(command_title, reason, debug_cache)
                    return False, [], [], reason
                resolved_data["diameter"] = target_diameter
                debug_cache["resolved_diameter_mm"] = preflight.get(
                    "target_diameter_mm"
                )
                debug_cache["diameter_mismatch_decision"] = DIAMETER_DECISION_TARGET
                debug_cache["warnings"].append(
                    "Diameter mismatch decision: generated route uses target diameter {:.1f} mm.".format(
                        preflight.get("target_diameter_mm")
                    )
                )
            else:
                debug_cache["diameter_mismatch_decision"] = DIAMETER_DECISION_SOURCE
                debug_cache["warnings"].append(
                    "Diameter mismatch decision: generated route uses source diameter {:.1f} mm.".format(
                        preflight.get("source_diameter_mm")
                    )
                )
            print(
                "GENERATE ROUTED CONNECTION PREFLIGHT -> diameter mismatch decision: {}; route diameter mm: {}".format(
                    debug_cache.get("diameter_mismatch_decision"),
                    debug_cache.get("resolved_diameter_mm"),
                )
            )
        if preflight.get("flat_different_z"):
            warning_text = (
                "Flat / zero slope selected while endpoint elevations differ by "
                "{:.1f} mm; vertical absorber segment(s) will be required."
            ).format(preflight.get("flat_z_delta_mm"))
            if not forms.alert(
                "{}\n\nContinue with routed connection?".format(warning_text),
                title=command_title,
                yes=True,
                no=True,
            ):
                reason = "User cancelled after flat-route elevation warning."
                _show_hardened_cancelled(command_title, reason, debug_cache)
                return False, [], [], reason

    if (
        slope_info["mode"] != SLOPE_MODE_FLAT
        and slope_info["mode"] != SLOPE_MODE_PRESERVE_ENDPOINTS
    ):
        has_sloped_horizontal = False
        for idx in range(0, len(route_points) - 1):
            if _xy_distance(route_points[idx], route_points[idx + 1]) > _mm_to_ft(
                MM_TOL
            ):
                seg_slope = _segment_slope_ratio(
                    route_points[idx], route_points[idx + 1]
                )
                if seg_slope is not None and abs(seg_slope) > 1e-9:
                    has_sloped_horizontal = True
        if not has_sloped_horizontal:
            reason = "Requested non-flat slope could not be applied to any horizontal route segment."
            debug_cache["failure_reason"] = reason
            if hardened:
                _show_hardened_early_failure(command_title, reason, debug_cache)
            else:
                _print_route_debug(
                    None, None, None, None, None, debug_cache=debug_cache
                )
                forms.alert(reason, title=command_title)
            return False, [], [], reason

    created_pipes = []
    created_fittings = []
    failure_reason = None
    tg = DB.TransactionGroup(doc, "BIM3DNA Generate Routed Pipe Connection")
    tg_started = False
    t = None
    t_started = False
    fitting_t = None
    fitting_t_started = False
    try:
        tg.Start()
        tg_started = True
        debug_cache["transaction_started"] = True
        create_transaction_name = (
            "Generate Routed Connection - Create Pipes" if hardened else command_title
        )
        t = DB.Transaction(doc, create_transaction_name)
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
            created_pipes.append(
                _create_pipe_segment(
                    doc, source_pipe, p0, p1, resolved_data=resolved_data
                )
            )
            debug_cache["created_pipe_ids"].append(_eid_int(created_pipes[-1]))
            seg_slope = _segment_slope_ratio(p0, p1)
            debug_cache["created_pipe_final_slopes"].append(_format_slope(seg_slope))

        doc.Regenerate()
        if hardened:
            _commit_transaction_or_raise(t, "Pipe creation")
            t_started = False
            fitting_ids_before = _collect_pipe_fitting_ids(doc)
            debug_cache["fitting_ids_before_count"] = (
                len(fitting_ids_before) if fitting_ids_before is not None else None
            )
            fitting_t = DB.Transaction(
                doc, "Generate Routed Connection - Connect Fittings"
            )
            _attach_route_failure_preprocessor(fitting_t)
            fitting_t.Start()
            fitting_t_started = True
            created_fittings = _join_pipe_chain_with_fittings(
                doc,
                source_conn,
                target_conn,
                created_pipes,
                route_points,
                diagnostics=debug_cache["fitting_connect_attempts"],
            )
            doc.Regenerate()
            _commit_transaction_or_raise(fitting_t, "Fitting connection")
            fitting_t_started = False
            fitting_ids_after = _collect_pipe_fitting_ids(doc)
            debug_cache["fitting_ids_after_count"] = (
                len(fitting_ids_after) if fitting_ids_after is not None else None
            )
            if fitting_ids_before is not None and fitting_ids_after is not None:
                debug_cache["created_fitting_ids"] = sorted(
                    list(fitting_ids_after.difference(fitting_ids_before))
                )
                if debug_cache["created_fitting_ids"]:
                    debug_cache["fitting_detection_status"] = (
                        "New OST_PipeFitting elements detected by document snapshot."
                    )
                else:
                    debug_cache["fitting_detection_status"] = (
                        "No new OST_PipeFitting elements detected; ConnectTo may have completed without creating a fitting."
                    )
            else:
                debug_cache["fitting_detection_status"] = (
                    "Pipe fitting snapshot unavailable."
                )
                debug_cache["warnings"].append(
                    "Pipe fitting id snapshot was unavailable; ConnectTo/fitting operations still completed."
                )
        else:
            # Preserve the existing shared workflow for opening/link operations.
            created_fittings = _join_pipe_chain_with_fittings(
                doc, source_conn, target_conn, created_pipes, route_points
            )
            debug_cache["created_fitting_ids"] = [
                _eid_int(f) for f in created_fittings if f
            ]
            doc.Regenerate()
            t.Commit()
            t_started = False
        if hardened:
            _assimilate_group_or_raise(tg, "Generate Routed Connection")
        else:
            tg.Assimilate()
        tg_started = False
    except Exception as ex:
        failure_reason = str(ex)
        debug_cache["failure_reason"] = failure_reason
        try:
            if fitting_t and fitting_t_started:
                fitting_t.RollBack()
        except Exception:
            pass
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
        if hardened:
            forms.alert(
                "FAILED\n\n"
                "Created pipe ids before rollback: {}\n"
                "Warnings: {}\n"
                "Source / target: {} / {}\n"
                "Slope mode: {}\n"
                "System / pipe / level ids: {} / {} / {}\n"
                "Source / target diameter mm: {} / {}\n"
                "Route diameter mm: {}\n"
                "Diameter decision: {}\n"
                "Endpoint Z delta mm: {}\n"
                "Route segments: {}\n"
                "Transaction started: {}\n"
                "Rollback completed: {}\n"
                "Reason: {}\n\n"
                "Structured diagnostics were printed to the pyRevit output.".format(
                    debug_cache.get("created_pipe_ids") or [],
                    "; ".join(debug_cache.get("warnings") or []) or "None",
                    debug_cache.get("source_element_id"),
                    debug_cache.get("target_element_id"),
                    (debug_cache.get("slope_info") or {}).get("mode"),
                    debug_cache.get("resolved_system_type_id"),
                    debug_cache.get("resolved_pipe_type_id"),
                    debug_cache.get("resolved_level_id"),
                    debug_cache.get("source_diameter_mm"),
                    debug_cache.get("target_diameter_mm"),
                    debug_cache.get("resolved_diameter_mm"),
                    debug_cache.get("diameter_mismatch_decision"),
                    debug_cache.get("flat_z_delta_mm"),
                    len(debug_cache.get("route_segment_slopes") or []),
                    debug_cache.get("transaction_started"),
                    debug_cache.get("rollback_completed"),
                    failure_reason,
                ),
                title=command_title,
            )
        else:
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
                    "\n".join(
                        [
                            "  {}".format(_xyz_mm_from_tuple(p))
                            for p in debug_cache.get("route_points_mm") or []
                        ]
                    ),
                    debug_cache.get("rollback_completed"),
                    failure_reason,
                ),
                title=command_title,
            )
        return False, [], [], failure_reason

    _print_route_debug(None, None, None, None, None, debug_cache=debug_cache)
    if hardened:
        created_fitting_ids = debug_cache.get("created_fitting_ids") or []
        fitting_attempts = debug_cache.get("fitting_connect_attempts") or []
        all_attempts_used_connect_to = bool(fitting_attempts) and all(
            [
                attempt.get("result") == "ConnectTo succeeded"
                for attempt in fitting_attempts
            ]
        )
        fitting_ids_text = str(created_fitting_ids)
        if not created_fitting_ids and all_attempts_used_connect_to:
            fitting_ids_text = (
                "[] (ConnectTo succeeded; no explicit pipe fitting elements detected)"
            )

        success_lines = [
            "SUCCESS",
            "",
            "Created pipe ids: {}".format(debug_cache.get("created_pipe_ids") or []),
            "Created fitting ids: {}".format(fitting_ids_text),
            "Warnings: {}".format(
                "; ".join(debug_cache.get("warnings") or []) or "None"
            ),
            "Source / target ids: {} / {}".format(
                debug_cache.get("source_element_id"),
                debug_cache.get("target_element_id"),
            ),
            "Slope mode: {}".format((debug_cache.get("slope_info") or {}).get("mode")),
            "Source / target diameter mm: {} / {}".format(
                debug_cache.get("source_diameter_mm"),
                debug_cache.get("target_diameter_mm"),
            ),
            "Route diameter mm: {}".format(debug_cache.get("resolved_diameter_mm")),
            "Diameter decision: {}".format(
                debug_cache.get("diameter_mismatch_decision")
            ),
        ]
        if debug_cache.get("opening_element_ids"):
            success_lines.extend(
                [
                    "Waypoint mode: {}".format(
                        debug_cache.get("opening_waypoint_mode") or "-"
                    ),
                    "Alignment mode: {}".format(
                        debug_cache.get("opening_waypoint_alignment_mode") or "-"
                    ),
                    "Shaft XY mode: {}".format(
                        debug_cache.get("opening_shaft_xy_mode") or "-"
                    ),
                    "Route shape mode: {}".format(
                        debug_cache.get("opening_route_shape_mode") or "-"
                    ),
                ]
            )
        success_lines.extend(
            [
                "Route segments: {}".format(
                    len(debug_cache.get("route_segment_slopes") or [])
                ),
                "Fitting/connect attempts: {}".format(len(fitting_attempts)),
                "",
                "Structured diagnostics were printed to the pyRevit output.",
            ]
        )
        forms.alert(
            "\n".join(success_lines),
            title=command_title,
        )
    else:
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

    slope_info = _prompt_route_slope_mode(
        source_pipe, target_pipe, source_conn, target_conn
    )
    if not slope_info:
        _show_hardened_cancelled(
            "Generate Routed Connection",
            "User cancelled route slope selection.",
        )
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
        hardened=True,
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
