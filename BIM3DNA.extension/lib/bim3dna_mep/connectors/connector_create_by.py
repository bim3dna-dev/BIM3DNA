# -*- coding: utf-8 -*-

from Autodesk.Revit.DB import BuiltInParameter, FilteredElementCollector, Level, MEPCurve, Transaction
from Autodesk.Revit.DB.Plumbing import Pipe, PipeType, PipingSystemType

from bim3dna_mep.connectors.connector_access import (
    connector_diameter,
    connector_domain_label,
    connector_origin,
    format_xyz_mm,
    get_connectors,
    get_element_id_value,
)
from bim3dna_mep.connectors.connector_matching import find_best_connector_pair


def get_valid_element_id(element_id):
    try:
        if element_id and element_id.IntegerValue > 0:
            return element_id
    except Exception:
        pass
    return None


def infer_system_type_id(doc, owner):
    try:
        mep_system = owner.MEPSystem
        if mep_system:
            system_type_id = get_valid_element_id(mep_system.GetTypeId())
            if system_type_id:
                return (system_type_id, "owner MEPSystem type")
    except Exception:
        pass
    try:
        param = owner.get_Parameter(BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
        if param:
            system_type_id = get_valid_element_id(param.AsElementId())
            if system_type_id:
                return (system_type_id, "owner RBS_PIPING_SYSTEM_TYPE_PARAM")
    except Exception:
        pass
    systems = list(FilteredElementCollector(doc).OfClass(PipingSystemType).ToElements())
    if len(systems) == 1:
        return (systems[0].Id, "only PipingSystemType in document")
    return (None, "Could not infer PipingSystemType.")


def infer_pipe_type_id(doc, owner):
    try:
        if isinstance(owner, MEPCurve):
            pipe_type_id = get_valid_element_id(owner.GetTypeId())
            if pipe_type_id and isinstance(doc.GetElement(pipe_type_id), PipeType):
                return (pipe_type_id, "source MEPCurve pipe type")
    except Exception:
        pass
    pipe_types = list(FilteredElementCollector(doc).OfClass(PipeType).ToElements())
    if len(pipe_types) == 1:
        return (pipe_types[0].Id, "only PipeType in document")
    return (None, "Could not infer PipeType.")


def infer_level_id(doc, owner, z_value):
    try:
        level_id = get_valid_element_id(owner.LevelId)
        if level_id:
            return (level_id, "owner LevelId")
    except Exception:
        pass
    levels = list(FilteredElementCollector(doc).OfClass(Level).ToElements())
    if not levels:
        return (None, "No levels found in document.")
    nearest = None
    nearest_delta = None
    for level in levels:
        delta = abs(level.Elevation - z_value)
        if nearest is None or delta < nearest_delta:
            nearest = level
            nearest_delta = delta
    if nearest:
        return (nearest.Id, "nearest level by connector Z")
    return (None, "Could not infer Level.")


def nearest_connector_to_point(elem, point):
    best = None
    for connector in get_connectors(elem):
        origin = connector_origin(connector)
        if origin is None:
            continue
        distance = origin.DistanceTo(point)
        if best is None or distance < best[0]:
            best = (distance, connector)
    return best[1] if best else None


def set_pipe_diameter(pipe, diameter):
    if diameter is None:
        return "No diameter available from connector."
    try:
        param = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
        if param and not param.IsReadOnly:
            param.Set(diameter)
            return "Diameter set from connector."
    except Exception as ex:
        return "Could not set pipe diameter: {}".format(ex)
    return "Pipe diameter parameter unavailable or read-only."


def connect_by_elements(doc, elem_a, elem_b):
    result = {
        "ok": False,
        "reason": "",
        "first_element_id": get_element_id_value(elem_a),
        "second_element_id": get_element_id_value(elem_b),
        "created_pipe_id": None,
    }
    connector_a, connector_b, match_reason = find_best_connector_pair(
        elem_a, elem_b, open_only=True, require_direction=False
    )
    result["match_reason"] = match_reason
    if connector_a is None or connector_b is None:
        result["reason"] = match_reason
        return result

    if connector_domain_label(connector_a) != connector_domain_label(connector_b):
        result["reason"] = "Connector domains differ."
        return result

    point_a = connector_origin(connector_a)
    point_b = connector_origin(connector_b)
    if point_a is None or point_b is None:
        result["reason"] = "Could not resolve connector origins."
        return result
    result["first_connector_origin"] = format_xyz_mm(point_a)
    result["second_connector_origin"] = format_xyz_mm(point_b)

    source_owner = connector_a.Owner
    system_type_id, system_reason = infer_system_type_id(doc, source_owner)
    pipe_type_id, pipe_reason = infer_pipe_type_id(doc, source_owner)
    level_id, level_reason = infer_level_id(doc, source_owner, point_a.Z)
    result["system_type_reason"] = system_reason
    result["pipe_type_reason"] = pipe_reason
    result["level_reason"] = level_reason
    if system_type_id is None or pipe_type_id is None or level_id is None:
        result["reason"] = "Inference failed; system, pipe type, and level are required."
        return result

    diameter = connector_diameter(connector_a)
    transaction = Transaction(doc, "BIM3DNA - Connect By")
    try:
        transaction.Start()
        pipe = Pipe.Create(doc, system_type_id, pipe_type_id, level_id, point_a, point_b)
        doc.Regenerate()
        result["created_pipe_id"] = pipe.Id.IntegerValue
        result["diameter_result"] = set_pipe_diameter(pipe, diameter)
        doc.Regenerate()
        pipe_conn_a = nearest_connector_to_point(pipe, point_a)
        pipe_conn_b = nearest_connector_to_point(pipe, point_b)
        if pipe_conn_a is None or pipe_conn_b is None:
            raise Exception("Could not resolve new pipe connectors.")
        pipe_conn_a.ConnectTo(connector_a)
        pipe_conn_b.ConnectTo(connector_b)
        doc.Regenerate()
        transaction.Commit()
    except Exception as ex:
        try:
            transaction.RollBack()
        except Exception:
            pass
        result["reason"] = "Connect By failed: {}".format(ex)
        return result

    result["ok"] = True
    result["reason"] = "Created pipe and connected both ends."
    return result

