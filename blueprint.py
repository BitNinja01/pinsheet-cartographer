"""Flask Blueprint for Cartographer web pages."""
from __future__ import annotations

import logging

from flask import (
    Blueprint,
    current_app,
    g,
    render_template,
)

from cartographer.data import load_courses_geo
from cartographer.renderer import render_hole_svg

log = logging.getLogger("pinsheet")

bp = Blueprint(
    "cartographer",
    __name__,
    url_prefix="/plugins/cartographer",
)


def _get_settings():
    return {
        "cartographer.yardage_arcs": current_app.config.get(
            "plugins.cartographer.yardage_arcs", True
        ),
        "cartographer.yardage_arc_distances": current_app.config.get(
            "plugins.cartographer.yardage_arc_distances", [100, 125, 150, 175, 200]
        ),
    }


@bp.route("/")
def course_picker():
    courses_geo = load_courses_geo()
    courses = []
    for name, data in sorted(courses_geo.items()):
        holes = data.get("holes", {})
        scale = data.get("scale", {})
        courses.append({
            "name": name,
            "hole_count": len(holes),
            "tagged_at": scale.get("tagged_at", ""),
            "feature_count": scale.get("feature_count", 0),
        })
    return render_template(
        "course_picker.html",
        courses=courses,
        current_page="cartographer",
        settings=getattr(g, "settings", {}),
    )


@bp.route("/<string:course>/hole/<int:hole_number>")
def hole_viewer(course, hole_number):
    courses_geo = load_courses_geo()
    course_data = courses_geo.get(course)
    if not course_data:
        return render_template(
            "hole_viewer.html",
            error=f'No geometry data for "{course}". Run the tagger first.',
            course_name=course,
            current_page="cartographer",
            settings=getattr(g, "settings", {}),
        ), 404

    holes = course_data.get("holes", {})
    max_hole = max(int(k) for k in holes.keys()) if holes else 0
    if hole_number < 1 or hole_number > max_hole:
        return render_template(
            "hole_viewer.html",
            error=f"Hole {hole_number} not found on {course}.",
            course_name=course,
            current_page="cartographer",
            settings=getattr(g, "settings", {}),
        ), 404

    hole_key = str(hole_number)
    if hole_key not in holes:
        return render_template(
            "hole_viewer.html",
            error=f"No geometry for hole {hole_number}.",
            course_name=course,
            current_page="cartographer",
            settings=getattr(g, "settings", {}),
        ), 404

    settings = _get_settings()
    try:
        svg_content = render_hole_svg(course, hole_number, settings=settings)
    except Exception:
        log.exception("cartographer: failed to render hole %d for %s", hole_number, course)
        svg_content = ""

    prev_hole = hole_number - 1 if hole_number > 1 else None
    next_hole = hole_number + 1 if hole_number < max_hole else None

    return render_template(
        "hole_viewer.html",
        svg_content=svg_content,
        course_name=course,
        hole_number=hole_number,
        prev_hole=prev_hole,
        next_hole=next_hole,
        error=None,
        current_page="cartographer",
        settings=getattr(g, "settings", {}),
    )


@bp.route("/<string:course>/gallery")
def course_gallery(course):
    courses_geo = load_courses_geo()
    course_data = courses_geo.get(course)
    if not course_data:
        return render_template(
            "course_gallery.html",
            error=f'No geometry data for "{course}".',
            course_name=course,
            holes=[],
            current_page="cartographer",
            settings=getattr(g, "settings", {}),
        ), 404

    holes_data = course_data.get("holes", {})
    settings = _get_settings()
    max_hole = max(int(k) for k in holes_data.keys()) if holes_data else 0
    holes = []
    for h in range(1, max_hole + 1):
        hole_key = str(h)
        svg = ""
        if hole_key in holes_data:
            try:
                svg = render_hole_svg(course, h, settings=settings)
            except Exception:
                log.exception("cartographer: failed to render hole %d for %s", h, course)
                svg = ""
        holes.append({"number": h, "svg": svg, "has_data": hole_key in holes_data})

    return render_template(
        "course_gallery.html",
        course_name=course,
        holes=holes,
        error=None,
        current_page="cartographer",
        settings=getattr(g, "settings", {}),
    )
