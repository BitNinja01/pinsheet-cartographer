"""Cartographer plugin for PinSheet Server.

Generates golf yardage book PDFs from OpenStreetMap course geometry.
Provides hole viewer, course gallery, and course picker web pages.
"""
from __future__ import annotations

import importlib
import logging
import shutil
import sqlite3
import subprocess
from pathlib import Path

from flask import Blueprint

log = logging.getLogger("pinsheet")

# JS prefix used to identify our foot block for cleanup
_FOOT_JS_MARKER = "data-action=upload-osm"

plugin_info = {
    "name": "cartographer",
    "version": "1.5.2",
    "description": "Course geometry, hole diagrams, and yardage book generation",
    "author": "PinSheet",
}


def _install_fonts() -> None:
    fonts_dir = Path(__file__).parent / "fonts" / "JetBrainsMono"
    target_dir = Path.home() / ".local" / "share" / "fonts" / "pinsheet"
    target_dir.mkdir(parents=True, exist_ok=True)
    needs_cache = False
    for ttf in fonts_dir.glob("*.ttf"):
        dst = target_dir / ttf.name
        if not dst.exists() or dst.stat().st_size != ttf.stat().st_size:
            shutil.copy2(ttf, dst)
            needs_cache = True
    if needs_cache and shutil.which("fc-cache"):
        subprocess.run(["fc-cache", "-f"], check=False)


def _create_tables(db_path: Path) -> None:
    db = sqlite3.connect(str(db_path))
    db.execute("""
        CREATE TABLE IF NOT EXISTS plugin_cartographer_geometry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            course_name TEXT NOT NULL,
            tagged_at TEXT,
            pixels_per_yard REAL,
            feature_count INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, course_name)
        )
    """)
    db.commit()
    db.close()


def _migrate_tables(db_path: Path) -> None:
    db = sqlite3.connect(str(db_path))
    try:
        db.execute(
            "ALTER TABLE plugin_cartographer_geometry ADD COLUMN pdf_generated_at TEXT"
        )
    except sqlite3.OperationalError:
        pass
    db.close()


def _ensure_cairo() -> bool:
    """Ensure the libcairo2 system library is available for cairosvg.

    Detects missing libcairo and attempts to install it via the system
    package manager (best-effort, requires passwordless sudo). Returns True
    if cairo is available, False if PDF export cannot work.
    """
    try:
        __import__("cairosvg")
    except ImportError:
        return False

    try:
        from cairosvg import svg2pdf
        svg2pdf(bytestring=b"<svg xmlns='http://www.w3.org/2000/svg'><rect width='10' height='10'/></svg>")
        return True
    except OSError:
        pass

    for cmd in (
        ["sudo", "apt-get", "install", "-y", "libcairo2"],
        ["sudo", "dnf", "install", "-y", "cairo"],
        ["sudo", "yum", "install", "-y", "cairo"],
        ["sudo", "pacman", "-S", "--noconfirm", "cairo"],
        ["brew", "install", "cairo"],
    ):
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if result.returncode == 0:
                log.info("cartographer: installed libcairo via %s", cmd[0])
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        except Exception:
            continue

    return False


def _course_actions(course_name: str) -> list[dict]:
    from .data import get_osm_path, load_courses_geo
    import sqlite3
    from datetime import datetime, timezone

    osm_path = get_osm_path(course_name)
    has_osm = osm_path.exists()

    courses_geo = load_courses_geo()
    geo = courses_geo.get(course_name, {})
    has_geometry = bool(geo.get("holes", {}))

    pdf_status = None
    try:
        from flask import current_app
        db = sqlite3.connect(str(current_app.config["DB_PATH"]))
        row = db.execute(
            "SELECT pdf_generated_at FROM plugin_cartographer_geometry WHERE course_name = ?",
            (course_name,),
        ).fetchone()
        db.close()
        if row and row[0]:
            ts = row[0]
            days = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).days
            pdf_status = "stale" if days >= 182 else "fresh"
    except Exception:
        pass

    from urllib.parse import quote
    encoded = quote(course_name)
    actions = []

    if has_osm:
        actions.append({"label": "View", "url": f"/plugins/cartographer/{encoded}/gallery"})
        actions.append({"label": "Tag", "url": f"/plugins/cartographer/{encoded}/tag"})
        actions.append({"label": "Delete OSM", "url": "#", "attrs": {"data-action": "delete-osm", "data-course": course_name}})
    elif has_geometry:
        actions.append({"label": "View", "url": f"/plugins/cartographer/{encoded}/gallery"})
        actions.append({"label": "Upload OSM", "url": "#", "attrs": {"data-action": "upload-osm", "data-course": course_name}})
    else:
        actions.append({"label": "Upload OSM", "url": "#", "attrs": {"data-action": "upload-osm", "data-course": course_name}})

    if pdf_status == "fresh":
        from .data import get_plugin_data_dir
        safe = course_name.lower().replace(" ", "_").replace("'", "").replace('"', "")
        zip_path = get_plugin_data_dir() / "yardage_books" / safe / f"{safe}.zip"
        if zip_path.exists():
            actions.append({"label": "Download PDF", "url": f"/plugins/cartographer/{encoded}/pdf/download"})
            actions.append({"label": "Regenerate PDF", "url": f"/plugins/cartographer/{encoded}/pdf"})
        elif has_geometry:
            actions.append({"label": "Generate PDF", "url": f"/plugins/cartographer/{encoded}/pdf"})
    elif pdf_status == "stale":
        actions.append({"label": "Regen PDF", "url": f"/plugins/cartographer/{encoded}/pdf"})
    elif has_geometry:
        actions.append({"label": "Generate PDF", "url": f"/plugins/cartographer/{encoded}/pdf"})

    return actions


def register(app):
    # 0. Ensure system deps for cairosvg (best-effort)
    try:
        if not _ensure_cairo():
            log.warning("cartographer: libcairo not found and auto-install failed. PDF export will error. Install manually: sudo apt install libcairo2")
    except Exception:
        log.warning("cartographer: system dep check failed", exc_info=True)

    # 1. Set server-aware data directory
    carto_data = importlib.import_module(__name__ + ".data")
    carto_data._server_data_dir = Path(app.config["DATA_DIR"]) / "plugins" / "pinsheet-cartographer"
    carto_data._server_data_dir.mkdir(parents=True, exist_ok=True)

    # 2. Install fonts (best-effort)
    try:
        _install_fonts()
    except Exception:
        log.warning("cartographer: font installation failed", exc_info=True)

    # 3. Create DB tables
    try:
        _create_tables(app.config["DB_PATH"])
    except Exception:
        log.warning("cartographer: DB table creation failed", exc_info=True)

    try:
        _migrate_tables(app.config["DB_PATH"])
    except Exception:
        log.warning("cartographer: table migration failed", exc_info=True)

    # 4. Log XML parser availability for diagnostic
    try:
        import lxml.etree  # noqa: F401
        log.info("cartographer: using lxml XML parser")
    except ImportError:
        log.info("cartographer: lxml not available — using stdlib xml.etree.ElementTree (slower, fewer features)")

    # 5. Register Blueprint
    try:
        _bp_spec = importlib.util.spec_from_file_location(
            __name__ + ".blueprint", str(Path(__file__).parent / "blueprint.py"),
        )
        _bp_mod = importlib.util.module_from_spec(_bp_spec)
        import sys as _sys
        _sys.modules[_bp_mod.__name__] = _bp_mod
        _bp_spec.loader.exec_module(_bp_mod)
        app.register_blueprint(_bp_mod.bp)

        _csrf = app.extensions.get("csrf")
        if _csrf is not None:
            for _rule in app.url_map.iter_rules():
                if _rule.endpoint and _rule.endpoint.startswith("cartographer.") and (_rule.methods & {"POST", "DELETE"}):
                    _csrf.exempt(app.view_functions[_rule.endpoint])
    except Exception as _exc:
        log.warning("cartographer: blueprint registration failed — %s", _exc)

    # 6. Inject CSS
    head_tag = '<link rel="stylesheet" href="/plugins/cartographer/static/cartographer.css">'
    app._plugin_blocks["head"] = (
        (app._plugin_blocks.get("head", "") + "\n" + head_tag).strip()
    )

    app._plugin_course_actions.append({"actions_fn": _course_actions})

    _detail_actions_js = (
        '<script>'
        '(function(){'
        'var m=location.pathname.match(/^\\/courses\\/([^/]+)$/);'
        'if(m&&document.querySelector(".round-actions")){'
        'var enc=encodeURIComponent(decodeURIComponent(m[1]));'
        'fetch("/plugins/cartographer/"+enc+"/actions-html").then(function(r){return r.text()}).then(function(h){'
        'if(h){'
        'var div=document.querySelector(".round-actions");'
        'div.insertAdjacentHTML("beforeend",h);'
        '}'
        '});'
        '}'
        '})();'
        'document.addEventListener("click",function(e){'
        'var t=e.target.closest("[data-action=upload-osm]");'
        'if(!t)return;'
        'var inp=document.createElement("input");'
        'inp.type="file";inp.accept=".osm";inp.style.display="none";'
        'inp.addEventListener("change",function(){'
        'var f=this.files[0];if(!f)return;'
        'var fd=new FormData();fd.append("osm_file",f);'
        'var btn=t;btn.textContent="Uploading...";'
        'fetch("/plugins/cartographer/"+encodeURIComponent(t.getAttribute("data-course"))+"/upload-osm",{method:"POST",body:fd})'
        '.then(function(r){if(r.ok){location.reload()}else{return r.json().then(function(d){btn.textContent=d.message||"Upload failed";setTimeout(function(){btn.textContent="Upload OSM"},3000)})}})'
        '.catch(function(){btn.textContent="Network error";setTimeout(function(){btn.textContent="Upload OSM"},3000)});'
        '});'
        'document.body.appendChild(inp);inp.click();document.body.removeChild(inp);'
        '});'
        'document.addEventListener("click",function(e){'
        'var t=e.target.closest("[data-action=delete-osm]");'
        'if(!t)return;'
        'e.preventDefault();'
        'if(!confirm("Delete cached OSM and elevation data for this course?"))return;'
        'var btn=t;var orig=btn.textContent;btn.textContent="Deleting...";'
        'fetch("/plugins/cartographer/"+encodeURIComponent(t.getAttribute("data-course"))+"/osm",{method:"DELETE"})'
        '.then(function(r){if(r.ok){location.reload()}else{return r.json().then(function(d){alert(d.message||"Delete failed");btn.textContent=orig})}})'
        '.catch(function(){alert("Network error");btn.textContent=orig});'
        '});'
        '</script>'
    )
    app._plugin_blocks["foot"] = (
        (app._plugin_blocks.get("foot", "") + "\n" + _detail_actions_js).strip()
    )

    # 8. Nav link removed — cartographer actions are on the core Courses pages

    # 9. Default settings
    app.config.setdefault("plugins.cartographer.yardage_arcs", True)
    app.config.setdefault("plugins.cartographer.yardage_arc_distances", [100, 125, 150, 175, 200])

    log.info("cartographer: registered v%s", plugin_info["version"])


def unregister(app):
    carto_data = importlib.import_module(__name__ + ".data")
    carto_data._server_data_dir = None
    app.config.pop("plugins.cartographer.yardage_arcs", None)
    app.config.pop("plugins.cartographer.yardage_arc_distances", None)

    head_tag = '<link rel="stylesheet" href="/plugins/cartographer/static/cartographer.css">'
    current_head = app._plugin_blocks.get("head", "")
    app._plugin_blocks["head"] = current_head.replace(head_tag, "").strip()

    current_foot = app._plugin_blocks.get("foot", "")
    if _FOOT_JS_MARKER in current_foot:
        import re as _re
        app._plugin_blocks["foot"] = _re.sub(
            r'<script[^>]*>.*?' + _re.escape(_FOOT_JS_MARKER) + r'.*?</script>',
            "", current_foot, flags=_re.DOTALL
        ).strip()

    app._plugin_course_actions.clear()
