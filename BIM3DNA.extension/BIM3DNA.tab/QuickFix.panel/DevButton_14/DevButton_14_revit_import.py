# -*- coding: utf-8 -*-
import sys
import os
from pyrevit import revit, DB

# Get the SAT file path from command-line argument
if len(sys.argv) < 2:
    print("⚠️ Error: No SAT file provided for import.")
    sys.exit(1)

sat_file = sys.argv[1]

if not os.path.exists(sat_file):
    print(f"⚠️ Error: SAT file not found at {sat_file}")
    sys.exit(1)

print(f"📢 Importing SAT file into Revit as Generic Model: {sat_file}")

doc = revit.doc
t = DB.Transaction(doc, "Import Mesh as Generic Model")
t.Start()

try:
    import_options = DB.SATImportOptions()
    generic_model_cat = DB.Category.GetCategory(
        doc, DB.BuiltInCategory.OST_GenericModel
    )

    success = doc.Import(sat_file, import_options, doc.ActiveView, None)

    if success:
        print("✅ Successfully imported as Generic Model.")
    else:
        print("⚠️ Failed to import mesh into Revit.")

    t.Commit()
except Exception as e:
    print(f"⚠️ Import failed with error: {str(e)}")
    t.RollBack()
