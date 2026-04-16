# -*- coding: utf-8 -*-
__title__ = "Import\nOBJ as Mass"
__doc__ = "Imports OBJ mesh as Generic Model using DirectShape (chunked + validated)."

import os
import clr
from pyrevit import revit, DB, script
from Autodesk.Revit.DB import *
from System.Collections.Generic import List

# Setup
doc = revit.doc
output = script.get_output()
chunk_size = 500

output.print_md("📦 Reading OBJ file: C:\\Zonneveld\\Point_Clouds\\refined_mesh.obj")

# Path from wizard
path_file = r"C:\Zonneveld\Point_Clouds\refined_mesh_path.txt"
if not os.path.exists(path_file):
    output.print_md("❌ Path file not found.")
    script.exit()

with open(path_file, "r") as f:
    obj_path = f.read().strip()

if not os.path.exists(obj_path):
    output.print_md("❌ OBJ not found at: " + obj_path)
    script.exit()

# Load OBJ
vertices = []
faces = []
try:
    with open(obj_path, "r") as obj_file:
        for line in obj_file:
            parts = line.strip().split()
            if not parts:
                continue
            if parts[0] == "v":
                x, y, z = map(float, parts[1:4])
                vertices.append(XYZ(x, y, z))
            elif parts[0] == "f":
                try:
                    indices = [int(p.split("/")[0]) - 1 for p in parts[1:4]]
                    pt_list = [vertices[i] for i in indices]
                    # Skip degenerate triangles
                    if (
                        pt_list[0].IsAlmostEqualTo(pt_list[1])
                        or pt_list[0].IsAlmostEqualTo(pt_list[2])
                        or pt_list[1].IsAlmostEqualTo(pt_list[2])
                    ):
                        continue
                    faces.append(pt_list)
                except:
                    continue
except Exception as e:
    output.print_md("❌ Failed to parse OBJ: " + str(e))
    script.exit()

output.print_md("🔢 Total vertices: " + str(len(vertices)))
output.print_md("🔺 Faces parsed: " + str(len(faces)))

if len(faces) == 0:
    output.print_md("❌ No valid faces found to import.")
    script.exit()

# Begin import
t = Transaction(doc, "Import OBJ as Generic Model")
t.Start()

try:
    chunk_counter = 0
    for i in range(0, len(faces), chunk_size):
        chunk = faces[i : i + chunk_size]
        builder = TessellatedShapeBuilder()
        builder.OpenConnectedFaceSet(False)

        for face in chunk:
            try:
                tess_face = TessellatedFace(List[XYZ](face), ElementId.InvalidElementId)
                builder.AddFace(tess_face)
            except:
                continue

        builder.CloseConnectedFaceSet()
        builder.Target = TessellatedShapeBuilderTarget.AnyGeometry
        builder.Fallback = TessellatedShapeBuilderFallback.Salvage
        builder.Build()

        shape = builder.GetBuildResult().GetGeometricalObjects()
        if shape.Count == 0:
            continue

        ds = DirectShape.CreateElement(doc, ElementId(BuiltInCategory.OST_GenericModel))
        ds.ApplicationId = "AI_Mesh_Agent"
        ds.ApplicationDataId = "Chunk_" + str(chunk_counter)
        ds.SetShape(shape)
        chunk_counter += 1

    if chunk_counter == 0:
        output.print_md("❌ All geometry chunks were invalid or empty.")
        t.RollBack()
    else:
        t.Commit()
        output.print_md(
            "✅ Successfully imported "
            + str(chunk_counter)
            + " chunks as DirectShapes."
        )

except Exception as e:
    output.print_md("❌ Import failed: " + str(e))
    t.RollBack()
