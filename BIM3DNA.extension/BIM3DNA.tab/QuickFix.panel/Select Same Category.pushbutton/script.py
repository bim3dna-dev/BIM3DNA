__title__ = "Select Same \n Category "
__doc__ = """Selects all elements in the current project that belong to the same category as the selected element."""

from Autodesk.Revit.DB import *
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    FamilyInstanceFilter,
    ElementId,
    Transaction,
    BuiltInCategory,
)
from pyrevit import revit, DB, forms
from pyrevit import script as pyscript
from System.Collections.Generic import List
from collections import defaultdict

# Access Revit document

uidoc = revit.uidoc
doc = revit.doc

# uidoc = __revit__.ActiveUIDocument
# doc = uidoc.Document
app = __revit__.Application
doc = __revit__.ActiveUIDocument.Document


import System.Windows.Forms as WinForms
import clr

clr.AddReference("System")
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("RevitServices")
clr.AddReference("RevitNodes")
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
clr.AddReference("System.Collections")

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

# Debug flag: set to False to disable logs
DEBUG = False


def debug_log(message):
    """Helper function to print debug messages."""
    if DEBUG:
        print(message)


# Get active selection
selection = revit.get_selection()
if not selection:
    forms.alert("At least one object must be selected.", exitscript=True)

# Dictionary to store category elements
category_elements = defaultdict(list)

# Process each selected element
for selected_element in selection:
    debug_log("Processing element ID: {0}".format(selected_element.Id))

    # Get the category of the element
    if selected_element.Category:
        category_name = selected_element.Category.Name
    else:
        forms.alert(
            "Selected element does not belong to a valid category.", exitscript=True
        )

    debug_log("Selected category: {0}".format(category_name))

    # Collect all elements of the same category
    same_category_elements = (
        FilteredElementCollector(doc)
        .OfCategoryId(selected_element.Category.Id)
        .WhereElementIsNotElementType()
        .ToElements()
    )

    for element in same_category_elements:
        category_elements[category_name].append(element.Id)

# Highlight all selected elements
all_selected_elements = [
    el_id for elements in category_elements.values() for el_id in elements
]
if not all_selected_elements:
    forms.alert("No matching elements found.", exitscript=True)

revit.get_selection().set_to(all_selected_elements)

# Prepare the summary message
summary_message = "Selection Summary:\n"
total_selected_count = 0
for category_name, elements in category_elements.items():
    element_count = len(elements)
    total_selected_count += element_count
    summary_message += "Category: {0}, Total Selected: {1}\n".format(
        category_name, element_count
    )

summary_message += "\nTotal Elements Selected: {0}".format(total_selected_count)

# Show the summary popup
forms.alert(summary_message, title="Selection Summary")

# Log results if debugging is enabled
if DEBUG:
    for category_name, elements in category_elements.items():
        debug_log("Category: {0}".format(category_name))
        for element_id in elements:
            debug_log("Element ID: {0}".format(element_id))
