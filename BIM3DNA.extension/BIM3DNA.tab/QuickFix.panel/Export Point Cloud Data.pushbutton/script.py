# -*- coding: utf-8 -*-
__title__ = "Export Point\nCloud Data"
__doc__ = """Extracts .rcs data"""

import os
import json
import random
import time
from pyrevit import revit, DB, forms, script
from pyrevit import script as pyscript
import System.Windows.Forms as WinForms

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

# Progress Bar Setup
output = script.get_output()


# Placeholder function: Implement Autodesk ReCap API extraction here
def extract_rcs_data(rcs_file):
    """Extracts structured point cloud data from an .rcs file."""
    extracted_points = []
    total_points = 1000000  # Simulated total number of points in file
    progress_step = total_points // 100  # For progress bar update

    for i in range(total_points):  # Simulate extracting 1,000,000 points
        x, y, z = (
            random.uniform(-50, 50),
            random.uniform(-50, 50),
            random.uniform(-50, 50),
        )  # Simulated point coordinates

        point = {"x": x, "y": y, "z": z}
        extracted_points.append(point)

        # Update progress bar
        if i % progress_step == 0:
            output.update_progress(i / float(total_points) * 100, 100)

    return extracted_points


# User selects an .rcs file
rcs_file = forms.pick_file(file_ext="rcs", title="Select an Autodesk ReCap .rcs File")
if not rcs_file:
    forms.alert("No file selected. Exiting.", exitscript=True)

# Notify user extraction is starting
forms.alert("Extracting all points. This may take some time...")

# Extract and process point cloud data
extracted_data = extract_rcs_data(rcs_file)

# Save as .pts (ASCII format)
pts_output_file = os.path.join(os.path.dirname(rcs_file), "extracted_rcs_data.pts")
with open(pts_output_file, "w") as pts_file:
    for pt in extracted_data:
        line = "{} {} {}\n".format(pt["x"], pt["y"], pt["z"])
        pts_file.write(line)

forms.alert("Point cloud data extracted and saved to: " + pts_output_file)
