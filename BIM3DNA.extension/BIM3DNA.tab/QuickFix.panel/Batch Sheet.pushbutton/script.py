# -*- coding: utf-8 -*-
__title__ = "Batch \n Sheet "
__doc__ = """Create sheets or placeholder sheets with options for batch title block selection, templates, and sheet set grouping."""

from Autodesk.Revit.DB import *
from pyrevit import revit
from pyrevit import script as pyscript
import System.Windows.Forms as WinForms
from pyrevit import forms

import clr

clr.AddReference("System")
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from System.Collections.Generic import List

import os

# Access Revit document
uidoc = revit.uidoc
doc = revit.doc
app = __revit__.Application

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

DEBUG = False


def debug_log(message):
    if DEBUG:
        print(message)


def get_title_blocks():
    titleblocks = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsElementType()
        .ToElements()
    )

    titleblock_dict = {}
    for tb in titleblocks:
        family_name = tb.FamilyName
        type_name_param = tb.LookupParameter("Type Name")
        type_name = type_name_param.AsString() if type_name_param else "Unnamed"
        key = "{}: {}".format(family_name, type_name)
        titleblock_dict[key] = tb
        debug_log("Found Title Block: {}".format(key))

    return titleblock_dict


def create_placeholder(sheet_num, sheet_name):
    try:
        with Transaction(doc, "Create Placeholder Sheet") as t:
            t.Start()
            new_ph_sheet = ViewSheet.CreatePlaceholder(doc)
            new_ph_sheet.Name = sheet_name
            new_ph_sheet.SheetNumber = sheet_num
            t.Commit()
            debug_log("✅ Placeholder created: {} - {}".format(sheet_num, sheet_name))
    except Exception as e:
        debug_log(
            "❌ Failed to create placeholder: {} - {} | Error: {}".format(
                sheet_num, sheet_name, e
            )
        )


def create_sheet(sheet_num, sheet_name, titleblock_id):
    try:
        with Transaction(doc, "Create Sheet") as t:
            t.Start()
            new_sheet = ViewSheet.Create(doc, titleblock_id)
            new_sheet.Name = sheet_name
            new_sheet.SheetNumber = sheet_num
            t.Commit()
            debug_log("✅ Sheet created: {} - {}".format(sheet_num, sheet_name))
    except Exception as e:
        debug_log(
            "❌ Failed to create sheet: {} - {} | Error: {}".format(
                sheet_num, sheet_name, e
            )
        )


def save_template(sheet_data):
    template_folder = os.path.join(os.getenv("APPDATA"), "BatchSheetTemplates")
    if not os.path.exists(template_folder):
        os.makedirs(template_folder)
    template_path = os.path.join(template_folder, "last_batch_template.txt")
    with open(template_path, "w") as f:
        entries = []
        for num, name in sheet_data.items():
            entries.append("{} {}".format(num, name))
        f.write(", ".join(entries))


def load_template():
    template_folder = os.path.join(os.getenv("APPDATA"), "BatchSheetTemplates")
    template_path = os.path.join(template_folder, "last_batch_template.txt")
    if os.path.exists(template_path):
        with open(template_path, "r") as f:
            return f.read()
    return ""


# Main
if __name__ == "__main__":

    # Always ask manual entry
    sheet_input = forms.ask_for_string(
        prompt="Enter sheet numbers and names separated by comma and space (e.g., 'A101 Floor Plan, A201 Structural Plan').",
        title="Enter Sheet Data",
        multiline=True,
    )

    if not sheet_input:
        forms.alert(
            "No input provided. Exiting script.", title="No Input", exitscript=True
        )

    # Parse sheet data
    sheet_data = {}

    if "," in sheet_input:
        raw_entries = sheet_input.split(",")
    else:
        raw_entries = sheet_input.splitlines()

    for entry in raw_entries:
        entry = " ".join(entry.split())  # Clean double spaces
        entry = entry.strip()
        if not entry:
            continue
        if " " in entry:
            sheet_num, sheet_name = map(str.strip, entry.split(None, 1))
        else:
            continue

        if sheet_num and sheet_name:
            sheet_data[sheet_num] = sheet_name

    if not sheet_data:
        forms.alert(
            "No valid sheet data provided. Ensure correct format like 'A101 Floor Plan, A201 Structural Plan'.",
            title="Invalid Data",
            exitscript=True,
        )

    # Save current batch template
    save_template(sheet_data)

    # Validate duplicate sheet numbers
    existing_sheet_numbers = [
        s.SheetNumber for s in FilteredElementCollector(doc).OfClass(ViewSheet)
    ]

    duplicates = [sn for sn in sheet_data.keys() if sn in existing_sheet_numbers]
    if duplicates:
        forms.alert(
            "The following sheet numbers already exist:\n\n{}\n\nPlease correct them.".format(
                "\n".join(duplicates)
            ),
            title="Duplicate Sheet Numbers Found",
            exitscript=True,
        )

    # Preview Sheets
    preview_list = []
    for sn, name in sheet_data.items():
        preview_list.append("{} - {}".format(sn, name))

    confirm = forms.alert(
        "You are about to create the following sheets:\n\n{}\n\nProceed?".format(
            "\n".join(preview_list)
        ),
        options=["Proceed", "Cancel"],
        exitscript=True,
    )

    # Ask user if Placeholder or Full Sheets
    create_type = forms.alert(
        "Choose sheet creation type:",
        options=["Create Placeholder Sheets", "Create Sheets with Title Block"],
        exitscript=True,
    )

    # Sheet creation
    if create_type == "Create Placeholder Sheets":
        for sheet_num, sheet_name in sheet_data.items():
            create_placeholder(sheet_num, sheet_name)

        forms.alert(
            "{} placeholder sheets successfully created!".format(len(sheet_data)),
            title="Success",
        )

    else:
        titleblocks = get_title_blocks()
        if not titleblocks:
            forms.alert(
                "No title blocks found. Exiting script.",
                exitscript=True,
            )

        titleblock_choice = forms.alert(
            "Title block selection method:",
            options=[
                "Use Same Title Block for All Sheets",
                "Select Title Block Per Sheet",
            ],
            exitscript=True,
        )

        if titleblock_choice == "Use Same Title Block for All Sheets":
            selected_titleblock = forms.SelectFromList.show(
                sorted(titleblocks.keys()),
                title="Select Title Block",
                multiselect=False,
            )
            if not selected_titleblock:
                forms.alert("No title block selected. Exiting script.", exitscript=True)
            titleblock_id = titleblocks[selected_titleblock].Id

            for sheet_num, sheet_name in sheet_data.items():
                create_sheet(sheet_num, sheet_name, titleblock_id)

        else:
            for sheet_num, sheet_name in sheet_data.items():
                selected_titleblock = forms.SelectFromList.show(
                    sorted(titleblocks.keys()),
                    title="Select Title Block for {}".format(sheet_num),
                    multiselect=False,
                )
                if not selected_titleblock:
                    forms.alert(
                        "No title block selected. Exiting script.", exitscript=True
                    )
                titleblock_id = titleblocks[selected_titleblock].Id
                create_sheet(sheet_num, sheet_name, titleblock_id)

        forms.alert(
            "{} sheets successfully created!".format(len(sheet_data)), title="Success"
        )
