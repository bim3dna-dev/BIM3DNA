# -*- coding: utf-8 -*-
__title__ = "Select Same \n Type"
__doc__ = """Selects all elements of the same type as the selected element."""

# ╦╔╦╗╔═╗╔═╗╦═╗╔╦╗╔═╗
# ║║║║╠═╝║ ║╠╦╝ ║ ╚═╗
# ╩╩ ╩╩  ╚═╝╩╚═ ╩ ╚═╝
# ==================================================
from Autodesk.Revit.DB import *
from System.Collections.Generic import List
from Autodesk.Revit.UI import TaskDialog

# from RevitServices.Persistence import DocumentManager
from pyrevit import revit, DB
from pyrevit import script as pyscript
from pyrevit import forms

# .NET Imports
import System.Windows.Forms as WinForms
import clr

clr.AddReference("System")
clr.AddReference("RevitAPI")
clr.AddReference("RevitServices")
from System.Collections.Generic import List


# ╦  ╦╔═╗╦═╗╦╔═╗╔╗ ╦  ╔═╗╔═╗
# ╚╗╔╝╠═╣╠╦╝║╠═╣╠╩╗║  ║╣ ╚═╗
#  ╚╝ ╩ ╩╩╚═╩╩ ╩╚═╝╩═╝╚═╝╚═╝
# ==================================================
app = __revit__.Application
# uidoc = __revit__.ActiveUIDocument
uidoc = revit.uidoc
# doc = __revit__.ActiveUIDocument.Document  # type:Document
doc = revit.doc

# ╔╦╗╔═╗╦╔╗╔
# ║║║╠═╣║║║║
# ╩ ╩╩ ╩╩╝╚╝
# ==================================================
def _silent_alert(msg, title="pyRevit", exitscript=False, **kwargs):
    # No icon => no Windows system sound
    WinForms.MessageBox.Show(
        str(msg), str(title),
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.None
    )
    if exitscript:
        pyscript.exit()

# Replace pyRevit's alert with our silent version
forms.alert = _silent_alert
# --- end silent override---

# Debug toggle
DEBUG = False


def dlogg(msg):
    if DEBUG:
        print(msg)


sel = revit.get_selection()
if not sel:
    forms.alert("At least one object must be selected.", exitscript=True)

# Collect matches per (categoryId, typeId/linestyleId) across ALL seed elements
all_ids = set()  # de-duplicated ElementIds
groups = (
    {}
)  # key -> {'ids': set(), 'catname': str, 'typename': str, 'view_specific': {}}
seed_categories = set()  # set of seed category names


def key_for(elem):
    """Return (kind, intId) key for 'type' vs 'linestyle'."""
    if isinstance(elem, CurveElement):
        return ("linestyle", elem.LineStyle.Id.IntegerValue)
    else:
        return ("type", elem.GetTypeId().IntegerValue)


def type_name_for(elem):
    if isinstance(elem, CurveElement):
        try:
            return elem.LineStyle.Name
        except:
            return "(line style)"
    else:
        try:
            t = doc.GetElement(elem.GetTypeId())
            return t.Name if t and hasattr(t, "Name") else "(type)"
        except:
            return "(type)"


def category_name_for(elem):
    try:
        return elem.Category.Name if elem.Category else "(no category)"
    except:
        # Some elements (e.g., Dimensions) can be tricky
        return elem.GetType().Name


for seed in sel:
    # Remember seed category names for the header
    seed_categories.add(category_name_for(seed))

    # Decide class/category filter for the collector
    by_class = None
    cat_id = None
    if isinstance(seed, Dimension):
        by_class = Dimension
    elif seed.Category:  # safe guard
        cat_id = seed.Category.Id

    # Which "type" are we matching?
    skind, stype_int = key_for(seed)

    # Build collector
    if by_class:
        col = (
            FilteredElementCollector(doc)
            .OfClass(by_class)
            .WhereElementIsNotElementType()
        )
    else:
        col = (
            FilteredElementCollector(doc)
            .OfCategoryId(cat_id)
            .WhereElementIsNotElementType()
        )

    # Filter to same type/linestyle
    for e in col:
        ekind, etype_int = key_for(e)
        if ekind == skind and etype_int == stype_int:
            all_ids.add(e.Id)

            gkey = (category_name_for(e), ekind, etype_int)  # stable grouping key
            if gkey not in groups:
                groups[gkey] = {
                    "ids": set(),
                    "catname": category_name_for(e),
                    "typename": type_name_for(e),
                    "view_specific": {},  # viewName -> count
                }

            groups[gkey]["ids"].add(e.Id)

            # view/model breakdown
            try:
                if e.ViewSpecific:
                    vname = revit.query.get_name(doc.GetElement(e.OwnerViewId))
                    groups[gkey]["view_specific"][vname] = (
                        groups[gkey]["view_specific"].get(vname, 0) + 1
                    )
            except:
                pass

# Apply selection
if not all_ids:
    forms.alert("No matching elements found.", exitscript=True)

# Set selection (list of ElementId is fine)
uidoc.Selection.SetElementIds(List[ElementId](list(all_ids)))

# Build summary
total = len(all_ids)
cat_list = sorted(seed_categories)
if len(cat_list) == 1:
    header = "Category: {}\n".format(cat_list[0])
else:
    header = "Categories ({}): {}\n".format(len(cat_list), ", ".join(cat_list))

lines = []
lines.append(header)
lines.append("Total matches selected: {}\n".format(total))

# Per-group breakdown
for (catname, ekind, etype_int), data in sorted(
    groups.items(), key=lambda kv: (kv[1]["catname"], kv[1]["typename"])
):
    cnt = len(data["ids"])
    view_cnt = sum(data["view_specific"].values())
    model_cnt = cnt - view_cnt
    line = "- {} – {}: {} selected".format(catname, data["typename"], cnt)
    if view_cnt > 0:
        line += " (model: {}, view-specific: {} in {} view{})".format(
            model_cnt,
            view_cnt,
            len(data["view_specific"]),
            "" if len(data["view_specific"]) == 1 else "s",
        )
    lines.append(line)

summary_text = "\n".join(lines)

# Show popup (robust across pyRevit versions)
TaskDialog.Show("Select Same Type - Summary", summary_text)
