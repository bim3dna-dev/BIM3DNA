import clr
import os
import sys

clr.AddReference("RevitAPI")
clr.AddReference("RevitServices")

from Autodesk.Revit.DB import *
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager
from System.Collections.Generic import List

doc = DocumentManager.Instance.CurrentDBDocument

obj_path_file = r"C:\Zonneveld\Point_Clouds\refined_mesh_path.txt"
if not os.path.exists(obj_path_file):
    print("❌ Path file missing.")
    sys.exit(1)

with open(obj_path_file, "r") as f:
    obj_path = f.read().strip()

if not os.path.exists(obj_path):
    print("❌ OBJ not found:", obj_path)
    sys.exit(1)

print("📦 Reading OBJ file:", obj_path)

vertices = []
faces = []

with open(obj_path, "r") as f:
    for line in f:
        parts = line.strip().split()
        if not parts:
            continue
        if parts[0] == "v":
            x, y, z = map(float, parts[1:4])
            vertices.append(XYZ(x, y, z))
        elif parts[0] == "f":
            if len(parts) < 4:
                idx = [int(p.split("/")[0]) - 1 for p in parts[1:4]]
                faces.append([vertices[i] for i in idx])

print("🔢 Total vertices:", len(vertices))
print("🔺 Faces parsed:", len(faces))

TransactionManager.Instance.EnsureInTransaction(doc)

try:
    builder = TessellatedShapeBuilder()
    builder.OpenConnectedFaceSet(False)

    chunk_size = 500
    added_faces = 0

    for i in range(0, len(faces), chunk_size):
        chunk = faces[i : i + chunk_size]
        for tri_pts in chunk:
            try:
                tri = TessellatedFace(List[XYZ](tri_pts), ElementId.InvalidElementId)
                builder.AddFace(tri)
                added_faces += 1
            except:
                continue

    builder.CloseConnectedFaceSet()
    builder.Target = TessellatedShapeBuilderTarget.AnyGeometry
    builder.Fallback = TessellatedShapeBuilderFallback.Salvage
    builder.Build()

    result = builder.GetBuildResult()
    geom_objs = result.GetGeometricalObjects()

    ds_type = DirectShapeType.Create(
        doc, "AI_GeneratedMass", ElementId(BuiltInCategory.OST_GenericModel)
    )
    ds_type.SetShape(geom_objs)

    ds = DirectShape.CreateElement(doc, ElementId(BuiltInCategory.OST_GenericModel))
    ds.ApplicationId = "AI_Mesh_Agent"
    ds.ApplicationDataId = "AI_Generated"
    ds.SetTypeId(ds_type.Id)

    print("✅ Mesh successfully imported as Generic Model.")
except Exception as e:
    print("❌ Import failed:", e)
    TransactionManager.Instance.ForceCloseTransaction()
    sys.exit(1)

TransactionManager.Instance.TransactionTaskDone()
