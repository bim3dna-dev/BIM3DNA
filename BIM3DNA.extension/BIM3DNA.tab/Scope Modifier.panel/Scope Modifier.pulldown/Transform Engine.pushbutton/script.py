# -*- coding: utf-8 -*-
__title__ = "Transform Engine"
__doc__ = """Version = 1.0
Date    = 12.28.2025
________________________________________________________________
Description:

________________________________________________________________
How-To:

________________________________________________________________
Author: Emin Avdovic"""

# ==================================================
# Imports
# ==================================================
from Autodesk.Revit.DB import *
from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    DWGExportOptions,
    ElementId,
    FamilySymbol,
    FamilyInstance,
    FBXExportOptions,
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
    ImageExportOptions,
    ImageFileType,
    ImageResolution,
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
)
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.UI import *
from Autodesk.Revit.UI import TaskDialog
from Autodesk.Revit.UI import UIDocument
from Autodesk.Revit.UI import UIApplication
from Autodesk.Revit.DB.Structure import *
from Autodesk.Revit.Exceptions import *
from Autodesk.Revit.Attributes import *
from Autodesk.Revit.Exceptions import ArgumentException
from System.Collections.Generic import List as ClrList

import clr
import System
import System.IO
import os
import subprocess
import datetime
import tempfile
import json
import imp

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
from pyrevit import script

from System import Array
import math, re, sys


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
_MODULE_STATE_PATH = (
    os.path.join(_LIB_DIR, "module_state.py") if _LIB_DIR else None
)

try:
    module_state = imp.load_source(
        "bim3dna_scope_modifier_module_state", _MODULE_STATE_PATH
    )
except Exception as ex:
    print("SCOPE MODIFIER IMPORT ERROR -> module_state could not be imported")
    print("script dir:", _THIS_DIR)
    print("extension dir:", _EXT_DIR)
    print("lib dir:", _LIB_DIR)
    print("error:", ex)
    raise

# ==================================================
# Revit Document Setup
# ==================================================
app = __revit__.Application
uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document
INCLUDE_CONNECTED_FAMILIES = False
TRANSFORM_SAFE_CATEGORIES = set(
    [
        "Pipes",
        "Pipe Fittings",
        "Pipe Accessories",
        "Ducts",
        "Duct Fittings",
        "Duct Accessories",
    ]
)
CONNECTED_FAMILY_CATEGORIES = set(
    [
        "Plumbing Fixtures",
        "Plumbing Equipment",
        "Mechanical Equipment",
        "Air Terminals",
    ]
)
PIPE_GROUP_CATEGORIES = set(
    ["Pipes", "Pipe Fittings", "Pipe Accessories"]
)
DUCT_GROUP_CATEGORIES = set(
    ["Ducts", "Duct Fittings", "Duct Accessories"]
)
TRANSFORM_INPUT_INFO = {
    "source_payload": "current selection",
    "raw_loaded_count": 0,
    "skipped": [],
    "skipped_by_reason_category": {},
}

# VERBOSE = False

# def debug(*args):
#     if VERBOSE:
#         print(" ".join([str(a) for a in args]))


# output = script.get_output()
# output.close_others()

try:
    from PIL import Image as PILImage

    PIL_OK = True
except ImportError:
    PIL_OK = False

# ==================================================
# Selection Filters
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


# ==================================================
# Frame helpers (scope box based)
# ==================================================


def build_frame_from_scopebox(scopebox):
    bb = scopebox.get_BoundingBox(None)
    if not bb or not bb.Transform:
        raise Exception("Scope box has no bounding box/transform.")
    tf = bb.Transform
    dx = bb.Max.X - bb.Min.X
    dy = bb.Max.Y - bb.Min.Y
    bx = tf.BasisX.Normalize()
    by = tf.BasisY.Normalize()
    if dy > dx:
        bx, by = by, bx
    bz = tf.BasisZ.Normalize()
    origin_local = XYZ(
        (bb.Min.X + bb.Max.X) * 0.5,
        (bb.Min.Y + bb.Max.Y) * 0.5,
        (bb.Min.Z + bb.Max.Z) * 0.5,
    )
    origin_world = tf.OfPoint(origin_local)
    by = bz.CrossProduct(bx).Normalize()
    bx = by.CrossProduct(bz).Normalize()
    return {"origin": origin_world, "x": bx, "y": by, "z": bz}


def frame_to_transform(frame):
    t = Transform.Identity
    t.Origin = frame["origin"]
    t.BasisX = frame["x"]
    t.BasisY = frame["y"]
    t.BasisZ = frame["z"]
    return t


def compute_transform(src_frame, tgt_frame):
    src_t = frame_to_transform(src_frame)
    tgt_t = frame_to_transform(tgt_frame)
    return tgt_t.Multiply(src_t.Inverse)


def log_frame(label, frame):
    o = frame["origin"]
    print(
        "{} origin: ({:.3f}, {:.3f}, {:.3f})".format(
            label, float(o.X), float(o.Y), float(o.Z)
        )
    )


def summarize_transform(xf):
    trans = xf.Origin
    bx = xf.BasisX
    angle = math.atan2(bx.Y, bx.X)
    deg = angle * 180.0 / math.pi
    print(
        "Transform: translate ({:.3f}, {:.3f}, {:.3f}), rotateZ {:.3f} deg".format(
            float(trans.X), float(trans.Y), float(trans.Z), float(deg)
        )
    )


# ==================================================
# Main workflow
# ==================================================


def pick_scopebox(prompt):
    ref = uidoc.Selection.PickObject(
        ObjectType.Element, ScopeBoxSelectionFilter(), prompt
    )
    return doc.GetElement(ref.ElementId)


def pick_elements():
    ids = list(uidoc.Selection.GetElementIds())
    if ids:
        TRANSFORM_INPUT_INFO["source_payload"] = "current selection"
        TRANSFORM_INPUT_INFO["raw_loaded_count"] = len(ids)
        return [doc.GetElement(i) for i in ids]
    # Prefer the pre-classified payload produced by Element Collector.
    try:
        def find_state_file(name):
            for d in module_state._candidate_dirs(doc):
                print("transform_engine: checking {}".format(d))
                p = os.path.join(d, name)
                if os.path.isfile(p):
                    return p
            return None

        copyable_path = module_state.get_latest_copyable_payload_path()
        if not copyable_path:
            copyable_path = find_state_file("copyable_latest.json")
        if copyable_path and os.path.isfile(copyable_path):
            with open(copyable_path, "r") as fp:
                data = json.load(fp)
            ids_saved = data.get("copyable_ids", [])
            elems = [
                doc.GetElement(ElementId(int(i))) for i in ids_saved if i is not None
            ]
            elems = [e for e in elems if e and e.IsValidObject]
            TRANSFORM_INPUT_INFO["source_payload"] = copyable_path
            TRANSFORM_INPUT_INFO["raw_loaded_count"] = data.get(
                "valid_count", len(ids_saved)
            )
            TRANSFORM_INPUT_INFO["skipped"] = data.get("skipped", [])
            TRANSFORM_INPUT_INFO["skipped_by_reason_category"] = data.get(
                "skipped_by_reason_category", {}
            )
            if elems:
                print(
                    "transform_engine: loaded {} copyable ids from {}".format(
                        len(elems), copyable_path
                    )
                )
                return elems

        collected_path = find_state_file("collected_latest.json")
        if collected_path and os.path.isfile(collected_path):
            with open(collected_path, "r") as fp:
                data = json.load(fp)
            ids_saved = (
                data.get("source_state", {})
                .get("payload", {})
                .get("elements", {})
                .get("ids", [])
            )
            if not ids_saved:
                ids_saved = data.get("elements", {}).get("valid_ids", [])
            elems = [
                doc.GetElement(ElementId(int(i))) for i in ids_saved if i is not None
            ]
            elems = [e for e in elems if e and e.IsValidObject]
            if elems:
                classification = module_state.classify_copyable_elements(
                    doc, [e.Id for e in elems]
                )
                elems = [
                    doc.GetElement(ElementId(int(i)))
                    for i in classification["copyable_ids"]
                ]
                elems = [e for e in elems if e and e.IsValidObject]
                TRANSFORM_INPUT_INFO["source_payload"] = collected_path
                TRANSFORM_INPUT_INFO["raw_loaded_count"] = len(ids_saved)
                TRANSFORM_INPUT_INFO["skipped"] = classification["skipped"]
                TRANSFORM_INPUT_INFO["skipped_by_reason_category"] = (
                    classification["skipped_by_reason_category"]
                )
                print(
                    "TRANSFORM ENGINE -> no copyable payload found; "
                    "classified collected payload at runtime."
                )
                print(
                    "transform_engine: loaded {} ids from {}".format(
                        len(elems), collected_path
                    )
                )
                return elems
    except Exception:
        pass
    # last resort: prompt for selection
    td = TaskDialog("transform_engine")
    td.MainInstruction = "No elements provided"
    td.MainContent = "Use window/box selection now?"
    td.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
    if td.Show() == TaskDialogResult.Yes:
        try:
            refs = uidoc.Selection.PickObjects(
                ObjectType.Element, "Box-select elements to copy/transform"
            )
            return [doc.GetElement(r.ElementId) for r in refs]
        except Exception:
            return []
    return []


class DupTypeHandler(IDuplicateTypeNamesHandler):
    def OnDuplicateTypeNamesFound(self, args):
        return DuplicateTypeAction.UseDestinationTypes


class RollbackOnTransformError(IFailuresPreprocessor):
    def __init__(self, errors):
        self.errors = errors

    def PreprocessFailures(self, accessor):
        try:
            for message in accessor.GetFailureMessages():
                try:
                    description = message.GetDescriptionText()
                except Exception:
                    description = "Revit copy failure"
                lower_description = str(description).lower()
                is_connectivity_failure = (
                    "connected in a network" in lower_description
                    or "disconnect the family from the network"
                    in lower_description
                    or "can no longer keep the connectivity"
                    in lower_description
                )
                if is_connectivity_failure:
                    self.errors.append(
                        "NETWORK_CONNECTIVITY_COPY_FAILURE: "
                        + str(description)
                    )
                    return FailureProcessingResult.ProceedWithRollBack
                if message.GetSeverity() == FailureSeverity.Warning:
                    accessor.DeleteWarning(message)
                else:
                    self.errors.append(description)
                    return FailureProcessingResult.ProceedWithRollBack
            return FailureProcessingResult.Continue
        except Exception:
            return FailureProcessingResult.ProceedWithRollBack


def _category_name(element):
    try:
        return element.Category.Name if element and element.Category else "<None>"
    except Exception:
        return "<Error>"


def _transform_summary(xf):
    angle = math.atan2(xf.BasisX.Y, xf.BasisX.X)
    return {
        "translate_x": float(xf.Origin.X),
        "translate_y": float(xf.Origin.Y),
        "translate_z": float(xf.Origin.Z),
        "rotate_z_degrees": float(angle * 180.0 / math.pi),
    }


def _copy_ids_in_transaction(ids, xf, options, transaction_name):
    """Copy one batch and rollback it on any Revit error."""
    transaction = Transaction(doc, transaction_name)
    failures = []
    try:
        transaction.Start()
        failure_options = transaction.GetFailureHandlingOptions()
        failure_options.SetFailuresPreprocessor(
            RollbackOnTransformError(failures)
        )
        failure_options.SetClearAfterRollback(True)
        transaction.SetFailureHandlingOptions(failure_options)

        id_list = ClrList[ElementId]()
        for element_id in ids:
            id_list.Add(element_id)
        copied_ids = list(
            ElementTransformUtils.CopyElements(doc, id_list, doc, xf, options)
        )
        status = transaction.Commit()
        if status != TransactionStatus.Committed:
            return False, [], failures or ["Revit rolled back the copy batch."]
        return True, copied_ids, failures
    except Exception as ex:
        try:
            transaction.RollBack()
        except Exception:
            pass
        text = str(ex)
        lower_text = text.lower()
        if (
            "connected in a network" in lower_text
            or "disconnect the family from the network" in lower_text
            or "can no longer keep the connectivity" in lower_text
        ):
            text = "NETWORK_CONNECTIVITY_COPY_FAILURE: " + text
        failures.append(text)
        return False, [], failures


def _copy_group_with_fallback(group_name, ids, xf, options):
    """Try the connected category group, then conservative chunk fallbacks."""
    created_ids = []
    failed_ids = []
    failed_batches = []
    failure_messages = []
    mapping_pairs = []

    success, copied, errors = _copy_ids_in_transaction(
        ids, xf, options, "Transform Engine - {}".format(group_name)
    )
    if success:
        created_ids.extend(copied)
        if len(copied) == len(ids):
            mapping_pairs.extend(zip(ids, copied))
        return {
            "created_ids": created_ids,
            "failed_ids": failed_ids,
            "failed_batches": failed_batches,
            "failure_messages": failure_messages,
            "mapping_pairs": mapping_pairs,
        }

    failed_batches.append(
        {
            "group": group_name,
            "stage": "full group",
            "count": len(ids),
            "errors": errors,
        }
    )
    failure_messages.extend(errors)

    for chunk_start in range(0, len(ids), 250):
        chunk_250 = ids[chunk_start : chunk_start + 250]
        success, copied, errors = _copy_ids_in_transaction(
            chunk_250,
            xf,
            options,
            "Transform Engine - {} chunk 250".format(group_name),
        )
        if success:
            created_ids.extend(copied)
            if len(copied) == len(chunk_250):
                mapping_pairs.extend(zip(chunk_250, copied))
            continue

        failed_batches.append(
            {
                "group": group_name,
                "stage": "chunk 250",
                "count": len(chunk_250),
                "errors": errors,
            }
        )
        failure_messages.extend(errors)
        for small_start in range(0, len(chunk_250), 50):
            chunk_50 = chunk_250[small_start : small_start + 50]
            success, copied, errors = _copy_ids_in_transaction(
                chunk_50,
                xf,
                options,
                "Transform Engine - {} chunk 50".format(group_name),
            )
            if success:
                created_ids.extend(copied)
                if len(copied) == len(chunk_50):
                    mapping_pairs.extend(zip(chunk_50, copied))
                continue
            failed_ids.extend(chunk_50)
            failed_batches.append(
                {
                    "group": group_name,
                    "stage": "chunk 50",
                    "count": len(chunk_50),
                    "ids": [value.IntegerValue for value in chunk_50],
                    "errors": errors,
                }
            )
            failure_messages.extend(errors)

    return {
        "created_ids": created_ids,
        "failed_ids": failed_ids,
        "failed_batches": failed_batches,
        "failure_messages": failure_messages,
        "mapping_pairs": mapping_pairs,
    }


def main():
    try:
        src_sb = pick_scopebox("Pick SOURCE scope box")
        tgt_sb = pick_scopebox("Pick TARGET scope box")
    except Exception:
        TaskDialog.Show("transform_engine", "Scope box selection cancelled.")
        return

    try:
        src_frame = build_frame_from_scopebox(src_sb)
        tgt_frame = build_frame_from_scopebox(tgt_sb)
    except Exception as ex:
        TaskDialog.Show("transform_engine", "Frame build failed: {}".format(ex))
        return

    log_frame("Source", src_frame)
    log_frame("Target", tgt_frame)
    xf = compute_transform(src_frame, tgt_frame)
    summarize_transform(xf)

    elems = pick_elements()
    classification = module_state.classify_copyable_elements(
        doc, [e.Id for e in elems if e and e.IsValidObject]
    )
    elems = [
        doc.GetElement(ElementId(int(i)))
        for i in classification["copyable_ids"]
    ]
    elems = [e for e in elems if e and e.IsValidObject]
    combined_skipped = list(TRANSFORM_INPUT_INFO.get("skipped", []))
    combined_skipped.extend(classification["skipped"])
    combined_breakdown = dict(
        TRANSFORM_INPUT_INFO.get("skipped_by_reason_category", {})
    )
    for key, count in classification["skipped_by_reason_category"].items():
        combined_breakdown[key] = combined_breakdown.get(key, 0) + count
    print("TRANSFORM ENGINE INPUT ->")
    print(
        "loaded ids: {}".format(
            TRANSFORM_INPUT_INFO.get("raw_loaded_count") or len(elems)
        )
    )
    print("copyable ids: {}".format(len(elems)))
    print("skipped non-copyable: {}".format(len(combined_skipped)))
    print("source payload: {}".format(TRANSFORM_INPUT_INFO.get("source_payload")))
    for key, count in sorted(
        combined_breakdown.items(), key=lambda item: item[1], reverse=True
    )[:20]:
        print(" - {} : {}".format(key, count))
    elem_ids = [e.Id for e in elems if e and e.IsValidObject]
    if not elem_ids:
        TaskDialog.Show(
            "transform_engine",
            "No elements provided (selection empty and no saved state found).",
        )
        return

    transform_summary = _transform_summary(xf)
    category_ids = {}
    connected_family_skips = []
    unsupported_copyable_skips = []
    for element_id in elem_ids:
        element = doc.GetElement(element_id)
        category = _category_name(element)
        if category in CONNECTED_FAMILY_CATEGORIES and not INCLUDE_CONNECTED_FAMILIES:
            connected_family_skips.append(
                {
                    "id": element_id.IntegerValue,
                    "category": category,
                    "reason": "CONNECTED_FAMILY_SKIPPED_DEFAULT",
                }
            )
            continue
        if category not in TRANSFORM_SAFE_CATEGORIES:
            unsupported_copyable_skips.append(
                {
                    "id": element_id.IntegerValue,
                    "category": category,
                    "reason": "NOT_TRANSFORM_SAFE_DEFAULT",
                }
            )
            continue
        category_ids.setdefault(category, []).append(element_id)

    pipe_ids = []
    duct_ids = []
    for category, ids_for_category in category_ids.items():
        if category in PIPE_GROUP_CATEGORIES:
            pipe_ids.extend(ids_for_category)
        elif category in DUCT_GROUP_CATEGORIES:
            duct_ids.extend(ids_for_category)
    transform_safe_count = len(pipe_ids) + len(duct_ids)

    safe_breakdown = {}
    for category, ids_for_category in category_ids.items():
        safe_breakdown[category] = len(ids_for_category)
    connected_breakdown = {}
    for record in connected_family_skips:
        category = record["category"]
        connected_breakdown[category] = connected_breakdown.get(category, 0) + 1

    loaded_count = TRANSFORM_INPUT_INFO.get("raw_loaded_count") or len(elems)
    copyable_count = len(elems)
    print("TRANSFORM ENGINE PREFLIGHT ->")
    print("loaded ids: {}".format(loaded_count))
    print("copyable ids: {}".format(copyable_count))
    print("transform-safe ids: {}".format(transform_safe_count))
    print("connected-family skipped: {}".format(len(connected_family_skips)))
    print("non-copyable skipped: {}".format(len(combined_skipped)))
    print("source payload: {}".format(TRANSFORM_INPUT_INFO.get("source_payload")))
    for category in sorted(TRANSFORM_SAFE_CATEGORIES):
        print(
            " - transform-safe :: {} : {}".format(
                category, safe_breakdown.get(category, 0)
            )
        )
    print("TRANSFORM ENGINE CONNECTED FAMILY SKIPS ->")
    for category in sorted(CONNECTED_FAMILY_CATEGORIES):
        print(" - {} : {}".format(category, connected_breakdown.get(category, 0)))

    preflight = TaskDialog("Transform Engine Preflight")
    preflight.MainInstruction = "Proceed with transform-safe copy only?"
    preflight.MainContent = (
        "Transform:\n"
        "translate X/Y/Z: {0:.3f}, {1:.3f}, {2:.3f}\n"
        "rotateZ: {3:.3f} deg\n\n"
        "Input:\n"
        "Copyable: {4}\n"
        "Transform-safe: {5}\n"
        "Skipped connected families: {6}\n"
        "Skipped non-copyable: {7}"
    ).format(
        transform_summary["translate_x"],
        transform_summary["translate_y"],
        transform_summary["translate_z"],
        transform_summary["rotate_z_degrees"],
        copyable_count,
        transform_safe_count,
        len(connected_family_skips),
        len(combined_skipped),
    )
    preflight.CommonButtons = (
        TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
    )
    if preflight.Show() != TaskDialogResult.Yes:
        print("TRANSFORM ENGINE -> cancelled before transaction")
        return
    if transform_safe_count == 0:
        TaskDialog.Show(
            "transform_engine", "No transform-safe pipe or duct elements found."
        )
        return

    opt = CopyPasteOptions()
    opt.SetDuplicateTypeNamesHandler(DupTypeHandler())
    copied_ids = []
    failed_ids = []
    failed_batches = []
    failure_messages = []
    mapping_pairs = []
    for group_name, group_ids in (
        ("Pipe network core", pipe_ids),
        ("Duct network core", duct_ids),
    ):
        if not group_ids:
            continue
        result = _copy_group_with_fallback(group_name, group_ids, xf, opt)
        copied_ids.extend(result["created_ids"])
        failed_ids.extend(result["failed_ids"])
        failed_batches.extend(result["failed_batches"])
        failure_messages.extend(result["failure_messages"])
        mapping_pairs.extend(result["mapping_pairs"])
        print(
            "TRANSFORM ENGINE GROUP -> {} | input: {} | created: {} | failed: {}".format(
                group_name,
                len(group_ids),
                len(result["created_ids"]),
                len(result["failed_ids"]),
            )
        )
    copied = len(copied_ids)

    # assign systems to copied pipes/ducts based on source mapping
    sys_set = 0
    sys_fail = 0
    if mapping_pairs:
        for sid, nid in mapping_pairs:
            try:
                src = doc.GetElement(sid)
                dst = doc.GetElement(nid)
                if not src or not dst or not src.IsValidObject or not dst.IsValidObject:
                    continue
                catname = _category_name(src)
                if catname.lower().startswith("pipe"):
                    param = src.get_Parameter(
                        BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM
                    )
                    dstp = dst.get_Parameter(
                        BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM
                    )
                elif catname.lower().startswith("duct"):
                    param = src.get_Parameter(
                        BuiltInParameter.RBS_DUCT_SYSTEM_TYPE_PARAM
                    )
                    dstp = dst.get_Parameter(
                        BuiltInParameter.RBS_DUCT_SYSTEM_TYPE_PARAM
                    )
                else:
                    continue
                if param and dstp and not dstp.IsReadOnly:
                    stid = param.AsElementId()
                    if stid and stid != ElementId.InvalidElementId:
                        dstp.Set(stid)
                        sys_set += 1
                    else:
                        sys_fail += 1
            except Exception:
                sys_fail += 1

    created_id_values = [element_id.IntegerValue for element_id in copied_ids]
    failed_id_values = [element_id.IntegerValue for element_id in failed_ids]
    result_payload = {
        "doc_identity": module_state.get_doc_identity(doc),
        "source_payload": TRANSFORM_INPUT_INFO.get("source_payload"),
        "source_scope_box_id": src_sb.Id.IntegerValue,
        "target_scope_box_id": tgt_sb.Id.IntegerValue,
        "input_loaded_count": loaded_count,
        "input_copyable_count": copyable_count,
        "transform_safe_count": transform_safe_count,
        "created_ids": created_id_values,
        "failed_ids": failed_id_values,
        "skipped_noncopyable": combined_skipped,
        "skipped_connected_families": connected_family_skips,
        "skipped_unsupported_copyable": unsupported_copyable_skips,
        "failed_batches": failed_batches,
        "failure_messages": failure_messages,
        "skipped": combined_skipped,
        "command_mode": "copy and transform",
        "transform": transform_summary,
        "category_counts": combined_breakdown,
    }
    result_path = module_state.save_named_payload(
        doc, result_payload, "transform_created_latest.json"
    )
    print("TRANSFORM ENGINE RESULT ->")
    print("loaded ids: {}".format(loaded_count))
    print("copyable ids: {}".format(copyable_count))
    print("transform-safe input: {}".format(transform_safe_count))
    print("created: {}".format(len(created_id_values)))
    print("failed: {}".format(len(failed_id_values)))
    print("skipped non-copyable: {}".format(len(combined_skipped)))
    print(
        "skipped connected families: {}".format(
            len(connected_family_skips)
        )
    )
    print(
        "skipped unsupported copyable: {}".format(
            len(unsupported_copyable_skips)
        )
    )
    if failure_messages:
        print("failure messages:")
        for message in failure_messages[:20]:
            print(" - {}".format(message))
    print("created payload: {}".format(result_path))

    TaskDialog.Show(
        "transform_engine",
        "Transform-safe input: {}\nCreated: {}\nFailed: {}\n"
        "Skipped connected families: {}\nSkipped non-copyable: {}\n"
        "Skipped unsupported copyable: {}\nSystems set: {} | failed: {}\n"
        "Result:\n{}".format(
            transform_safe_count,
            len(created_id_values),
            len(failed_id_values),
            len(connected_family_skips),
            len(combined_skipped),
            len(unsupported_copyable_skips),
            sys_set,
            sys_fail,
            result_path,
        ),
    )


if __name__ == "__main__":
    main()
