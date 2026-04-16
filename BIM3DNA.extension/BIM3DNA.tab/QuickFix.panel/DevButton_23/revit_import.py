# revit_import.py
import os
import clr
import traceback

clr.AddReference("RevitAPI")
clr.AddReference("RevitServices")
from Autodesk.Revit.DB import *
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager
from System.Collections.Generic import List

doc = DocumentManager.Instance.CurrentDBDocument

# Load path
path_file = r"C:\Zonneveld\Point_Clouds\refined_mesh_path.txt"
if not os.path.exists(path_file):
    print("❌ Path file missing.")
    exit()

with open(path_file, "r") as f:
    obj_path = f.read().strip()

if not os.path.exists(obj_path):
    print("❌ OBJ file not found.")
    exit()

print("📦 Reading OBJ file:", obj_path)

# Read and normalize geometry
vertices = []
faces = []
try:
    with open(obj_path, "r") as f:
        for line in f:
            if line.startswith("v "):
                parts = line.strip().split()
                x, y, z = map(float, parts[1:4])
                vertices.append(XYZ(x, y, z))
            elif line.startswith("f "):
                parts = line.strip().split()
                idx = [int(p.split("/")[0]) - 1 for p in parts[1:4]]
                faces.append(idx)
except Exception as e:
    print("❌ Failed parsing OBJ:", str(e))
    exit()

print("🔢 Total vertices:", len(vertices))
print("🔺 Faces parsed:", len(faces))

# Normalize vertices near origin (optional)
offset = vertices[0]
vertices = [XYZ(v.X - offset.X, v.Y - offset.Y, v.Z - offset.Z) for v in vertices]

TransactionManager.Instance.EnsureInTransaction(doc)

try:
    chunk_size = 500  # safe for CPython + Revit memory
    face_chunks = [faces[i : i + chunk_size] for i in range(0, len(faces), chunk_size)]

    for chunk in face_chunks:
        builder = TessellatedShapeBuilder()
        builder.OpenConnectedFaceSet(False)

        for face in chunk:
            try:
                pts = List[XYZ](
                    [vertices[face[0]], vertices[face[1]], vertices[face[2]]]
                )
                builder.AddFace(TessellatedFace(pts, ElementId.InvalidElementId))
            except:
                continue

        builder.CloseConnectedFaceSet()
        builder.Target = TessellatedShapeBuilderTarget.AnyGeometry
        builder.Fallback = TessellatedShapeBuilderFallback.Salvage
        builder.Build()

        ds = DirectShape.CreateElement(doc, ElementId(BuiltInCategory.OST_GenericModel))
        ds.SetShape(builder.GetBuildResult().GetGeometricalObjects())

    print("✅ Mesh imported into Revit as Generic Model.")
except Exception as e:
    print("❌ Import failed:", str(e))
    traceback.print_exc()
    TransactionManager.Instance.ForceCloseTransaction()
    exit()
else:
    TransactionManager.Instance.TransactionTaskDone()
