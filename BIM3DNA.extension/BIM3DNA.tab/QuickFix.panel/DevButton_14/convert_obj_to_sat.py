import FreeCAD
import Part
import Import
import Mesh
import os

# Define paths
input_path = r"C:\Zonneveld\Point_Clouds\refined_mesh.obj"
output_path = r"C:\Zonneveld\Point_Clouds\refined_mesh.sat"

# Load the mesh from .obj
mesh_obj = Mesh.Mesh(input_path)

# Convert mesh to shape
shape = Part.Shape()
shape.makeShapeFromMesh(mesh_obj.Topology, 0.05)
solid = Part.makeSolid(shape)

# Create document and add shape
doc = FreeCAD.newDocument()
part_obj = doc.addObject("Part::Feature", "MeshShape")
part_obj.Shape = solid

# Export to SAT
Part.export([part_obj], output_path)

print("✅ Conversion complete.")
print("📁 Saved SAT to:", output_path)
