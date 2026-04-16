# -*- coding: utf-8 -*-
__title__ = "AI Filtering \n & Refinement"
__doc__ = """Automatically filters and classifies point cloud data into 'Building' vs. 'Non-Building' elements."""

import subprocess
import os

# Get the directory of this script
script_dir = os.path.dirname(os.path.abspath(__file__))

# Construct the correct path to the CPython script
cpython_script = os.path.join(script_dir, "DevButton_14_cpython.py")

# Check if the CPython script exists
if not os.path.exists(cpython_script):
    raise RuntimeError("CPython script not found: " + cpython_script)

# Manually set CPython path (Update if needed)
python_executable = (
    r"C:\Zonneveld\Zonneveld_Revit_API_2\.venv\Scripts\python.exe"  # Adjust if needed
)

# Run the CPython script as an external process & capture output
process = subprocess.Popen(
    [python_executable, cpython_script],
    shell=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)

# Wait for process to finish
stdout, stderr = process.communicate()

# Print output & errors to debug
print("STDOUT:", stdout.decode())
print("STDERR:", stderr.decode())

if stderr:
    raise RuntimeError("CPython Script Error: " + stderr.decode())
