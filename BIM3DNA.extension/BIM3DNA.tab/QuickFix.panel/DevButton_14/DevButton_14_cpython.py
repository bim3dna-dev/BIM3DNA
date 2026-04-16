# -*- coding: utf-8 -*-
__title__ = "Open3D Cpython"
__doc__ = """Automatically filters and classifies point cloud data into 'Building' vs. 'Non-Building' elements."""

import os
import sys
import open3d as o3d
import numpy as np
import time
import copy
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QLabel,
    QFileDialog,
    QProgressBar,
    QComboBox,
)
from PySide6.QtCore import Qt, QThread, Signal
import subprocess

# Define output directory for saving point cloud and mesh
output_dir = r"C:\Zonneveld\Point_Clouds"


# Output directory
output_dir = r"C:\Zonneveld\Point_Clouds"


class PointCloudUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Point Cloud to Revit Mass Converter")
        self.setGeometry(200, 200, 400, 300)

        layout = QVBoxLayout()

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setAlignment(Qt.AlignCenter)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.select_button = QPushButton("Select PTS File", self)
        self.select_button.clicked.connect(self.load_point_cloud)
        layout.addWidget(self.select_button)

        self.mesh_button = QPushButton("Create Mesh", self)
        self.mesh_button.clicked.connect(self.create_mesh)
        self.mesh_button.setEnabled(False)
        layout.addWidget(self.mesh_button)

        self.convert_button = QPushButton("Convert OBJ to SAT (FreeCAD)", self)
        self.convert_button.clicked.connect(self.convert_obj_to_sat)
        self.convert_button.setEnabled(False)
        layout.addWidget(self.convert_button)

        self.revit_button = QPushButton("Import Mesh as Revit Mass", self)
        self.revit_button.clicked.connect(self.import_mesh_to_revit)
        self.revit_button.setEnabled(False)
        layout.addWidget(self.revit_button)

        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

        self.pcd = None
        self.mesh = None
        self.obj_file_path = os.path.join(output_dir, "refined_mesh.obj")

    def load_point_cloud(self):
        pts_file, _ = QFileDialog.getOpenFileName(
            self, "Select PTS File", "", "PTS Files (*.pts);;All Files (*)"
        )
        if not pts_file:
            print("No file selected.")
            return

        print("Loading point cloud:", pts_file)
        points = []

        try:
            with open(pts_file, "r") as file:
                for line in file:
                    parts = line.strip().split()
                    if len(parts) >= 6:
                        try:
                            x, y, z = map(float, parts[:3])
                            r, g, b = map(int, parts[3:6])
                            points.append([x, y, z, r / 255.0, g / 255.0, b / 255.0])
                        except ValueError:
                            continue

            points = np.array(points)

            if points.shape[0] == 0:
                print("No valid points found!")
                return

            print("Loaded", len(points), "points.")
            self.pcd = o3d.geometry.PointCloud()
            self.pcd.points = o3d.utility.Vector3dVector(points[:, :3])
            self.pcd.colors = o3d.utility.Vector3dVector(points[:, 3:])
            self.progress_bar.setValue(100)
            self.mesh_button.setEnabled(True)

            print("Showing point cloud preview...")
            self.show_point_cloud()

        except Exception as e:
            print("Error reading file:", str(e))

    def show_point_cloud(self):
        vis = o3d.visualization.Visualizer()
        vis.create_window("3D View - Point Cloud")
        vis.add_geometry(self.pcd)
        vis.run()
        vis.destroy_window()

    def create_mesh(self):
        if self.pcd is None:
            print("No point cloud loaded.")
            return

        print("Generating mesh with cleanup...")
        self.progress_bar.setValue(10)

        self.pcd.estimate_normals(
            search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=1.0, max_nn=30)
        )
        self.pcd.orient_normals_consistent_tangent_plane(k=10)
        self.progress_bar.setValue(30)

        mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
            self.pcd, depth=9
        )
        self.progress_bar.setValue(50)

        mesh.remove_degenerate_triangles()
        mesh.remove_duplicated_triangles()
        mesh.remove_non_manifold_edges()
        mesh.remove_unreferenced_vertices()
        mesh.compute_vertex_normals()

        self.mesh = mesh
        self.obj_file_path = os.path.join(output_dir, "refined_mesh.obj")
        success = o3d.io.write_triangle_mesh(
            self.obj_file_path, self.mesh, write_ascii=True
        )

        if success:
            print("Mesh saved as OBJ:", self.obj_file_path)
            self.revit_button.setEnabled(True)
            self.convert_button.setEnabled(True)
            self.progress_bar.setValue(100)
            self.show_mesh(self.mesh)
        else:
            print("Failed to save OBJ.")

    def show_mesh(self, mesh):
        vis = o3d.visualization.Visualizer()
        vis.create_window("3D View - Mesh Preview")
        vis.add_geometry(mesh)
        vis.run()
        vis.destroy_window()

    def convert_obj_to_sat(self):
        """Runs FreeCAD to convert OBJ to SAT using external script."""
        if not os.path.exists(self.obj_file_path):
            print("OBJ file not found for SAT conversion.")
            return

        freecad_script_path = os.path.join(output_dir, "convert_obj_to_sat.py")
        sat_file_path = self.obj_file_path.replace(".obj", ".sat")
        input_path = self.obj_file_path.replace("\\", "\\\\")
        output_path = sat_file_path.replace("\\", "\\\\")

        with open(freecad_script_path, "w") as f:
            f.write(
                """
import FreeCAD
import Part
import Import
import Mesh
import os

try:
    input_path = r"%s"
    output_path = r"%s"

    mesh_obj = Mesh.Mesh(input_path)
    shape = Part.Shape()
    shape.makeShapeFromMesh(mesh_obj.Topology, 0.05)
    solid = Part.makeSolid(shape)

    doc = FreeCAD.newDocument()
    part_obj = doc.addObject("Part::Feature", "MeshShape")
    part_obj.Shape = solid
    Part.export([part_obj], output_path)

    print("SAT export complete.")
except Exception as e:
    print("Failed to convert mesh to solid:", str(e))
"""
                % (input_path, output_path)
            )

        print("Launching FreeCAD to convert OBJ to SAT...")
        try:
            subprocess.run(
                [
                    freecad_path,
                    freecad_script_path,
                ],
                check=True,
            )
            print("SAT file generated:", sat_file_path)
        except Exception as e:
            print("FreeCAD conversion failed:", str(e))

    def import_mesh_to_revit(self):
        if not self.obj_file_path:
            print("No mesh to import.")
            return

        if not os.path.exists(self.obj_file_path):
            print("OBJ file missing at", self.obj_file_path)
            return

        print("Manual Step: Import OBJ into Revit or convert to SAT for native mass.")
        print("OBJ Path:", self.obj_file_path)


# Run GUI
if __name__ == "__main__":
    print("Starting Point Cloud to Revit Mass Converter...")
    app = QApplication(sys.argv)
    window = PointCloudUI()
    window.show()
    sys.exit(app.exec())
