import sys
import json
import numpy as np
import open3d as o3d
import os
from sklearn.linear_model import RANSACRegressor

# from sklearn.cluster import DBSCAN, KMeans
# from scipy.spatial import ConvexHull

# Define file paths
temp_dir = "C:\\Zonneveld\\temp"
point_cloud_path = "C:\\Zonneveld\\Point_Clouds\\Aerial scan farmhouse.pts"
detected_surfaces_path = os.path.join(temp_dir, "detected_surfaces.json")
log_file_path = os.path.join(temp_dir, "ai_debug_log.txt")


# Logging function
def log_message(message):
    with open(log_file_path, "a") as log_file:
        log_file.write(message + "\n")
    print(message)


# Check if point cloud file exists
if not os.path.exists(point_cloud_path):
    log_message("Point cloud file not found. Exiting.")
    exit()

# Load point cloud
log_message(f"Processing Point Cloud: {point_cloud_path}")
with open(point_cloud_path, "r") as f:
    lines = f.readlines()

num_points = int(lines[0].strip())  # First line contains the number of points
points = []

for line in lines[1 : num_points + 1]:  # Read point data
    parts = line.strip().split()
    if len(parts) >= 3:
        x, y, z = map(float, parts[:3])
        points.append([x, y, z])

log_message(f"Found {len(points)} points in the PTS file.")

# Convert to Open3D format
pcd = o3d.geometry.PointCloud()
pcd.points = o3d.utility.Vector3dVector(np.array(points))

# Adjusted RANSAC Plane Segmentation Parameters
distance_threshold = 0.025  # Fine-tuned for better detection
min_inliers = 1400  # Lowered to include more surfaces
num_iterations = 3000  # Keeping high iterations for better accuracy
log_message("Running RANSAC plane detection...")
planes_detected = []
plane_count = 0

while len(pcd.points) > 500:
    plane_model, inliers = pcd.segment_plane(
        distance_threshold=distance_threshold, ransac_n=3, num_iterations=num_iterations
    )

    inlier_cloud = pcd.select_by_index(inliers)
    outlier_cloud = pcd.select_by_index(inliers, invert=True)

    if len(inliers) < min_inliers:
        log_message(f"Skipped small plane with {len(inliers)} points.")
        pcd = outlier_cloud
        continue

    # Get plane bounding box
    bbox = inlier_cloud.get_axis_aligned_bounding_box()
    min_bound = bbox.min_bound.tolist()
    max_bound = bbox.max_bound.tolist()

    planes_detected.append(
        {
            "plane_id": plane_count,
            "equation": list(plane_model),
            "bounding_box": {"min": min_bound, "max": max_bound},
            "num_points": len(inliers),
        }
    )

    log_message(f"Detected plane {plane_count} with {len(inliers)} points.")
    plane_count += 1

    # Update remaining point cloud
    pcd = outlier_cloud

# Save detected surfaces
with open(detected_surfaces_path, "w") as f:
    json.dump({"detected_surfaces": planes_detected}, f, indent=4)

log_message(f"Feature detection completed. Output saved to {detected_surfaces_path}")
log_message("AI Analysis Completed.")
