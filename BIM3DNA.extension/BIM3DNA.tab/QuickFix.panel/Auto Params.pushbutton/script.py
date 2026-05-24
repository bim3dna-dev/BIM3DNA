# -*- coding: utf-8 -*-
__title__ = "Auto \n Params"
__doc__ = """Automatically bind selected shared parameters to selected categories
as Instance or Type parameters with custom grouping."""

# ==================================================
from Autodesk.Revit.DB import (
    Category,
    BuiltInCategory,
    InstanceBinding,
    TypeBinding,
    ExternalDefinition,
    Transaction,
    CategorySet,
    GroupTypeId,
)
from pyrevit import revit
from pyrevit import script as pyscript
import System.Windows.Forms as WinForms
from pyrevit import forms


import clr
import Autodesk.Revit.DB as DB

clr.AddReference("System")
from System.Collections.Generic import List

# Revit app and document access
app = revit.doc.Application
doc = revit.doc
rvt_year = int(app.VersionNumber)

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

# ==================================================
# Load shared parameter file
sp_file = app.OpenSharedParameterFile()
if not sp_file:
    forms.alert(
        "No shared parameter file found. Set one up in Revit first.", exitscript=True
    )

# Parse shared parameters into dictionary
shared_param_dict = {}
for group in sp_file.Groups:
    for p_def in group.Definitions:
        full_name = "[{}] {}".format(group.Name, p_def.Name)
        shared_param_dict[full_name] = p_def

# Ask user to select parameters
selected_names = forms.SelectFromList.show(
    sorted(shared_param_dict.keys()),
    multiselect=True,
    title="Select Shared Parameters to Bind",
    button_name="Bind Parameters",
)

if not selected_names:
    forms.alert("No parameters selected. Exiting script.", exitscript=True)

selected_defs = [shared_param_dict[name] for name in selected_names]

# Ask binding type: instance or type
binding_choice = forms.alert(
    "Choose parameter binding type:", options=["Instance", "Type"], exitscript=True
)

# Ask which categories to bind to
category_options = {
    "Walls": BuiltInCategory.OST_Walls,
    "Floors": BuiltInCategory.OST_Floors,
    "Doors": BuiltInCategory.OST_Doors,
    "Windows": BuiltInCategory.OST_Windows,
    "Views": BuiltInCategory.OST_Views,
    "Title Blocks": BuiltInCategory.OST_TitleBlocks,
    "Rooms": BuiltInCategory.OST_Rooms,
}

cat_selection = forms.SelectFromList.show(
    sorted(category_options.keys()),
    multiselect=True,
    title="Select Categories for Binding",
)

if not cat_selection:
    forms.alert("No categories selected. Exiting script.", exitscript=True)

# Build CategorySet
cat_set = app.Create.NewCategorySet()
for cat_name in cat_selection:
    cat = Category.GetCategory(doc, category_options[cat_name])
    if cat:
        cat_set.Insert(cat)

# Create appropriate binding
if binding_choice == "Instance":
    binding = app.Create.NewInstanceBinding(cat_set)
else:
    binding = app.Create.NewTypeBinding(cat_set)

# Select parameter group
group = GroupTypeId.General if rvt_year >= 2024 else DB.BuiltInParameterGroup.PG_GENERAL

# Begin transaction
with Transaction(doc, "Bind Shared Parameters") as t:
    t.Start()
    added = []
    skipped = []
    for p_def in selected_defs:
        try:
            doc.ParameterBindings.Insert(p_def, binding, group)
            added.append(p_def.Name)
        except Exception as e:
            skipped.append("{} (error: {})".format(p_def.Name, str(e)))
    t.Commit()

# Final result message
msg = "Parameter binding complete.\n\nAdded: {}\nSkipped: {}\n".format(
    len(added), len(skipped)
)
if skipped:
    msg += "\nSkipped Parameters:\n" + "\n".join(skipped)

forms.alert(msg, title="Binding Results")
