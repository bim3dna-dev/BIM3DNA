# -*- coding: utf-8 -*-
__title__ = "Purge \n Unused"
__doc__ = """Safely purge unused families, dimensions, view templates, filters, elevation markers with full category control and full report."""

import os
import datetime
from Autodesk.Revit.DB import (
    Transaction,
    FilteredElementCollector,
    BuiltInCategory,
    Dimension,
    ElevationMarker,
    View,
    ParameterFilterElement,
    Element,
)
from pyrevit import revit
from pyrevit import script as pyscript
import System.Windows.Forms as WinForms
from pyrevit import forms

# Access Revit document
doc = revit.doc
uidoc = revit.uidoc
logger = pyscript.get_logger()

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

# Setup log collection
deleted_elements = []
skipped_elements = []

# Setup optional log file
log_folder = os.path.join(os.getenv("APPDATA"), "PurgeReports")
if not os.path.exists(log_folder):
    os.makedirs(log_folder)
timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
log_path = os.path.join(log_folder, "purge_report_{}.txt".format(timestamp))


def write_log_file(deleted_list, skipped_list):
    with open(log_path, "w") as f:
        f.write("=== Purge Report ===\n\n")
        f.write("Deleted elements:\n")
        for d in deleted_list:
            f.write("- {}\n".format(d))
        f.write("\nSkipped elements:\n")
        for s in skipped_list:
            f.write("- {}\n".format(s))


# Helper: Delete elements
def delete_elements(elements, category_name):
    deleted = []
    skipped = []

    if not elements:
        return deleted, skipped

    with Transaction(doc, "Purge {}".format(category_name)):
        for el in elements:
            try:
                el_id = el.Id
                doc.Delete(el_id)
                deleted.append(
                    "{} (ID: {})".format(
                        el.Name if hasattr(el, "Name") else str(el_id), el_id
                    )
                )
            except Exception as e:
                skipped.append("{} (Reason: {})".format(el.Id, str(e)))

    return deleted, skipped


# Step 1: Ask purge operations
purge_options = forms.SelectFromList.show(
    [
        "Unused Families & Types",
        "All Constraints (Dimensions)",
        "Empty Elevation Markers",
        "Unused View Templates",
        "Unused Filters",
    ],
    title="Select Purge Operations",
    multiselect=True,
)

if not purge_options:
    forms.alert("No purge operations selected. Exiting.", exitscript=True)

# Step 2: Category filtering for Families (IF SELECTED)
selected_family_categories = None

if "Unused Families & Types" in purge_options:
    # Collect categories from current model
    all_families = (
        FilteredElementCollector(doc).WhereElementIsElementType().ToElements()
    )

    category_names = set()
    category_lookup = {}

    for fam in all_families:
        try:
            cat = fam.Category
            if cat:
                category_names.add(cat.Name)
                category_lookup[cat.Name] = cat.Id.IntegerValue
        except:
            continue

    if not category_names:
        forms.alert("No categories found for family types. Exiting.", exitscript=True)

    selected_category_names = forms.SelectFromList.show(
        sorted(list(category_names)),
        title="Select Family Categories to Purge",
        multiselect=True,
    )

    if not selected_category_names:
        forms.alert(
            "No categories selected. Cancelling families purge.", exitscript=False
        )
        purge_options.remove("Unused Families & Types")
    else:
        selected_family_categories = [
            category_lookup[name] for name in selected_category_names
        ]

# Step 3: Perform selected purges

# --- Purge Unused Families ---
if "Unused Families & Types" in purge_options:
    collector = FilteredElementCollector(doc).WhereElementIsElementType()
    families = collector.ToElements()

    unused_families = []

    for fam in families:
        try:
            if (
                fam.Category
                and fam.Category.Id.IntegerValue in selected_family_categories
            ):
                if not fam.IsInUse():
                    unused_families.append(fam)
        except:
            continue

    deleted, skipped = delete_elements(
        unused_families, "Unused Families (Selected Categories)"
    )
    deleted_elements.extend(deleted)
    skipped_elements.extend(skipped)

# --- Purge All Constraints (Dimensions) ---
if "All Constraints (Dimensions)" in purge_options:
    dims = FilteredElementCollector(doc).OfClass(Dimension).ToElements()
    candidate_dims = []

    for d in dims:
        try:
            if not d.IsLocked and not d.IsReadOnly:
                candidate_dims.append(d)
        except:
            continue

    deleted, skipped = delete_elements(candidate_dims, "Dimensions (Constraints)")
    deleted_elements.extend(deleted)
    skipped_elements.extend(skipped)

# --- Purge Empty Elevation Markers ---
if "Empty Elevation Markers" in purge_options:
    markers = FilteredElementCollector(doc).OfClass(ElevationMarker).ToElements()
    candidate_markers = []

    for m in markers:
        try:
            if m.GetViewCount() == 0:
                candidate_markers.append(m)
        except:
            continue

    deleted, skipped = delete_elements(candidate_markers, "Elevation Markers")
    deleted_elements.extend(deleted)
    skipped_elements.extend(skipped)

# --- Purge Unused View Templates ---
if "Unused View Templates" in purge_options:
    view_templates = []
    views = FilteredElementCollector(doc).OfClass(View).ToElements()

    for v in views:
        if v.IsTemplate:
            view_templates.append(v)

    used_templates = set()
    non_templates = [v for v in views if not v.IsTemplate]

    for v in non_templates:
        try:
            if v.ViewTemplateId.IntegerValue > 0:
                used_templates.add(v.ViewTemplateId)
        except:
            continue

    candidate_templates = [vt for vt in view_templates if vt.Id not in used_templates]

    deleted, skipped = delete_elements(candidate_templates, "View Templates")
    deleted_elements.extend(deleted)
    skipped_elements.extend(skipped)

# --- Purge Unused Filters ---
if "Unused Filters" in purge_options:
    all_filters = (
        FilteredElementCollector(doc).OfClass(ParameterFilterElement).ToElements()
    )

    used_filter_ids = set()

    for view in (
        FilteredElementCollector(doc)
        .OfClass(View)
        .WhereElementIsNotElementType()
        .ToElements()
    ):
        try:
            ids = view.GetFilters()
            for fid in ids:
                used_filter_ids.add(fid)
        except:
            continue

    candidate_filters = [f for f in all_filters if f.Id not in used_filter_ids]

    deleted, skipped = delete_elements(candidate_filters, "View Filters")
    deleted_elements.extend(deleted)
    skipped_elements.extend(skipped)

# Step 4: Write log
write_log_file(deleted_elements, skipped_elements)

# Step 5: Final summary
summary_message = (
    "Purge completed.\n\n"
    "Deleted elements: {}\n"
    "Skipped elements: {}\n\n"
    "Detailed log saved to:\n{}".format(
        len(deleted_elements), len(skipped_elements), log_path
    )
)

forms.alert(summary_message, title="Purge Summary")
