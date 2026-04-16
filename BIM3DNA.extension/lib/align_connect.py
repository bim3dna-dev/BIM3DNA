# -*- coding: utf-8 -*-
import math
import clr

clr.AddReference("System")

from pyrevit import revit, DB
from System.Collections.Generic import List

import connectors as cn


def _mm_to_internal(mm):
    return DB.UnitUtils.ConvertToInternalUnits(mm, DB.UnitTypeId.Millimeters)


def _to_mm(val_internal):
    return DB.UnitUtils.ConvertFromInternalUnits(val_internal, DB.UnitTypeId.Millimeters)


def _xyz_mm_str(pt):
    if pt is None:
        return "None"
    try:
        return "({:.3f}, {:.3f}, {:.3f})".format(_to_mm(pt.X), _to_mm(pt.Y), _to_mm(pt.Z))
    except Exception:
        return "None"


def _is_pipe(el):
    try:
        return el and el.Category and el.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_PipeCurves)
    except Exception:
        return False


def _pipe_endpoints(el):
    try:
        loc = el.Location
        crv = getattr(loc, "Curve", None)
        if crv and isinstance(crv, DB.Line):
            return crv.GetEndPoint(0), crv.GetEndPoint(1)
    except Exception:
        pass
    return None, None


def _connector_end_index_on_pipe(pipe_el, conn):
    p0, p1 = _pipe_endpoints(pipe_el)
    if p0 is None or p1 is None:
        return None
    try:
        d0 = conn.Origin.DistanceTo(p0)
        d1 = conn.Origin.DistanceTo(p1)
        return 0 if d0 <= d1 else 1
    except Exception:
        return None


def _find_end_conn_on_owner_near_point(owner_el, pt):
    best = None
    best_d = None
    for c in cn.end_connectors(owner_el):
        try:
            d = c.Origin.DistanceTo(pt)
            if best_d is None or d < best_d:
                best_d = d
                best = c
        except Exception:
            pass
    return best, best_d


def _find_conn_on_owner_near_point(owner_el, pt, only_ends=False):
    """Find connector on owner_el with origin nearest pt. Returns None if no connectors."""
    best = None
    best_d = None
    conns = cn.end_connectors(owner_el) if only_ends else cn.iter_connectors(owner_el)
    for c in conns:
        try:
            d = c.Origin.DistanceTo(pt)
            if best_d is None or d < best_d:
                best_d = d
                best = c
        except Exception:
            pass
    return best, best_d


def _traverse_connected_element_ids(seed_el):
    """BFS over connector network. Returns set(ElementId)."""
    visited = set()
    queue = [seed_el]
    while queue:
        el = queue.pop(0)
        if not el:
            continue
        eid = el.Id
        if eid.IntegerValue in visited:
            continue
        visited.add(eid.IntegerValue)

        for c in cn.iter_connectors(el):
            try:
                # AllRefs gives connected connectors
                refs = c.AllRefs
            except Exception:
                refs = None
            if not refs:
                continue
            for rc in refs:
                try:
                    other_owner = rc.Owner
                except Exception:
                    other_owner = None
                if other_owner:
                    oid = other_owner.Id.IntegerValue
                    if oid not in visited:
                        queue.append(other_owner)
    # return ElementId set
    out = set()
    for i in visited:
        out.add(DB.ElementId(i))
    return out


def _angle_between(v1, v2):
    try:
        dot = max(-1.0, min(1.0, v1.Normalize().DotProduct(v2.Normalize())))
        return math.acos(dot)
    except Exception:
        return 0.0

def _is_parallel(v1, v2, tol=1e-6):
    try:
        return v1.CrossProduct(v2).GetLength() < tol
    except Exception:
        return False


def find_connector_by_owner_and_nearest_point(doc, owner_id, point):
    """Resolve connector by owner ElementId and nearest connector origin to point."""
    try:
        el = doc.GetElement(owner_id)
    except Exception:
        el = None
    if not el:
        return None

    best = None
    best_dist = None
    for c in cn.iter_connectors(el):
        try:
            d = c.Origin.DistanceTo(point)
            if best_dist is None or d < best_dist:
                best = c
                best_dist = d
        except Exception:
            continue
    return best


def align_connect(source_el, target_el, source_conn, target_conn,
                  include_connected=False, allow_rotate=True):
    doc = revit.doc

    if not source_el or not target_el or not source_conn or not target_conn:
        return {"moved": [], "connected": False, "warnings": ["Missing inputs."]}

    # Determine move set
    if include_connected:
        move_id_set = set(_traverse_connected_element_ids(target_el))
        move_id_set.add(target_el.Id)
        move_ids = list(move_id_set)
    else:
        move_ids = [target_el.Id]

    target_in_move = any(eid.IntegerValue == target_el.Id.IntegerValue for eid in move_ids)
    ids_net = List[DB.ElementId]()
    for eid in move_ids:
        ids_net.Add(eid)

    src_owner_id = source_el.Id
    tgt_owner_id = target_el.Id
    src_conn_origin_before = source_conn.Origin
    tgt_conn_origin_before = target_conn.Origin
    expected_target_after_move = target_conn.Origin + (source_conn.Origin - target_conn.Origin)

    src_is_pipe = _is_pipe(source_el)
    tgt_is_pipe = _is_pipe(target_el)
    src_end_index = _connector_end_index_on_pipe(source_el, source_conn) if src_is_pipe else None
    tgt_end_index = _connector_end_index_on_pipe(target_el, target_conn) if tgt_is_pipe else None
    tgt_ep0_before, tgt_ep1_before = _pipe_endpoints(target_el) if tgt_is_pipe else (None, None)

    src_pt = source_conn.Origin
    tgt_pt = target_conn.Origin
    move_vec = src_pt - tgt_pt

    warnings = []
    connected = False
    diag_lines = []
    diag_lines.append("target element id: {}".format(target_el.Id.IntegerValue))
    diag_lines.append("include_connected: {}".format(include_connected))
    diag_lines.append("target in move set before move: {}".format(target_in_move))
    diag_lines.append("source_conn.Origin mm: {}".format(_xyz_mm_str(src_conn_origin_before)))
    diag_lines.append("target_conn.Origin mm: {}".format(_xyz_mm_str(tgt_conn_origin_before)))
    diag_lines.append("move_vec mm: {}".format(_xyz_mm_str(move_vec)))
    diag_lines.append("expected target point after move mm: {}".format(_xyz_mm_str(expected_target_after_move)))
    if tgt_is_pipe:
        diag_lines.append("target pipe endpoints BEFORE mm: p0={}, p1={}".format(
            _xyz_mm_str(tgt_ep0_before), _xyz_mm_str(tgt_ep1_before)
        ))
    else:
        diag_lines.append("target pipe endpoints BEFORE mm: N/A (not pipe)")

    t = DB.Transaction(doc, "Align/Connect")
    t.Start()
    try:
        # Move
        DB.ElementTransformUtils.MoveElements(doc, ids_net, move_vec)

        # Rotate (best effort)
        if allow_rotate:
            src_dir = cn.connector_direction(source_conn)
            tgt_dir = cn.connector_direction(target_conn)
            if src_dir and tgt_dir:
                src_dir = src_dir.Normalize()
                tgt_dir = tgt_dir.Normalize()
                dot = max(-1.0, min(1.0, tgt_dir.DotProduct(src_dir)))
                ang = math.acos(dot)
                if ang > 1e-6:
                    axis = tgt_dir.CrossProduct(src_dir)  # rotate target dir onto source dir
                    if axis.GetLength() < 1e-9:
                        # 180 deg flip: choose any axis perpendicular to tgt_dir
                        # pick global Z unless parallel, else X
                        z = DB.XYZ(0, 0, 1)
                        x = DB.XYZ(1, 0, 0)
                        axis = tgt_dir.CrossProduct(z)
                        if axis.GetLength() < 1e-9:
                            axis = tgt_dir.CrossProduct(x)
                    axis = axis.Normalize()
                    line = DB.Line.CreateUnbound(src_pt, axis)
                    DB.ElementTransformUtils.RotateElements(doc, ids_net, line, ang)
            else:
                warnings.append("Skipped rotation: no direction vectors.")

        doc.Regenerate()

        # Re-resolve connectors after move using their OWNERS, not generic "closest"
        src_owner = source_el
        tgt_owner = target_el

        # Source resolution by original selected end if pipe (recommended), else nearest to source point.
        if src_is_pipe and src_end_index is not None:
            src_ep0_after, src_ep1_after = _pipe_endpoints(src_owner)
            src_anchor = src_ep0_after if src_end_index == 0 else src_ep1_after
            new_src, dsrc = _find_end_conn_on_owner_near_point(src_owner, src_anchor)
        else:
            new_src, dsrc = _find_conn_on_owner_near_point(src_owner, src_pt, only_ends=False)

        # Target resolution by selected target pipe end index, not nearest to source point.
        if tgt_is_pipe and tgt_end_index is not None:
            tgt_ep0_after, tgt_ep1_after = _pipe_endpoints(tgt_owner)
            tgt_anchor = tgt_ep0_after if tgt_end_index == 0 else tgt_ep1_after
            new_tgt, dtgt = _find_end_conn_on_owner_near_point(tgt_owner, tgt_anchor)
        else:
            tgt_ep0_after, tgt_ep1_after = (None, None)
            new_tgt, dtgt = _find_conn_on_owner_near_point(tgt_owner, src_pt, only_ends=False)

        if tgt_is_pipe:
            diag_lines.append("target pipe endpoints AFTER mm: p0={}, p1={}".format(
                _xyz_mm_str(tgt_ep0_after), _xyz_mm_str(tgt_ep1_after)
            ))
        else:
            diag_lines.append("target pipe endpoints AFTER mm: N/A (not pipe)")

        if new_src and new_tgt:
            try:
                if not new_tgt.IsConnected:
                    new_tgt.ConnectTo(new_src)
                doc.Regenerate()
            except Exception as ex:
                warnings.append("ConnectTo failed: {}".format(ex))

            # Validate connection by proximity (1mm) + IsConnected
            tol = _mm_to_internal(1.0)
            try:
                dist = new_src.Origin.DistanceTo(new_tgt.Origin)
            except Exception:
                dist = None
            diag_lines.append("resolved target connector origin AFTER mm: {}".format(_xyz_mm_str(new_tgt.Origin)))
            diag_lines.append("gap to source AFTER move mm: {}".format(
                "{:.3f}".format(_to_mm(dist)) if dist is not None else "None"
            ))

            if dist is not None and dist <= tol and new_tgt.IsConnected:
                connected = True
            else:
                connected = False
                warnings.append("After move, connector gap is {} (internal).".format(dist))
        else:
            warnings.append("Could not re-resolve connectors after transform.")
            diag_lines.append("resolved target connector origin AFTER mm: None")
            diag_lines.append("gap to source AFTER move mm: None")

        warnings.append("\n".join(diag_lines))
        t.Commit()
    except Exception as ex:
        t.RollBack()
        warnings.append("\n".join(diag_lines))
        warnings.append("Transaction failed: {}".format(ex))
        return {"moved": [], "connected": False, "warnings": warnings}

    return {"moved": [i.IntegerValue for i in move_ids], "connected": connected, "warnings": warnings}
