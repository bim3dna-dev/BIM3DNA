# -*- coding: utf-8 -*-
__title__ = "RenderHQ"
__doc__ = """Version = 1.0
Date    = 19.06.2025
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

# ==================================================
# Revit Document Setup
# ==================================================
app = __revit__.Application
uidoc = __revit__.ActiveUIDocument

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

# 1. Get the active 3D view
doc = __revit__.ActiveUIDocument.Document
view = doc.ActiveView
if not isinstance(view, View3D) or view.IsTemplate:
    TaskDialog.Show("Render & Enhance", "Please activate a 3D view first.")
    script.exit()

# 3. Prepare export settings
folder = os.path.expanduser("~\\Desktop\\AI_Render_Exports")
if not os.path.exists(folder):
    os.makedirs(folder)

timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
base_name = view.Name.replace(":", "_").replace(" ", "_") + "_" + timestamp

img_export_options = ImageExportOptions()
img_export_options.ExportRange = ExportRange.CurrentView
img_export_options.FilePath = os.path.join(folder, base_name)
img_export_options.ZoomType = ZoomFitType.Zoom
img_export_options.PixelSize = 10000  # Use 0 for max resolution
img_export_options.HLRandWFViewsFileType = ImageFileType.PNG
# img_export_options.ShadowViewsFileType = ImageFileType.PNG
# img_export_options.RasterViewsFileType = ImageFileType.PNG

# 4. Export image
doc.ExportImage(img_export_options)

# Find the first image create (PNG or JPG)
output_file = None
for ext in ["001.png", "001.jpg"]:
    candidate = os.path.join(folder, base_name + ext)
    if os.path.exists(candidate):
        output_file = candidate
        break

if output_file is None:
    TaskDialog.Show(
        "Render & Enhance", "No image exported. Check your view mode and crop region."
    )
    script.exit()

# Optionally, auto-convert JPG to PNG for AI workflows
if output_file.lower().endswith(".jpg") and PIL_OK:
    img = PILImage.open(output_file)
    png_file = output_file.replace(".jpg", ".png")
    img.save(png_file)
    output_file = png_file

TaskDialog.Show(
    "Render & Enhance", "Image exported for AI enhancement:\n" + output_file
)
subprocess.Popen(["explorer", output_file])
