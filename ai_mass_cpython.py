# -*- coding: utf-8 -*-
__title__ = "AI MASS"
__doc__ = """Automatically filters and classifies point cloud data into 'Building' vs. 'Non-Building' elements."""

import sys
import os
import json
import tensorflow as tf
import numpy as np

# AI Output Path
AI_RESULTS_PATH = r"C:\Zonneveld\AI_Results\ai_massing_output.json"

# ✅ Force UTF-8 encoding in Windows to fix UnicodeEncodeError
sys.stdout.reconfigure(encoding="utf-8")

print("✅ AI Script Started.")

# ✅ Ensure correct number of arguments
if len(sys.argv) < 2:
    sys.stdout.write("❌ No input JSON provided. Exiting.\n")
    sys.exit(1)

try:
    # ✅ Read JSON input
    json_data = json.loads(sys.argv[1])
    sys.stdout.write("✅ AI Script Started.\n")
    sys.stdout.write("🔍 Received JSON Data: " + str(json_data) + "\n")

    # Extract dimensions
    min_x = json_data["min_x"]
    max_x = json_data["max_x"]
    min_y = json_data["min_y"]
    max_y = json_data["max_y"]
    min_z = json_data["min_z"]
    max_z = json_data["max_z"]

    # ✅ Generate simplified footprint (placeholder for AI segmentation)
    ai_output = {
        "footprint": [[min_x, min_y], [max_x, min_y], [max_x, max_y], [min_x, max_y]],
        "min_x": min_x,
        "max_x": max_x,
        "min_y": min_y,
        "max_y": max_y,
        "min_z": min_z,
        "max_z": max_z,
        "center_x": (min_x + max_x) / 2,
        "center_y": (min_y + max_y) / 2,
    }

    # ✅ Save AI Output
    AI_RESULTS_PATH = r"C:\Zonneveld\AI_Results\ai_massing_output.json"

    with open(AI_RESULTS_PATH, "w") as f:
        json.dump(ai_output, f)

    sys.stdout.write("✅ AI Massing saved successfully at: " + AI_RESULTS_PATH + "\n")

except Exception as e:
    sys.stdout.write("❌ Error processing AI script: " + str(e) + "\n")
    sys.exit(1)
