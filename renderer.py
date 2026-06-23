# plugins/cartographer/renderer.py
"""SVG diagram generation for Cartographer.

Takes fitted hole geometry (pixel coordinates) and produces SVG strings
for hole layout diagrams and green detail views.
"""
from __future__ import annotations

import math

import svgwrite

from .data import load_courses_geo
from .geometry import (
    project_course, fit_hole, smooth_hole_geometry, chaikin_smooth,
    chaikin_smooth_open,
    compute_pixels_per_yard_from_geometry, get_green_centroid,
    find_overview_rotation, opening_ring,
    rotate_ring, rotate_point, compute_fit,
)

# Feature render colours — (stroke, fill)
_COLOURS = {
    "rough_boundary": ("#000000", "#a8d1de"),
    "fairway":        ("#000000", "#ccebb0"),
    "water":          ("#000000", "#a8d1de"),
    "paths":          ("#000000", "#d4c9a8"),
    "waterways":      ("#1565C0", "#a8d1de"),
    "bunkers":        ("#000000", "#f5e8c5"),
    "green":          ("#000000", "#87debd"),
}

_STROKE_WIDTH = 0.644586

# Hole layout canvas size (SVG user units = points at 72dpi, 4.25" wide)
HOLE_CANVAS_W = 270.0   # PRINTABLE_W
HOLE_CANVAS_H = 486.0   # PAGE_CONTENT_H
HOLE_LEFT_BIAS = 45.0  # pts — shift hole leftward; clamped to padding floor in fit_hole()


def _draw_polygons(
    dwg: svgwrite.Drawing,
    group: svgwrite.container.Group,
    rings: list[list],
    stroke: str,
    fill: str,
    stroke_width: float | None = None,
    stroke_opacity: float | None = None,
    stroke_dasharray: str | None = None,
) -> None:
    """Draw a list of polygon rings onto a svgwrite group."""
    if stroke_width is None:
        stroke_width = _STROKE_WIDTH
    style: dict[str, str] = {}
    if stroke_opacity is not None:
        style["stroke-opacity"] = str(stroke_opacity)
    if stroke_dasharray is not None:
        style["stroke-dasharray"] = stroke_dasharray
    for ring in rings:
        if len(ring) < 3:
            continue
        points = [(float(x), float(y)) for x, y in ring]
        group.add(dwg.polygon(
            points=points,
            stroke=stroke,
            fill=fill,
            stroke_width=stroke_width,
            fill_rule="evenodd",
            style=";".join(f"{k}:{v}" for k, v in style.items()) if style else None,
        ))


def _draw_lines(
    dwg: svgwrite.Drawing,
    group: svgwrite.container.Group,
    lines: list[list],
    stroke: str,
    stroke_width: float | None = None,
) -> None:
    """Draw a list of LineStrings onto a svgwrite group."""
    if stroke_width is None:
        stroke_width = _STROKE_WIDTH
    for line in lines:
        if len(line) < 2:
            continue
        points = [(float(x), float(y)) for x, y in line]
        group.add(dwg.polyline(
            points=points,
            stroke=stroke,
            fill="none",
            stroke_width=stroke_width,
        ))


def render_hole(
    hole_geom: dict,
    settings: dict | None = None,
    canvas_w: float = HOLE_CANVAS_W,
    canvas_h: float = HOLE_CANVAS_H,
) -> str:
    """Render a fitted hole geometry dict to an SVG string.

    hole_geom: output of geometry.fit_hole() — pixel coordinates.
    settings: plugin settings dict; uses 'cartographer.yardage_arcs' and
              'cartographer.yardage_arc_distances' keys.
    """
    if settings is None:
        settings = {}

    dwg = svgwrite.Drawing(size=(f"{canvas_w}pt", f"{canvas_h}pt"),
                           viewBox=f"0 0 {canvas_w} {canvas_h}")

    # Render order: rough → fairway → water → paths → bunkers → green → tees → arcs
    for feature_type in ("rough_boundary", "fairway", "water", "bunkers", "green"):
        stroke_col, fill_col = _COLOURS[feature_type]
        if feature_type == "rough_boundary":
            fill_col = _COLOURS["fairway"][1]
        rings = hole_geom.get(feature_type, [])
        g = dwg.g()
        _draw_polygons(dwg, g, rings, stroke=stroke_col, fill=fill_col)
        dwg.add(g)

    # Paths are LineStrings, not polygons — draw separately
    stroke_col, _ = _COLOURS["paths"]
    paths = hole_geom.get("paths", [])
    if paths:
        g = dwg.g()
        _draw_lines(dwg, g, paths, stroke=stroke_col)
        dwg.add(g)

    # Waterways are open LineStrings — draw with water colour, no fill
    stroke_col, _ = _COLOURS["waterways"]
    waterways = hole_geom.get("waterways", [])
    if waterways:
        g = dwg.g()
        _draw_lines(dwg, g, waterways, stroke=stroke_col, stroke_width=1.5)
        dwg.add(g)

    # Tee box markers — same fill/stroke as rough/water
    for tee_name, (tx, ty) in hole_geom.get("tee_boxes", {}).items():
        dwg.add(dwg.circle(
            center=(float(tx), float(ty)),
            r=4,
            fill=_COLOURS["fairway"][1],
            stroke=_COLOURS["rough_boundary"][0],
            stroke_width=_STROKE_WIDTH,
        ))

    # Yardage arcs
    if settings.get("cartographer.yardage_arcs", True):
        arcs = hole_geom.get("_arcs", [])
        for cx, cy, r in arcs:
            dwg.add(dwg.circle(
                center=(float(cx), float(cy)),
                r=float(r),
                fill="none",
                stroke="#000000",
                stroke_width=0.5,
                stroke_dasharray="3,3",
            ))
        if arcs:
            cx, cy = arcs[0][0], arcs[0][1]
            dwg.add(dwg.circle(
                center=(float(cx), float(cy)),
                r=1.5,
                fill="#000000",
                stroke="none",
            ))

    # Fairway contours and arrows — clipped to fairway polygon
    contour_paths = hole_geom.get("contours", [])
    fairway_arrows = hole_geom.get("fairway_arrows", [])
    show_contours = settings.get("cartographer.fairway_contours", False)
    show_arrows = settings.get("cartographer.fairway_arrows", False)

    if ((contour_paths and show_contours) or (fairway_arrows and show_arrows)):
        fairway_rings = hole_geom.get("fairway", [])
        if fairway_rings:
            # Process contour paths for drawing
            processed: list[list[list[float]]] = []
            if show_contours and contour_paths:
                for path in contour_paths:
                    if len(path) >= 2:
                        pts = [(float(p[0]), float(p[1])) for p in path]
                        if len(pts) >= 2 * 33:
                            decimated = pts[::33]
                            if decimated[-1] != pts[-1]:
                                decimated.append(pts[-1])
                        else:
                            decimated = pts
                        smoothed = chaikin_smooth_open(decimated, iterations=3)
                        if len(smoothed) >= 2:
                            total_len = sum(
                                math.hypot(smoothed[i][0] - smoothed[i-1][0],
                                           smoothed[i][1] - smoothed[i-1][1])
                                for i in range(1, len(smoothed))
                            )
                            if total_len >= 30.0:
                                processed.append([[x, y] for x, y in smoothed])

            g = dwg.g()
            clip_id = f"fw-{id(hole_geom)}"
            clip = dwg.defs.add(dwg.clipPath(id=clip_id))
            for ring in fairway_rings:
                clip.add(dwg.polygon(points=[(float(x), float(y)) for x, y in ring]))
            inner = g.add(dwg.g(clip_path=f"url(#{clip_id})"))

            if processed:
                for path in processed:
                    if len(path) >= 2:
                        d = "M " + " ".join(f"{p[0]:.2f},{p[1]:.2f}" for p in path)
                        inner.add(dwg.path(
                            d=d, stroke="#000000",
                            stroke_width=_STROKE_WIDTH, fill="none",
                        ))

            if fairway_arrows and show_arrows:
                arrow_color = "#000000"
                for (cx, cy), (dx, dy) in fairway_arrows:
                    angle = math.atan2(dy, dx)
                    leg_len = 5.0
                    half_angle = math.radians(30)
                    lx = cx + leg_len * math.cos(angle - half_angle)
                    ly = cy + leg_len * math.sin(angle - half_angle)
                    rx = cx + leg_len * math.cos(angle + half_angle)
                    ry = cy + leg_len * math.sin(angle + half_angle)
                    inner.add(dwg.polyline(
                        points=[(lx, ly), (cx, cy), (rx, ry)],
                        stroke=arrow_color, stroke_width=0.75, fill="none",
                    ))
                    shaft_len = 10.0
                    inner.add(dwg.line(
                        start=(cx, cy),
                        end=(cx + dx * shaft_len, cy + dy * shaft_len),
                        stroke=arrow_color, stroke_width=0.75,
                    ))
            dwg.add(g)

    return dwg.tostring()


def render_green(
    green_geom: dict,
    canvas_size: float = 200.0,
    fitted: bool = False,
    rotation_deg: float | None = None,
    canvas_w: float | None = None,
    canvas_h: float | None = None,
    shading_data: dict | None = None,
) -> str:
    """Render a green detail SVG with elevation shading.

    green_geom: a hole geometry dict (only 'green' key is used).
    canvas_size: fallback square canvas size if canvas_w/canvas_h not provided.
    canvas_w: explicit canvas width; defaults to canvas_size.
    canvas_h: explicit canvas height; defaults to canvas_size.
    fitted: if True, green rings are already fitted — skip fit_hole().
    rotation_deg: rotation angle to use when fitting green-only geometry.
    shading_data: optional dict with 'png_bytes', 'svg_corners', 'green_ring'.
        When provided, drawn instead of the ruled grid or contours.
    """
    ch = canvas_h if canvas_h is not None else canvas_size
    cw = canvas_w if canvas_w is not None else canvas_size

    dwg = svgwrite.Drawing(
        size=(f"{cw}pt", f"{ch}pt"),
        viewBox=f"0 0 {cw} {ch}",
    )
    dwg.add(dwg.rect(insert=(0, 0), size=(cw, ch), fill="white"))

    x_offset = (cw - ch) / 2 if not fitted else 0

    if fitted:
        raw = {"green": green_geom.get("green", []),
               "fairway": green_geom.get("fairway", []),
               "water": green_geom.get("water", []),
               "bunkers": green_geom.get("bunkers", []),
               "rough_boundary": green_geom.get("rough_boundary", []),
               "paths": green_geom.get("paths", [])}
    else:
        raw, _, _, _ = fit_hole(
            {"green": green_geom.get("green", []),
             "fairway": [], "bunkers": [], "water": [], "rough_boundary": [], "tee_boxes": {}},
            ch, ch, padding=15.0,
            rotation=rotation_deg,
        )
    raw["green"] = [chaikin_smooth(r) for r in raw.get("green", [])]

    stroke_col, fill_col = _COLOURS["green"]
    g = dwg.g(transform=f"translate({x_offset}, 0)")
    if shading_data is not None:
        for feature_type in ("rough_boundary", "fairway", "water", "bunkers"):
            s, _ = _COLOURS[feature_type]
            rings = raw.get(feature_type, [])
            if rings:
                _draw_polygons(dwg, g, rings, stroke=s, fill="white",
                               stroke_opacity=0.5, stroke_dasharray="4,4")
        stroke_col_p, _ = _COLOURS["paths"]
        path_lines = raw.get("paths", [])
        if path_lines:
            _draw_lines(dwg, g, path_lines, stroke=stroke_col_p)
        _draw_polygons(dwg, g, raw.get("green", []), stroke="none", fill="white")
        green_ring = raw.get("green", [])
        if green_ring:
            _draw_elevation_shading(
                dwg, g,
                png_bytes=shading_data["png_bytes"],
                bbox=shading_data["bbox"],
                green_ring=green_ring[0],
                rotate_angle=shading_data.get("rotate_angle", 0.0),
                rotate_cx=shading_data.get("rotate_cx", 0.0),
                rotate_cy=shading_data.get("rotate_cy", 0.0),
                contour_paths=shading_data.get("contour_paths"),
                show_heightmap=shading_data.get("show_heightmap", True),
                show_contours=shading_data.get("show_contours", True),
                show_arrows=shading_data.get("show_arrows", False),
            )
        _draw_polygons(dwg, g, raw.get("green", []), stroke=stroke_col, fill="none")
    else:
        _draw_polygons(dwg, g, raw.get("green", []), stroke=stroke_col, fill=fill_col)
    dwg.add(g)
    if shading_data is None:
        _draw_green_grid(dwg, ch, cw, x_offset)

    return dwg.tostring()


def _compute_arrows(
    png_bytes: bytes,
    bbox: tuple[float, float, float, float],
    contour_paths: list[list[list[float]]],
    spacing: float = 5.0,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Compute downhill-direction arrows at sample points along contour polylines.

    Returns a list of ((anchor_x, anchor_y), (dir_x, dir_y)) tuples where
    (dir_x, dir_y) is a unit-length vector pointing downhill (high -> low).

    The shading image has white=high, black=low. np.gradient returns
    (dy, dx) pointing from low->high, matching the downhill direction.

    spacing: arc-length interval in SVG points between sample arrows.
    """
    import numpy as np
    import io
    from PIL import Image

    if not contour_paths:
        return []

    img = Image.open(io.BytesIO(png_bytes))
    arr = np.array(img.convert("L"), dtype=float)
    h, w = arr.shape
    dy_arr, dx_arr = np.gradient(arr)

    bx, by, bx2, by2 = bbox
    bbox_w = bx2 - bx
    bbox_h = by2 - by
    if bbox_w <= 0 or bbox_h <= 0:
        return []

    arrows: list[tuple[tuple[float, float], tuple[float, float]]] = []

    for path in contour_paths:
        if len(path) < 3:
            continue

        cumulative = [0.0]
        for i in range(1, len(path)):
            seg_len = math.hypot(
                path[i][0] - path[i-1][0],
                path[i][1] - path[i-1][1],
            )
            cumulative.append(cumulative[-1] + seg_len)

        total_len = cumulative[-1]
        if total_len < spacing * 2:
            continue

        n_samples = max(1, int(total_len / spacing))
        sample_stops = [i * total_len / n_samples for i in range(n_samples)]

        seg_idx = 0
        for stop in sample_stops:
            while seg_idx < len(cumulative) - 1 and cumulative[seg_idx + 1] < stop:
                seg_idx += 1
            if seg_idx >= len(path) - 1:
                break

            seg_start = cumulative[seg_idx]
            seg_end = cumulative[seg_idx + 1]
            seg_range = seg_end - seg_start
            t = (stop - seg_start) / seg_range if seg_range > 0 else 0.0
            t = max(0.0, min(1.0, t))

            sx = path[seg_idx][0] + t * (path[seg_idx + 1][0] - path[seg_idx][0])
            sy = path[seg_idx][1] + t * (path[seg_idx + 1][1] - path[seg_idx][1])

            px = (sx - bx) / bbox_w * w
            py = (sy - by) / bbox_h * h
            pi = int(round(py))
            pj = int(round(px))

            if 0 <= pi < h and 0 <= pj < w:
                gx = dx_arr[pi, pj]
                gy = dy_arr[pi, pj]
                mag = math.hypot(gx, gy)
                if mag > 1e-9:
                    arrows.append(((sx, sy), (gx / mag, gy / mag)))

    return arrows


def _draw_elevation_shading(
    dwg: svgwrite.Drawing,
    group: svgwrite.container.Group,
    png_bytes: bytes,
    bbox: tuple[float, float, float, float],
    green_ring: list[list[float]],
    rotate_angle: float = 0.0,
    rotate_cx: float = 0.0,
    rotate_cy: float = 0.0,
    contour_paths: list[list[list[float]]] | None = None,
    show_heightmap: bool = True,
    show_contours: bool = True,
    show_arrows: bool = True,
) -> None:
    """Draw elevation shading as an SVG <image> clipped to the green polygon.

    png_bytes: PNG bytes of the grayscale shading image (unrotated, covers
        the projected geographic bounding box).
    bbox: (min_x, min_y, max_x, max_y) of the projected geographic bbox
        in SVG space — not the fitted polygon bbox.
    green_ring: fitted green polygon vertices — used for clipPath.
    rotate_angle: rotation angle (degrees) matching fit_hole so the
        shading aligns with the green.
    rotate_cx, rotate_cy: rotation centre in SVG space (green centroid).
    """
    import base64

    if len(green_ring) < 3:
        return

    bx, by, bx2, by2 = bbox
    bw, bh = bx2 - bx, by2 - by
    if bw <= 0 or bh <= 0:
        return

    clip_id = f"green-clip-{id(png_bytes)}"
    defs = dwg.defs
    clip = defs.add(dwg.clipPath(id=clip_id))
    pts = [(float(x), float(y)) for x, y in green_ring]
    clip.add(dwg.polygon(points=pts))

    b64 = base64.b64encode(png_bytes).decode("ascii")
    data_uri = f"data:image/png;base64,{b64}"

    outer = group.add(dwg.g(clip_path=f"url(#{clip_id})"))
    inner = outer.add(dwg.g(transform=f"rotate({rotate_angle}, {rotate_cx}, {rotate_cy})"))
    if show_heightmap:
        inner.add(dwg.image(
            href=data_uri,
            insert=(bx, by),
            size=(bw, bh),
            opacity="0.5",
        ))
    if show_contours and contour_paths:
        for path in contour_paths:
            if len(path) >= 2:
                d = "M " + " ".join(f"{p[0]:.2f},{p[1]:.2f}" for p in path)
                inner.add(dwg.path(d=d, stroke="#000000", stroke_width=_STROKE_WIDTH, fill="none"))

    if show_arrows and contour_paths:
        arrow_color = _COLOURS["green"][0]
        arrows = _compute_arrows(png_bytes, bbox, contour_paths, spacing=12.0)
        for (cx, cy), (dx, dy) in arrows:
            angle = math.atan2(dy, dx)
            leg_len = 5.0
            half_angle = math.radians(30)
            lx = cx + leg_len * math.cos(angle - half_angle)
            ly = cy + leg_len * math.sin(angle - half_angle)
            rx = cx + leg_len * math.cos(angle + half_angle)
            ry = cy + leg_len * math.sin(angle + half_angle)
            inner.add(dwg.polyline(
                points=[(lx, ly), (cx, cy), (rx, ry)],
                stroke=arrow_color, stroke_width=0.75, fill="none",
            ))
            shaft_len = 10.0
            inner.add(dwg.line(
                start=(cx, cy),
                end=(cx + dx * shaft_len, cy + dy * shaft_len),
                stroke=arrow_color, stroke_width=0.75,
            ))


def _draw_green_grid(
    dwg: svgwrite.Drawing,
    ch: float,
    cw: float,
    x_offset: float,
) -> None:
    """Original 6x6 ruled green grid (fallback)."""
    grid_step = ch / 6
    for i in range(1, 6):
        dwg.add(dwg.line(
            start=(x_offset + i * grid_step, 0),
            end=(x_offset + i * grid_step, ch),
            stroke="#000000", stroke_width=0.3,
        ))
        dwg.add(dwg.line(
            start=(0, i * grid_step),
            end=(cw, i * grid_step),
            stroke="#000000", stroke_width=0.3,
        ))


def render_hole_svg(course_name: str, hole_number: int, settings: dict | None = None) -> str:
    """Public convenience function for PinSheet screens.

    Loads courses_geo.json, projects and fits the specified hole,
    and returns an SVG string. Returns an empty string if geometry
    is not available for the course.
    """
    courses_geo = load_courses_geo()
    course_data = courses_geo.get(course_name)
    if not course_data:
        return ""

    holes = course_data.get("holes", {})
    scale_data = course_data.get("scale", {})
    hole_key = str(hole_number)

    if hole_key not in holes:
        return ""

    # Compute ppy per-hole so arc radii are self-consistent with fit_hole scaling.
    ppy = compute_pixels_per_yard_from_geometry({hole_key: holes[hole_key]}, canvas_h=HOLE_CANVAS_H)
    effective_scale = {**scale_data, "pixels_per_yard": ppy}
    projected = project_course(holes, effective_scale)
    hole_geom = projected.get(hole_key, {})
    hole_geom = smooth_hole_geometry(hole_geom, pixels_per_yard=ppy)

    fitted, _, _, scale = fit_hole(hole_geom, HOLE_CANVAS_W, HOLE_CANVAS_H)

    # ppy derived from this hole's geometry; scale is fit_hole's shrink factor.
    if settings is None:
        settings = {}
    if settings.get("cartographer.yardage_arcs", True):
        distances = settings.get("cartographer.yardage_arc_distances", [100, 125, 150])
        gcx, gcy = get_green_centroid(fitted)
        fitted["_arcs"] = [(gcx, gcy, d * ppy * scale) for d in distances]

    return render_hole(fitted, settings=settings)


def svg_to_png(svg: str, target_w: int, target_h: int) -> bytes:
    """Convert SVG string to PNG bytes, scaled to fill terminal dimensions
    while preserving the content's natural aspect ratio."""
    import io, cairosvg
    from PIL import Image

    png_bytes = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=2000)
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    bbox = img.getbbox()
    if bbox:
        m = 40
        bbox = (max(0, bbox[0] - m), max(0, bbox[1] - m),
                min(img.width, bbox[2] + m), min(img.height, bbox[3] + m))
        img = img.crop(bbox)

    img_w, img_h = img.size
    if img_w < 1 or img_h < 1:
        out = io.BytesIO()
        img.save(out, "PNG")
        return out.getvalue()

    img_aspect = img_w / img_h
    target_aspect = target_w / target_h

    if img_aspect > target_aspect:
        # Image is wider than terminal — fit to target width
        final_w = target_w
        final_h = max(1, int(target_w / img_aspect))
    else:
        # Image is taller than terminal — fit to target height
        final_h = target_h
        final_w = max(1, int(target_h * img_aspect))

    img = img.resize((final_w, final_h), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def render_course_overview(
    projected: dict,
    canvas_w: float,
    canvas_h: float,
    padding: float = 20.0,
    rotation: float | None = None,
    pixels_per_yard: float = 0.0,
) -> str:
    """Render all 18 holes as a course overview SVG.

    projected: output of project_course() — pixel coords per hole.
    canvas_w, canvas_h: SVG canvas dimensions in points.
    padding: margin around the course geometry.
    rotation: rotation angle in degrees. If None, automatically computed
        to maximise the scale-to-fit. 0 disables rotation.
    pixels_per_yard: projection scale. When > 0, fairway polygons are
        morphologically opened (3-yard buffer) to remove narrow protrusions.
    """
    dwg = svgwrite.Drawing(
        size=(f"{canvas_w}pt", f"{canvas_h}pt"),
        viewBox=f"0 0 {canvas_w} {canvas_h}",
    )
    dwg.add(dwg.rect(insert=(0, 0), size=(canvas_w, canvas_h), fill="white"))

    # Collect all geometry from all holes for rotation computation
    all_features: dict[str, list] = {ft: [] for ft in ("fairway", "green", "bunkers", "rough_boundary")}
    all_tees: list[tuple[float, float]] = []
    all_points: list[tuple[float, float]] = []

    for hole_num in range(1, 19):
        hole = projected.get(str(hole_num), {})
        for ft in all_features:
            for ring in hole.get(ft, []):
                if ring:
                    all_features[ft].append(ring)
                    all_points.extend(ring)
        for tx, ty in hole.get("tee_boxes", {}).values():
            all_tees.append((tx, ty))
            all_points.append((tx, ty))

    if not all_points:
        return dwg.tostring()

    # Compute optimal rotation if not provided
    if rotation is None:
        rotation = find_overview_rotation(all_points, canvas_w, canvas_h, padding)

    # Rotate all features around centroid if rotation is non-zero
    if rotation != 0.0:
        cx = sum(x for x, y in all_points) / len(all_points)
        cy = sum(y for x, y in all_points) / len(all_points)
        origin = (cx, cy)
        for ft in all_features:
            all_features[ft] = [rotate_ring(r, rotation, origin) for r in all_features[ft]]
        all_tees = [list(rotate_point(x, y, rotation, origin)) for x, y in all_tees]

    # Morphological opening on fairway rings at overview scale
    if pixels_per_yard > 0:
        opened: dict[str, list] = {ft: [] for ft in all_features}
        for ft in all_features:
            for ring in all_features[ft]:
                ring_pts = [(x, y) for x, y in ring]
                if ft == "fairway":
                    ring_pts = opening_ring(ring_pts, 3.0, pixels_per_yard)
                ring_pts = [list(pt) for pt in ring_pts]
                if len(ring_pts) >= 3:
                    opened[ft].append(ring_pts)
        all_features = opened

    # Recompute bounds from (potentially rotated/opened) features
    global_min_x = global_min_y = float("inf")
    global_max_x = global_max_y = float("-inf")
    for ring_list in all_features.values():
        for ring in ring_list:
            for x, y in ring:
                global_min_x = min(global_min_x, x)
                global_max_x = max(global_max_x, x)
                global_min_y = min(global_min_y, y)
                global_max_y = max(global_max_y, y)
    for x, y in all_tees:
        global_min_x = min(global_min_x, x)
        global_max_x = max(global_max_x, x)
        global_min_y = min(global_min_y, y)
        global_max_y = max(global_max_y, y)

    scale, offset_x, offset_y = compute_fit(
        global_min_x, global_min_y, global_max_x, global_max_y,
        canvas_w, canvas_h, padding,
    )

    def tx(x: float, y: float) -> tuple[float, float]:
        return x * scale + offset_x, y * scale + offset_y

    def tx_ring(ring: list) -> list:
        return [list(tx(x, y)) for x, y in ring]

    # Render features with thin strokes
    thin_stroke = 0.4
    for feature_type in ("rough_boundary", "fairway", "bunkers", "green"):
        rings = all_features[feature_type]
        if not rings:
            continue
        stroke_col, fill_col = _COLOURS[feature_type]
        if feature_type == "rough_boundary":
            fill_col = _COLOURS["fairway"][1]
        g = dwg.g()
        for ring in rings:
            pts = tx_ring(ring)
            if len(pts) >= 3:
                g.add(dwg.polygon(
                    points=pts,
                    fill=fill_col,
                    stroke=stroke_col,
                    stroke_width=thin_stroke,
                    stroke_linejoin="round",
                ))
        dwg.add(g)

    # Tee box markers
    for tx_pos, ty_pos in all_tees:
        sx, sy = tx(tx_pos, ty_pos)
        dwg.add(dwg.circle(
            center=(sx, sy),
            r=1.5,
            fill=_COLOURS["fairway"][1],
            stroke=_COLOURS["rough_boundary"][0],
            stroke_width=0.3,
        ))

    return dwg.tostring()
