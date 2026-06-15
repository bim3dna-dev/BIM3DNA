# -*- coding: utf-8 -*-
__title__ = "Element Collector"
__doc__ = """Load latest module_state payload and bucket elements for ModuleEngine.
No UI, prints a concise report and saves collected payloads.
"""

from Autodesk.Revit.DB import (
    BuiltInCategory,
    ElementId,
    FilteredElementCollector,
    IndependentTag,
    TextNote,
)
from Autodesk.Revit.UI import TaskDialog

import os
import sys
import json
import imp
from collections import defaultdict
from datetime import datetime
from System.Collections.Generic import List


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

uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document


def _bic(name):
    """Return BuiltInCategory value if it exists, else None."""
    try:
        return getattr(BuiltInCategory, name)
    except Exception:
        return None


def warn(msg):
    TaskDialog.Show("element_collector", msg)


def load_latest_payload():
    payload = module_state.load_latest_payload(doc)
    if not payload:
        warn("No latest.json found. Run region_scanner first.")
        sys.exit()
    return payload


def validate_doc(payload):
    payload_doc = payload.get("doc_identity") or payload.get("doc") or {}
    payload_title = ""
    if isinstance(payload_doc, dict):
        payload_title = (
            payload_doc.get("title")
            or payload_doc.get("doc_title")
            or payload_doc.get("name")
            or ""
        )
    if not module_state.payload_matches_current_doc(payload, doc):
        current_identity = module_state.get_doc_identity(doc)
        print("ELEMENT COLLECTOR -> payload doc mismatch")
        print("payload doc: {}".format(payload_doc))
        print("current doc: {}".format(current_identity))
        warn(
            "Payload doc title mismatch.\nPayload: {}\nCurrent: {}".format(
                payload_title, doc.Title
            )
        )
        sys.exit()
    print("ELEMENT COLLECTOR -> payload document identity accepted")
    print("payload title: {}".format(payload_title))
    print("current title: {}".format(doc.Title))


def bucket_pipeline(elem):
    """Return pipeline bucket key or None."""
    cat = elem.Category
    bic = cat.Id.IntegerValue if cat else None
    bic_map = {
        "pipes": _bic("OST_PipeCurves"),
        "pipe_fittings": _bic("OST_PipeFitting"),
        "pipe_accessories": _bic("OST_PipeAccessory"),
        "ducts": _bic("OST_DuctCurves"),
        "duct_fittings": _bic("OST_DuctFitting"),
        "mechanical_equipment": _bic("OST_MechanicalEquipment"),
        "plumbing_fixtures": _bic("OST_PlumbingFixtures"),
        "piping_systems": _bic("OST_PipingSystem"),
        "duct_systems": _bic("OST_DuctSystem"),
        "grids": _bic("OST_Grids"),
        "levels": _bic("OST_Levels"),
        "model_groups": _bic("OST_IOSModelGroups"),
        "rvt_links": _bic("OST_RvtLinks"),
        "views": _bic("OST_Views"),
        "section_boxes": _bic("OST_SectionBox"),
        "scope_boxes": _bic("OST_ScopeBoxes"),
        "cameras": _bic("OST_Cameras"),
        "pipe_tags": _bic("OST_PipeTags"),
        "duct_tags": _bic("OST_DuctTags"),
        "me_tags": _bic("OST_MechanicalEquipmentTags"),
    }

    def _eq(bic_val):
        return bic is not None and bic_val is not None and bic == int(bic_val)

    if _eq(bic_map["pipes"]):
        return "pipeline.mep.pipes"
    if _eq(bic_map["pipe_fittings"]):
        return "pipeline.mep.pipe_fittings"
    if _eq(bic_map["pipe_accessories"]):
        return "pipeline.mep.pipe_accessories"
    if _eq(bic_map["ducts"]):
        return "pipeline.mep.ducts"
    if _eq(bic_map["duct_fittings"]):
        return "pipeline.mep.duct_fittings"
    if _eq(bic_map["mechanical_equipment"]):
        return "pipeline.mep.mechanical_equipment"
    if _eq(bic_map["plumbing_fixtures"]):
        return "pipeline.mep.plumbing_fixtures"
    if _eq(bic_map["piping_systems"]):
        return "pipeline.systems.piping_systems"
    if _eq(bic_map["duct_systems"]):
        return "pipeline.systems.duct_systems"
    if (
        _eq(bic_map["pipe_tags"])
        or _eq(bic_map["duct_tags"])
        or _eq(bic_map["me_tags"])
    ):
        return "pipeline.annotation.tags"
    if isinstance(elem, TextNote):
        return "pipeline.annotation.text_notes"
    if _eq(bic_map["grids"]):
        return "pipeline.reference.grids"
    if _eq(bic_map["levels"]):
        return "pipeline.reference.levels"
    if _eq(bic_map["model_groups"]):
        return "pipeline.containers.groups"
    if _eq(bic_map["rvt_links"]):
        return "pipeline.links.rvt_links"
    if (
        _eq(bic_map["views"])
        or _eq(bic_map["section_boxes"])
        or _eq(bic_map["scope_boxes"])
        or _eq(bic_map["cameras"])
    ):
        return "pipeline.exclusions.view_like"
    return None


def normalize_category(cat):
    if not cat:
        return ("<None>", -1)
    name = (cat.Name or "").strip()
    norm_name = name.title()
    return (norm_name, cat.Id.IntegerValue)


def main():
    payload = load_latest_payload()
    validate_doc(payload)
    core = payload.get("payload", {})
    id_list = core.get("elements", {}).get("ids", [])
    if not id_list:
        warn("No element ids in payload.")
        sys.exit()

    valid_ids = []
    invalid_ids = []

    by_cat_id = defaultdict(list)
    cat_name_by_id = {}
    pipeline_buckets = defaultdict(list)

    for i in id_list:
        try:
            eid = ElementId(int(i))
        except Exception:
            invalid_ids.append(i)
            continue
        elem = doc.GetElement(eid)
        if not elem or not elem.IsValidObject:
            invalid_ids.append(i)
            continue
        valid_ids.append(eid.IntegerValue)

        cname, cid = normalize_category(elem.Category)
        by_cat_id[cid].append(eid.IntegerValue)
        if cid not in cat_name_by_id:
            cat_name_by_id[cid] = cname

        bucket = bucket_pipeline(elem)
        if bucket:
            pipeline_buckets[bucket].append(eid.IntegerValue)

    collected = {
        "schema_version": 1,
        "source_state": payload,
        "collected_utc": datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ"),
        "doc": {
            "title": doc.Title,
            "path": getattr(doc, "PathName", None),
        },
        "elements": {
            "valid_ids": valid_ids,
            "invalid_ids": invalid_ids,
            "by_category_id": dict(by_cat_id),
            "category_name_by_id": cat_name_by_id,
        },
        "pipeline": dict(pipeline_buckets),
    }

    classification = module_state.classify_copyable_elements(doc, valid_ids)
    source_payload_path = module_state.get_latest_region_payload_path()
    copyable_payload = {
        "schema_version": 1,
        "created_utc": datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ"),
        "doc_identity": module_state.get_doc_identity(doc),
        "source_payload": source_payload_path,
        "valid_count": len(valid_ids),
        "copyable_count": classification["counts"]["copyable"],
        "skipped_count": classification["counts"]["skipped"],
        "copyable_ids": classification["copyable_ids"],
        "skipped": classification["skipped"],
        "skipped_by_reason_category": classification[
            "skipped_by_reason_category"
        ],
    }

    # Persist both the complete collection and destructive-workflow-safe subset.
    base_dir, loc_label = module_state.get_state_dir(doc)
    if not os.path.isdir(base_dir):
        os.makedirs(base_dir)
    stamp = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
    ts_path = os.path.join(base_dir, "collected_{}.json".format(stamp))
    latest_path = os.path.join(base_dir, "collected_latest.json")
    with open(ts_path, "w") as fp:
        json.dump(collected, fp, indent=2, sort_keys=True)
    with open(latest_path, "w") as fp:
        json.dump(collected, fp, indent=2, sort_keys=True)
    copyable_path = module_state.save_named_payload(
        doc, copyable_payload, "copyable_latest.json"
    )
    module_state.set_latest_copyable_payload(copyable_path)

    # Report
    print("Element Collector")
    print(" - Loaded ids: {}".format(len(id_list)))
    print(" - Valid: {} | Invalid: {}".format(len(valid_ids), len(invalid_ids)))
    top_cats = sorted(by_cat_id.items(), key=lambda x: len(x[1]), reverse=True)[:15]
    print(" - Top categories (by count):")
    for cid, lst in top_cats:
        print("    {} | {} elem".format(cat_name_by_id.get(cid, cid), len(lst)))

    # Key pipeline buckets
    key_buckets = [
        "pipeline.mep.pipes",
        "pipeline.mep.pipe_fittings",
        "pipeline.mep.pipe_accessories",
        "pipeline.mep.ducts",
        "pipeline.mep.duct_fittings",
        "pipeline.mep.mechanical_equipment",
        "pipeline.systems.piping_systems",
        "pipeline.systems.duct_systems",
        "pipeline.annotation.tags",
        "pipeline.annotation.text_notes",
    ]
    print(" - Pipeline buckets:")
    for kb in key_buckets:
        print("    {}: {}".format(kb, len(pipeline_buckets.get(kb, []))))

    print("ELEMENT COLLECTOR COPYABILITY ->")
    print("copyable: {}".format(classification["counts"]["copyable"]))
    print("skipped: {}".format(classification["counts"]["skipped"]))
    print("Skipped breakdown:")
    breakdown = classification["skipped_by_reason_category"]
    for key, count in sorted(
        breakdown.items(), key=lambda item: item[1], reverse=True
    )[:20]:
        print(" - {} : {}".format(key, count))

    TaskDialog.Show(
        "element_collector",
        "Collected: {}\nValid: {}\nCopyable: {}\n"
        "Skipped non-copyable: {}\nInvalid: {}\nSaved:\n{}".format(
            len(id_list),
            len(valid_ids),
            classification["counts"]["copyable"],
            classification["counts"]["skipped"],
            len(invalid_ids),
            copyable_path,
        ),
    )


if __name__ == "__main__":
    main()
