# -*- coding: utf-8 -*-
__title__ = "Create \n Print Set"
__doc__ = """Create a print set from selected views, grouped by type, with template saving/loading and preview confirmation."""

import clr

clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

import os
import json
import System
import System.Drawing as Drawing
from Autodesk.Revit.DB import (
    Transaction,
    FilteredElementCollector,
    PrintRange,
    View,
    ViewSet,
    ViewSheetSet,
)
from pyrevit import revit
from pyrevit import script as pyscript
import System.Windows.Forms as WinForms
from pyrevit import forms

# Access Revit document
doc = revit.doc
uidoc = revit.uidoc
logger = pyscript.get_logger()


def _show_silent_dialog(msg, title, options=None):
    """Build a minimal WinForms dialog with no system sound."""
    form = WinForms.Form()
    form.Text = str(title)
    form.StartPosition = WinForms.FormStartPosition.CenterScreen
    form.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    form.MinimizeBox = False
    form.MaximizeBox = False
    form.ShowInTaskbar = False
    form.TopMost = True
    form.AutoSize = True
    form.AutoSizeMode = WinForms.AutoSizeMode.GrowAndShrink
    form.Padding = WinForms.Padding(12)

    # Message label
    lbl = WinForms.Label()
    lbl.AutoSize = True
    lbl.MaximumSize = Drawing.Size(520, 0)  # wrap the text
    lbl.Text = str(msg)

    # Buttons panel
    btn_panel = WinForms.FlowLayoutPanel()
    btn_panel.FlowDirection = WinForms.FlowDirection.LeftToRight
    btn_panel.AutoSize = True
    btn_panel.WrapContents = True
    btn_panel.Padding = WinForms.Padding(0, 12, 0, 0)

    result_holder = {"value": None}

    def make_handler(val):
        def _handler(sender, e):
            result_holder["value"] = val
            form.DialogResult = WinForms.DialogResult.OK
            form.Close()

        return _handler

    # If options given, make one button per option; otherwise make a single OK
    opts = options if (options and len(options) > 0) else ["OK"]
    for opt in opts:
        btn = WinForms.Button()
        btn.Text = str(opt)
        btn.AutoSize = True
        btn.Margin = WinForms.Padding(6)
        btn.Click += make_handler(opt)
        btn_panel.Controls.Add(btn)
        # Choose first button as AcceptButton for Enter key
        if form.AcceptButton is None:
            form.AcceptButton = btn
        # If we see a cancel-like option, let Esc close on it
        if str(opt).lower() in ("cancel", "close", "no", "exit"):
            form.CancelButton = btn

    # Layout container
    container = WinForms.TableLayoutPanel()
    container.AutoSize = True
    container.ColumnCount = 1
    container.RowCount = 2
    container.Controls.Add(lbl, 0, 0)
    container.Controls.Add(btn_panel, 0, 1)
    form.Controls.Add(container)

    form.ShowDialog()  # no sound is played
    return result_holder["value"]


# Keep a reference to the original in case you ever need it
_original_alert = forms.alert


def _silent_alert(msg, title="pyRevit", exitscript=False, **kwargs):
    """Silent alert that matches pyRevit semantics:
    - With options: return the chosen option (never auto-exit)
    - Without options: show message and exit if exitscript=True
    """
    options = kwargs.get("options", None)
    choice = _show_silent_dialog(msg, title, options=options)
    # If no options were provided, behave like a one-way notice dialog.
    if not options and exitscript:
        pyscript.exit()
    return choice


forms.alert = _silent_alert
# --- end silent override ---

# Template folder
template_folder = os.path.join(os.getenv("APPDATA"), "BatchPrintSetTemplates")
if not os.path.exists(template_folder):
    os.makedirs(template_folder)


def save_template(view_names, template_name):
    save_path = os.path.join(template_folder, template_name + ".json")
    with open(save_path, "w") as f:
        json.dump({"views": view_names}, f, indent=4)


def load_template():
    templates = [f[:-5] for f in os.listdir(template_folder) if f.endswith(".json")]
    if not templates:
        forms.alert("No saved templates found.", exitscript=True)

    chosen_template = forms.SelectFromList.show(
        templates,
        title="Select Template to Load",
        multiselect=False,
    )
    if not chosen_template:
        forms.alert("No template selected. Exiting.", exitscript=True)

    with open(os.path.join(template_folder, chosen_template + ".json"), "r") as f:
        data = json.load(f)
        return data.get("views", [])


# Step 0: Ask what to do first
action = forms.alert(
    "Choose an action:",
    options=["Manual Selection", "Load Template", "Exit"],
    exitscript=True,
)

if action == "Exit":
    pyscript.exit()

# Step 1: Collect all views and group by type
all_views = (
    FilteredElementCollector(doc)
    .OfClass(View)
    .WhereElementIsNotElementType()
    .ToElements()
)

available_views = []
for view in all_views:
    if (
        not view.IsTemplate
        and view.CanBePrinted
        and not view.Name.startswith("<Revision Schedule>")
    ):
        available_views.append(view)

# Prepare grouped view names
grouped_view_names = []
view_lookup = {}

for view in sorted(available_views, key=lambda v: (v.ViewType.ToString(), v.Name)):
    view_type = view.ViewType.ToString().upper()
    grouped_name = "[{}] {}".format(view_type, view.Name)
    grouped_view_names.append(grouped_name)
    view_lookup[grouped_name] = view

selected_views = []

# Step 2: Based on action
if action == "Manual Selection":
    selected_grouped_names = forms.SelectFromList.show(
        grouped_view_names,
        title="Select Views for Print Set",
        multiselect=True,
    )

    if not selected_grouped_names:
        forms.alert("No views selected. Print set creation cancelled.", exitscript=True)
        pyscript.exit()

    selected_views = []
    selected_view_names = set()

    for grouped_name in selected_grouped_names:
        view = view_lookup.get(grouped_name)
        if view:
            # Detect duplicate names
            if view.Name in selected_view_names:
                forms.alert(
                    "Duplicate view detected: '{}'. Exiting.".format(view.Name),
                    exitscript=True,
                )
            selected_view_names.add(view.Name)
            selected_views.append(view)

    # Ask if user wants to save this selection
    save_now = forms.alert(
        "Do you want to save this selection as a Template?",
        options=["Yes", "No"],
        exitscript=False,
    )

    if save_now == "Yes":
        template_name = forms.ask_for_string(
            prompt="Enter a name for the template:",
            title="Save Template",
            default="NewTemplate",
        )
        if template_name:
            save_template([v.Name for v in selected_views], template_name)
            forms.alert("Template '{}' saved successfully.".format(template_name))

elif action == "Load Template":
    # Load saved view names
    template_view_names = load_template()

    # Match loaded names to current model views
    selected_views = []
    missing_views = []
    available_view_names = {v.Name: v for v in available_views}

    for name in template_view_names:
        view = available_view_names.get(name)
        if view:
            selected_views.append(view)
        else:
            missing_views.append(name)

    if not selected_views:
        forms.alert(
            "No matching views found in this project. Exiting.", exitscript=True
        )

    if missing_views:
        forms.alert(
            "Some views were not found and skipped:\n\n{}".format(
                "\n".join(missing_views)
            )
        )

# Step 3: Ask for Print Set name
print_set_name = forms.ask_for_string(
    prompt="Enter a name for the print set (default is 'ViewPrintSet').",
    title="Print Set Name",
    default="ViewPrintSet",
)

if not print_set_name:
    forms.alert("Print set creation cancelled. No name provided.", exitscript=True)

# Step 4: Preview Selected Views
preview_list = ["- {}".format(view.Name) for view in selected_views]

proceed = forms.alert(
    "You are about to create Print Set '{}'\n\nThe following views will be included:\n\n{}\n\nProceed?".format(
        print_set_name, "\n".join(preview_list)
    ),
    options=["Proceed", "Cancel"],
    exitscript=False,
)
if proceed != "Proceed":
    pyscript.exit()

# Step 5: Create the Print Set
print_manager = doc.PrintManager
print_manager.PrintRange = PrintRange.Select
view_sheet_setting = print_manager.ViewSheetSetting

view_set = ViewSet()
for view in selected_views:
    view_set.Insert(view)

# Check for existing print sets
existing_print_sets = {
    vss.Name: vss
    for vss in FilteredElementCollector(doc)
    .OfClass(ViewSheetSet)
    .WhereElementIsNotElementType()
    .ToElements()
}

with Transaction(doc, "Create Print Set") as transaction:
    transaction.Start()

    # Delete existing print set if name matches
    if print_set_name in existing_print_sets:
        view_sheet_setting.CurrentViewSheetSet = existing_print_sets[print_set_name]
        view_sheet_setting.Delete()
        logger.info("Deleted existing print set: " + print_set_name)

    # Create and save the new print set
    view_sheet_setting.CurrentViewSheetSet.Views = view_set
    view_sheet_setting.SaveAs(print_set_name)
    transaction.Commit()

forms.alert("Print set '{}' created successfully.".format(print_set_name))
