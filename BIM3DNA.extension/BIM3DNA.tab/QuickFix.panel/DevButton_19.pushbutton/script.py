# -*- coding: utf-8 -*-
__title__ = "Connect\nPipes"
__doc__ = """Connect\nPipes"""

import os
import sys

from pyrevit import revit, DB, forms

LIB_PATH = r"C:\Zonneveld\Zonneveld_Revit_API_2\ZonneveldRevitAPI\lib"
if os.path.isdir(LIB_PATH) and LIB_PATH not in sys.path:
    sys.path.insert(0, LIB_PATH)

# Direct module imports from lib root (no package)
import selection
import connectors
import align_connect
import graph_mep
import pathfinding
import isolate_view


def _is_pipe(el):
    try:
        return el.Category and el.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_PipeCurves)
    except Exception:
        return False


def _ft_to_mm(ft):
    return DB.UnitUtils.ConvertFromInternalUnits(ft, DB.UnitTypeId.Millimeters)


def run_align_connect():
    # pick source element + point
    src_ref = selection.pick_point_on_element(prompt="Pick source element (point on element)")
    if not src_ref:
        return
    src_el = revit.doc.GetElement(src_ref.ElementId)
    src_pt = src_ref.GlobalPoint

    # pick target element + point
    tgt_ref = selection.pick_point_on_element(prompt="Pick target element (point on element)")
    if not tgt_ref:
        return
    tgt_el = revit.doc.GetElement(tgt_ref.ElementId)
    tgt_pt = tgt_ref.GlobalPoint

    src_conn = connectors.closest_connector_to_point(src_el, src_pt, only_ends=_is_pipe(src_el))
    tgt_conn = connectors.closest_connector_to_point(tgt_el, tgt_pt, only_ends=_is_pipe(tgt_el))

    if not src_conn or not tgt_conn:
        forms.alert("Could not resolve connectors on one or both elements.")
        return

    d0 = src_conn.Origin.DistanceTo(tgt_conn.Origin)
    forms.alert(
        "Source connector (mm): ({:.1f}, {:.1f}, {:.1f})\n"
        "Target connector (mm): ({:.1f}, {:.1f}, {:.1f})\n"
        "Pre-move connector gap: {:.1f} mm".format(
            _ft_to_mm(src_conn.Origin.X), _ft_to_mm(src_conn.Origin.Y), _ft_to_mm(src_conn.Origin.Z),
            _ft_to_mm(tgt_conn.Origin.X), _ft_to_mm(tgt_conn.Origin.Y), _ft_to_mm(tgt_conn.Origin.Z),
            _ft_to_mm(d0)
        ),
        title="DEBUG"
    )

    include_connected = forms.alert("Move connected network with target?", yes=True, no=True)  # returns True/False
    allow_rotate = False

    res = align_connect.align_connect(
        source_el=src_el,
        target_el=tgt_el,
        source_conn=src_conn,
        target_conn=tgt_conn,
        include_connected=include_connected,
        allow_rotate=allow_rotate
    )

    forms.alert(res.to_text() if hasattr(res, "to_text") else str(res), title="Align/Connect Result")


def run_shortest_path():
    doc = revit.doc
    view = doc.ActiveView

    a_ref = selection.pick_point_on_element(prompt="Pick start element (point on element)")
    if not a_ref:
        return
    a_el = doc.GetElement(a_ref.ElementId)
    a_pt = a_ref.GlobalPoint
    a_conn = connectors.closest_connector_to_point(a_el, a_pt, only_ends=False)

    b_ref = selection.pick_point_on_element(prompt="Pick end element (point on element)")
    if not b_ref:
        return
    b_el = doc.GetElement(b_ref.ElementId)
    b_pt = b_ref.GlobalPoint
    b_conn = connectors.closest_connector_to_point(b_el, b_pt, only_ends=False)

    if not a_conn or not b_conn:
        forms.alert("Could not resolve connectors on one or both elements.")
        return

    elems = graph_mep.gather_mep_elements_in_view(doc, view)
    graph = graph_mep.build_connector_graph(elems)

    start_node = connectors.connector_key(a_conn)
    end_node = connectors.connector_key(b_conn)

    path_nodes, cost = pathfinding.dijkstra(graph, start_node, end_node)
    if not path_nodes:
        forms.alert("No path found between selected connectors.")
        return

    path_ids = graph_mep.nodes_to_element_ids(graph, path_nodes)  # implement helper
    isolate = forms.alert("Isolate path in view?", yes=True, no=True)

    isolate_view.highlight_elements(view, path_ids)
    if isolate:
        isolate_view.isolate_elements_temporarily(view, path_ids)

    forms.alert("Path elements: {}\nCost (internal units): {}".format(len(path_ids), cost), title="Shortest Path")


def main():
    choice = forms.CommandSwitchWindow.show(
        ["Align/Connect", "Shortest Path"],
        message="Choose operation"
    )
    if not choice:
        return
    if choice == "Align/Connect":
        run_align_connect()
    else:
        run_shortest_path()


if __name__ == "__main__":
    main()
