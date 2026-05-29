"""Diagnose: inspect raw contour polylines from Maplewood DEM."""
import sys
import json
from pathlib import Path
from collections import defaultdict
import numpy as np
from PIL import Image

sys.path.insert(0, ".")

from elevation import compute_elevation_shading, compute_contours

PARENT_DATA = Path("/mnt/Claude/repositories/pinsheet/data/plugins/cartographer")
GEO_PATH = PARENT_DATA / "courses_geo.json"
DEM_PATH = PARENT_DATA / "dem" / "9bae2a00a3f5a84d.tif"

with open(GEO_PATH) as f:
    courses = json.load(f)

course_data = courses.get("Maplewood", {})
holes_geo = course_data.get("holes", {})
holes_geo = {str(k): v for k, v in holes_geo.items()}

EPSILON = 0.5  # close-enough threshold in contour-space pixels

hole_key = "1"
green_ring = holes_geo[hole_key].get("green", [[]])[0]
shading_img = compute_elevation_shading(green_ring, DEM_PATH)
if shading_img is None:
    print("No shading image")
    sys.exit(0)

print(f"Shading image: {shading_img.size} (mode={shading_img.mode})")

contour_render_scale = 2
SVG_BW, SVG_BH = 200, 150
img_contour = shading_img.resize(
    (max(1, int(SVG_BW * contour_render_scale)),
     max(1, int(SVG_BH * contour_render_scale))),
    Image.LANCZOS,
)
z_arr = np.array(img_contour, dtype=float)
contour_levels = [i * 255.0 / 13 for i in range(1, 13)]
raw_contours = compute_contours(z_arr, contour_levels)

total = 0
for level in sorted(raw_contours):
    pls = raw_contours[level]
    total += len(pls)
    print(f"  Level {level:.1f}: {len(pls)} polylines, "
          f"lens={[len(p) for p in pls][:6]}")

print(f"\nTotal polylines: {total}")

# Check endpoint proximity across polylines at same level
total_near_misses = 0
for level in sorted(raw_contours):
    pls = raw_contours[level]
    # Collect endpoints
    endpoints = []
    for pi, pl in enumerate(pls):
        if len(pl) >= 1:
            endpoints.append((pi, 0, tuple(pl[0])))
            endpoints.append((pi, -1, tuple(pl[-1])))

    near = 0
    for i in range(len(endpoints)):
        for j in range(i + 1, len(endpoints)):
            pi, pos_i, pt_i = endpoints[i]
            pj, pos_j, pt_j = endpoints[j]
            if pi == pj:
                continue
            d = ((pt_i[0] - pt_j[0])**2 + (pt_i[1] - pt_j[1])**2)**0.5
            if d < EPSILON:
                near += 1

    if near > 0:
        print(f"  Level {level:.1f}: {near} near-endpoint pairs between DIFFERENT polylines")

    total_near_misses += near

print(f"\nTotal near-endpoint pairs between different polylines: {total_near_misses}")

if total_near_misses > 0:
    print("\n*** Contour polylines that SHOULD be connected are SEPARATE ***")
    print("Fix: _connect_segments is not properly welding all coincident endpoints")
else:
    print("\nNo disconnected endpoint pairs found in raw marching squares output")
