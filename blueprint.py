"""Flask Blueprint for Cartographer web pages."""
from __future__ import annotations

import logging
from urllib.parse import quote

from flask import (
    Blueprint,
    current_app,
    g,
    jsonify,
    render_template,
    request,
)

from .data import get_osm_path, load_courses_geo

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

    try:
        import sqlite3
        _db = sqlite3.connect(str(current_app.config["DB_PATH"]))
        _server_names = {row["name"] for row in _db.execute("SELECT name FROM courses").fetchall()}
        _db.close()
    except Exception:
        _server_names = set()

    for name in sorted(set(_server_names) | set(courses_geo.keys())):
        geo_data = courses_geo.get(name, {})
        holes = geo_data.get("holes", {})
        scale = geo_data.get("scale", {})
        has_osm = get_osm_path(name).exists()
        courses.append({
            "name": name,
            "hole_count": len(holes),
            "tagged_at": scale.get("tagged_at", ""),
            "feature_count": scale.get("feature_count", 0),
            "has_osm": has_osm,
            "has_geometry": bool(holes),
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
        from .renderer import render_hole_svg
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
                from .renderer import render_hole_svg
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


@bp.route("/<string:course>/tag")
def tagger_ui(course):
    osm_path = get_osm_path(course)
    has_osm = osm_path.exists()
    return render_template(
        "tagger.html",
        course_name=course,
        course_encoded=quote(course),
        has_osm=has_osm,
        error=None if has_osm else f'No OSM data for "{course}".',
        current_page="cartographer",
        settings=getattr(g, "settings", {}),
    )


@bp.route("/<string:course>/tag/api/features")
def tagger_api_features(course):
    from .tagger.server import handle_get_features
    return handle_get_features(course)


@bp.route("/<string:course>/tag/api/save", methods=["POST"])
def tagger_api_save(course):
    from .tagger.server import handle_save
    return handle_save(course, request.get_json())


@bp.route("/<string:course>/tag/api/splits")
def tagger_api_get_splits(course):
    from .tagger.server import handle_get_splits
    return handle_get_splits(course)


@bp.route("/<string:course>/tag/api/splits", methods=["POST"])
def tagger_api_add_split(course):
    from .tagger.server import handle_add_split
    return handle_add_split(course, request.get_json())


@bp.route("/<string:course>/tag/api/splits/<int:split_id>", methods=["DELETE"])
def tagger_api_delete_split(course, split_id):
    from .tagger.server import handle_delete_split
    return handle_delete_split(course, split_id)


@bp.route("/<string:course>/tag/api/assignments")
def tagger_api_assignments(course):
    from .tagger.server import handle_get_assignments
    return handle_get_assignments(course)


@bp.route("/<string:course>/upload-osm", methods=["POST"])
def upload_osm(course):
    import xml.etree.ElementTree as ET

    if "osm_file" not in request.files:
        return jsonify({"status": "error", "message": "No file provided"}), 400

    f = request.files["osm_file"]
    if not f.filename or not f.filename.endswith(".osm"):
        return jsonify({"status": "error", "message": "File must have .osm extension"}), 400

    data = f.read()
    if not data:
        return jsonify({"status": "error", "message": "File is empty"}), 400

    try:
        root = ET.fromstring(data)
        if root.tag != "osm":
            raise ValueError("root tag is not osm")
    except Exception:
        return jsonify({"status": "error", "message": "File does not appear to be a valid OSM XML file"}), 400

    path = get_osm_path(course)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return jsonify({"status": "ok"})
