"""Flask Blueprint for Cartographer web pages."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    g,
    jsonify,
    render_template,
    request,
    send_file,
    stream_with_context,
)
from flask_login import current_user

from .data import get_osm_path, load_courses_geo

log = logging.getLogger("pinsheet")

bp = Blueprint(
    "cartographer",
    __name__,
    url_prefix="/plugins/cartographer",
)

_pdf_jobs: dict[str, dict] = {}

_DEPS = ("cairosvg", "pypdf", "PIL", "numpy", "rasterio", "skimage")


def _check_pdf_deps() -> str | None:
    missing = []
    sys_deps = []
    for dep in _DEPS:
        try:
            __import__(dep)
        except ImportError:
            missing.append(dep)
        except OSError:
            sys_deps.append(dep)
    parts = []
    if missing:
        parts.append(f"Missing Python packages: {', '.join(missing)}. Run: pip install -r plugins/pinsheet-cartographer/requirements.txt")
    if sys_deps:
        parts.append(f"Missing system libraries for: {', '.join(sys_deps)}. Install libcairo2 (Debian: sudo apt install libcairo2, Fedora: sudo dnf install cairo)")
    return "; ".join(parts) if parts else None


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
        _db.row_factory = sqlite3.Row
        _server_names = {row["name"] for row in _db.execute("SELECT name FROM courses").fetchall()}
        _db.close()
    except Exception:
        _server_names = set()

    pdf_timestamps = {}
    try:
        _db2 = sqlite3.connect(str(current_app.config["DB_PATH"]))
        _db2.row_factory = sqlite3.Row
        for row in _db2.execute("SELECT course_name, pdf_generated_at FROM plugin_cartographer_geometry WHERE pdf_generated_at IS NOT NULL"):
            pdf_timestamps[row["course_name"]] = row["pdf_generated_at"]
        _db2.close()
    except Exception:
        pass

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
            "pdf_generated_at": pdf_timestamps.get(name),
            "pdf_status": "stale" if (ts := pdf_timestamps.get(name)) and (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).days >= 182 else "fresh" if ts else None,
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


@bp.route("/<string:course>/api/hole/<int:hole_number>/svg")
def hole_svg(course, hole_number):
    courses_geo = load_courses_geo()
    course_data = courses_geo.get(course)
    if not course_data:
        return "", 404

    holes = course_data.get("holes", {})
    if str(hole_number) not in holes:
        return "", 404

    try:
        from .renderer import render_hole_svg
        svg = render_hole_svg(course, hole_number, settings=_get_settings())
        return svg, 200, {"Content-Type": "image/svg+xml"}
    except Exception:
        log.exception("cartographer: failed to render SVG for hole %d / %s", hole_number, course)
        return "", 500


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
    max_hole = max(int(k) for k in holes_data.keys()) if holes_data else 0
    holes = [{"number": h, "has_data": str(h) in holes_data} for h in range(1, max_hole + 1)]

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


@bp.route("/<string:course>/pdf")
def pdf_export(course):
    from .data import _get_plugin_data_dir
    path = _get_plugin_data_dir()
    geo_path = path / "courses_geo.json"
    has_geo = geo_path.exists()

    db = sqlite3.connect(str(current_app.config["DB_PATH"]))
    row = db.execute("SELECT data FROM courses WHERE name = ?", (course,)).fetchone()
    tees = []
    if row:
        course_data = json.loads(row[0])
        tees = sorted(course_data.get("tees", {}).keys())
    db.close()

    return render_template(
        "pdf_export.html",
        course_name=course,
        course_encoded=quote(course),
        tees=tees,
        has_geometry=has_geo,
        settings=_get_settings(),
        current_page="cartographer",
    )


@bp.route("/<string:course>/pdf/generate", methods=["POST"])
def pdf_generate(course):
    try:
        return _do_pdf_generate(course)
    except Exception as exc:
        log.exception("cartographer: pdf_generate failed for %s", course)
        return jsonify({"status": "error", "error": f"Server error: {type(exc).__name__}: {exc}"}), 500


def _do_pdf_generate(course):
    dep_err = _check_pdf_deps()
    if dep_err:
        return jsonify({"status": "error", "error": dep_err}), 400

    from .data import load_courses_geo, _get_plugin_data_dir as _pd
    geo = load_courses_geo()
    if course not in geo:
        return jsonify({"status": "error", "error": f"No geometry data for '{course}'"}), 400

    safe = course.lower().replace(" ", "_").replace("'", "").replace('"', "")
    job_id = f"{safe}_{int(time.time())}"
    now = datetime.now(timezone.utc).isoformat()

    data_dir = current_app.config["DATA_DIR"]

    user_id = current_user.id if current_user and not current_user.is_anonymous else 1

    db = sqlite3.connect(str(current_app.config["DB_PATH"]))
    try:
        db.row_factory = sqlite3.Row
        course_row = db.execute(
            "SELECT data FROM courses WHERE name = ?", (course,)
        ).fetchone()
        if course_row:
            courses_data = {course: json.loads(course_row[0])}
        else:
            courses_data = {}
        round_rows = db.execute(
            "SELECT * FROM rounds WHERE course_name = ? ORDER BY date", (course,)
        ).fetchall()
        rounds_data = []
        for rr in round_rows:
            rdict = {
                "date": rr["date"],
                "course": rr["course_name"],
                "holes_selection": rr["holes_played"] or "all",
                "handicap_index": rr["computed_handicap"] or "15.0",
                "total_gross": rr["total_gross"] or "0",
                "total_putts": rr["total_putts"] or "0",
                "holes": json.loads(rr["holes"]) if rr["holes"] else {},
            }
            rounds_data.append(rdict)
    finally:
        db.close()

    output_dir = _pd() / "yardage_books" / safe
    output_dir.mkdir(parents=True, exist_ok=True)

    job = {
        "status": "running",
        "current": 0,
        "total": 25,
        "detail": "Starting...",
        "error": None,
        "output_dir": output_dir,
        "started_at": time.time(),
    }
    _pdf_jobs[job_id] = job

    req_settings = request.get_json() or {}

    def _generate_job():
        try:
            from .pdf import generate_book

            slot1 = req_settings.get("slot1", "green_grid")
            slot2 = req_settings.get("slot2", "stats_panel")
            show_stats = req_settings.get("show_stats", True)
            pdf_settings = req_settings.get("settings", {})

            def _progress(current, total):
                job["current"] = current
                job["total"] = total

            def _status(msg):
                job["detail"] = msg

            generate_book(
                course_name=course,
                output_dir=output_dir,
                slot1_mode=slot1,
                slot2_mode=slot2,
                show_calculated_stats=show_stats,
                settings=pdf_settings,
                progress_callback=_progress,
                status_callback=_status,
                data_dir=data_dir,
                courses_data=courses_data,
                rounds_data=rounds_data,
            )

            import shutil
            zip_path = output_dir / f"{safe}.zip"
            shutil.make_archive(str(zip_path.with_suffix("")), "zip", output_dir / "booklets")

            try:
                _db = sqlite3.connect(str(current_app.config["DB_PATH"]))
                _db.execute(
                    "UPDATE plugin_cartographer_geometry SET pdf_generated_at = ? WHERE course_name = ?",
                    (now, course),
                )
                if _db.total_changes == 0:
                    _db.execute(
                        "INSERT INTO plugin_cartographer_geometry (user_id, course_name, pdf_generated_at) VALUES (?, ?, ?)",
                        (user_id, course, now),
                    )
                _db.commit()
                _db.close()
            except Exception:
                log.warning("cartographer: failed to write pdf_generated_at", exc_info=True)

            job["status"] = "complete"

        except Exception as e:
            log.exception("cartographer: PDF generation failed for %s", course)
            job["status"] = "error"
            job["error"] = str(e)

    t = threading.Thread(target=_generate_job, daemon=True)
    t.start()

    return jsonify({"status": "ok", "job_id": job_id})


@bp.route("/<string:course>/pdf/status/<job_id>")
def pdf_status(course, job_id):
    job = _pdf_jobs.get(job_id)
    if job is None:
        return jsonify({"status": "unknown", "error": "Job not found"}), 404
    return jsonify({
        "status": job["status"],
        "current": job["current"],
        "total": job["total"],
        "detail": job["detail"],
        "error": job.get("error"),
    })


@bp.route("/<string:course>/pdf/stream/<job_id>")
def pdf_stream(course, job_id):
    def _generate():
        while True:
            job = _pdf_jobs.get(job_id)
            if job is None:
                yield f"event: error\ndata: {json.dumps('Job not found')}\n\n"
                return
            if job["status"] == "running":
                yield f"event: progress\ndata: {json.dumps({'current': job['current'], 'total': job['total']})}\n\n"
                yield f"event: status\ndata: {json.dumps(job['detail'])}\n\n"
            elif job["status"] == "complete":
                yield f"event: complete\ndata: {json.dumps({'download_url': f'/plugins/cartographer/{quote(course)}/pdf/download'})}\n\n"
                return
            elif job["status"] == "error":
                yield f"event: error\ndata: {json.dumps(job['error'])}\n\n"
                return
            time.sleep(0.5)

    response = Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


@bp.route("/<string:course>/pdf/download")
def pdf_download(course):
    from .data import _get_plugin_data_dir
    safe = course.lower().replace(" ", "_").replace("'", "").replace('"', "")
    zip_path = _get_plugin_data_dir() / "yardage_books" / safe / f"{safe}.zip"
    if not zip_path.exists():
        abort(404, "PDF not found. Generate it first.")
    return send_file(
        zip_path,
        as_attachment=True,
        download_name=f"{safe}_yardage_book.zip",
    )
