# plugins/cartographer/geometry.py
"""Geometry operations for Cartographer.

All input coordinates are WGS84 [lat, lon] pairs.
Output coordinates are in pixels, using an equirectangular projection
centred on the course bounding box scaled by pixels_per_yard.
"""
from __future__ import annotations

import math
from typing import Any

from shapely.geometry import Polygon


def _haversine_yards(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the distance in yards between two lat/lon points."""
    R = 6371000  # Earth radius in metres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    metres = R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return metres * 1.09361  # metres to yards


def compute_pixels_per_yard(
    point_a: list[float],
    point_b: list[float],
    distance_yards: float,
) -> float:
    """Compute pixels_per_yard from two reference points and a known distance.

    NOTE: Not currently called by cartographer — the tagger UI computes
    pixels_per_yard directly in JavaScript from screen pixel distance.
    Retained as a utility for future headless/scripted scale calibration.

    point_a and point_b are [lat, lon] pairs.
    distance_yards is the real-world distance between them in yards.
    """
    actual_yards = _haversine_yards(point_a[0], point_a[1], point_b[0], point_b[1])
    return distance_yards / actual_yards if actual_yards > 0 else 1.0


def compute_pixels_per_yard_from_geometry(
    holes: dict,
    canvas_h: float = 504.0,
    padding: float = 20.0,
) -> float:
    """Derive pixels_per_yard from the geographic bounding box of hole geometry.

    Computes the haversine diagonal of the course's lat/lon bounding box,
    then sets pixels_per_yard so that diagonal fills the canvas height minus
    padding. This ensures yardage arc radii are correctly proportioned relative
    to the projected hole geometry after fit_hole() scaling.

    Returns 1.0 if no coordinates are available (safe fallback).
    """
    all_lats: list[float] = []
    all_lons: list[float] = []

    for hole_data in holes.values():
        for feature_type in ("fairway", "green", "bunkers", "water", "waterways",
                             "rough_boundary", "paths"):
            for ring in hole_data.get(feature_type, []):
                for pt in ring:
                    all_lats.append(pt[0])
                    all_lons.append(pt[1])
        for pt in hole_data.get("tee_boxes", {}).values():
            all_lats.append(pt[0])
            all_lons.append(pt[1])

    if not all_lats:
        return 1.0

    min_lat, max_lat = min(all_lats), max(all_lats)
    min_lon, max_lon = min(all_lons), max(all_lons)

    diagonal_yards = _haversine_yards(min_lat, min_lon, max_lat, max_lon)
    if diagonal_yards <= 0:
        return 1.0

    available_px = canvas_h - 2 * padding
    return available_px / diagonal_yards


def _latlon_to_xy(
    lat: float,
    lon: float,
    origin_lat: float,
    origin_lon: float,
    yards_per_degree_lat: float,
    yards_per_degree_lon: float,
    pixels_per_yard: float,
) -> tuple[float, float]:
    """Project a lat/lon point to pixel coordinates relative to an origin."""
    dy = (lat - origin_lat) * yards_per_degree_lat * pixels_per_yard
    dx = (lon - origin_lon) * yards_per_degree_lon * pixels_per_yard
    return dx, -dy  # flip Y so north is up


def project_ring(
    ring: list[list[float]],
    origin_lat: float,
    origin_lon: float,
    yards_per_degree_lat: float,
    yards_per_degree_lon: float,
    pixels_per_yard: float,
) -> list[tuple[float, float]]:
    """Project a polygon ring from lat/lon to pixel coordinates."""
    return [
        _latlon_to_xy(pt[0], pt[1], origin_lat, origin_lon,
                      yards_per_degree_lat, yards_per_degree_lon, pixels_per_yard)
        for pt in ring
    ]


def project_course(holes: dict, scale_data: dict, only_hole: str | None = None) -> dict:
    """Project hole geometry from lat/lon to pixel coordinates.

    Returns a new dict with the same structure as holes but with pixel
    coordinates instead of lat/lon.

    scale_data must contain 'pixels_per_yard'.
    If only_hole is provided, only that hole key is projected.
    """
    pixels_per_yard = float(scale_data["pixels_per_yard"])

    # Collect all lat/lon points to find course centroid for projection origin
    all_lats, all_lons = [], []
    for hole_key, hole_data in holes.items():
        if only_hole is not None and hole_key != only_hole:
            continue
        for feature_type in ("fairway", "green", "bunkers", "water", "waterways", "rough_boundary", "paths", "contours"):
            for ring in hole_data.get(feature_type, []):
                for pt in ring:
                    all_lats.append(pt[0])
                    all_lons.append(pt[1])
        for pt in hole_data.get("tee_boxes", {}).values():
            all_lats.append(pt[0])
            all_lons.append(pt[1])

    # Also collect all points for centroid when projecting only one hole,
    # since the centroid needs the full course extent to produce consistent
    # pixel coordinates across all holes.
    if only_hole is not None:
        for hole_key, hole_data in holes.items():
            if hole_key == only_hole:
                continue
            for feature_type in ("fairway", "green", "bunkers", "water", "waterways", "rough_boundary", "paths", "contours"):
                for ring in hole_data.get(feature_type, []):
                    for pt in ring:
                        all_lats.append(pt[0])
                        all_lons.append(pt[1])
            for pt in hole_data.get("tee_boxes", {}).values():
                all_lats.append(pt[0])
                all_lons.append(pt[1])

    if not all_lats:
        return holes

    origin_lat = sum(all_lats) / len(all_lats)
    origin_lon = sum(all_lons) / len(all_lons)

    # Compute metres per degree at this latitude
    metres_per_degree_lat = 111132.0
    metres_per_degree_lon = 111320.0 * math.cos(math.radians(origin_lat))
    yards_per_degree_lat = metres_per_degree_lat * 1.09361
    yards_per_degree_lon = metres_per_degree_lon * 1.09361

    projected = {}
    target_holes = {only_hole: holes[only_hole]} if only_hole is not None else holes
    for hole_num, hole_data in target_holes.items():
        ph: dict[str, Any] = {}

        for feature_type in ("fairway", "green", "bunkers", "water", "waterways", "rough_boundary", "paths", "contours"):
            rings = hole_data.get(feature_type, [])
            ph[feature_type] = [
                project_ring(ring, origin_lat, origin_lon,
                             yards_per_degree_lat, yards_per_degree_lon, pixels_per_yard)
                for ring in rings
            ]

        tee_boxes = {}
        for tee_name, pt in hole_data.get("tee_boxes", {}).items():
            tee_boxes[tee_name] = _latlon_to_xy(
                pt[0], pt[1], origin_lat, origin_lon,
                yards_per_degree_lat, yards_per_degree_lon, pixels_per_yard
            )
        ph["tee_boxes"] = tee_boxes

        projected[hole_num] = ph

    return projected


def get_hole_bounds(hole_geom: dict) -> tuple[float, float, float, float]:
    """Return (min_x, min_y, max_x, max_y) bounding box for a projected hole."""
    all_x, all_y = [], []
    for feature_type in ("fairway", "green", "bunkers", "rough_boundary"):
        for ring in hole_geom.get(feature_type, []):
            for x, y in ring:
                all_x.append(x)
                all_y.append(y)
    for x, y in hole_geom.get("tee_boxes", {}).values():
        all_x.append(x)
        all_y.append(y)
    if not all_x:
        return 0.0, 0.0, 100.0, 100.0
    return min(all_x), min(all_y), max(all_x), max(all_y)


def get_green_centroid(hole_geom: dict) -> tuple[float, float]:
    """Return the centroid (cx, cy) of the green polygon(s) in pixel coordinates."""
    rings = hole_geom.get("green", [])
    if not rings:
        min_x, min_y, max_x, max_y = get_hole_bounds(hole_geom)
        return (min_x + max_x) / 2, (min_y + max_y) / 2

    polys = []
    for ring in rings:
        if len(ring) >= 3:
            try:
                polys.append(Polygon(ring))
            except Exception:
                pass

    if not polys:
        min_x, min_y, max_x, max_y = get_hole_bounds(hole_geom)
        return (min_x + max_x) / 2, (min_y + max_y) / 2

    union = polys[0]
    for p in polys[1:]:
        union = union.union(p)
    c = union.centroid
    return c.x, c.y


def get_green_rotation(hole_geom: dict) -> float:
    """Return the rotation angle (degrees) needed to orient green to top of canvas.

    Uses the full hole geometry to compute the angle from hole centre
    to green centroid, returning -90° minus that angle. Returns -90.0
    when no green rings exist (green at hole centre).
    """
    green_cx, green_cy = get_green_centroid(hole_geom)
    min_x, min_y, max_x, max_y = get_hole_bounds(hole_geom)
    hole_cx = (min_x + max_x) / 2
    hole_cy = (min_y + max_y) / 2

    dx = green_cx - hole_cx
    dy = green_cy - hole_cy
    angle_to_green = math.degrees(math.atan2(dy, dx))
    return -90.0 - angle_to_green


def rotate_ring(ring: list, angle_deg: float, origin: tuple[float, float]) -> list:
    cx, cy = origin
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    return [[
        (x - cx) * cos_a - (y - cy) * sin_a + cx,
        (x - cx) * sin_a + (y - cy) * cos_a + cy
    ] for x, y in ring]


def rotate_point(x: float, y: float, angle_deg: float, origin: tuple[float, float]) -> tuple[float, float]:
    cx, cy = origin
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    dx = x - cx
    dy = y - cy
    return (dx * cos_a - dy * sin_a + cx, dx * sin_a + dy * cos_a + cy)


def compute_fit(
    min_x: float, min_y: float, max_x: float, max_y: float,
    canvas_w: float, canvas_h: float,
    padding: float = 20.0, left_bias: float = 0.0,
) -> tuple[float, float, float]:
    geom_w = max_x - min_x or 1.0
    geom_h = max_y - min_y or 1.0
    avail_w = canvas_w - 2 * padding
    avail_h = canvas_h - 2 * padding
    scale = min(avail_w / geom_w, avail_h / geom_h)
    offset_x = padding + (avail_w - geom_w * scale) / 2 - min_x * scale
    offset_x -= left_bias
    left_edge = min_x * scale + offset_x
    if left_edge < padding:
        offset_x += padding - left_edge
    offset_y = padding + (avail_h - geom_h * scale) / 2 - min_y * scale
    return scale, offset_x, offset_y


def fit_hole(
    hole_geom: dict,
    canvas_width: float,
    canvas_height: float,
    padding: float = 20.0,
    rotation: float | None = None,
    left_bias: float = 0.0,
) -> tuple[dict, float, float, float]:
    """Rotate hole so green faces top, then scale and centre within canvas bounds.

    If rotation is provided (degrees), use it directly instead of computing
    from hole geometry — useful when fitting a subset of features (e.g. green
    only) while preserving the orientation from the full hole.

    left_bias shifts the hole leftward by that many canvas units after centering,
    clamped so the hole never exits the padding boundary on the left side.

    Returns (transformed_hole_geom, offset_x, offset_y, scale_factor).
    The returned geom has coordinates ready for SVG rendering.
    scale_factor is the ratio applied to project_course coordinates to fit
    the canvas — needed by callers that compute distances in canvas space.
    """
    min_x, min_y, max_x, max_y = get_hole_bounds(hole_geom)
    hole_cx = (min_x + max_x) / 2
    hole_cy = (min_y + max_y) / 2

    if rotation is not None:
        rotation_deg = rotation
    else:
        rotation_deg = get_green_rotation(hole_geom)

    origin = (hole_cx, hole_cy)
    rotated: dict[str, Any] = {}
    for feature_type in ("fairway", "green", "bunkers", "water", "waterways", "rough_boundary", "paths", "contours"):
        rotated[feature_type] = [rotate_ring(r, rotation_deg, origin) for r in hole_geom.get(feature_type, [])]
    rotated["tee_boxes"] = {
        name: list(rotate_point(x, y, rotation_deg, origin))
        for name, (x, y) in hole_geom.get("tee_boxes", {}).items()
    }

    mr_x, mr_y, mx_x, mx_y = get_hole_bounds(rotated)
    scale_factor, offset_x, offset_y = compute_fit(
        mr_x, mr_y, mx_x, mx_y, canvas_width, canvas_height, padding, left_bias,
    )

    def transform_point(px: float, py: float) -> tuple[float, float]:
        return px * scale_factor + offset_x, py * scale_factor + offset_y

    def transform_ring(ring: list) -> list:
        return [list(transform_point(x, y)) for x, y in ring]

    fitted: dict[str, Any] = {}
    for feature_type in ("fairway", "green", "bunkers", "water", "waterways", "rough_boundary", "paths", "contours"):
        fitted[feature_type] = [transform_ring(r) for r in rotated.get(feature_type, [])]
    fitted["tee_boxes"] = {
        name: list(transform_point(x, y))
        for name, (x, y) in rotated.get("tee_boxes", {}).items()
    }

    return fitted, offset_x, offset_y, scale_factor


def compute_yardage_arcs(
    green_centroid: tuple[float, float],
    distances_yards: list[int],
    pixels_per_yard: float,
    scale_factor: float = 1.0,
) -> list[tuple[float, float, float]]:
    """Return list of (cx, cy, radius_px) for yardage arc circles.

    green_centroid: pixel coords of green centre after fit_hole transform.
    distances_yards: e.g. [100, 125, 150, 175, 200].
    pixels_per_yard: from scale_data, already applied during project_course.
    scale_factor: the scale factor applied during fit_hole.
    """
    cx, cy = green_centroid
    return [
        (cx, cy, dist * pixels_per_yard * scale_factor)
        for dist in distances_yards
    ]


def find_overview_rotation(
    all_points: list[tuple[float, float]],
    canvas_w: float,
    canvas_h: float,
    padding: float = 10.0,
) -> float:
    """Find rotation angle that maximises the uniform scale-to-fit of a set of points.

    Brute-force search over -90 to 90 degrees at 2-degree increments around the
    centroid. Returns the angle whose rotated bounding box achieves the largest
    scale factor when fitted into (canvas_w - 2*padding) x (canvas_h - 2*padding).

    Returns 0.0 for fewer than 2 points.
    """
    if len(all_points) < 2:
        return 0.0

    cx = sum(x for x, y in all_points) / len(all_points)
    cy = sum(y for x, y in all_points) / len(all_points)

    avail_w = canvas_w - 2 * padding
    avail_h = canvas_h - 2 * padding

    best_angle = 0.0
    best_scale = 0.0

    for angle_deg in range(-90, 91, 2):
        rad = math.radians(angle_deg)
        cos_a, sin_a = math.cos(rad), math.sin(rad)

        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")
        for x, y in all_points:
            dx = x - cx
            dy = y - cy
            rx = dx * cos_a - dy * sin_a + cx
            ry = dx * sin_a + dy * cos_a + cy
            min_x = min(min_x, rx)
            max_x = max(max_x, rx)
            min_y = min(min_y, ry)
            max_y = max(max_y, ry)

        geom_w = max_x - min_x or 1.0
        geom_h = max_y - min_y or 1.0
        scale = min(avail_w / geom_w, avail_h / geom_h)

        if scale > best_scale:
            best_scale = scale
            best_angle = angle_deg

    return best_angle


def _angle_between(v1: tuple[float, float], v2: tuple[float, float]) -> float:
    """Return the turn angle in degrees between two edge vectors (0-180)."""
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    mag = math.sqrt(v1[0] ** 2 + v1[1] ** 2) * math.sqrt(v2[0] ** 2 + v2[1] ** 2)
    if mag == 0:
        return 0.0
    cos_a = max(-1.0, min(1.0, dot / mag))
    return math.degrees(math.acos(cos_a))


def _dedupe_adjacent(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Remove consecutive duplicate points from a ring."""
    if not points:
        return points
    result = [points[0]]
    for p in points[1:]:
        if p != result[-1]:
            result.append(p)
    if len(result) >= 2 and result[0] == result[-1]:
        result.pop()
    return result if len(result) >= 3 else points


def opening_ring(
    ring: list[tuple[float, float]],
    buffer_yards: float,
    pixels_per_yard: float,
) -> list[tuple[float, float]]:
    """Remove narrow protrusions from a polygon ring via morphological opening.

    Erodes the polygon by buffer_yards then dilates back by the same amount.
    Narrow appendages narrower than 2× buffer_yards are sheared off, while
    the main body is restored. Falls back to the original ring if the
    operation would destroy the polygon.

    Operates in yard-space (coords / ppy) so the buffer distance is
    meaningful regardless of projection scale.
    """
    if len(ring) < 4 or pixels_per_yard <= 0:
        return ring

    try:
        yard_ring = [(x / pixels_per_yard, y / pixels_per_yard) for x, y in ring]
        poly = Polygon(yard_ring)
        if not poly.is_valid or poly.area == 0:
            return ring

        eroded = poly.buffer(-buffer_yards, join_style=2)
        if eroded.is_empty:
            return ring

        opened = eroded.buffer(buffer_yards, join_style=2)
        if opened.is_empty or opened.area < poly.area * 0.5:
            return ring

        result = list(opened.exterior.coords)
        if len(result) < 3:
            return ring

        result = [(x * pixels_per_yard, y * pixels_per_yard) for x, y in result]
        return _dedupe_adjacent(result)
    except Exception:
        return ring


def chaikin_smooth(ring: list[tuple[float, float]], iterations: int = 3) -> list[tuple[float, float]]:
    """Smooth a closed polygon ring using Chaikin's corner-cutting algorithm.
    
    Each iteration subdivides edges and cuts corners, converging toward
    a cubic B-spline. 2-3 iterations usually suffice.
    """
    if len(ring) < 3:
        return ring
    
    points = ring
    for _ in range(iterations):
        n = len(points)
        smoothed = []
        for i in range(n):
            p0_x, p0_y = points[i]
            p1_x, p1_y = points[(i + 1) % n]
            q = (0.75 * p0_x + 0.25 * p1_x, 0.75 * p0_y + 0.25 * p1_y)
            r = (0.25 * p0_x + 0.75 * p1_x, 0.25 * p0_y + 0.75 * p1_y)
            smoothed.append(q)
            smoothed.append(r)
        points = smoothed
    
    return points


def chaikin_smooth_open(line: list[tuple[float, float]], iterations: int = 3) -> list[tuple[float, float]]:
    """Smooth an open polyline using Chaikin's corner-cutting algorithm.

    Unlike chaikin_smooth(), this variant preserves the start and end
    points exactly — no wrap-around edge between last and first point.
    Use this for waterway linestrings and other open paths.
    """
    if len(line) < 2:
        return line

    points = line
    for _ in range(iterations):
        n = len(points)
        smoothed = [points[0]]
        for i in range(n - 1):
            p0_x, p0_y = points[i]
            p1_x, p1_y = points[i + 1]
            smoothed.append((0.75 * p0_x + 0.25 * p1_x, 0.75 * p0_y + 0.25 * p1_y))
            smoothed.append((0.25 * p0_x + 0.75 * p1_x, 0.25 * p0_y + 0.75 * p1_y))
        smoothed.append(points[-1])
        points = smoothed

    return points


def smooth_hole_geometry(
    hole_geom: dict,
    iterations: int = 3,
    pixels_per_yard: float = 0.0,
) -> dict:
    """Apply morphological opening + Chaikin smoothing to a hole's geometry dict.

    Fairway polygons are first morphologically opened (erode then dilate)
    with a 3-yard structuring element to remove narrow walkway protrusions.
    Then all closed polygon rings use chaikin_smooth().
    Open linear features (paths, waterways) use chaikin_smooth_open().
    Tee box points are passed through unchanged.

    Pass pixels_per_yard to enable opening. When 0 (default),
    opening is skipped (backward-compatible).
    """
    smoothed = {}
    for feature_type in ("fairway", "green", "bunkers", "water", "rough_boundary"):
        rings = hole_geom.get(feature_type, [])
        if feature_type == "fairway" and pixels_per_yard > 0:
            rings = [opening_ring(ring, 3.0, pixels_per_yard) for ring in rings]
        smoothed[feature_type] = [chaikin_smooth(ring, iterations) for ring in rings]
    # Open linear features — use open smoother that preserves endpoints
    smoothed["paths"] = [chaikin_smooth_open(line, iterations) for line in hole_geom.get("paths", [])]
    smoothed["waterways"] = [chaikin_smooth_open(line, iterations) for line in hole_geom.get("waterways", [])]
    smoothed["tee_boxes"] = hole_geom.get("tee_boxes", {})
    smoothed["contours"] = list(hole_geom.get("contours", []))
    return smoothed
