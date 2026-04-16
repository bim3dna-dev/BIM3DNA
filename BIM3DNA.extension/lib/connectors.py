# -*- coding: utf-8 -*-
from pyrevit import DB

def _get_connector_manager(element):
    """Return ConnectorManager for common MEP element types."""
    try:
        # Pipe, Duct, etc.
        if hasattr(element, "ConnectorManager") and element.ConnectorManager:
            return element.ConnectorManager
    except Exception:
        pass
    try:
        # FamilyInstance-based (fittings/accessories/equipment)
        mep = getattr(element, "MEPModel", None)
        if mep and hasattr(mep, "ConnectorManager") and mep.ConnectorManager:
            return mep.ConnectorManager
    except Exception:
        pass
    return None

def iter_connectors(element):
    """Return all connectors on an element; empty list if none."""
    cm = _get_connector_manager(element)
    if not cm:
        return []
    conns = []
    try:
        for c in cm.Connectors:
            conns.append(c)
    except Exception:
        pass
    return list(conns)

def end_connectors(element):
    """Return only End connectors for an element."""
    out = []
    for c in iter_connectors(element):
        try:
            if c.ConnectorType == DB.ConnectorType.End:
                out.append(c)
        except Exception:
            continue
    return out

def closest_connector_to_point(element, point, only_ends=False):
    """Return nearest connector to a point, or None if unavailable."""
    if element is None or point is None:
        return None
    if only_ends:
        conns = end_connectors(element)
    else:
        ends = end_connectors(element)
        conns = ends if ends else iter_connectors(element)
    if not conns:
        return None
    best = None
    best_d = None
    for c in conns:
        try:
            d = c.Origin.DistanceTo(point)
            if best_d is None or d < best_d:
                best_d = d
                best = c
        except Exception:
            continue
    return best

def connector_key(conn, mm_round=1.0):
    """Return (ownerIdInt, x_mm_round, y_mm_round, z_mm_round)."""
    try:
        oid = conn.Owner.Id.IntegerValue
    except Exception:
        oid = -1
    try:
        # Internal units are feet; convert to mm for rounding
        x = DB.UnitUtils.ConvertFromInternalUnits(conn.Origin.X, DB.UnitTypeId.Millimeters)
        y = DB.UnitUtils.ConvertFromInternalUnits(conn.Origin.Y, DB.UnitTypeId.Millimeters)
        z = DB.UnitUtils.ConvertFromInternalUnits(conn.Origin.Z, DB.UnitTypeId.Millimeters)
        xr = int(round(x / mm_round))
        yr = int(round(y / mm_round))
        zr = int(round(z / mm_round))
        return (oid, xr, yr, zr)
    except Exception:
        return (oid, 0, 0, 0)

def _pipe_axis_direction(pipe_el, conn):
    try:
        loc = pipe_el.Location
        crv = getattr(loc, "Curve", None)
        if not crv or not isinstance(crv, DB.Line):
            return None
        p0 = crv.GetEndPoint(0)
        p1 = crv.GetEndPoint(1)
        axis = (p1 - p0)
        if axis.GetLength() < 1e-9:
            return None
        axis = axis.Normalize()
        # choose sign based on which end the connector is nearer to
        d0 = conn.Origin.DistanceTo(p0)
        d1 = conn.Origin.DistanceTo(p1)
        # outward direction: from pipe interior towards the connector end
        # if connector near p0, outward is (p0 - p1) = -axis; if near p1, outward is axis
        return (-axis) if d0 <= d1 else axis
    except Exception:
        return None

def connector_direction(conn):
    """Best-effort connector direction as DB.XYZ, else None."""
    try:
        owner = conn.Owner
        # PipeCurves category => use pipe axis
        try:
            if owner and owner.Category and owner.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_PipeCurves):
                v = _pipe_axis_direction(owner, conn)
                if v:
                    return v
        except Exception:
            pass
        v = getattr(conn, "Direction", None)
        if v and v.GetLength() > 1e-9:
            return v.Normalize()
    except Exception:
        pass
    # Many connectors have CoordinateSystem.BasisZ that corresponds to normal
    try:
        cs = conn.CoordinateSystem
        if cs:
            v = cs.BasisZ
            if v and v.GetLength() > 1e-9:
                return v.Normalize()
    except Exception:
        pass
    # Fallback: try BasisX
    try:
        cs = conn.CoordinateSystem
        if cs:
            v = cs.BasisX
            if v and v.GetLength() > 1e-9:
                return v.Normalize()
    except Exception:
        pass
    # Last fallback: BasisY
    try:
        cs = conn.CoordinateSystem
        if cs:
            v = cs.BasisY
            if v and v.GetLength() > 1e-9:
                return v.Normalize()
    except Exception:
        pass
    return None
