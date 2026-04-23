# -*- coding: utf-8 -*-
__title__ = "OVT Sheet\nTransfer"
__doc__ = """Version = 1.0
Date    = 03.08.2025
________________________________________________________________
Description:
Automates transfer of sheets, views, and scope boxes from OVT1 to OVT2.
________________________________________________________________
How-To:

1. [Hold ALT + CLICK] on the button to open its source folder.
2. Create your boundary (with detail lines) in the view.
3. Click the button and follow the prompts.
________________________________________________________________
Author: Emin Avdovic"""

# ==================================================
# Imports
# ==================================================
from Autodesk.Revit.DB import *
from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    ElementId,
    FamilySymbol,
    FamilyInstance,
    FilteredElementCollector,
    FormatOptions,
    FilterStringRule,
    FilterStringRuleEvaluator,
    FilterStringBeginsWith,
    FilterStringContains,
    FilterStringEquals,
    XYZ,
    Transaction,
    TextNote,
    TextNoteType,
    TextNoteOptions,
    IndependentTag,
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
    ViewPlan,
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
    CurveLoop,
)
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.UI import *
from Autodesk.Revit.UI import TaskDialog
from Autodesk.Revit.UI import UIDocument
from Autodesk.Revit.DB.Structure import *
from Autodesk.Revit.Exceptions import *
from Autodesk.Revit.Attributes import *
from Autodesk.Revit.Exceptions import ArgumentException
from System.Collections.Generic import List

import clr
import System
import System.IO

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

from System import Array
from pyrevit import forms
import math, re, sys

# ==================================================
# Revit Document Setup
# ==================================================
app = __revit__.Application
uidoc = __revit__.ActiveUIDocument
doc = __revit__.ActiveUIDocument.Document

VERBOSE = False

# === TO HIDE THE DEBUG CONSOLE ===
# def debug(*args):
#     if VERBOSE:
#         print(" ".join([str(a) for a in args]))


# Find the target document by name or prompt user to pick from open docs
def find_target_doc(target_title_substring):
    from Autodesk.Revit.ApplicationServices import Application

    app = doc.Application
    for open_doc in app.Documents:
        if target_title_substring in open_doc.Title and open_doc != doc:
            return open_doc
    raise Exception("Target doc with '{}' not found.".format(target_title_substring))


# Set this to part of your target doc filename (e.g., "OVT2")
target_doc = find_target_doc("BNZ_OMO_OV2_N_IT_MPO_Sanitaire Installatie")


# === STEP 1 & 2: COLLECT VIEWS, SHEETS, SCOPE BOXES
def collect_views_and_sheets(source_doc, main_view_name):
    # Find main view (floor plan) by name
    main_view = None
    for v in FilteredElementCollector(source_doc).OfClass(ViewPlan):
        if v.Name == main_view_name and not v.IsTemplate:
            main_view = v
            break
    if not main_view:
        raise Exception("Main view '{}' not found.".format(main_view_name))

    # Find dependent views
    dependent_views = []
    for v in FilteredElementCollector(source_doc).OfClass(ViewPlan):
        if (
            not v.IsTemplate
            and v.GetPrimaryViewId().IntegerValue == main_view.Id.IntegerValue
        ):
            dependent_views.append(v)
    all_views = [main_view] + dependent_views
    print("DEBUG: Views found: ", [v.Name for v in all_views])
    # Find sheets containing any of these views
    view_ids = set(v.Id.IntegerValue for v in all_views)
    sheets = []
    for s in FilteredElementCollector(source_doc).OfClass(ViewSheet):
        for vp_id in s.GetAllViewports():
            vp = source_doc.GetElement(vp_id)
            if hasattr(vp, "ViewId") and vp.ViewId.IntegerValue in view_ids:
                sheets.append(s)
                break
    print("DEBUG: Sheets found: ", [s.SheetNumber for s in sheets])
    # Find all unique scope boxes
    scope_box_ids = set()
    for s in sheets:
        for vp_id in s.GetAllViewports():
            vp = source_doc.GetElement(vp_id)
            if hasattr(vp, "ViewId") and vp.ViewId.IntegerValue in view_ids:
                # Try to get scope box from the viewport (works for placed dependent views)
                scope_box_param = vp.LookupParameter("Scope Box")
                if (
                    scope_box_param
                    and scope_box_param.StorageType == StorageType.ElementId
                ):
                    sb_id = scope_box_param.AsElementId()
                    if sb_id != ElementId.InvalidElementId:
                        scope_box_ids.add(sb_id)
    print(
        "DEBUG: Scope boxes found: ",
        [source_doc.GetElement(sb).Name for sb in scope_box_ids],
    )
    return all_views, sheets, scope_box_ids


# === STEP 3: COPY SCOPE BOXES
def copy_scope_boxes(source_doc, target_doc, scope_box_ids):
    from Autodesk.Revit.DB import CopyPasteOptions, ElementTransformUtils

    if not scope_box_ids:
        return {}
    copy_options = CopyPasteOptions()
    # Correct: convert to .NET List[ElementId]
    scope_box_ids_list = List[ElementId]()
    original_ids = []
    for eid in scope_box_ids:
        scope_box_ids_list.Add(eid)
        original_ids.append(eid)

    with Transaction(target_doc, "Copy Scope Boxes") as t:
        t.Start()
        # CopyElements returns new element ids in the target doc
        new_ids = ElementTransformUtils.CopyElements(
            source_doc, scope_box_ids_list, target_doc, None, copy_options
        )
        t.Commit()
    new_ids = list(new_ids)
    if len(new_ids) != len(original_ids):
        raise Exception("Scope box copy count mismatch.")
    id_dict = {}
    for orig_id, new_id in zip(original_ids, new_ids):
        id_dict[orig_id] = new_id
    return id_dict


# === STEP 4: COPY VIEWS
def copy_views(source_doc, target_doc, views, scopebox_id_map):
    from Autodesk.Revit.DB import CopyPasteOptions, ElementTransformUtils

    view_id_map = {}
    copy_options = CopyPasteOptions()
    target_template_lookup = {}
    for tmpl in FilteredElementCollector(target_doc).OfClass(View):
        if getattr(tmpl, "IsTemplate", False):
            key = (tmpl.Name, tmpl.ViewType)
            if key not in target_template_lookup:
                target_template_lookup[key] = tmpl.Id

    template_cache = {}

    def resolve_template_id(template_id):
        if (
            not template_id
            or template_id == ElementId.InvalidElementId
            or template_id.IntegerValue == -1
        ):
            return ElementId.InvalidElementId
        if template_id in template_cache:
            return template_cache[template_id]
        template = source_doc.GetElement(template_id)
        if not template:
            template_cache[template_id] = ElementId.InvalidElementId
            return template_cache[template_id]
        key = (template.Name, template.ViewType)
        template_cache[template_id] = target_template_lookup.get(
            key, ElementId.InvalidElementId
        )
        return template_cache[template_id]

    primary_views = []
    dependent_views = []
    for v in views:
        primary_id = ElementId.InvalidElementId
        try:
            primary_id = v.GetPrimaryViewId()
        except Exception:
            pass
        if primary_id == ElementId.InvalidElementId:
            primary_views.append(v)
        else:
            dependent_views.append(v)

    if not primary_views:
        raise Exception("No primary views available to copy.")

    views_list = List[ElementId]([v.Id for v in primary_views])
    with Transaction(target_doc, "Copy Views") as t:
        t.Start()
        new_view_ids = ElementTransformUtils.CopyElements(
            source_doc, views_list, target_doc, None, copy_options
        )
        new_view_ids = list(new_view_ids)
        if len(new_view_ids) != len(primary_views):
            raise Exception("View copy count mismatch.")
        for v, new_view_id in zip(primary_views, new_view_ids):
            new_view = target_doc.GetElement(new_view_id)
            view_id_map[v.Id] = new_view_id
            # Assign scope box if applicable
            if hasattr(v, "ScopeBox") and v.ScopeBox and v.ScopeBox.IntegerValue != -1:
                if v.ScopeBox in scopebox_id_map:
                    new_view.ScopeBox = scopebox_id_map[v.ScopeBox]
            # Copy view template if exists
            resolved_template = resolve_template_id(v.ViewTemplateId)
            if resolved_template != ElementId.InvalidElementId:
                new_view.ViewTemplateId = resolved_template

        # Recreate dependent views by duplicating the copied primary view
        for dep_view in dependent_views:
            parent_id = dep_view.GetPrimaryViewId()
            if parent_id not in view_id_map:
                raise Exception(
                    "Primary view for '{}' was not copied; cannot recreate dependent.".format(
                        dep_view.Name
                    )
                )
            new_parent = target_doc.GetElement(view_id_map[parent_id])
            new_dep_id = new_parent.Duplicate(ViewDuplicateOption.AsDependent)
            new_dep = target_doc.GetElement(new_dep_id)

            # Apply metadata to the duplicated dependent view
            try:
                new_dep.Name = dep_view.Name
            except Exception:
                suffix = 1
                base_name = dep_view.Name
                while True:
                    try:
                        new_dep.Name = "{}_copy{}".format(base_name, suffix)
                        break
                    except Exception:
                        suffix += 1

            resolved_template = resolve_template_id(dep_view.ViewTemplateId)
            if resolved_template != ElementId.InvalidElementId:
                new_dep.ViewTemplateId = resolved_template
            if (
                hasattr(dep_view, "ScopeBox")
                and dep_view.ScopeBox
                and dep_view.ScopeBox.IntegerValue != -1
            ):
                if dep_view.ScopeBox in scopebox_id_map:
                    new_dep.ScopeBox = scopebox_id_map[dep_view.ScopeBox]
            new_dep.CropBoxVisible = dep_view.CropBoxVisible
            new_dep.CropBoxActive = dep_view.CropBoxActive

            try:
                dep_mgr = dep_view.GetCropRegionShapeManager()
                new_mgr = new_dep.GetCropRegionShapeManager()
                if (
                    dep_mgr
                    and new_mgr
                    and dep_mgr.CanHaveShape()
                    and new_mgr.CanHaveShape()
                ):
                    shapes = dep_mgr.GetCropRegionShape()
                    if shapes:
                        shape_list = List[CurveLoop]()
                        for loop in shapes:
                            shape_list.Add(loop)
                        new_mgr.SetCropShape(shape_list)
            except Exception:
                pass

            view_id_map[dep_view.Id] = new_dep_id
        t.Commit()
    return view_id_map


# === STEP 5: COPY SHEETS AND PLACE VIEWS
def copy_sheets(source_doc, target_doc, sheets, view_id_map):
    from Autodesk.Revit.DB import Viewport

    def get_titleblock_type_id(document, sheet):
        # Revit 2020+ has GetTitleBlockIds, but fall back if unavailable
        if hasattr(sheet, "GetTitleBlockIds"):
            tb_ids = sheet.GetTitleBlockIds()
            if tb_ids:
                titleblock = document.GetElement(list(tb_ids)[0])
                if titleblock:
                    return titleblock.GetTypeId()
        collector = (
            FilteredElementCollector(document, sheet.Id)
            .OfCategory(BuiltInCategory.OST_TitleBlocks)
            .WhereElementIsNotElementType()
        )
        tb = collector.FirstElement()
        if tb:
            return tb.GetTypeId()
        return ElementId.InvalidElementId

    existing_sheet_numbers = set(
        s.SheetNumber for s in FilteredElementCollector(target_doc).OfClass(ViewSheet)
    )
    copied_sheet_count = 0
    with Transaction(target_doc, "Copy Sheets and Place Views") as t:
        t.Start()
        for s in sheets:
            new_sheet_number = s.SheetNumber
            # If conflict, auto-rename with _copy, _copy2, etc.
            count = 1
            orig_sheet_number = new_sheet_number
            while new_sheet_number in existing_sheet_numbers:
                new_sheet_number = "{}_copy{}".format(
                    orig_sheet_number, count if count > 1 else ""
                )
                count += 1
            # Copy titleblock family
            titleblock_type_id = get_titleblock_type_id(source_doc, s)
            if titleblock_type_id != ElementId.InvalidElementId:
                new_sheet = ViewSheet.Create(target_doc, titleblock_type_id)
            else:
                new_sheet = ViewSheet.Create(target_doc, ElementId.InvalidElementId)
            new_sheet.SheetNumber = new_sheet_number
            new_sheet.Name = s.Name
            # Place new views at same viewport locations
            for vp_id in s.GetAllViewports():
                vp = source_doc.GetElement(vp_id)
                old_view_id = vp.ViewId
                if old_view_id in view_id_map:
                    new_view_id = view_id_map[old_view_id]
                    box_center = vp.GetBoxCenter()
                    Viewport.Create(target_doc, new_sheet.Id, new_view_id, box_center)
            existing_sheet_numbers.add(new_sheet_number)
            copied_sheet_count += 1
        t.Commit()
    return copied_sheet_count


# === MAIN FUNCTION
def main():
    def pick_main_view_names(doc):
        view_names = sorted(
            [
                v.Name
                for v in FilteredElementCollector(doc).OfClass(ViewPlan)
                if not v.IsTemplate
            ]
        )
        result = forms.SelectFromList.show(
            view_names,
            title="Select Main Floor Plan(s)",
            multiselect=True,
        )
        if not result:
            return []
        if isinstance(result, list):
            return result
        return [result]

    selected_view_names = pick_main_view_names(doc)
    if not selected_view_names:
        TaskDialog.Show("Cancelled", "No main views selected.")
        return

    total_views = 0
    total_sheets = 0
    total_scope_boxes = 0
    failures = []

    for main_view_name in selected_view_names:
        try:
            all_views, sheets, scope_box_ids = collect_views_and_sheets(
                doc, main_view_name
            )
            if not all_views or not sheets:
                failures.append(
                    "{}: No matching views or sheets found.".format(main_view_name)
                )
                continue

            scopebox_id_map = (
                copy_scope_boxes(doc, target_doc, scope_box_ids)
                if scope_box_ids
                else {}
            )
            view_id_map = copy_views(doc, target_doc, all_views, scopebox_id_map)
            sheet_count = copy_sheets(doc, target_doc, sheets, view_id_map)

            total_views += len(all_views)
            total_sheets += sheet_count
            total_scope_boxes += len(scope_box_ids)
        except Exception as exc:
            failures.append("{}: {}".format(main_view_name, exc))

    if total_views == 0 and failures:
        TaskDialog.Show(
            "Transfer Failed",
            "No views were copied.\n{}".format("\n".join(failures[:5])),
        )
        return

    msg_lines = [
        "Copied {} views, {} sheets, and {} scope boxes.".format(
            total_views, total_sheets, total_scope_boxes
        )
    ]
    if failures:
        msg_lines.append(
            "Skipped {} selection(s):\n{}".format(
                len(failures), "\n".join(failures[:5])
            )
        )
        if len(failures) > 5:
            msg_lines.append("...and more. See console for details.")

    TaskDialog.Show("Transfer Complete", "\n".join(msg_lines))


main()
