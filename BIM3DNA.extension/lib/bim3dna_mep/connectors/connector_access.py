# -*- coding: utf-8 -*-
"""Connector access helpers for BIM3DNA MEP tools.

MVP scope is pipe-domain Revit MEP connectors on MEPCurve and FamilyInstance
owners. Fabrication parts and non-MEP semantic conversion are intentionally
not handled here.
"""

from Autodesk.Revit.DB import FamilyInstance, MEPCurve, UnitTypeId, UnitUtils, XYZ


def ft_to_mm(value):
    return UnitUtils.ConvertFromInternalUnits(value, UnitTypeId.Millimeters)


def mm_to_ft(value):
    return UnitUtils.ConvertToInternalUnits(value, UnitTypeId.Millimeters)


def xyz_to_mm_tuple(point):
    if point is None:
        return None
    return (ft_to_mm(point.X), ft_to_mm(point.Y), ft_to_mm(point.Z))


def format_xyz_mm(point):
    vals = xyz_to_mm_tuple(point)
    if vals is None:
        return "-"
    return "({:.1f}, {:.1f}, {:.1f}) mm".format(vals[0], vals[1], vals[2])


def get_element_category_name(elem):
    try:
        return elem.Category.Name if elem and elem.Category else "-"
    except Exception:
        return "-"


def get_element_id_value(elem):
    try:
        return elem.Id.IntegerValue
    except Exception:
        return None


def get_connector_manager(elem):
    if elem is None:
        return None
    try:
        if isinstance(elem, MEPCurve) and elem.ConnectorManager:
            return elem.ConnectorManager
    except Exception:
        pass
    try:
        if isinstance(elem, FamilyInstance) and elem.MEPModel:
            return elem.MEPModel.ConnectorManager
    except Exception:
        pass
    return None


def get_connectors(elem):
    manager = get_connector_manager(elem)
    if manager is None:
        return []
    connectors = []
    try:
        for connector in manager.Connectors:
            connectors.append(connector)
    except Exception:
        return []
    return connectors


def connector_owner(connector):
    try:
        return connector.Owner
    except Exception:
        return None


def connector_owner_id(connector):
    return get_element_id_value(connector_owner(connector))


def connector_origin(connector):
    try:
        return connector.Origin
    except Exception:
        return None


def connector_domain_label(connector):
    try:
        return str(connector.Domain)
    except Exception:
        return "-"


def connector_shape_label(connector):
    try:
        return str(connector.Shape)
    except Exception:
        return "-"


def connector_type_label(connector):
    try:
        return str(connector.ConnectorType)
    except Exception:
        return "-"


def connector_direction(connector):
    try:
        return connector.CoordinateSystem.BasisZ
    except Exception:
        return None


def connector_direction_label(connector):
    direction = connector_direction(connector)
    if direction is None:
        return "-"
    return "({:.4f}, {:.4f}, {:.4f})".format(direction.X, direction.Y, direction.Z)


def connector_radius(connector):
    try:
        return connector.Radius
    except Exception:
        return None


def connector_diameter(connector):
    radius = connector_radius(connector)
    if radius is not None:
        return radius * 2.0
    try:
        return connector.Width
    except Exception:
        return None


def connector_diameter_mm(connector):
    diameter = connector_diameter(connector)
    if diameter is None:
        return None
    return ft_to_mm(diameter)


def is_connector_connected(connector):
    try:
        return bool(connector.IsConnected)
    except Exception:
        return False


def all_ref_connectors(connector):
    refs = []
    try:
        for ref in connector.AllRefs:
            refs.append(ref)
    except Exception:
        pass
    return refs


def external_connected_refs(connector):
    owner_id = connector_owner_id(connector)
    refs = []
    for ref in all_ref_connectors(connector):
        ref_owner_id = connector_owner_id(ref)
        if ref_owner_id is None or ref_owner_id == owner_id:
            continue
        refs.append(ref)
    return refs


def connected_owner_ids(connector):
    ids = []
    for ref in external_connected_refs(connector):
        owner_id = connector_owner_id(ref)
        if owner_id is not None and owner_id not in ids:
            ids.append(owner_id)
    return ids


def is_physical_connector(connector):
    if connector is None:
        return False
    if connector_origin(connector) is None:
        return False
    type_label = connector_type_label(connector).lower()
    if "logical" in type_label or "reference" in type_label:
        return False
    domain_label = connector_domain_label(connector).lower()
    if "undefined" in domain_label:
        return False
    return True


def distance_between_connectors(connector_a, connector_b):
    point_a = connector_origin(connector_a)
    point_b = connector_origin(connector_b)
    if point_a is None or point_b is None:
        return None
    return point_a.DistanceTo(point_b)


def connector_summary(connector, index=None):
    owner_id = connector_owner_id(connector)
    diameter = connector_diameter_mm(connector)
    return {
        "index": index,
        "owner_id": owner_id,
        "origin": connector_origin(connector),
        "origin_mm": format_xyz_mm(connector_origin(connector)),
        "domain": connector_domain_label(connector),
        "shape": connector_shape_label(connector),
        "type": connector_type_label(connector),
        "direction": connector_direction_label(connector),
        "diameter_mm": diameter,
        "is_connected": is_connector_connected(connector),
        "connected_owner_ids": connected_owner_ids(connector),
        "physical": is_physical_connector(connector),
    }

