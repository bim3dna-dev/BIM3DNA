# -*- coding: utf-8 -*-
__title__ = "Region Scanner"
__doc__ = """Minimal region scanner
Version = 0.1
Date    = 03.04.2026
______________________________________________
Description:
- Let user pick a scope box OR a closed loop of detail lines.
- Collect elements in the active view whose bounding-box center is inside the region.
- Show a quick count + list in the console and a dialog.
______________________________________________
How-To:
1) Click button.
2) Choose Scope Box (Yes) or Detail Lines (No).
3) Pick the element(s) as prompted.
______________________________________________
Author: PLEGTVOS
"""

from Autodesk.Revit.DB import (
    BuiltInCategory,
    ElementId,
    FilteredElementCollector,
    Transaction,
    XYZ,
    Reference,
    Category,
)
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogResult
from Autodesk.Revit.Exceptions import InvalidOperationException

import sys
import os
import imp
from collections import defaultdict
from System.Collections.Generic import List
from Autodesk.Revit.DB import BasicFileInfo

# Load the extension-local state helper explicitly to avoid same-name modules.
_here = os.path.dirname(__file__)
_extension_root = _here
while _extension_root and not _extension_root.lower().endswith(".extension"):
    _next_root = os.path.dirname(_extension_root)
    if _next_root == _extension_root:
        break
    _extension_root = _next_root
_module_state_path = os.path.join(_extension_root, "lib", "module_state.py")
module_state = imp.load_source(
    "bim3dna_scope_modifier_module_state", _module_state_path
)

app = __revit__.Application
uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

DIAGNOSTICS = True  # toggle verbose diagnostics
TEMP_ISOLATE = True  # toggle temporary isolate after scan
ALT_ISOLATE_HIDE_OTHER = (
    False  # last-resort: hide all others in view (persisting until reset)
)


# -----------------------------
# Selection filters
# -----------------------------
class DetailLineSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        return elem.Category and elem.Category.Id.IntegerValue == int(
            BuiltInCategory.OST_Lines
        )

    def AllowReference(self, ref, point):
        return False


class ScopeBoxSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        return elem.Category and elem.Category.Id.IntegerValue == int(
            BuiltInCategory.OST_VolumeOfInterest
        )

    def AllowReference(self, ref, point):
        return False


# -----------------------------
# Geometry helpers
# -----------------------------
def points_are_close(pt1, pt2, tol=1e-6):
    return (
        abs(pt1.X - pt2.X) < tol
        and abs(pt1.Y - pt2.Y) < tol
        and abs(pt1.Z - pt2.Z) < tol
    )


def order_segments_to_polygon(segments):
    if not segments:
        return None
    polygon = [segments[0][0], segments[0][1]]
    segments.pop(0)
    changed = True
    while segments and changed:
        changed = False
        last_pt = polygon[-1]
        for idx, seg in enumerate(segments):
            ptA, ptB = seg
            if points_are_close(last_pt, ptA):
                polygon.append(ptB)
                segments.pop(idx)
                changed = True
                break
            elif points_are_close(last_pt, ptB):
                polygon.append(ptA)
                segments.pop(idx)
                changed = True
                break
    if polygon and points_are_close(polygon[0], polygon[-1]):
        polygon.pop()
        return polygon
    return None


def is_point_inside_polygon(point, polygon):
    x, y = point.X, point.Y
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i].X, polygon[i].Y
        xj, yj = polygon[j].X, polygon[j].Y
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        ):
            inside = not inside
        j = i
    return inside


def bbox_center(elem, view):
    try:
        bb = elem.get_BoundingBox(view)
    except Exception:
        return None
    if not bb:
        return None
    return XYZ(
        (bb.Min.X + bb.Max.X) * 0.5,
        (bb.Min.Y + bb.Max.Y) * 0.5,
        (bb.Min.Z + bb.Max.Z) * 0.5,
    )


def normalize_category(cat):
    if not cat:
        return ("<None>", -1)
    name = (cat.Name or "").strip()
    # collapse casing differences (Center line vs Center Line)
    norm_name = name.title()
    return (norm_name, cat.Id.IntegerValue)


def try_temp_isolate(doc, view, ids):
    """Attempt temporary isolate; return True if succeeded."""
    if not ids or not hasattr(view, "IsolateElementsTemporary"):
        return False
    transaction = Transaction(doc, "Scope Modifier - Temporary Isolate Region")
    try:
        transaction.Start()
        view.IsolateElementsTemporary(ids)
        transaction.Commit()
        print("REGION SCANNER -> temporary isolate applied: {}".format(ids.Count))
        return True
    except Exception as ex1:
        try:
            transaction.RollBack()
        except Exception:
            pass
        print("REGION SCANNER -> temporary isolate skipped: {}".format(ex1))
        if ALT_ISOLATE_HIDE_OTHER:
            fallback_transaction = Transaction(doc, "Hide others (fallback isolate)")
            try:
                fallback_transaction.Start()
                all_ids = [
                    e.Id
                    for e in FilteredElementCollector(doc, view.Id)
                    .WhereElementIsNotElementType()
                    .ToElements()
                    if e.Id not in ids
                ]
                if all_ids:
                    view.HideElements(List[ElementId](all_ids))
                fallback_transaction.Commit()
                return True
            except Exception as ex2:
                try:
                    fallback_transaction.RollBack()
                except Exception:
                    pass
                print("Fallback hide-others failed: {}".format(ex2))
    return False


# -----------------------------
# Region acquisition
# -----------------------------
def pick_scope_box():
    try:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element,
            ScopeBoxSelectionFilter(),
            "Pick a scope box",
        )
    except Exception:
        return None
    if not ref:
        return None
    elem = doc.GetElement(ref)
    if not elem:
        return None
    bb = elem.get_BoundingBox(None)
    if not bb:
        return None
    return ("scopebox", bb)


def pick_detail_lines_polygon():
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element,
            DetailLineSelectionFilter(),
            "Select boundary detail lines (closed loop)",
        )
    except Exception:
        return None
    if not refs:
        return None
    segments = []
    for r in refs:
        crv = doc.GetElement(r).GeometryCurve
        segments.append((crv.GetEndPoint(0), crv.GetEndPoint(1)))
    poly = order_segments_to_polygon(segments[:])
    if not poly:
        TaskDialog.Show("Region Scanner", "Detail lines do not form a closed loop.")
        return None
    boundary_ids = [doc.GetElement(r).Id for r in refs]
    return ("polygon", poly, boundary_ids)


def choose_region():
    td = TaskDialog("Region Scanner")
    td.MainInstruction = "Choose region source"
    td.MainContent = "Yes = Scope Box\nNo = Detail Lines"
    td.CommonButtons = (
        TaskDialogCommonButtons.Yes
        | TaskDialogCommonButtons.No
        | TaskDialogCommonButtons.Cancel
    )
    res = td.Show()
    if res == TaskDialogResult.Yes:
        return pick_scope_box()
    if res == TaskDialogResult.No:
        return pick_detail_lines_polygon()
    return None


# -----------------------------
# Main logic
# -----------------------------
def main():
    region = choose_region()
    if not region:
        sys.exit("Cancelled.")

    if region[0] == "scopebox":
        kind, shape = region
        boundary_ids = []
    else:
        kind, shape, boundary_ids = region

    view = uidoc.ActiveView
    elems = (
        FilteredElementCollector(doc, view.Id)
        .WhereElementIsNotElementType()
        .ToElements()
    )

    total_candidates = len(elems)
    no_bbox = 0
    outside = 0
    hidden = 0
    category_counts = defaultdict(int)
    category_counts_by_id = defaultdict(int)
    category_name_by_id = {}
    outside_by_cat = defaultdict(int)
    outside_by_cat_id = defaultdict(int)

    inside = []
    for e in elems:
        if kind == "polygon" and boundary_ids and e.Id in boundary_ids:
            continue
        try:
            if hasattr(view, "IsElementVisible") and not view.IsElementVisible(e.Id):
                hidden += 1
                continue
        except Exception:
            pass
        ctr = bbox_center(e, view)
        if not ctr:
            no_bbox += 1
            continue
        if kind == "scopebox":
            bb = shape
            if (
                ctr.X >= bb.Min.X
                and ctr.X <= bb.Max.X
                and ctr.Y >= bb.Min.Y
                and ctr.Y <= bb.Max.Y
            ):
                inside.append(e)
                cname, cid = normalize_category(e.Category)
                category_counts[cname] += 1
                category_counts_by_id[cid] += 1
                if cid not in category_name_by_id:
                    category_name_by_id[cid] = cname
            else:
                outside += 1
                cname, cid = normalize_category(e.Category)
                outside_by_cat[cname] += 1
                outside_by_cat_id[cid] += 1
                if cid not in category_name_by_id:
                    category_name_by_id[cid] = cname
        elif kind == "polygon":
            if is_point_inside_polygon(ctr, shape):
                inside.append(e)
                cname, cid = normalize_category(e.Category)
                category_counts[cname] += 1
                category_counts_by_id[cid] += 1
                if cid not in category_name_by_id:
                    category_name_by_id[cid] = cname
            else:
                outside += 1
                cname, cid = normalize_category(e.Category)
                outside_by_cat[cname] += 1
                outside_by_cat_id[cid] += 1
                if cid not in category_name_by_id:
                    category_name_by_id[cid] = cname

    core_payload = {
        "view": {
            "id": view.Id.IntegerValue,
            "name": view.Name,
            "type": str(view.ViewType),
        },
        "region": {
            "method": "SECTION_BOX" if kind == "scopebox" else "DETAIL_LINES",
            "bbox_min": (
                (shape.Min.X, shape.Min.Y, shape.Min.Z) if kind == "scopebox" else None
            ),
            "bbox_max": (
                (shape.Max.X, shape.Max.Y, shape.Max.Z) if kind == "scopebox" else None
            ),
            "polygon_xyz": (
                [(p.X, p.Y, p.Z) for p in shape] if kind == "polygon" else None
            ),
            "boundary_ids": (
                [bid.IntegerValue for bid in boundary_ids] if boundary_ids else []
            ),
            "view_crop_transform": {
                "origin": (
                    view.CropBox.Transform.Origin.X,
                    view.CropBox.Transform.Origin.Y,
                    view.CropBox.Transform.Origin.Z,
                ),
                "basisX": (
                    view.CropBox.Transform.BasisX.X,
                    view.CropBox.Transform.BasisX.Y,
                    view.CropBox.Transform.BasisX.Z,
                ),
                "basisY": (
                    view.CropBox.Transform.BasisY.X,
                    view.CropBox.Transform.BasisY.Y,
                    view.CropBox.Transform.BasisY.Z,
                ),
                "basisZ": (
                    view.CropBox.Transform.BasisZ.X,
                    view.CropBox.Transform.BasisZ.Y,
                    view.CropBox.Transform.BasisZ.Z,
                ),
            },
        },
        "elements": {
            "ids": [e.Id.IntegerValue for e in inside],
            "by_category": dict(category_counts),
            "by_category_id": dict(category_counts_by_id),
            "category_name_by_id": category_name_by_id,
        },
        "diagnostics": {
            "scanned_total": total_candidates,
            "selected": len(inside),
            "no_bbox": no_bbox,
            "outside_region": outside,
            "hidden": hidden,
            "boundary": len(boundary_ids) if kind == "polygon" else 0,
            "outside_by_category": dict(outside_by_cat),
            "outside_by_category_id": dict(outside_by_cat_id),
        },
    }

    print("REGION SCANNER -> building module_state payload")
    print(
        "REGION SCANNER -> payload source type: {}".format(type(core_payload).__name__)
    )
    payload = module_state.to_payload(doc, core_payload)

    selected_ids = core_payload["elements"]["ids"]
    msg = "Found {} element(s) in region.".format(len(selected_ids))
    print(msg)

    if DIAGNOSTICS:
        print("Diagnostics:")
        print(" - Candidates in view: {}".format(total_candidates))
        print(" - No bbox: {}".format(no_bbox))
        print(" - Outside: {}".format(outside))
        print(" - Hidden: {}".format(hidden))
        print(
            " - Boundary excluded: {}".format(
                len(boundary_ids) if kind == "polygon" else 0
            )
        )
        print(" - By category (top 15):")
        for cat, cnt in sorted(
            category_counts.items(), key=lambda x: x[1], reverse=True
        )[:15]:
            print("    {}: {}".format(cat, cnt))
        print(" - Outside by category (top 15):")
        for cat, cnt in sorted(
            outside_by_cat.items(), key=lambda x: x[1], reverse=True
        )[:15]:
            print("    {}: {}".format(cat, cnt))

    # Persist payload
    saved_path = module_state.save_payload(doc, payload)
    module_state.set_latest_region_payload(saved_path)
    print("REGION SCANNER -> payload saved: {}".format(saved_path))

    if selected_ids:
        ids_to_select = List[ElementId]([ElementId(i) for i in selected_ids])
        if TEMP_ISOLATE:
            if try_temp_isolate(doc, view, ids_to_select):
                uidoc.Selection.SetElementIds(ids_to_select)
            else:
                uidoc.Selection.SetElementIds(ids_to_select)
        else:
            uidoc.Selection.SetElementIds(ids_to_select)

    TaskDialog.Show("Region Scanner", msg)
    return payload


if __name__ == "__main__":
    main()
