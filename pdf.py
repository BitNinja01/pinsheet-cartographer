# plugins/cartographer/pdf.py
"""PDF export for Cartographer yardage books.

Generates 20 narrow PDFs (4.25" x 14") in cross-paired order and
combines them into 5 saddle-stitch booklet PDFs (8.5" x 14") using pypdf.
"""
from __future__ import annotations

import io
import math
import sys
from pathlib import Path

import numpy as np

import cairosvg
from PIL import Image
from shapely.geometry import Polygon, LineString
from pypdf import PdfWriter, PdfReader, Transformation

from .data import load_courses_geo
from .geometry import (
    project_course, fit_hole, smooth_hole_geometry, chaikin_smooth, chaikin_smooth_open,
    get_green_centroid, get_green_rotation, compute_yardage_arcs,
    compute_pixels_per_yard_from_geometry,
)
from .renderer import render_hole, render_green, render_course_overview
from .elevation import get_course_dem, compute_elevation_shading, compute_slot_contours
from .layout import (
    compose_sheet, compose_front_page, compose_back_page, compose_chart_page,
    compose_notes_page, render_hole_page, render_bottom_slots,
    flip_page_svg, PAGE_W, PAGE_CONTENT_H, SLOT_H, PRINTABLE_W, MARGIN
)

HOLE_CANVAS_W = 270.0   # PRINTABLE_W
HOLE_CANVAS_H = 486.0   # PAGE_CONTENT_H
HOLE_LEFT_BIAS = 44.0   # pts — shift hole leftward on top pages; clamped to padding floor in fit_hole()


def _svg_to_pdf_bytes(svg_string: str) -> bytes:
    """Convert an SVG string to PDF bytes via cairosvg."""
    return cairosvg.svg2pdf(bytestring=svg_string.encode("utf-8"))


def _clip_contour_to_green(
    fitted_contour_ring: list[list[float]],
    fitted_green_poly: Polygon | None,
) -> list[list[float]]:
    """Clip a fitted contour path to the green polygon boundary.

    Args:
        fitted_contour_ring: List of [x, y] pairs in fitted (slot) coordinates.
        fitted_green_poly: Shapely Polygon of the fitted green.

    Returns:
        Clipped [x, y] pairs, or empty list if no intersection.
    """
    if fitted_green_poly is None or not fitted_contour_ring:
        return fitted_contour_ring

    line = LineString(fitted_contour_ring)
    clipped = line.intersection(fitted_green_poly)

    if clipped.is_empty:
        return []
    elif clipped.geom_type == "LineString":
        return list(clipped.coords)
    elif clipped.geom_type == "MultiLineString":
        longest = max(clipped.geoms, key=lambda g: g.length)
        return list(longest.coords)
    else:
        return []


def _compute_slot_content(
    hole_num: int,
    hole_geom: dict,
    slot_context_features: dict,
    holes_geo: dict,
    settings: dict,
    dem_path: Path | None,
    contour_cache: dict[int, list] | None,
    status_callback: callable,
    slot1_mode: str,
    slot2_mode: str,
    ppy: float,
    scale: float,
) -> tuple[str, str]:
    """Compute slot content (green grid with elevation shading) for a hole."""
    green_rot = get_green_rotation(hole_geom)
    slot1_svg = ""
    slot2_svg = ""

    show_heightmap = settings.get("cartographer.green_heightmap", True)
    show_contours = settings.get("cartographer.green_contours", True)
    show_arrows = settings.get("cartographer.green_arrows", True)

    if slot1_mode == "green_grid" or slot2_mode == "green_grid":
        proj_greens = hole_geom.get("green", [])
        if proj_greens:
            proj_ring = proj_greens[0]

            px = [p[0] for p in proj_ring]
            py = [p[1] for p in proj_ring]
            gc_cx = (min(px) + max(px)) / 2
            gc_cy = (min(py) + max(py)) / 2
            rad = math.radians(green_rot)
            cos_a, sin_a = math.cos(rad), math.sin(rad)

            rotated_green = []
            for x, y in proj_ring:
                rx = (x - gc_cx) * cos_a - (y - gc_cy) * sin_a + gc_cx
                ry = (x - gc_cx) * sin_a + (y - gc_cy) * cos_a + gc_cy
                rotated_green.append([rx, ry])

            rpx = [p[0] for p in rotated_green]
            rpy = [p[1] for p in rotated_green]
            rmin_x, rmax_x = min(rpx), max(rpx)
            rmin_y, rmax_y = min(rpy), max(rpy)
            rw = rmax_x - rmin_x or 1.0
            rh = rmax_y - rmin_y or 1.0

            padding = 15.0
            avail_w = PRINTABLE_W - 2 * padding
            avail_h = SLOT_H - 2 * padding
            slot_scale = min(avail_w / rw, avail_h / rh)
            off_x = padding + (avail_w - rw * slot_scale) / 2 - rmin_x * slot_scale
            off_y = padding + (avail_h - rh * slot_scale) / 2 - rmin_y * slot_scale

            slot_fitted: dict[str, list] = {
                ft: [] for ft in ("fairway", "water", "bunkers",
                                  "waterways", "rough_boundary", "paths", "contours")
            }
            slot_fitted["tee_boxes"] = {}
            slot_fitted["green"] = [chaikin_smooth(
                [[x * slot_scale + off_x, y * slot_scale + off_y] for x, y in rotated_green]
            )]

            green_cx = sum(px) / len(px)
            green_cy = sum(py) / len(py)

            def _fit_point(x, y):
                rx = (x - green_cx) * cos_a - (y - green_cy) * sin_a + green_cx
                ry = (x - green_cx) * sin_a + (y - green_cy) * cos_a + green_cy
                return [rx * slot_scale + off_x, ry * slot_scale + off_y]

            for feature_type in ("fairway", "water", "bunkers", "rough_boundary"):
                rings = slot_context_features.get(feature_type, [])
                fitted_rings = []
                for ring in rings:
                    fitted = [_fit_point(x, y) for x, y in ring]
                    fitted_rings.append(chaikin_smooth(fitted, iterations=1))
                slot_fitted[feature_type] = fitted_rings

            fitted_paths = []
            for line in slot_context_features.get("paths", []):
                fitted_paths.append([_fit_point(x, y) for x, y in line])
            slot_fitted["paths"] = fitted_paths
        else:
            slot_fitted = {"green": [], "fairway": [], "bunkers": [],
                           "water": [], "waterways": [], "rough_boundary": [],
                           "paths": [], "contours": [], "tee_boxes": {}}

        # Elevation shading: extract DEM, resize to projected geographic bbox,
        # then apply rotation as an SVG transform (not PIL — avoids aspect-ratio
        # mismatch between geographic bbox and fitted polygon bbox).
        shading_data = None
        greens = slot_fitted.get("green", [])
        contour_paths = []
        if dem_path is not None and greens and (show_heightmap or show_contours or show_arrows):
            orig_greens = holes_geo[str(hole_num)].get("green", [])
            if orig_greens:
                if status_callback and (contour_cache is None or hole_num not in contour_cache):
                    status_callback(f"Computing elevation shading for hole {hole_num}...")
                shading_img = compute_elevation_shading(orig_greens[0], dem_path)
                if shading_img is not None:
                    # Compute projected geographic bbox (unrotated)
                    proj_green = hole_geom.get("green", [])
                    if proj_green:
                        px = [p[0] for p in proj_green[0]]
                        py = [p[1] for p in proj_green[0]]
                        pmin_x, pmax_x = min(px), max(px)
                        pmin_y, pmax_y = min(py), max(py)
                        pw, ph = pmax_x - pmin_x, pmax_y - pmin_y

                        # SVG position of projected bbox (same *scale + offset as fit_hole)
                        svg_bx = pmin_x * slot_scale + off_x
                        svg_by = pmin_y * slot_scale + off_y
                        svg_bw = pw * slot_scale
                        svg_bh = ph * slot_scale

                        # Rotation centre must match fit_hole() which rotates
                        # around the projected green bbox centre. In SVG space
                        # that centre is the image centre.
                        gcx = svg_bx + svg_bw / 2
                        gcy = svg_by + svg_bh / 2

                        img_resized = shading_img.resize(
                            (max(1, int(svg_bw)), max(1, int(svg_bh))), Image.LANCZOS
                        )
                        import io as _io
                        buf = _io.BytesIO()
                        img_resized.save(buf, format="PNG")

                        if contour_cache is not None and hole_num in contour_cache:
                            contour_paths = contour_cache[hole_num]
                        else:
                            if status_callback:
                                status_callback(f"Extracting & connecting contour lines...")
                            contour_paths = compute_slot_contours(
                                shading_img, svg_bx, svg_by, svg_bw, svg_bh,
                            )
                            if contour_cache is not None:
                                contour_cache[hole_num] = contour_paths
                            if status_callback:
                                status_callback(f"Generating sheet for hole {hole_num}...")

                        shading_data = {
                            "png_bytes": buf.getvalue(),
                            "bbox": (svg_bx, svg_by, svg_bx + svg_bw, svg_by + svg_bh),
                            "rotate_angle": green_rot,
                            "rotate_cx": gcx,
                            "rotate_cy": gcy,
                            "contour_paths": contour_paths,
                            "show_heightmap": show_heightmap,
                            "show_contours": show_contours,
                            "show_arrows": show_arrows,
                        }
    else:
        slot_fitted = None
        shading_data = None

    if slot1_mode == "green_grid":
        slot1_svg = render_green(
            slot_fitted, canvas_w=PRINTABLE_W, canvas_h=SLOT_H,
            fitted=True, shading_data=shading_data,
        )
    if slot2_mode == "green_grid":
        slot2_svg = render_green(
            slot_fitted, canvas_w=PRINTABLE_W, canvas_h=SLOT_H,
            fitted=True, shading_data=shading_data,
        )

    return slot1_svg, slot2_svg


def _get_hole_render_data(
    hole_num: int,
    holes_geo: dict,
    scale_data: dict,
    settings: dict,
    course_ps: dict,
    slot1_mode: str,
    slot2_mode: str,
    dem_path: Path | None = None,
    contour_cache: dict[int, list] | None = None,
    status_callback: callable = None,
    compute_slots: bool = True,
) -> dict | None:
    """Render a single hole and return raw data for page composition.

    Returns dict with keys: hole_svg, par, tee_yardages, slot1_svg, slot2_svg
    or None if hole geometry is missing.

    dem_path: optional path to cached DEM GeoTIFF for elevation shading.
    compute_slots: if False, skip green-grid/shading/contour computation
                   (used for top-half pages where only the hole diagram is rendered).
    """
    hole_key = str(hole_num)
    if hole_key not in holes_geo:
        return None

    ppy = compute_pixels_per_yard_from_geometry(
        {hole_key: holes_geo[hole_key]}, canvas_h=HOLE_CANVAS_H
    )
    effective_scale = {**scale_data, "pixels_per_yard": ppy}
    projected = project_course(holes_geo, effective_scale, only_hole=hole_key)

    hole_geom = projected.get(hole_key, {})
    if not hole_geom:
        return None

    slot_context_features: dict[str, list] = {
        ft: list(hole_geom.get(ft, []))
        for ft in ("fairway", "water", "bunkers", "rough_boundary", "paths")
    }

    hole_geom = smooth_hole_geometry(hole_geom, pixels_per_yard=ppy)

    fitted, _, _, scale = fit_hole(hole_geom, HOLE_CANVAS_W, HOLE_CANVAS_H, left_bias=HOLE_LEFT_BIAS)

    if settings.get("cartographer.yardage_arcs", True):
        distances = settings.get("cartographer.yardage_arc_distances", [100, 125, 150])
        gcx, gcy = get_green_centroid(fitted)
        fitted["_arcs"] = compute_yardage_arcs((gcx, gcy), distances, ppy, scale)

    hole_svg = render_hole(fitted, settings=settings)

    hole_ps_data = course_ps.get("holes", {}).get(hole_key, {})
    tee_yardages = {t: int(y) for t, y in hole_ps_data.get("tees", {}).items()}
    par = int(hole_ps_data.get("par", 4))

    slot1_svg = ""
    slot2_svg = ""
    if compute_slots:
        slot1_svg, slot2_svg = _compute_slot_content(
            hole_num, hole_geom, slot_context_features, holes_geo,
            settings, dem_path, contour_cache, status_callback,
            slot1_mode, slot2_mode, ppy, scale,
        )

    return {
        "hole_svg": hole_svg,
        "par": par,
        "tee_yardages": tee_yardages,
        "slot1_svg": slot1_svg,
        "slot2_svg": slot2_svg,
    }


def _compose_standard_sheet(
    top_hole: int,
    bottom_hole: int,
    holes_geo: dict,
    scale_data: dict,
    settings: dict,
    course_ps: dict,
    slot1_mode: str,
    slot2_mode: str,
    dem_path: Path | None,
    contour_cache: dict,
    status_callback: callable,
    stats_data: dict | None,
) -> str:
    """Render a standard sheet: top-hole diagram + bottom-hole data slot."""
    top_hd = _get_hole_render_data(
        top_hole, holes_geo, scale_data, settings, course_ps,
        slot1_mode, slot2_mode, dem_path=dem_path, contour_cache=contour_cache,
        status_callback=status_callback, compute_slots=False,
    )
    bottom_hd = _get_hole_render_data(
        bottom_hole, holes_geo, scale_data, settings, course_ps,
        slot1_mode, slot2_mode, dem_path=dem_path, contour_cache=contour_cache,
        status_callback=status_callback,
    )
    if top_hd:
        top_svg = render_hole_page(
            hole_svg=top_hd["hole_svg"], hole_num=top_hole, par=top_hd["par"],
            tee_yardages=top_hd["tee_yardages"],
        )
        bottom_svg = render_bottom_slots(
            slot1_content=slot1_mode, slot2_content=slot2_mode,
            slot1_svg=bottom_hd["slot1_svg"] if bottom_hd else "",
            slot2_svg=bottom_hd["slot2_svg"] if bottom_hd else "",
            stats_data=stats_data,
            hole_num=bottom_hole if bottom_hd else top_hole,
        )
        return compose_sheet(top_svg, bottom_svg)
    else:
        import svgwrite as svg
        dwg = svg.Drawing(size=(f"{PAGE_W}pt", f"{PAGE_CONTENT_H}pt"), viewBox=f"0 0 {PAGE_W} {PAGE_CONTENT_H}")
        dwg.add(dwg.rect(insert=(0, 0), size=(PAGE_W, PAGE_CONTENT_H), fill="white"))
        blank = dwg.tostring()
        return compose_sheet(blank, blank)


def generate_book(
    course_name: str,
    output_dir: Path,
    slot1_mode: str = "green_grid",
    slot2_mode: str = "stats_panel",
    show_calculated_stats: bool = True,
    settings: dict | None = None,
    progress_callback: callable = None,
    status_callback: callable = None,
    data_dir: Path | None = None,
    courses_data: dict | None = None,
    rounds_data: list[dict] | None = None,
) -> None:
    """Generate a complete yardage book for a course.

    course_name: must match a key in courses_geo.json and courses.json.
    output_dir: directory where booklet PDFs will be written.
    slot1_mode: "green_grid", "stats_panel", or "notes"
    slot2_mode: "green_grid", "stats_panel", or "notes"
    show_calculated_stats: if False, stat boxes render as blank (labels + underlines)
    settings: plugin settings dict.
    progress_callback: optional function(current_page, total_pages) for progress updates
    status_callback: optional function(message) for status text updates
    data_dir: override data directory for courses.json; if None, auto-resolve.
    courses_data: pre-loaded courses dict; if given, skip reading courses.json.
    rounds_data: pre-loaded rounds list (must be pre-filtered to this course);
                  if given, skip reading rounds files.
    """
    if settings is None:
        settings = {}

    # Load geometry
    courses_geo = load_courses_geo()
    course_geo = courses_geo.get(course_name)
    if not course_geo:
        raise ValueError(f"No geometry found for course '{course_name}'. Run: python -m cartographer.tagger \"{course_name}\"")

    # Load course data from PinSheet's courses.json for tee yardages
    if data_dir is not None:
        courses_json = data_dir / "courses.json"
    elif getattr(sys, "frozen", False):
        courses_json = Path(sys.executable).parent / "data" / "courses.json"
    else:
        courses_json = Path(__file__).parent.parent.parent / "data" / "courses.json"

    import json
    if courses_data is not None:
        pinsheet_courses = courses_data
    else:
        pinsheet_courses = json.loads(courses_json.read_text()) if courses_json.exists() else {}
    course_ps = pinsheet_courses.get(course_name, {})

    # Compute course-level metadata for front/back pages
    total_par = 0
    tee_totals: dict[str, int] = {}
    for hk, hd in course_ps.get("holes", {}).items():
        total_par += int(hd.get("par", 4))
        for tee, yrd in hd.get("tees", {}).items():
            tee_totals[tee] = tee_totals.get(tee, 0) + int(yrd)

    # Load rounds data for stats (if needed)
    rounds_by_hole = {}
    stats_data = None
    if "stats_panel" in [slot1_mode, slot2_mode] and show_calculated_stats:
        from . import stats as stats_module
        
        # Load all rounds for this course
        if rounds_data is not None:
            all_rounds = rounds_data
        else:
            all_rounds = []
            rounds_dir = courses_json.parent / "rounds"
            if rounds_dir.exists():
                for year_file in rounds_dir.glob("*.json"):
                    year_rounds = json.loads(year_file.read_text())
                    for date_str, date_rounds in year_rounds.items():
                        for idx_str, rnd in date_rounds.items():
                            if rnd.get("course") == course_name:
                                all_rounds.append(rnd)
        
        # Group by hole number
        for hole_num in range(1, 19):
            rounds_by_hole[hole_num] = [
                r for r in all_rounds
                if str(hole_num) in r.get("holes", {})
            ]
        
        # Compute stats for each hole
        stats_data = {}
        for hole_num in range(1, 19):
            hole_key = str(hole_num)
            hole_ps_data = course_ps.get("holes", {}).get(hole_key, {})
            par = int(hole_ps_data.get("par", 4))
            hole_hcp = int(hole_ps_data.get("handicap", hole_num))
            
            # Get current handicap index (use most recent round's handicap)
            handicap_index = 15.0  # default
            if all_rounds:
                most_recent = max(all_rounds, key=lambda r: r.get("date", ""))
                hi_str = most_recent.get("handicap_index", "15.0")
                try:
                    handicap_index = float(hi_str)
                except ValueError:
                    pass
            
            hole_rounds = rounds_by_hole.get(hole_num, [])
            stats_data[hole_num] = {
                "fairway_misses": stats_module.calc_fairway_misses(hole_rounds, hole_num, par),
                "gir_misses": stats_module.calc_gir_misses(hole_rounds, hole_num),
                "benchmark": stats_module.calc_benchmark(hole_rounds, hole_num, par, handicap_index, hole_hcp),
                "penalties": stats_module.calc_penalties(hole_rounds, hole_num),
            }

    holes_geo = course_geo.get("holes", {})
    scale_data = course_geo.get("scale", {})

    dem_path = get_course_dem(course_name, holes_geo, status_callback=status_callback, force=False)

    safe_course = course_name.lower().replace(" ", "_").replace("'", "").replace('"', "")

    # Generate 20 narrow PDFs in cross-paired order
    output_dir.mkdir(parents=True, exist_ok=True)
    sheets_dir = output_dir / "sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    booklets_dir = output_dir / "booklets"
    booklets_dir.mkdir(parents=True, exist_ok=True)
    narrow_pdfs: list[bytes] = []

    contour_cache: dict[int, list] = {}

    total_steps = 25  # 20 sheets + 5 booklets

    for page_idx in range(0, 20):
        if progress_callback:
            progress_callback(page_idx + 1, total_steps)

        if page_idx <= 8:
            top_hole = 9 - page_idx
            bottom_hole = 9 if page_idx == 0 else 18 - top_hole
            fname = f"{safe_course}_{top_hole}_{bottom_hole}.pdf"
            svg_str = _compose_standard_sheet(
                top_hole, bottom_hole, holes_geo, scale_data, settings, course_ps,
                slot1_mode, slot2_mode, dem_path, contour_cache, status_callback, stats_data,
            )

        elif page_idx == 9:
            fname = f"{safe_course}_chart_18.pdf"
            chart_svg = compose_chart_page()
            hd = _get_hole_render_data(
                18, holes_geo, scale_data, settings, course_ps,
                slot1_mode, slot2_mode, dem_path=dem_path, contour_cache=contour_cache,
                status_callback=status_callback,
            )
            if hd:
                bottom_svg = render_bottom_slots(
                    slot1_content=slot1_mode, slot2_content=slot2_mode,
                    slot1_svg=hd["slot1_svg"], slot2_svg=hd["slot2_svg"],
                    stats_data=stats_data,
                    hole_num=18,
                )
            else:
                import svgwrite as svg
                dwg = svg.Drawing(size=("306pt", f"{PAGE_CONTENT_H}pt"), viewBox=f"0 0 306 {PAGE_CONTENT_H}")
                dwg.add(dwg.rect(insert=(0, 0), size=(306, PAGE_CONTENT_H), fill="white"))
                bottom_svg = dwg.tostring()
            svg_str = compose_sheet(chart_svg, bottom_svg)

        elif page_idx <= 17:
            top_hole = page_idx
            bottom_hole = 18 - top_hole
            fname = f"{safe_course}_{top_hole}_{bottom_hole}.pdf"
            svg_str = _compose_standard_sheet(
                top_hole, bottom_hole, holes_geo, scale_data, settings, course_ps,
                slot1_mode, slot2_mode, dem_path, contour_cache, status_callback, stats_data,
            )

        elif page_idx == 18:
            fname = f"{safe_course}_18_notes.pdf"
            hd = _get_hole_render_data(
                18, holes_geo, scale_data, settings, course_ps,
                slot1_mode, slot2_mode, dem_path=dem_path, contour_cache=contour_cache,
                status_callback=status_callback, compute_slots=False,
            )
            if hd:
                top_svg = render_hole_page(
                    hole_svg=hd["hole_svg"], hole_num=18, par=hd["par"],
                    tee_yardages=hd["tee_yardages"],
                )
            else:
                import svgwrite as svg
                dwg = svg.Drawing(size=("306pt", f"{PAGE_CONTENT_H}pt"), viewBox=f"0 0 306 {PAGE_CONTENT_H}")
                dwg.add(dwg.rect(insert=(0, 0), size=(306, PAGE_CONTENT_H), fill="white"))
                top_svg = dwg.tostring()
            notes_svg = compose_notes_page()
            svg_str = compose_sheet(top_svg, notes_svg)

        elif page_idx == 19:
            fname = f"{safe_course}_cover.pdf"
            overview_ppy = compute_pixels_per_yard_from_geometry(
                holes_geo, canvas_h=HOLE_CANVAS_H
            )
            overview_scale = {**scale_data, "pixels_per_yard": overview_ppy}
            projected = project_course(holes_geo, overview_scale)
            overview_svg = render_course_overview(
                projected,
                PRINTABLE_W,
                PAGE_CONTENT_H - 4 * MARGIN - 24,
                padding=10.0,
                pixels_per_yard=overview_ppy,
            )
            back_svg = compose_back_page(full_course_svg=overview_svg)
            back_svg = flip_page_svg(back_svg, PAGE_W, PAGE_CONTENT_H)
            front_svg = compose_front_page(
                course_name=course_name,
                location=course_ps.get("location"),
                total_par=total_par,
                tee_totals=tee_totals,
            )
            svg_str = compose_sheet(back_svg, front_svg)

        pdf_bytes = _svg_to_pdf_bytes(svg_str)
        narrow_pdfs.append(pdf_bytes)
        narrow_path = sheets_dir / fname
        narrow_path.write_bytes(pdf_bytes)

    # Combine into 5 saddle-stitch booklets, each with two 8.5"x14" pages
    # Each page has two narrow PDFs merged side-by-side
    booklet_pages = [
        ([8, 9], [18, 19]),   # 1/chart + 18/cover
        ([6, 7], [16, 17]),   # 3/2 + 16/17
        ([4, 5], [14, 15]),   # 5/4 + 14/15
        ([2, 3], [12, 13]),   # 7/6 + 12/13
        ([0, 1], [10, 11]),   # 9/8 + 10/11
    ]

    for booklet_idx, (left_pair, right_pair) in enumerate(booklet_pages, start=1):
        if progress_callback:
            progress_callback(20 + booklet_idx, total_steps)
        writer = PdfWriter()

        for pair in (left_pair, right_pair):
            page = writer.add_blank_page(612, 1008)  # 8.5" x 14"
            left_pdf = PdfReader(io.BytesIO(narrow_pdfs[pair[0]])).pages[0]
            right_pdf = PdfReader(io.BytesIO(narrow_pdfs[pair[1]])).pages[0]
            page.merge_transformed_page(left_pdf, Transformation().translate(0, 0))
            page.merge_transformed_page(right_pdf, Transformation().translate(PAGE_W, 0))

        booklet_path = booklets_dir / f"{safe_course}_booklet_{booklet_idx:02d}.pdf"
        with open(booklet_path, "wb") as f:
            writer.write(f)

    print(f"Yardage book generated: {output_dir}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate a PinSheet yardage book PDF")
    parser.add_argument("course_name", help="Course name (must match courses.json)")
    parser.add_argument("--output", default=".", help="Output directory")
    args = parser.parse_args()
    generate_book(args.course_name, Path(args.output).expanduser())
