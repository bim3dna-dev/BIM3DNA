# -*- coding: utf-8 -*-
import os
import numpy as np

obj_path = r"C:\Zonneveld\Point_Clouds\refined_mesh.obj"
output_path = r"C:\Zonneveld\Point_Clouds\refined_mesh_normalized.obj"

vertices = []
faces = []

with open(obj_path, "r") as f:
    for line in f:
        if line.startswith("v "):
            parts = list(map(float, line.strip().split()[1:4]))
            vertices.append(parts)
        elif line.startswith("f "):
            face = [int(p.split("/")[0]) for p in line.strip().split()[1:]]
            faces.append(face)

v_array = np.array(vertices)
centroid = v_array.mean(axis=0)
normalized = v_array - centroid

with open(output_path, "w") as f:
    for v in normalized:
        f.write("v {:.6f} {:.6f} {:.6f}\n".format(*v))
    for face in faces:
        f.write("f {} {} {}\n".format(*face[:3]))

print("✅ Normalized OBJ saved to:", output_path)
